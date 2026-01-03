from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from datetime import datetime
import os
from dotenv import load_dotenv
from bson import ObjectId
from flask_jwt_extended import jwt_required, get_jwt_identity
import re

load_dotenv()

progress_bp = Blueprint("progress", __name__, url_prefix="/progress")

# MongoDB
MONGO_URI = os.getenv("MONGODB_URI")
client = MongoClient(MONGO_URI)
db = client["learnflow"]
users = db["users"]
attempts = db["attempts"]


# ---------------------------------------
# Save / Update Day Progress
# ---------------------------------------
@progress_bp.route("/update", methods=["POST"])
@jwt_required()
def update_progress():
    data = request.json or {}

    # Prefer JWT identity for user id; fall back to provided userId if any
    try:
        user_id = get_jwt_identity()
    except Exception:
        user_id = data.get("userId")

    day = str(data.get("day"))  # keep string keys
    score = data.get("score")
    completed = data.get("completed", True)

    if not user_id or not day:
        return jsonify({"success": False, "message": "Missing data"}), 400

    try:
        user_object_id = ObjectId(user_id)
    except Exception:
        return jsonify({"success": False, "message": "Invalid userId"}), 400

    # 1️⃣ Ensure progress object exists
    users.update_one(
        {
            "_id": user_object_id,
            "$or": [
                {"progress": {"$exists": False}},
                {"progress": {"$not": {"$type": "object"}}},
            ]
        },
        {
            "$set": {
                "progress": {
                    "days": {},
                    "finalAssessmentCompleted": False,
                    "createdAt": datetime.utcnow()
                }
            }
        }
    )

    # 2️⃣ Build safe update
    update_data = {
        f"progress.days.{day}.completed": completed,
        "progress.updatedAt": datetime.utcnow()
    }

    if score is not None:
        update_data[f"progress.days.{day}.score"] = score

    # Optional: store per-question attempt details if provided
    # We separate short MCQ metadata (kept on the progress doc) from long
    # theory responses which we store in a separate `attempts` collection.
    raw_questions = data.get("questions") or data.get("attempt")
    mcq_sanitized = []
    theory_entries = []
    if raw_questions and isinstance(raw_questions, list):
        for idx, q in enumerate(raw_questions):
            try:
                qtext = (q.get("question") or q.get("q") or "")
                qtype = q.get("type") or q.get("qtype") or ("theory" if q.get("answer") or q.get("selectedAnswer") and isinstance(q.get("selectedAnswer"), str) else "mcq")

                if str(qtype).lower() == "mcq" or q.get("options"):
                    mcq_sanitized.append({
                        "question": qtext[:500],
                        "difficulty": q.get("difficulty"),
                        "correctAnswer": q.get("correctAnswer") if "correctAnswer" in q else q.get("correct"),
                        "selectedAnswer": q.get("selectedAnswer") if "selectedAnswer" in q else q.get("selected"),
                        "options": (q.get("options") or [])[:10],
                    })
                else:
                    # theory entry: store minimal metadata here, full text goes to attempts collection
                    theory_entries.append({
                        "index": idx,
                        "question": qtext[:2000],
                        "expectedKeywords": q.get("expectedKeywords") or q.get("keyPoints") or [],
                        "selectedAnswer": q.get("selectedAnswer") if "selectedAnswer" in q else q.get("answer") or q.get("selected"),
                    })
            except Exception:
                continue

        # attach MCQ metadata to progress doc
        if mcq_sanitized:
            update_data[f"progress.days.{day}.attempts"] = mcq_sanitized

        # if there are theory answers, store them in `attempts` collection and reference them
        if theory_entries:
            attempt_doc = {
                "userId": ObjectId(user_id),
                "day": day,
                "level": data.get("level"),
                "createdAt": datetime.utcnow(),
                "theoryResponses": theory_entries,
            }
            res = attempts.insert_one(attempt_doc)
            update_data[f"progress.days.{day}.theoryRef"] = str(res.inserted_id)
            update_data[f"progress.days.{day}.submittedAt"] = datetime.utcnow()
            if data.get("level"):
                update_data[f"progress.days.{day}.level"] = data.get("level")

    # 3️⃣ Update nested day safely
    users.update_one(
        {"_id": user_object_id},
        {"$set": update_data}
    )

    return jsonify({"success": True})

