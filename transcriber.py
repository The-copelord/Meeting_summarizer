"""
transcriber.py
Speech-to-text transcription using faster-whisper (with OpenAI Whisper fallback).
Model is loaded once and reused across all chunks.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Model singletons
_faster_whisper_model = None
_openai_whisper_model = None
_backend: Optional[str] = None  # "faster_whisper" | "openai_whisper" | None


def _load_model():
    """Load transcription model once. Prefer faster-whisper, fall back to openai-whisper."""
    global _faster_whisper_model, _openai_whisper_model, _backend

    if _backend is not None:
        return

    model_size = os.getenv("WHISPER_MODEL_SIZE", "base")

    # Try faster-whisper first
    try:
        from faster_whisper import WhisperModel
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        logger.info(f"Loading faster-whisper model '{model_size}' on {device}...")
        _faster_whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _backend = "faster_whisper"
        logger.info("faster-whisper model loaded successfully.")
        return
    except ImportError:
        logger.info("faster-whisper not available, trying openai-whisper...")
    except Exception as e:
        logger.warning(f"faster-whisper load failed: {e}")

    # Fallback to openai-whisper
    try:
        import whisper

        logger.info(f"Loading openai-whisper model '{model_size}'...")
        _openai_whisper_model = whisper.load_model(model_size)
        _backend = "openai_whisper"
        logger.info("openai-whisper model loaded successfully.")
        return
    except ImportError:
        logger.warning("Neither faster-whisper nor openai-whisper is installed.")
    except Exception as e:
        logger.warning(f"openai-whisper load failed: {e}")

    _backend = "none"
    logger.error("No transcription backend available. Transcription will return empty results.")


def transcribe_audio(audio_file: str) -> dict:
    """
    Transcribe an audio file.

    Returns:
        {
            "text": "full transcript text",
            "segments": [{"start": 0.0, "end": 3.5, "text": "Hello"}, ...]
        }
    """
    _load_model()

    if _backend == "faster_whisper":
        return _transcribe_faster_whisper(audio_file)
    elif _backend == "openai_whisper":
        return _transcribe_openai_whisper(audio_file)
    else:
        logger.error(f"No transcription backend available for {audio_file}")
        return {"text": "", "segments": []}


def _transcribe_faster_whisper(audio_file: str) -> dict:
    """Transcribe using faster-whisper."""
    try:
        segments_iter, info = _faster_whisper_model.transcribe(
            audio_file,
            beam_size=5,
            language=None,  # auto-detect
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        logger.info(
            f"Detected language '{info.language}' with probability {info.language_probability:.2f}"
        )

        segments = []
        full_text_parts = []
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                segments.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": text,
                })
                full_text_parts.append(text)

        full_text = " ".join(full_text_parts)
        logger.info(f"Transcribed {len(segments)} segments ({len(full_text)} chars)")
        return {"text": full_text, "segments": segments}

    except Exception as e:
        logger.error(f"faster-whisper transcription failed: {e}")
        return {"text": "", "segments": []}


def _transcribe_openai_whisper(audio_file: str) -> dict:
    """Transcribe using openai-whisper."""
    try:
        result = _openai_whisper_model.transcribe(audio_file, verbose=False)
        segments = [
            {
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip(),
            }
            for seg in result.get("segments", [])
            if seg.get("text", "").strip()
        ]
        full_text = result.get("text", "").strip()
        logger.info(f"Transcribed {len(segments)} segments ({len(full_text)} chars)")
        return {"text": full_text, "segments": segments}

    except Exception as e:
        logger.error(f"openai-whisper transcription failed: {e}")
        return {"text": "", "segments": []}


def get_backend_info() -> dict:
    """Return info about which transcription backend is loaded."""
    _load_model()
    return {
        "backend": _backend,
        "model_size": os.getenv("WHISPER_MODEL_SIZE", "base"),
        "available": _backend not in (None, "none"),
    }
