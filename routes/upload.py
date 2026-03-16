"""
routes/upload.py
File upload endpoint — saves file and creates a job record.
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Job, JobStatus, User

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
}

router = APIRouter(tags=["upload"])


@router.post("/uploadfile")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Validate extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Validate not empty
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Save to disk under a unique name
    job_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{job_id}{ext}"
    with open(save_path, "wb") as f:
        f.write(content)

    # Create job record
    job = Job(
        id=job_id,
        user_id=current_user.id,
        file_path=str(save_path),
        status=JobStatus.uploaded,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return {"job_id": job_id, "status": job.status.value}