from flask import Blueprint, jsonify
from pymongo import MongoClient
from bson import ObjectId
import os
from dotenv import load_dotenv
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta

load_dotenv()

mentor_bp = Blueprint("mentor", __name__, url_prefix="/mentor")

# MongoDB
MONGO_URI = os.getenv("MONGODB_URI")
client = MongoClient(MONGO_URI)
db = client["learnflow"]
users_collection = db["users"]

# -----------------------------
# Mentor Dashboard Endpoint
# -----------------------------
@mentor_bp.route("/dashboard", methods=["GET"])
@jwt_required()
def mentor_dashboard():
    # Determine mentor's full name from JWT identity
    mentor_name = "Mentor"
    try:
        mentor_id = get_jwt_identity()
        if mentor_id:
            mentor_user = users_collection.find_one({"_id": ObjectId(mentor_id)})
            if mentor_user and mentor_user.get("fullName"):
                mentor_name = mentor_user.get("fullName")
    except Exception:
        # fallback to default mentor name
        mentor_name = "Mentor"

    students_cursor = users_collection.find({"role": "student"})
    students_list = []
    total_students = 0
    active_students = 0
    tests_reviewed = 0

    for student in students_cursor:
        total_students += 1
        is_active = student.get("isActive", False)
        if is_active:
            active_students += 1

        progress = student.get("progress", {}).get("days", {})
        completed_days = [int(day) for day, info in progress.items() if info.get("completed")]

        if completed_days:
            last_day_num = max(completed_days)
            last_day = f"Day {last_day_num}"
            score = progress.get(str(last_day_num), {}).get("score", 0)
        else:
            last_day = "N/A"
            score = 0

        progress_percent = round(len(completed_days) / 5 * 100)  # 5-day course
        tests_reviewed += len(completed_days)

        students_list.append({
            "fullName": student.get("fullName"),
            "email": student.get("email"),
            "progressPercent": progress_percent,
            "lastDay": last_day,
            "score": score,
            "isActive": is_active
        })

    response = {
        "fullName": mentor_name,
        "totalCourses": 1,
        "totalStudents": total_students,
        "activeStudents": active_students,
        "testsReviewed": tests_reviewed,
        "students": students_list
    }

    return jsonify(response)
