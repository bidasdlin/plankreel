import os
import json
import uuid
import requests
from flask import Flask, request, jsonify, render_template, send_file
from dotenv import load_dotenv
import base64
import pdfplumber
from openai import OpenAI
from docx import Document as DocxDocument

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


def extract_text(filepath, ext):
    if ext == ".pdf":
        return extract_pdf_text(filepath)
    elif ext in (".docx", ".doc"):
        return extract_docx_text(filepath)
    elif ext == ".txt":
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    return ""


def extract_docx_text(filepath):
    try:
        doc = DocxDocument(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def extract_pdf_text(filepath):
    text = ""
    # Try pdfplumber
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        print(f"[PDF] pdfplumber extracted {len(text)} chars")
    except Exception as e:
        print(f"[PDF] pdfplumber error: {e}")

    # Try pymupdf
    if not text.strip():
        try:
            import fitz
            doc = fitz.open(filepath)
            for page in doc:
                text += page.get_text()
            doc.close()
            print(f"[PDF] pymupdf extracted {len(text)} chars")
        except Exception as e:
            print(f"[PDF] pymupdf error: {e}")

    # Vision OCR fallback
    print(f"[PDF] text after extraction: '{text[:50]}' (len={len(text.strip())})")
    if not text.strip():
        print("[PDF] Calling vision OCR...")
        text = ocr_pdf_with_vision(filepath)
        print(f"[PDF] OCR returned {len(text)} chars")

    return text


def ocr_pdf_with_vision(filepath):
    """Convert PDF pages to images and use Claude Haiku for fast OCR."""
    try:
        import fitz
        import anthropic

        doc = fitz.open(filepath)
        all_text = []
        claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        for page_num, page in enumerate(doc):
            # Use 72 DPI and JPEG to keep image under 5MB
            mat = fitz.Matrix(1.0, 1.0)  # 72 DPI
            pix = page.get_pixmap(matrix=mat)
            from PIL import Image as PILImage
            import io
            pil_img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=75)
            img_bytes = buf.getvalue()
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            print(f"[OCR] Page {page_num+1} image size: {len(img_bytes)/1024:.0f} KB")

            response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this installation manual page. Return only the raw text content, preserving structure. No commentary."
                        }
                    ]
                }]
            )
            page_text = response.content[0].text
            all_text.append(f"--- Page {page_num + 1} ---\n{page_text}")

        doc.close()
        return "\n\n".join(all_text)

    except Exception as e:
        print(f"[OCR ERROR] {type(e).__name__}: {e}")
        return ""


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


def kling_jwt_token():
    """Generate JWT token for Kling API authentication."""
    import jwt
    import time
    api_key = os.getenv("KLING_API_KEY")
    api_secret = os.getenv("KLING_API_SECRET")
    payload = {
        "iss": api_key,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, api_secret, algorithm="HS256")


def generate_video_kling(prompt, duration=10):
    """Call Kling 3.0 API to generate a video segment."""
    api_key = os.getenv("KLING_API_KEY")
    if not api_key:
        return {"status": "skipped", "message": "Kling API key not configured"}

    token = kling_jwt_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "model_name": "kling-v1-6",
        "prompt": prompt,
        "duration": 5,
        "aspect_ratio": "16:9",
        "mode": "pro",
        "cfg_scale": 0.5
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

    # Support PDF, Word, TXT
    fname = file.filename.lower()
    if fname.endswith(".pdf"):
        ext = ".pdf"
    elif fname.endswith(".docx"):
        ext = ".docx"
    elif fname.endswith(".doc"):
        ext = ".doc"
    elif fname.endswith(".txt"):
        ext = ".txt"
    else:
        return jsonify({"error": "支持 PDF、Word (.docx)、TXT 文件"}), 400

    filename = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    try:
        manual_text = extract_text(filepath, ext)
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
