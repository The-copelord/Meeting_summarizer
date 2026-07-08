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
import sys
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

def _now():
    return datetime.now(timezone.utc).astimezone()
from pathlib import Path

# Ensure project root is on sys.path so services.* imports work
# regardless of where uvicorn is launched from
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from apscheduler.schedulers.background import BackgroundScheduler

from database import SessionLocal
from models import Job, JobStatus, Result
from notifier import notify

# Services (carried over from original system)
from services.audio_utils import get_audio_duration, split_audio, convert_to_mp3, denoise_audio
from services.diarization import (
    perform_diarization,
    assign_speaker_to_transcript,
    format_speaker_transcript,
)
from services.transcriber import transcribe_audio
from services.aria import run_aria_pipeline
from services.llm_clients import set_llm_log_path

logger = logging.getLogger(__name__)

TEMP_DIR = Path(os.getenv("TEMP_DIR", "temp"))
TEMP_DIR.mkdir(exist_ok=True)

LOGS_DIR = Path(os.getenv("LOGS_DIR", "logs"))
LOGS_DIR.mkdir(exist_ok=True)

# Reuse thread pool across jobs — same as original system
_executor = ThreadPoolExecutor(max_workers=max(8, (os.cpu_count() or 4) * 2))

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".mpeg", ".mpg"}


# ── Single chunk processor (identical to original system) ────────────────────
# GPU concurrency limit — prevents CUDA OOM on low-VRAM GPUs
# RTX 3050 (4GB VRAM): 2 chunks max. Increase if you have more VRAM.
import torch as _torch_check
_GPU_WORKERS = 2 if _torch_check.cuda.is_available() else (os.cpu_count() or 4)
_gpu_semaphore = None   # initialised lazily in async context


# ── Single chunk processor ────────────────────────────────────────────────────

