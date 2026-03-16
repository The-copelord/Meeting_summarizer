# MeetingMind Analysis API v2

Async meeting audio analysis system with job queue, JWT authentication, and PostgreSQL. Designed to handle 2–3+ hour recordings without HTTP timeouts by processing everything in the background.

## Architecture

```
Client
  │
  ├── POST /user/signup          Register
  ├── POST /user/login           Get JWT token
  │
  ├── POST /uploadfile           Upload MP3/MP4 → returns job_id
  ├── POST /analyse              Queue job for processing
  │
  ├── GET  /get_status/{job_id}  Poll until status = "done"
  └── GET  /get_summary          Fetch transcript + summary
                                       │
                              Background Scheduler
                              (APScheduler, 10s poll)
                                       │
                              ┌────────▼────────┐
                              │  Audio Pipeline  │
                              │                 │
                              │  FFmpeg chunk   │
                              │  pyannote GPU   │
                              │  Whisper GPU    │  ← parallel
                              │  Groq LLM       │
                              └─────────────────┘
                                       │
                                  PostgreSQL
```

## Why this architecture?

The previous single-request approach held the HTTP connection open during processing. For a 2-hour meeting this would time out in most environments (nginx default: 60s, AWS ALB: 60s, Cloudflare: 100s).

The new flow:
1. Upload returns immediately with a `job_id` (< 1s)
2. `/analyse` queues the job (< 1s)
3. Background worker processes it asynchronously (minutes)
4. Client polls `/get_status/{job_id}` every few seconds
5. When status is `done`, client fetches `/get_summary`

## Project Structure

```
meeting_analysis_api/
├── main.py                 # FastAPI app, lifespan, middleware
├── database.py             # SQLAlchemy engine + session
├── models.py               # User, Job, Result ORM models
├── auth.py                 # bcrypt + JWT
├── routes/
│   ├── user.py             # POST /user/signup, /user/login
│   ├── upload.py           # POST /uploadfile
│   └── jobs.py             # POST /analyse, GET /get_status, /get_summary
├── services/
│   ├── audio_utils.py      # FFmpeg chunking + MP4 conversion
│   ├── diarization.py      # pyannote.audio speaker diarization
│   ├── transcription.py    # faster-whisper GPU transcription
│   └── summarizer.py       # Groq/Anthropic LLM summarization
├── workers/
│   └── scheduler.py        # APScheduler + full pipeline runner
├── uploads/                # Uploaded audio files (auto-created)
├── temp/                   # Chunk processing temp dir (auto-created)
├── requirements.txt
└── .env.example
```

## Prerequisites

**PostgreSQL** running locally or remotely.

```bash
# Ubuntu
sudo apt install postgresql
sudo -u postgres createdb meeting_analysis

# macOS
brew install postgresql
createdb meeting_analysis

# Windows
# Install from https://www.postgresql.org/download/windows/
# Then create DB via pgAdmin or psql
```

**FFmpeg:**
```bash
# Windows: https://ffmpeg.org/download.html (add to PATH)
# macOS: brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
```

**PyTorch with CUDA** (for GPU acceleration):
```bash
# Check CUDA version
nvidia-smi

# Install matching PyTorch (CUDA 12.1)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

## Installation

```bash
# 1. Clone
git clone https://github.com/Karthik-B-N/Meeting_summarizer.git
cd Meeting_summarizer

# 2. Virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install PyTorch first (GPU build)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# 4. Install remaining dependencies
pip install -r requirements.txt

# 5. Configure environment
cp .env.example .env
# Edit .env — set DATABASE_URL, SECRET_KEY, GROQ_API_KEY, HF_TOKEN

# 6. Run
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## API Usage

### 1. Sign Up
```bash
curl -X POST http://localhost:8000/user/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}'
```

### 2. Login
```bash
curl -X POST http://localhost:8000/user/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}'
# → {"access_token": "eyJ...", "token_type": "bearer"}
```

### 3. Upload File
```bash
curl -X POST http://localhost:8000/uploadfile \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@meeting.mp3"
# → {"job_id": "abc-123", "status": "uploaded"}
```

### 4. Start Analysis
```bash
curl -X POST http://localhost:8000/analyse \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "abc-123"}'
# → {"message": "Job queued for analysis", "job_id": "abc-123"}
```

### 5. Poll Status
```bash
curl http://localhost:8000/get_status/abc-123 \
  -H "Authorization: Bearer YOUR_TOKEN"
# → {"job_id": "abc-123", "status": "processing"}
# Keep polling until status = "done"
```

### 6. Get Summary
```bash
curl "http://localhost:8000/get_summary?job_id=abc-123" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Response:
```json
{
  "job_id": "abc-123",
  "transcript": "Speaker 1: Good morning...",
  "summary": {
    "overview": "...",
    "key_points": ["..."],
    "decisions": ["..."],
    "action_items": ["..."],
    "next_steps": ["..."],
    "chunk_summaries": ["..."]
  }
}
```

## Job Statuses

| Status | Meaning |
|--------|---------|
| `uploaded` | File saved, not yet queued |
| `queued` | Waiting for worker to pick up |
| `processing` | Worker is actively processing |
| `done` | Complete — summary available |
| `error` | Failed — check `error` field in status response |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection string |
| `SECRET_KEY` | — | JWT signing secret (change in production!) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime (24h) |
| `GROQ_API_KEY` | — | Groq LLM API key |
| `ANTHROPIC_API_KEY` | — | Anthropic fallback API key |
| `HF_TOKEN` | — | HuggingFace token for pyannote |
| `WHISPER_MODEL_SIZE` | `base` | Whisper model (auto-upgraded on GPU) |
| `UPLOAD_DIR` | `uploads` | Where uploaded files are stored |
| `TEMP_DIR` | `temp` | Temporary chunk processing directory |

## Troubleshooting

**Database connection error:**
```
sqlalchemy.exc.OperationalError: could not connect to server
```
→ Ensure PostgreSQL is running and `DATABASE_URL` in `.env` is correct.

**PyTorch 2.6 / pyannote weights error:**
Already patched in `main.py` — `torch.load` is patched before any imports run.

**CUDA not available:**
```bash
python -c "import torch; print(torch.cuda.is_available())"
```
→ Reinstall PyTorch with the correct CUDA index URL for your driver version.

**Jobs stuck in `queued`:**
→ Check scheduler is running — look for `"Background scheduler started"` in logs on startup.