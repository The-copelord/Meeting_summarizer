# MeetingMind — AI Meeting Audio Summarizer

A production-ready system that transforms long meeting recordings (MP3) into structured, speaker-aware meeting notes. Supports recordings of 2–3+ hours through hierarchical chunked processing.

## Architecture

```
MP3 Upload
   ↓
Audio Duration Detection (ffprobe)
   ↓
Chunk Audio into 10-minute segments (FFmpeg)
   ↓
Per Chunk: Speaker Diarization (pyannote.audio)
   ↓
Per Chunk: Speech-to-Text Transcription (Whisper)
   ↓
Merge Speaker Labels with Transcript Segments
   ↓
Chunk-Level Summaries (LLM via Groq/Anthropic)
   ↓
Final Structured Meeting Notes (Hierarchical LLM)
```

## Project Structure

```
meeting_summarizer/
├── main.py              # FastAPI app, pipeline orchestration
├── audio_utils.py       # Duration detection, FFmpeg chunking
├── diarization.py       # pyannote.audio speaker diarization
├── transcriber.py       # faster-whisper / openai-whisper transcription
├── summarizer.py        # LLM-based chunk + final summarization
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── static/
│   └── index.html       # Single-page web UI
└── temp/                # Auto-created temporary file storage
```

## Prerequisites

### System Dependencies

**FFmpeg** (required for audio processing):
```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html and add to PATH
```

**Python 3.10+** is required.

### API Keys

| Key | Required | Purpose |
|-----|----------|---------|
| `GROQ_API_KEY` | Recommended | Fast LLM inference (Llama-3.3-70B) |
| `ANTHROPIC_API_KEY` | Fallback | Claude Haiku for summarization |
| `HF_TOKEN` | For diarization | pyannote model access on HuggingFace |

At least one of `GROQ_API_KEY` or `ANTHROPIC_API_KEY` is required for summarization.

### HuggingFace Setup (for speaker diarization)

1. Create account at [huggingface.co](https://huggingface.co)
2. Accept model terms at: https://huggingface.co/pyannote/speaker-diarization-3.1
3. Generate an access token at: https://huggingface.co/settings/tokens
4. Set `HF_TOKEN=your_token` in your `.env` file

> **Note:** The system degrades gracefully without diarization — transcription and summarization still work, but all speech will be attributed to "Speaker 1".

## Installation

### 1. Clone / Set Up

```bash
cd meeting_summarizer
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

For GPU support (faster transcription):
```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 4. Run the Server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The web interface will be available at: **http://localhost:8000**

## Usage

### Web Interface

1. Open http://localhost:8000
2. Drag & drop or select an MP3 file
3. Click **Analyze Meeting**
4. View transcript, segment summaries, and final notes

### REST API

**Endpoint:** `POST /summarize-meeting`

```bash
curl -X POST http://localhost:8000/summarize-meeting \
  -F "file=@/path/to/meeting.mp3" \
  | python -m json.tool
```

**Response Format:**
```json
{
  "transcript": "Speaker 1: Good morning...\nSpeaker 2: ...",
  "chunk_summaries": [
    "Segment 1: The team discussed Q2 roadmap priorities...",
    "Segment 2: Budget allocations were reviewed..."
  ],
  "final_summary": {
    "overview": "A 2-hour product planning meeting covering Q2 roadmap...",
    "key_points": ["Q2 roadmap priorities set", "Budget constraints discussed"],
    "decisions": ["Launch date moved to June 15", "Design team gets 2 new hires"],
    "action_items": ["Sarah → Draft technical spec by Friday", "Tom → Schedule stakeholder review"],
    "next_steps": ["Follow-up meeting scheduled for next Tuesday"]
  },
  "duration_seconds": 7200,
  "num_chunks": 12,
  "speakers_detected": ["Speaker 1", "Speaker 2", "Speaker 3"]
}
```

**Additional Endpoints:**
- `GET /` — Web interface
- `GET /health` — Health check with backend info
- `GET /info` — System information

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `GROQ_API_KEY` | — | Groq API key for fast LLM inference |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (fallback LLM) |
| `HF_TOKEN` | — | HuggingFace token for pyannote |
| `WHISPER_MODEL_SIZE` | `base` | Whisper model: tiny/base/small/medium/large-v2/large-v3 |

## Performance Notes

- **Model loading:** Whisper and pyannote models are loaded once at startup and reused across requests
- **Chunking:** Audio is split into 10-minute segments processed sequentially
- **Long recordings:** 2-hour meetings are handled via hierarchical summarization (chunk → meta-chunk → final)
- **Temporary files:** Automatically cleaned up after each request via background task
- **GPU acceleration:** Automatically used if CUDA is available (significantly speeds up transcription)

## Whisper Model Recommendations

| Model | VRAM | Speed | Accuracy |
|-------|------|-------|----------|
| `tiny` | ~1 GB | Fastest | Lower |
| `base` | ~1 GB | Fast | Good for clear audio |
| `small` | ~2 GB | Moderate | Better |
| `medium` | ~5 GB | Slower | High |
| `large-v3` | ~10 GB | Slowest | Best |

For CPU-only systems, `base` or `small` are recommended.

## Error Handling

The API returns structured error responses:

| HTTP Code | Scenario |
|-----------|----------|
| 400 | Unsupported file type or empty file |
| 422 | Empty transcript (no speech detected) |
| 500 | Internal processing error |

## Troubleshooting

**FFmpeg not found:**
```
RuntimeError: Failed to detect audio duration
```
→ Install FFmpeg and ensure it's in your PATH.

**Empty transcript:**
```
"Transcription produced empty results"
```
→ Check that faster-whisper or openai-whisper is installed. Try a different WHISPER_MODEL_SIZE.

**Diarization disabled:**
```
WARNING: HF_TOKEN not set. Speaker diarization disabled.
```
→ Set HF_TOKEN. Transcription still works; all speech assigned to "Speaker 1".

**Summarization unavailable:**
```
ERROR: No LLM available. Set GROQ_API_KEY or ANTHROPIC_API_KEY.
```
→ Set at least one LLM API key.
