"""
main.py
FastAPI application for Meeting Audio Summarization.
Exposes POST /summarize-meeting endpoint and serves the web UI.
"""

# ── PyTorch 2.6 compatibility ─────────────────────────────────────────────────
# MUST be first — before any import that transitively loads torch.
# PyTorch 2.6 changed torch.load to default weights_only=True, which breaks
# pyannote/speechbrain. Patch it here before anything else runs.
import torch as _torch
import functools as _functools
_orig_torch_load = _torch.load
@_functools.wraps(_orig_torch_load)
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _orig_torch_load(*args, **kwargs)
_torch.load = _patched_torch_load
# ─────────────────────────────────────────────────────────────────────────────

import os
import uuid
import shutil
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Local modules
from audio_utils import get_audio_duration, split_audio, cleanup_directory, convert_to_mp3
from diarization import perform_diarization, assign_speaker_to_transcript, format_speaker_transcript
from transcriber import transcribe_audio, get_backend_info
from summarizer import summarize_chunk, generate_final_summary, get_llm_info

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── App Setup ────────────────────────────────────────────────────────────────
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Meeting Audio Summarizer",
    description="Upload an MP3 meeting recording and get a structured summary.",
    version="1.0.0",
)

# Serve static files (web UI)
STATIC_DIR = Path("static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Thread pool for CPU-bound tasks — enough workers to process all chunks concurrently
executor = ThreadPoolExecutor(max_workers=max(8, (os.cpu_count() or 4) * 2))


# ─── Response Models ──────────────────────────────────────────────────────────
class MeetingSummaryResponse(BaseModel):
    transcript: str
    chunk_summaries: list[str]
    final_summary: dict
    duration_seconds: float
    num_chunks: int
    speakers_detected: list[str]


# ─── Helper: process one chunk ────────────────────────────────────────────────
def _process_chunk(chunk_path: str, chunk_index: int, chunk_offset: float) -> dict:
    """
    Process a single audio chunk:
    1. Transcribe with Whisper
    2. Diarize with pyannote
    3. Merge into speaker-labeled transcript
    4. Generate chunk summary

    Returns dict with transcript text and summary.
    """
    logger.info(f"Processing chunk {chunk_index}: {chunk_path}")

    # Transcription
    transcription = transcribe_audio(chunk_path)
    transcript_segments = transcription.get("segments", [])
    raw_text = transcription.get("text", "")

    if not raw_text.strip():
        logger.warning(f"Chunk {chunk_index}: empty transcript")
        return {
            "index": chunk_index,
            "transcript": "",
            "summary": "",
            "speakers": [],
        }

    # Diarization
    diarization_segments = perform_diarization(chunk_path)

    # Merge speaker labels with transcript
    merged = assign_speaker_to_transcript(
        transcript_segments, diarization_segments, chunk_offset=chunk_offset
    )

    # Format as readable speaker transcript
    speaker_transcript = format_speaker_transcript(merged)

    # Collect unique speakers in this chunk
    speakers = list(dict.fromkeys(s["speaker"] for s in merged if s.get("speaker")))

    # Chunk-level summary
    summary = summarize_chunk(speaker_transcript or raw_text)

    return {
        "index": chunk_index,
        "transcript": speaker_transcript or raw_text,
        "summary": summary,
        "speakers": speakers,
    }


# ─── Core pipeline ────────────────────────────────────────────────────────────
async def run_pipeline(file_path: str, session_dir: str) -> dict:
    """
    Full meeting summarization pipeline.
    """
    loop = asyncio.get_event_loop()

    # 1. Duration detection
    logger.info("Detecting audio duration...")
    duration = await loop.run_in_executor(executor, get_audio_duration, file_path)

    # 2. Split audio into 10-minute chunks
    chunk_dir = os.path.join(session_dir, "chunks")
    logger.info(f"Splitting {duration:.0f}s audio into 10-minute chunks...")
    chunks = await loop.run_in_executor(executor, split_audio, file_path, 600, chunk_dir)

    if not chunks:
        raise RuntimeError("No audio chunks were generated")

    logger.info(f"Processing {len(chunks)} chunks...")

    # 3. Process each chunk (transcribe + diarize + summarize)
    # Calculate time offset for each chunk (for absolute timestamps)
    # Process all chunks in parallel using asyncio.gather
    tasks = [
        loop.run_in_executor(
            executor, _process_chunk, chunk_path, i + 1, i * 600.0
        )
        for i, chunk_path in enumerate(chunks)
    ]
    logger.info(f"Processing {len(tasks)} chunks in parallel...")
    chunk_results = list(await asyncio.gather(*tasks))

    # Sort by index to preserve transcript order
    chunk_results.sort(key=lambda r: r["index"])

    # 4. Combine transcripts
    all_transcripts = [r["transcript"] for r in chunk_results if r["transcript"]]
    combined_transcript = "\n\n".join(
        f"--- Segment {r['index']} ---\n{r['transcript']}"
        for r in chunk_results
        if r["transcript"]
    )

    # 5. Collect chunk summaries
    chunk_summaries = [r["summary"] for r in chunk_results if r["summary"]]

    # 6. Collect all speakers
    all_speakers = []
    seen = set()
    for r in chunk_results:
        for spk in r.get("speakers", []):
            if spk not in seen:
                all_speakers.append(spk)
                seen.add(spk)

    # 7. Generate final summary
    logger.info(f"Generating final summary from {len(chunk_summaries)} chunk summaries...")
    final_summary = await loop.run_in_executor(
        executor, generate_final_summary, chunk_summaries
    )

    return {
        "transcript": combined_transcript,
        "chunk_summaries": chunk_summaries,
        "final_summary": final_summary,
        "duration_seconds": duration,
        "num_chunks": len(chunks),
        "speakers_detected": all_speakers,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the web interface."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Meeting Summarizer API</h1><p>POST /summarize-meeting with an MP3 file.</p>")


@app.post("/summarize-meeting", response_model=MeetingSummaryResponse)
async def summarize_meeting(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload an MP3 meeting recording and receive a structured summary.
    
    - **file**: MP3 audio file (multipart/form-data)
    
    Returns transcript, chunk summaries, and final structured meeting notes.
    """
    # Validate file type
    filename = file.filename or ""
    content_type = file.content_type or ""
    
    VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}
    AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
    ALL_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
    file_ext = Path(filename).suffix.lower()

    if file_ext not in ALL_EXTENSIONS and "audio" not in content_type and "video" not in content_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file_ext}'. Supported: {', '.join(sorted(ALL_EXTENSIONS))}",
        )

    # Create session directory
    session_id = str(uuid.uuid4())[:8]
    session_dir = str(TEMP_DIR / session_id)
    os.makedirs(session_dir, exist_ok=True)

    # Save uploaded file
    save_path = os.path.join(session_dir, f"input{file_ext}")
    try:
        raw = await file.read()
        if len(raw) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        with open(save_path, "wb") as f:
            f.write(raw)
        logger.info(f"Saved upload: {save_path} ({len(raw):,} bytes)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Convert video files to MP3
    if file_ext in VIDEO_EXTENSIONS:
        logger.info(f"Video file detected ({file_ext}), converting to MP3...")
        loop = asyncio.get_event_loop()
        mp3_path = os.path.join(session_dir, "input.mp3")
        try:
            save_path = await loop.run_in_executor(
                executor, convert_to_mp3, save_path, mp3_path
            )
            logger.info(f"Conversion complete: {save_path}")
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=f"Video conversion failed: {e}")

    # Schedule cleanup after response
    background_tasks.add_task(cleanup_directory, session_dir)

    # Run pipeline
    try:
        result = await run_pipeline(save_path, session_dir)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(f"Pipeline error for session {session_id}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")

    # Validate non-empty transcript
    if not result.get("transcript", "").strip():
        raise HTTPException(
            status_code=422,
            detail="Transcription produced empty results. "
                   "Ensure the audio contains speech and the Whisper model is installed.",
        )

    return MeetingSummaryResponse(**result)


@app.get("/health")
async def health_check():
    """Health check endpoint with backend info."""
    return {
        "status": "ok",
        "transcription": get_backend_info(),
        "llm": get_llm_info(),
    }


@app.get("/info")
async def info():
    """System information."""
    return {
        "app": "Meeting Audio Summarizer",
        "version": "1.0.0",
        "transcription_backend": get_backend_info(),
        "llm_backend": get_llm_info(),
        "endpoints": {
            "POST /summarize-meeting": "Upload MP3, get structured summary",
            "GET /health": "Health check",
            "GET /": "Web interface",
        },
    }