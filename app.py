from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import re
import time
from functools import lru_cache
from dotenv import load_dotenv
import random
from auth import auth_bp
from progress import progress_bp
import cohere
from flask_jwt_extended import JWTManager
from mentor import mentor_bp

# ===============================
# Load environment variables
# ===============================
load_dotenv()

# ===============================
# Flask App Setup
# ===============================
app = Flask(__name__)
CORS(app)

# JWT setup
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "super-secret-key")
jwt = JWTManager(app)

# Register the auth Blueprint
app.register_blueprint(auth_bp)
app.register_blueprint(progress_bp)
app.register_blueprint(mentor_bp)

# ===============================
# Cohere Client Initialization
# ===============================
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
client = None

if COHERE_API_KEY:
    client = cohere.ClientV2(api_key=COHERE_API_KEY)
    print("✅ Cohere API initialized successfully")
else:
    print("⚠️ No Cohere API key found – using sample questions")

print("COHERE_API_KEY:", COHERE_API_KEY)
print("Client initialized?", client is not None)

# ===============================
# Training Days Data
# ===============================
TRAINING_DAYS = [
    {"day": 1, "topics": "4G to 5G Evolution, 5G Overview, 5G Service Categories, Use Cases"},
    {"day": 2, "topics": "5G RAN, ORAN, 5G Core, OSS/BSS, IMS"},
    {"day": 3, "topics": "5G QoS, MEC, Network Slicing, 5G and AI"},
    {"day": 4, "topics": "5G Registration, Authentication, PDU Session, Call Analysis"},
    {"day": 5, "topics": "PDU Sessions, Voice/Data Calls, Paging, Handover"}
]

# ===============================
# Sample Questions (Fallback)
# ===============================
def get_sample_questions(day, level):
    # Determine file path for the day
    file_path = os.path.join(os.path.dirname(__file__), f"day{day}_questions.json")

    # Check if JSON exists
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            data = json.load(f)

        # Return the requested level if available, else default to beginner
        return data.get(level.lower(), data.get("beginner"))

    # Fallback if file not found
    return {
        "mcqs": [
            {"question": "Fallback MCQ?", "options": {"A": "Yes", "B": "No", "C": "Maybe", "D": "None"}, "correct": "A"}
        ],
        "theory": [
            {"question": "Fallback Theory?", "answer": "Fallback answer.", "keyPoints": []}
        ]
    }

# ===============================
# Caching for generated questions
# ===============================
question_cache = {}  # key = (day, level)

# ===============================
# Generate AI Questions with retry & cache (Cohere Chat API)
# ===============================
def generate_questions_safe(day, level, retries=3):
    # Return cached questions if available
    cache_key = (day, level)
    if cache_key in question_cache:
        return question_cache[cache_key]

    topics = TRAINING_DAYS[day - 1]["topics"]

    prompt = f"""
You are a senior 5G telecom instructor.

Generate EXACTLY:
- 10 multiple-choice questions (A, B, C, D) with correct answer
- 5 theory questions with expected keywords

Difficulty Level: {level}
- beginner: simple concepts, easy questions
- intermediate: moderate difficulty, some technical depth
- advanced: deep technical concepts, complex questions

Topics to cover: {topics}

Return ONLY valid JSON in this format, nothing else:

{{
  "mcqs": [
    {{"question": "", "options": {{"A": "", "B": "", "C": "", "D": ""}}, "correct": "A", "explanation": ""}}
  ],
  "theory": [
    {{"question": "", "answer": "", "keyPoints": []}}
  ]
}}
"""

    for attempt in range(retries):
        try:
            response = client.chat(
                model="command-a-03-2025",  # use the latest Cohere chat model
                messages=[
                    {"role": "system", "content": "You are a senior 5G telecom instructor."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )

            # Get text content directly
            content = response.message.content[0].text if response.message.content else response.message.text
            print("💡 Cohere Raw Response:", content)
            
            # Strip triple backticks if present
            content_clean = re.sub(r"^```json|```$", "", content.strip(), flags=re.MULTILINE)

            # Parse JSON
            try:
                questions = json.loads(content_clean)
                question_cache[cache_key] = questions
                return questions
            except json.JSONDecodeError as e:
                print("⚠️ JSON parsing failed:", e)

            print("⚠️ JSON parsing failed, retrying...")

        except Exception as e:
            wait = (attempt + 1) * 2 + random.random()
            print(f"❌ Cohere error: {e}, retrying in {wait:.1f}s...")
            time.sleep(wait)

    print("❌ All retries failed, using fallback")
    questions = get_sample_questions(day, level)
    question_cache[cache_key] = questions
    return questions


# ===============================
# Generate Questions Route
# ===============================
@app.route("/api/generate-questions", methods=["POST"])
def generate_questions():
    data = request.json
    day = data.get("day", 1)
    level = data.get("level", "beginner")

    if not client:
        print("⚠️ AI client not initialized, returning static questions")
        return jsonify(get_sample_questions(day, level))

    questions = generate_questions_safe(day, level)
    return jsonify(questions)

# ===============================
# Final Assessment Route
# ===============================
@app.route("/api/final-assessment", methods=["GET"])
def get_final_assessment():
    try:
        # Try to generate AI-based theory questions first (if client available)
        if client:
            try:
                # Compose a prompt asking for 5 theory questions only
                prompt = f"""
You are a senior 5G telecom instructor. Generate exactly 5 theory-style questions
about 5G technology suitable for a final assessment. For each question include a
concise model answer and 3-6 expected keywords (keyPoints).

Return ONLY valid JSON in this exact format:

{{
  "theory": [
    {{"question": "", "answer": "", "keyPoints": ["", ""]}}
  ]
}}
"""

                response = client.chat(
                    model="command-a-03-2025",
                    messages=[
                        {"role": "system", "content": "You are a senior 5G telecom instructor."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.6,
                    max_tokens=1200
                )

                # Extract text content (handle V2 response shape)
                content = response.message.content[0].text if response.message.content else response.message.text
                content_clean = re.sub(r"^```json|```$", "", content.strip(), flags=re.MULTILINE)

                try:
                    ai_questions = json.loads(content_clean)
                    theory = ai_questions.get("theory", [])

                    # Validate we have at least 5 questions
                    if isinstance(theory, list) and len(theory) >= 5:
                        selected = random.sample(theory, 5)
                        return jsonify({"theory": selected})
                    # if not enough, fall through to fallback file
                except json.JSONDecodeError:
                    # fall through to file fallback
                    pass
            except Exception as e:
                # any AI error -> fallback to final.json
                print("⚠️ AI generation failed for final assessment:", e)

        # Fallback: load static final.json
        file_path = os.path.join(os.path.dirname(__file__), "final.json")

        if not os.path.exists(file_path):
            return jsonify({"error": "final.json not found"}), 404

        with open(file_path, "r") as f:
            data = json.load(f)

        questions = data.get("theory", [])

        if len(questions) < 5:
            return jsonify({"error": "Not enough questions in final.json"}), 400

        # Randomly select 5 questions from the static file
        selected_questions = random.sample(questions, 5)
        return jsonify({"theory": selected_questions})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===============================
# Health Check
# ===============================
@app.route("/api/health")
def health():
    return jsonify({
        "status": "OK",
        "ai_enabled": client is not None
    })

# ===============================
# Run Server
# ===============================
if __name__ == "__main__":
    print("🚀 Backend running at http://localhost:5000")
    app.run(debug=True)
