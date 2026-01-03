import os
from dotenv import load_dotenv
import cohere

# Load environment variables
load_dotenv()
api_key = os.getenv("COHERE_API_KEY")
if not api_key:
    raise ValueError("❌ COHERE_API_KEY not found")

# Initialize Cohere V2 client
co = cohere.ClientV2(api_key)

prompt = "Generate 1 simple MCQ question about 5G for beginners in JSON format."

try:
    response = co.chat(
        model="command-a-03-2025",
        messages=[{"role": "user", "content": prompt}]
    )

    # Extract text from the response
    output_text = response.message.content[0].text
    print("✅ Cohere Chat Response:")
    print(output_text)

except Exception as e:
    print("❌ Error:", e)
