from bson import ObjectId
from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
import os
from dotenv import load_dotenv
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from datetime import datetime

load_dotenv()

auth_bp = Blueprint("auth", __name__)

# -------------------------------
# MongoDB Setup (single collection)
# -------------------------------
MONGO_URI = os.getenv("MONGODB_URI")
client = MongoClient(MONGO_URI)
db = client["learnflow"]
users_collection = db["users"]  # single collection for all users

# ===============================
# Register Endpoint
# ===============================
@auth_bp.route("/api/register", methods=["POST"])
def register():
    data = request.json
    role = data.get("role")

    if role not in ["student", "mentor"]:
        return jsonify({"success": False, "message": "Invalid role"}), 400

    user_data = {
        "fullName": data.get("fullName"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "password": generate_password_hash(data.get("password")),
        "role": role,
        "extra": data.get("extra")  # education or expertise
    }

    # Check if email already exists in the users collection
    if users_collection.find_one({"email": user_data["email"]}):
        return jsonify({"success": False, "message": "Email already exists"}), 400
    
        # ✅ AUTO-CREATED FOR STUDENTS
    if role == "student":
        user_data["progress"] = {
            "days": {
                "1": {"completed": False, "score": None},
                "2": {"completed": False, "score": None},
                "3": {"completed": False, "score": None},
                "4": {"completed": False, "score": None},
                "5": {"completed": False, "score": None},
            },
            "finalAssessmentCompleted": False
        }

    users_collection.insert_one(user_data)
    return jsonify({"success": True, "message": f"{role.title()} registered successfully"})


# ===============================
# Login Endpoint
# ===============================
@auth_bp.route("/api/login", methods=["POST"])
def login():
    data = request.json
    role = data.get("role")
    password = data.get("password")

    if role not in ["student", "mentor"]:
        return jsonify({"success": False, "message": "Invalid role"}), 400

    # Fetch user
    if role == "student":
        email = data.get("email", "").strip()
        user = users_collection.find_one({"email": email, "role": "student"})
    else:
        full_name = data.get("fullName", "").strip()
        user = users_collection.find_one({"fullName": full_name, "role": "mentor"})

    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    if not check_password_hash(user["password"], password):
        return jsonify({"success": False, "message": "Incorrect password"}), 401

    # ✅ Set isActive to True on login
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"lastLogin": datetime.utcnow(), "isActive": True}}
    )

    # Create JWT
    access_token = create_access_token(identity=str(user["_id"]))

    return jsonify({
        "success": True,
        "message": "Login successful",
        "role": role,
        "token": access_token,
        "fullName": user["fullName"]
    })


# ===============================
# Logout Endpoint
# ===============================
@auth_bp.route("/api/logout", methods=["POST"])
@jwt_required()
def logout():
    try:
        user_id = get_jwt_identity()
        # ✅ Set isActive to False on logout
        users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"lastLogin": None, "isActive": False}}
        )
    except Exception:
        return jsonify({"success": False, "message": "Invalid userId or token"}), 400

    return jsonify({"success": True, "message": "Logged out successfully"})