@progress_bp.route("/<user_id>", methods=["GET"])
def get_progress(user_id):
    try:
        user = users.find_one(
            {"_id": ObjectId(user_id)},
            {"_id": 0, "progress": 1}
        )
    except Exception:
        return jsonify({"success": False, "message": "Invalid userId"}), 400

    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    progress = user.get("progress", {})
    days = progress.get("days", {})

    completed_days = [
        int(day) for day, data in days.items() if data.get("completed")
    ]

    max_completed_day = max(completed_days) if completed_days else 0

    scores = [
        d.get("score") for d in days.values()
        if isinstance(d.get("score"), (int, float))
    ]

    if scores:
        avg_score = round(sum(scores) / len(scores))
    else:
        avg_score = 0

    return jsonify({
        "completedDays": max_completed_day,
        "testsPassed": len(completed_days),
        "averageScore": f"{avg_score}%",
        "finalAssessmentCompleted": progress.get(
            "finalAssessmentCompleted", False
        ),
        "progress": days
    })

@progress_bp.route("/final", methods=["POST"])
@jwt_required()
def complete_final_assessment():
    user_id = get_jwt_identity()

    users.update_one(
        {"_id": ObjectId(user_id)},
        {
            "$set": {
                "progress.finalAssessmentCompleted": True,
                "progress.updatedAt": datetime.utcnow()
            }
        }
    )

    return jsonify({"success": True})

@progress_bp.route("/progress", methods=["GET"])
@jwt_required()
def get_progress_jwt():
    user_id = get_jwt_identity()  # MongoDB _id as string
    user = users.find_one({"_id": ObjectId(user_id)})

    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    # Keep the full progress object and expose the days map + final flag
    progress_obj = user.get("progress", {})
    days = progress_obj.get("days", {})

    completed_days = sum(1 for d in days.values() if d.get("completed"))
    tests_passed = completed_days

    # Calculate average score safely (ignore missing/None scores)
    scores = [d.get("score") for d in days.values() if isinstance(d.get("score"), (int, float))]
    if scores:
        average_score = f"{int(sum(scores) / len(scores))}%"
    else:
        average_score = "0%"

    return jsonify({
        "progress": days,
        "completedDays": completed_days,
        "testsPassed": tests_passed,
        "averageScore": average_score,
        "finalAssessmentCompleted": bool(progress_obj.get("finalAssessmentCompleted", False))
    })


# ---------------------------------------
# Mentor / Attempt retrieval (secure)
# ---------------------------------------
@progress_bp.route("/attempts/<user_id>/<day>", methods=["GET"])
@jwt_required()
def get_attempts_for_day(user_id, day):
    """Return MCQ metadata (from progress.days.<day>.attempts) and
    any stored theory responses from the `attempts` collection.

    Access rules:
    - Mentors/admins/teachers may view any student's full data.
    - A student may view their own attempts but will NOT see
      `correctAnswer` fields (redacted).
    """
    caller_id = get_jwt_identity()

    # Validate IDs
    try:
        caller_obj = users.find_one({"_id": ObjectId(caller_id)})
    except Exception:
        return jsonify({"success": False, "message": "Invalid caller id"}), 400

    caller_role = (caller_obj or {}).get("role", "student")
    is_mentor_like = str(caller_role).lower() in ("mentor", "admin", "teacher")

    # allow if mentor-like or the owner
    allowed = is_mentor_like or (str(caller_id) == str(user_id))
    if not allowed:
        return jsonify({"success": False, "message": "Forbidden"}), 403

    # Load the user's progress/day entry
    try:
        user_doc = users.find_one({"_id": ObjectId(user_id)}, {"progress.days": 1})
    except Exception:
        return jsonify({"success": False, "message": "Invalid user id"}), 400

    if not user_doc:
        return jsonify({"success": False, "message": "User not found"}), 404

    progress_obj = user_doc.get("progress", {})
    days = progress_obj.get("days", {})
    day_data = days.get(str(day), {})

    mcq_attempts = day_data.get("attempts", []) or []

    # If caller is not mentor-like, redact correctAnswer from MCQ attempts
    if not is_mentor_like:
        for a in mcq_attempts:
            if isinstance(a, dict) and "correctAnswer" in a:
                a.pop("correctAnswer", None)

    theory_responses = None
    theory_ref = day_data.get("theoryRef")
    if theory_ref:
        try:
            att = attempts.find_one({"_id": ObjectId(theory_ref)})
            if att:
                # include the full theoryResponses only for mentors or the owner
                theory_responses = att.get("theoryResponses", [])
        except Exception:
            theory_responses = None

    return jsonify({
        "success": True,
        "mcq": mcq_attempts,
        "theory": theory_responses,
        "dayMeta": day_data,
    })


