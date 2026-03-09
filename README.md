# PlankReel v1

AI-powered installation video prompt generator. Upload a PDF manual → DeepSeek AI generates cinematic video prompts → Send to Kling 3.0 or Seedance 2 for video generation.

## Workflow

```
PDF Manual → DeepSeek API → Video Prompts (JSON) → Kling 3.0 → Video Segments → CapCut → Final Video
```

## Setup

```bash
# 1. Clone
git clone https://github.com/bidasdlin/plankreel.git
cd plankreel

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API keys
cp .env.example .env
# Edit .env and fill in your API keys

# 4. Run
python app.py
# Open http://localhost:5000
```

## API Keys Required

| Key | Where to get |
|-----|-------------|
| `DEEPSEEK_API_KEY` | platform.deepseek.com |
| `KLING_API_KEY` | klingai.com/api |
| `KLING_API_SECRET` | klingai.com/api |

## Deploy to Server (Alibaba Cloud HK)

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

## Video Generation Flow

1. Upload installation manual PDF
2. Set product name and number of segments (3–10)
3. Click **Generate Video Prompts**
4. Review each segment's cinematic prompt
5. Export JSON → paste prompts into Kling/Seedance
6. Import generated video clips into CapCut
7. Add subtitles, brand elements, export

## Stack

- **Backend**: Python Flask
- **AI**: DeepSeek API (prompt generation)
- **Video**: Kling 3.0 API (video generation)
- **Frontend**: Vanilla HTML/CSS/JS