def _process_chunk(chunk_path: str, chunk_index: int, chunk_offset: float) -> dict:
    """
    Transcribe + diarize one audio chunk.
    ARIA summarization runs separately after all chunks complete.
    GPU semaphore (set in _run_pipeline) limits concurrency to prevent CUDA OOM.
    """
    logger.info(f"[Chunk {chunk_index}] Processing {chunk_path}")
    try:
        transcription = transcribe_audio(chunk_path)
        transcript_segments = transcription.get("segments", [])
        raw_text = transcription.get("text", "")

        if not raw_text.strip():
            logger.warning(f"[Chunk {chunk_index}] Empty transcript")
            return {"index": chunk_index, "transcript": "", "speakers": []}

        diarization_segments = perform_diarization(chunk_path)
        merged = assign_speaker_to_transcript(
            transcript_segments, diarization_segments, chunk_offset=chunk_offset
        )
        speaker_transcript = format_speaker_transcript(merged)
        speakers = list(dict.fromkeys(s["speaker"] for s in merged if s.get("speaker")))

    finally:
        # Free CUDA cache after each chunk to prevent VRAM accumulation
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.debug(f"[Chunk {chunk_index}] CUDA cache cleared")
        except Exception:
            pass

    return {
        "index":      chunk_index,
        "transcript": speaker_transcript or raw_text,
        "speakers":   speakers,
    }


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def _run_pipeline(file_path: str, session_dir: str,
                        provider: str = "groq", model: str = "llama-3.3-70b-versatile",
                        groq_key: str = None, anthropic_key: str = None,
                        openai_key: str = None, together_key: str = None,
                        mistral_key: str = None,
                        log_path: str = None) -> dict:
    """
    Full audio analysis pipeline:
    Phase 1 — Parallel transcription + diarization (GPU, semaphore-limited)
    Phase 2 — ARIA pipeline (sequential LLM summarization)
    """
    loop = asyncio.get_event_loop()

    # Initialise GPU semaphore in async context
    _gpu_semaphore = asyncio.Semaphore(_GPU_WORKERS)
    logger.info(f"GPU semaphore initialised — max {_GPU_WORKERS} concurrent chunks")

    # Convert video → MP3 if needed
    ext = Path(file_path).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        logger.info(f"Converting video {ext} to MP3...")
        mp3_path = str(Path(session_dir) / "input.mp3")
        file_path = await loop.run_in_executor(_executor, convert_to_mp3, file_path, mp3_path)

    # Noise reduction (before chunking so all chunks get clean audio)
    noise_level = os.getenv("NOISE_REDUCTION_LEVEL", "medium")
    if noise_level != "none":
        logger.info(f"Applying noise reduction (level={noise_level})...")
        denoised_path = str(Path(session_dir) / "denoised.mp3")
        file_path = await loop.run_in_executor(
            _executor, denoise_audio, file_path, denoised_path, noise_level
        )
        logger.info("Noise reduction complete")

    # Duration
    duration = await loop.run_in_executor(_executor, get_audio_duration, file_path)
    logger.info(f"Audio duration: {duration:.0f}s ({duration/60:.1f} min)")

    # Split into 10-minute chunks
    chunk_dir = str(Path(session_dir) / "chunks")
    chunks = await loop.run_in_executor(_executor, split_audio, file_path, 600, chunk_dir)
    if not chunks:
        raise RuntimeError("FFmpeg produced no audio chunks")

    logger.info(f"Processing {len(chunks)} chunks — max {_GPU_WORKERS} concurrent on GPU")

    # Phase 1: GPU-limited parallel transcription + diarization
    async def _run_chunk_limited(chunk_path, idx, offset):
        async with _gpu_semaphore:
            logger.info(f"[Chunk {idx}] GPU slot acquired")
            return await loop.run_in_executor(
                _executor, _process_chunk, chunk_path, idx, offset
            )

    tasks = [
        _run_chunk_limited(chunk_path, i + 1, i * 600.0)
        for i, chunk_path in enumerate(chunks)
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle chunk-level errors gracefully — don't let one bad chunk kill the job
    chunk_results = []
    for i, r in enumerate(raw_results):
        if isinstance(r, Exception):
            logger.error(f"Chunk {i+1} failed: {r}")
            chunk_results.append({"index": i + 1, "transcript": f"[Chunk {i+1} failed: {r}]", "speakers": []})
        else:
            chunk_results.append(r)
    chunk_results.sort(key=lambda r: r["index"])

    # Combine transcripts
    combined_transcript = "\n\n".join(
        f"--- Segment {r['index']} ---\n{r['transcript']}"
        for r in chunk_results if r["transcript"]
    )
    chunk_transcripts = [r["transcript"] for r in chunk_results]

    # Phase 2: ARIA pipeline (sequential — state capsule chains between chunks)
    logger.info(f"Running ARIA pipeline on {len(chunk_transcripts)} chunks "
                f"(provider={provider}, model={model})...")
    aria_kwargs = dict(
        provider=provider, model=model,
        groq_key=groq_key, anthropic_key=anthropic_key,
        openai_key=openai_key, together_key=together_key,
        mistral_key=mistral_key,
    )
    try:
        def _run_aria():
            if log_path:
                set_llm_log_path(log_path)
            return run_aria_pipeline(chunk_transcripts, **aria_kwargs)

        aria_result = await asyncio.wait_for(
            loop.run_in_executor(_executor, _run_aria),
            timeout=1800  # 30 min max
        )
    except asyncio.TimeoutError:
        logger.error("ARIA pipeline timed out after 30 minutes")
        aria_result = {
            "chunk_briefs": [], "rollup_briefs": [],
            "overview": "ARIA pipeline timed out.", "master_brief": "",
            "sections": {}, "key_points": [], "decisions": [],
            "action_items": [], "next_steps": [],
        }

    return {
        "transcript":       combined_transcript,
        "chunk_briefs":     aria_result.get("chunk_briefs", []),
        "rollup_briefs":    aria_result.get("rollup_briefs", []),
        "final_summary":    aria_result,
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

        # Set per-job LLM log file: logs/<filename>_<timestamp>.log
        import re as _re
        safe_name = _re.sub(r"[^\w\-]", "_", Path(job.original_filename or "unknown").stem)
        log_ts = _now().strftime("%Y%m%d_%H%M%S")
        log_path = str(LOGS_DIR / f"{safe_name}_{log_ts}.log")
        set_llm_log_path(log_path)
        logger.info(f"LLM call log → {log_path}")

        # Mark as processing
        job.status = JobStatus.processing
        job.updated_at = _now()
        db.commit()
        logger.info(f"Job {job_id} → processing")

        # Fetch user's API keys, provider, and model preference
        from models import User as UserModel
        user = db.query(UserModel).filter(UserModel.id == job.user_id).first()
        provider       = (getattr(user, "selected_provider", None) if user else None) or "groq"
        model          = (user.selected_model if user else None) or "llama-3.3-70b-versatile"
        groq_key       = (user.groq_api_key if user else None) or os.getenv("GROQ_API_KEY")
        anthropic_key  = (user.anthropic_api_key if user else None) or os.getenv("ANTHROPIC_API_KEY")
        openai_key     = (user.openai_api_key if user else None) or os.getenv("OPENAI_API_KEY")
        together_key   = (user.together_api_key if user else None) or os.getenv("TOGETHER_API_KEY")
        mistral_key    = (user.mistral_api_key if user else None) or os.getenv("MISTRAL_API_KEY")

        # Run the async pipeline synchronously from this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _run_pipeline(job.file_path, session_dir,
                              provider=provider, model=model,
                              groq_key=groq_key, anthropic_key=anthropic_key,
                              openai_key=openai_key, together_key=together_key,
                              mistral_key=mistral_key,
                              log_path=log_path)
            )
        finally:
            loop.close()

        # Store result + update duration metadata
        transcript    = result.get("transcript", "")
        final_summary = result.get("final_summary", {})
        chunk_briefs  = result.get("chunk_briefs", [])
        rollup_briefs = result.get("rollup_briefs", [])
        duration = result.get("duration_seconds")
        if duration:
            job.file_duration_seconds = int(duration)

        summary_payload = {
            **final_summary,
            "chunk_briefs":  chunk_briefs,
            "rollup_briefs": rollup_briefs,
        }

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
        job.updated_at = _now()
        db.commit()
        logger.info(f"Job {job_id} → done ✓")
        notify(job_id, "done")

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = JobStatus.error
                job.error_msg = str(e)[:1000]
                job.updated_at = _now()
                db.commit()
                notify(job_id, "error", str(e)[:200])
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
            job.updated_at = _now()
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