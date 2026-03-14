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

        cuda_available = torch.cuda.is_available()
        device = "cuda" if cuda_available else "cpu"
        compute_type = "float16" if cuda_available else "int8"

        if cuda_available:
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
            logger.info(f"GPU detected: {gpu_name} ({vram_gb:.1f} GB VRAM)")
            # Auto-upgrade model size based on available VRAM if user left default
            if model_size == "base" and vram_gb >= 10:
                model_size = "large-v3"
                logger.info("Sufficient VRAM detected — auto-upgrading to large-v3 for best accuracy")
            elif model_size == "base" and vram_gb >= 5:
                model_size = "medium"
                logger.info("Sufficient VRAM detected — auto-upgrading to medium for better accuracy")
            elif model_size == "base" and vram_gb >= 2:
                model_size = "small"
                logger.info("Sufficient VRAM detected — auto-upgrading to small for better accuracy")
        else:
            logger.warning(
                "CUDA not available — running Whisper on CPU. "
                "Install a CUDA-enabled PyTorch build for GPU acceleration: "
                "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121"
            )

        logger.info(f"Loading faster-whisper model '{model_size}' on {device} ({compute_type})...")
        _faster_whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _backend = "faster_whisper"
        logger.info(f"faster-whisper '{model_size}' loaded on {device.upper()}.")
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