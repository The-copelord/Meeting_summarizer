"""
audio_utils.py
Handles audio duration detection and chunking via FFmpeg.
"""

import os
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_audio_duration(file_path: str) -> float:
    """
    Returns the duration of an audio file in seconds using ffprobe.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(result.stdout.strip())
        logger.info(f"Audio duration: {duration:.2f}s")
        return duration
    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe error: {e.stderr}")
        raise RuntimeError(f"Failed to detect audio duration: {e.stderr}")
    except ValueError as e:
        raise RuntimeError(f"Could not parse duration from ffprobe output: {e}")


def split_audio(file_path: str, chunk_length: int = 600, output_dir: str = None) -> list[str]:
    """
    Splits an audio file into fixed-length chunks using FFmpeg.
    
    Args:
        file_path: Path to the input MP3 file.
        chunk_length: Duration of each chunk in seconds (default: 600 = 10 min).
        output_dir: Directory to save chunks. Defaults to a temp subdirectory.

    Returns:
        Sorted list of paths to the generated chunk files.
    """
    file_path = str(file_path)
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(file_path), "chunks")
    
    os.makedirs(output_dir, exist_ok=True)
    chunk_pattern = os.path.join(output_dir, "chunk_%03d.mp3")

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i", file_path,
                "-f", "segment",
                "-segment_time", str(chunk_length),
                "-c", "copy",
                "-reset_timestamps", "1",
                "-y",
                chunk_pattern,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg chunking error: {e.stderr}")
        raise RuntimeError(f"Failed to split audio: {e.stderr}")

    chunks = sorted(
        str(p) for p in Path(output_dir).glob("chunk_*.mp3")
    )
    logger.info(f"Created {len(chunks)} audio chunks in {output_dir}")
    return chunks


def cleanup_files(file_paths: list[str]) -> None:
    """Removes temporary files."""
    for fp in file_paths:
        try:
            if os.path.exists(fp):
                os.remove(fp)
                logger.debug(f"Deleted temp file: {fp}")
        except OSError as e:
            logger.warning(f"Could not delete {fp}: {e}")


def convert_to_mp3(input_path: str, output_path: str = None) -> str:
    """
    Convert any video/audio file to MP3 using FFmpeg.
    
    Args:
        input_path: Path to the input file (e.g. .mp4, .mkv, .webm).
        output_path: Optional output path. Defaults to same dir with .mp3 extension.
    
    Returns:
        Path to the converted MP3 file.
    """
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + ".mp3"

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i", input_path,
                "-vn",                  # Strip video
                "-acodec", "libmp3lame",
                "-ab", "192k",          # 192kbps — good quality for speech
                "-ar", "44100",         # Sample rate
                "-y",                   # Overwrite output
                output_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info(f"Converted {input_path} → {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg conversion error: {e.stderr}")
        raise RuntimeError(f"Failed to convert file to MP3: {e.stderr}")


def cleanup_directory(dir_path: str) -> None:
    """Removes all files in a directory, then the directory itself."""
    import shutil
    try:
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)
            logger.debug(f"Deleted temp directory: {dir_path}")
    except OSError as e:
        logger.warning(f"Could not delete directory {dir_path}: {e}")