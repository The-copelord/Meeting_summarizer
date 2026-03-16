"""
workers/scheduler.py
APScheduler-based background worker.
Polls the database for queued jobs and processes them through the full
audio analysis pipeline (FFmpeg → diarization → Whisper → LLM summary).
GPU acceleration and parallel chunk processing are preserved.
"""

import asyncio
import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from database import SessionLocal
from models import Job, JobStatus, Result

# Services (carried over from original system)
from services.audio_utils import get_audio_duration, split_audio, convert_to_mp3
from services.diarization import (
    perform_diarization,
    assign_speaker_to_transcript,
    format_speaker_transcript,
)
from services.transcriber import transcribe_audio
from services.summarizer import summarize_chunk, generate_final_summary

logger = logging.getLogger(__name__)

TEMP_DIR = Path(os.getenv("TEMP_DIR", "temp"))
TEMP_DIR.mkdir(exist_ok=True)

# Reuse thread pool across jobs — same as original system
_executor = ThreadPoolExecutor(max_workers=max(8, (os.cpu_count() or 4) * 2))

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}


# ── Single chunk processor (identical to original system) ────────────────────

def _process_chunk(chunk_path: str, chunk_index: int, chunk_offset: float) -> dict:
    """
    Transcribe + diarize one audio chunk.
    Runs in a thread pool worker.
    """
    logger.info(f"[Chunk {chunk_index}] Processing {chunk_path}")

    transcription = transcribe_audio(chunk_path)
    transcript_segments = transcription.get("segments", [])
    raw_text = transcription.get("text", "")

    if not raw_text.strip():
        logger.warning(f"[Chunk {chunk_index}] Empty transcript")
        return {"index": chunk_index, "transcript": "", "summary": "", "speakers": []}

    diarization_segments = perform_diarization(chunk_path)
    merged = assign_speaker_to_transcript(
        transcript_segments, diarization_segments, chunk_offset=chunk_offset
    )
    speaker_transcript = format_speaker_transcript(merged)
    speakers = list(dict.fromkeys(s["speaker"] for s in merged if s.get("speaker")))
    summary = summarize_chunk(speaker_transcript or raw_text)

    return {
        "index": chunk_index,
        "transcript": speaker_transcript or raw_text,
        "summary": summary,
        "speakers": speakers,
    }


# ── Full pipeline (async, parallel chunks) ───────────────────────────────────

async def _run_pipeline(file_path: str, session_dir: str) -> dict:
    """
    Full audio analysis pipeline.
    Preserves GPU acceleration and parallel chunk processing from original system.
    """
    loop = asyncio.get_event_loop()

    # Convert video → MP3 if needed
    ext = Path(file_path).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        logger.info(f"Converting video {ext} to MP3...")
        mp3_path = str(Path(session_dir) / "input.mp3")
        file_path = await loop.run_in_executor(_executor, convert_to_mp3, file_path, mp3_path)

    # Duration
    duration = await loop.run_in_executor(_executor, get_audio_duration, file_path)
    logger.info(f"Audio duration: {duration:.0f}s ({duration/60:.1f} min)")

    # Split into 10-minute chunks
    chunk_dir = str(Path(session_dir) / "chunks")
    chunks = await loop.run_in_executor(_executor, split_audio, file_path, 600, chunk_dir)
    if not chunks:
        raise RuntimeError("FFmpeg produced no audio chunks")

    logger.info(f"Processing {len(chunks)} chunks in parallel...")

    # Process all chunks concurrently (GPU Whisper + pyannote)
    tasks = [
        loop.run_in_executor(_executor, _process_chunk, chunk_path, i + 1, i * 600.0)
        for i, chunk_path in enumerate(chunks)
    ]
    chunk_results = list(await asyncio.gather(*tasks))
    chunk_results.sort(key=lambda r: r["index"])

    # Combine transcripts
    combined_transcript = "\n\n".join(
        f"--- Segment {r['index']} ---\n{r['transcript']}"
        for r in chunk_results if r["transcript"]
    )

    # Chunk summaries
    chunk_summaries = [r["summary"] for r in chunk_results if r["summary"]]

    # Final LLM summary
    logger.info(f"Generating final summary from {len(chunk_summaries)} chunk summaries...")
    final_summary = await loop.run_in_executor(
        _executor, generate_final_summary, chunk_summaries
    )

    return {
        "transcript": combined_transcript,
        "chunk_summaries": chunk_summaries,
        "final_summary": final_summary,
        "duration_seconds": duration,
    }


# ── Job runner ────────────────────────────────────────────────────────────────

def _run_job(job_id: str):
    """
    Entry point called by the scheduler for each queued job.
    Runs the async pipeline in a new event loop (scheduler thread).
    """
    db = SessionLocal()
    session_dir = str(TEMP_DIR / job_id)
    os.makedirs(session_dir, exist_ok=True)

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found in DB")
            return

        # Mark as processing
        job.status = JobStatus.processing
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"Job {job_id} → processing")

        # Run the async pipeline synchronously from this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_run_pipeline(job.file_path, session_dir))
        finally:
            loop.close()

        # Store result
        transcript = result.get("transcript", "")
        final_summary = result.get("final_summary", {})
        chunk_summaries = result.get("chunk_summaries", [])

        summary_payload = {**final_summary, "chunk_summaries": chunk_summaries}

        existing = db.query(Result).filter(Result.job_id == job_id).first()
        if existing:
            existing.transcript = transcript
            existing.summary_json = json.dumps(summary_payload)
        else:
            db.add(Result(
                job_id=job_id,
                transcript=transcript,
                summary_json=json.dumps(summary_payload),
            ))

        job.status = JobStatus.done
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"Job {job_id} → done ✓")

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = JobStatus.error
                job.error_msg = str(e)[:1000]
                job.updated_at = datetime.utcnow()
                db.commit()
        except Exception as inner:
            logger.error(f"Could not mark job {job_id} as error: {inner}")
    finally:
        db.close()
        # Clean up temp files
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir, ignore_errors=True)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _poll_and_dispatch():
    """
    Called every 10 seconds by APScheduler.
    Finds queued jobs and fires them off in the thread pool.
    """
    db = SessionLocal()
    try:
        queued = db.query(Job).filter(Job.status == JobStatus.queued).all()
        for job in queued:
            logger.info(f"Dispatching job {job.id}")
            job.status = JobStatus.processing
            job.updated_at = datetime.utcnow()
            db.commit()
            _executor.submit(_run_job, job.id)
    except Exception as e:
        logger.error(f"Scheduler poll error: {e}")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    """Start the APScheduler background scheduler. Called once at app startup."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _poll_and_dispatch,
        trigger="interval",
        seconds=10,
        id="job_poller",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Background scheduler started (polling every 10s)")
    return scheduler