@progress_bp.route("/attempts/by-email/<path:email>/<day>", methods=["GET"])
@jwt_required()
def get_attempts_for_day_by_email(email, day):
    """Find user by email (case-insensitive) and return the same payload as
    `/progress/attempts/<user_id>/<day>`.
    """
    caller_id = get_jwt_identity()

    # find user by email (case-insensitive)
    try:
        # email passed via URL will be percent-encoded; Flask provides decoded value
        user_doc = users.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}, {"_id": 1, "progress": 1})
    except Exception:
        return jsonify({"success": False, "message": "Invalid email or DB error"}), 400

    if not user_doc:
        return jsonify({"success": False, "message": "User not found for email"}), 404

    # now reuse same logic as get_attempts_for_day but without duplicating role lookup DB fetch
    # Resolve caller role
    try:
        caller_obj = users.find_one({"_id": ObjectId(caller_id)})
    except Exception:
        return jsonify({"success": False, "message": "Invalid caller id"}), 400

    caller_role = (caller_obj or {}).get("role", "student")
    is_mentor_like = str(caller_role).lower() in ("mentor", "admin", "teacher")

    # allow if mentor-like or the owner
    allowed = is_mentor_like or (str(caller_id) == str(user_doc.get("_id")))
    if not allowed:
        return jsonify({"success": False, "message": "Forbidden"}), 403

    progress_obj = user_doc.get("progress", {})
    days = progress_obj.get("days", {})
    day_data = days.get(str(day), {})

    mcq_attempts = day_data.get("attempts", []) or []

    if not is_mentor_like:
        for a in mcq_attempts:
            if isinstance(a, dict) and "correctAnswer" in a:
                a.pop("correctAnswer", None)

    theory_responses = None
    theory_ref = day_data.get("theoryRef")
    if theory_ref:
        try:
            att = attempts.find_one({"_id": ObjectId(theory_ref)})
            if att:
                theory_responses = att.get("theoryResponses", [])
        except Exception:
            theory_responses = None

    return jsonify({
        "success": True,
        "mcq": mcq_attempts,
        "theory": theory_responses,
        "dayMeta": day_data,
    })


@progress_bp.route("/by-email/<path:email>", methods=["GET"])
@jwt_required()
def get_progress_by_email(email):
    """Find user by email and return their `progress.days` map (no theory inlining).

    Access rules: mentors/admins can view any student's progress; a student can
    view their own progress.
    """
    caller_id = get_jwt_identity()

    # find user by email (case-insensitive)
    try:
        user_doc = users.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}, {"_id": 1, "progress": 1})
    except Exception:
        return jsonify({"success": False, "message": "Invalid email or DB error"}), 400

    if not user_doc:
        return jsonify({"success": False, "message": "User not found for email"}), 404

    # Resolve caller role
    try:
        caller_obj = users.find_one({"_id": ObjectId(caller_id)})
    except Exception:
        return jsonify({"success": False, "message": "Invalid caller id"}), 400

    caller_role = (caller_obj or {}).get("role", "student")
    is_mentor_like = str(caller_role).lower() in ("mentor", "admin", "teacher")

    allowed = is_mentor_like or (str(caller_id) == str(user_doc.get("_id")))
    if not allowed:
        return jsonify({"success": False, "message": "Forbidden"}), 403

    progress_obj = user_doc.get("progress", {})
    days = progress_obj.get("days", {})

    return jsonify({"success": True, "days": days})
