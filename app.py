import os
import json
import uuid
import requests
from flask import Flask, request, jsonify, render_template, send_file
from dotenv import load_dotenv
import pdfplumber
from openai import OpenAI

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "plankreel-secret")

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# DeepSeek client (compatible with OpenAI SDK)
deepseek_client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

PROMPT_SYSTEM = """You are a professional video production director specializing in product installation videos.
Your task is to analyze an installation manual and generate structured video prompts for AI video generation (Kling/Seedance).

Each prompt segment should:
- Cover ONE clear installation action
- Be 8-15 seconds long
- Include: scene description, action, camera angle, lighting, style
- End state of each segment should naturally connect to the start of the next
- Be written in English
- Be cinematic, professional, and brand-appropriate

Output ONLY valid JSON in this exact format:
{
  "title": "Product Installation Guide",
  "total_segments": 5,
  "segments": [
    {
      "id": 1,
      "title": "Segment title",
      "duration_seconds": 10,
      "prompt": "Full cinematic prompt here...",
      "start_frame_note": "What the first frame should show",
      "end_frame_note": "What the last frame should show (becomes next segment's start)"
    }
  ]
}"""


def extract_pdf_text(filepath):
    text = ""
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text


def generate_prompts(manual_text, segment_count=5, product_name=""):
    user_message = f"""Installation manual content:
---
{manual_text[:6000]}
---

Product name: {product_name or 'Not specified'}
Generate exactly {segment_count} video segments covering the complete installation process.
Ensure each segment's end frame naturally connects to the next segment's start frame."""

    response = deepseek_client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": user_message}
        ],
        temperature=0.7,
        max_tokens=3000
    )

    content = response.choices[0].message.content
    # Extract JSON from response
    start = content.find("{")
    end = content.rfind("}") + 1
    return json.loads(content[start:end])


def generate_video_kling(prompt, duration=10):
    """Call Kling 3.0 API to generate a video segment."""
    api_key = os.getenv("KLING_API_KEY")
    api_secret = os.getenv("KLING_API_SECRET")

    if not api_key:
        return {"status": "skipped", "message": "Kling API key not configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "kling-v3",
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": "16:9",
        "mode": "pro"
    }

    try:
        resp = requests.post(
            "https://api.klingai.com/v1/videos/text2video",
            headers=headers,
            json=payload,
            timeout=30
        )
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate-prompts", methods=["POST"])
def api_generate_prompts():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    segment_count = int(request.form.get("segments", 5))
    product_name = request.form.get("product_name", "")

    if not file.filename.endswith(".pdf"):
        return jsonify({"error": "Only PDF files supported"}), 400

    filename = f"{uuid.uuid4()}.pdf"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    try:
        manual_text = extract_pdf_text(filepath)
        if not manual_text.strip():
            return jsonify({"error": "Could not extract text from PDF"}), 400

        result = generate_prompts(manual_text, segment_count, product_name)

        # Save output JSON
        output_filename = f"plankreel_{uuid.uuid4().hex[:8]}.json"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        result["output_file"] = output_filename
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.remove(filepath)


@app.route("/api/generate-video", methods=["POST"])
def api_generate_video():
    data = request.json
    prompt = data.get("prompt", "")
    duration = data.get("duration", 10)

    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400

    result = generate_video_kling(prompt, duration)
    return jsonify(result)


@app.route("/api/download/<filename>")
def download_file(filename):
    filepath = os.path.join(OUTPUT_FOLDER, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, as_attachment=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
