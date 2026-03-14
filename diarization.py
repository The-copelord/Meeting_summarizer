"""
diarization.py
Speaker diarization using pyannote.audio.
Falls back gracefully if model/token not available.
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv
logger = logging.getLogger(__name__)
load_dotenv()
# Global pipeline instance (loaded once)
_diarization_pipeline = None
_pipeline_available = None


def _load_pipeline() -> Optional[object]:
    """Load pyannote diarization pipeline (once)."""
    global _diarization_pipeline, _pipeline_available

    if _pipeline_available is not None:
        return _diarization_pipeline

    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not hf_token:
        logger.warning(
            "HF_TOKEN not set. Speaker diarization disabled. "
            "Set HF_TOKEN env var with a HuggingFace token that has access to "
            "pyannote/speaker-diarization-3.1 to enable this feature."
        )
        _pipeline_available = False
        return None

    try:
        from pyannote.audio import Pipeline
        import torch

        logger.info("Loading pyannote diarization pipeline...")
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # pyannote.audio 3.x uses use_auth_token (confirmed via inspect.signature)
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token,
        )

        pipeline = pipeline.to(torch.device(device))
        _diarization_pipeline = pipeline
        _pipeline_available = True
        logger.info(f"Diarization pipeline loaded on {device}")
        return pipeline

    except ImportError:
        logger.warning("pyannote.audio not installed. Speaker diarization disabled.")
        _pipeline_available = False
        return None
    except Exception as e:
        logger.warning(f"Could not load diarization pipeline: {e}. Continuing without diarization.")
        _pipeline_available = False
        return None


def perform_diarization(audio_file: str) -> list[dict]:
    """
    Perform speaker diarization on an audio file.

    Returns a list of speaker segments:
        [{"speaker": "SPEAKER_00", "start": 0.0, "end": 5.2}, ...]

    Returns empty list if diarization is unavailable.
    """
    pipeline = _load_pipeline()
    if pipeline is None:
        logger.info(f"Diarization skipped for {audio_file} (pipeline unavailable)")
        return []

    try:
        logger.info(f"Running diarization on {audio_file}")
        diarization = pipeline(audio_file)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "speaker": speaker,
                "start": round(turn.start, 2),
                "end": round(turn.end, 2),
            })

        logger.info(f"Found {len(segments)} diarization segments with "
                    f"{len(set(s['speaker'] for s in segments))} speakers")
        return segments

    except Exception as e:
        logger.error(f"Diarization failed for {audio_file}: {e}")
        return []


def assign_speaker_to_transcript(
    transcript_segments: list[dict],
    diarization_segments: list[dict],
    chunk_offset: float = 0.0,
) -> list[dict]:
    """
    Merge Whisper transcript segments with diarization speaker labels.

    Args:
        transcript_segments: List of {start, end, text} from Whisper.
        diarization_segments: List of {speaker, start, end} from pyannote.
        chunk_offset: Time offset (seconds) of this chunk in the original audio.

    Returns:
        List of {speaker, start, end, text} merged segments.
    """
    if not diarization_segments:
        # No diarization — return with generic speaker label
        return [
            {
                "speaker": "Speaker 1",
                "start": s.get("start", 0) + chunk_offset,
                "end": s.get("end", 0) + chunk_offset,
                "text": s.get("text", "").strip(),
            }
            for s in transcript_segments
        ]

    # Build a fast lookup: for each transcript segment midpoint, find speaker
    def find_speaker(start: float, end: float) -> str:
        midpoint = (start + end) / 2.0
        best_speaker = "Unknown"
        best_overlap = 0.0

        for seg in diarization_segments:
            overlap_start = max(start, seg["start"])
            overlap_end = min(end, seg["end"])
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = seg["speaker"]

        # Fallback: nearest segment by midpoint
        if best_overlap == 0.0:
            nearest = min(
                diarization_segments,
                key=lambda s: abs((s["start"] + s["end"]) / 2.0 - midpoint),
            )
            best_speaker = nearest["speaker"]

        # Normalize speaker name: SPEAKER_00 → Speaker 1
        return _normalize_speaker_name(best_speaker)

    merged = []
    for seg in transcript_segments:
        s_start = seg.get("start", 0)
        s_end = seg.get("end", 0)
        speaker = find_speaker(s_start, s_end)
        merged.append({
            "speaker": speaker,
            "start": round(s_start + chunk_offset, 2),
            "end": round(s_end + chunk_offset, 2),
            "text": seg.get("text", "").strip(),
        })

    return merged


def _normalize_speaker_name(raw_name: str) -> str:
    """Convert SPEAKER_00 → Speaker 1 etc."""
    try:
        if "_" in raw_name:
            parts = raw_name.split("_")
            number = int(parts[-1]) + 1
            return f"Speaker {number}"
    except (ValueError, IndexError):
        pass
    return raw_name


def format_speaker_transcript(merged_segments: list[dict]) -> str:
    """
    Collapse consecutive same-speaker segments and produce readable transcript.

    Returns:
        Multi-line string like:
        Speaker 1: Good morning everyone.
        Speaker 2: Today we'll discuss...
    """
    if not merged_segments:
        return ""

    lines = []
    current_speaker = None
    current_text_parts = []

    for seg in merged_segments:
        speaker = seg.get("speaker", "Unknown")
        text = seg.get("text", "").strip()
        if not text:
            continue

        if speaker == current_speaker:
            current_text_parts.append(text)
        else:
            if current_speaker is not None and current_text_parts:
                lines.append(f"{current_speaker}: {' '.join(current_text_parts)}")
            current_speaker = speaker
            current_text_parts = [text]

    if current_speaker is not None and current_text_parts:
        lines.append(f"{current_speaker}: {' '.join(current_text_parts)}")

    return "\n".join(lines)