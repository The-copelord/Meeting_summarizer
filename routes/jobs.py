"""
routes/jobs.py
Job management endpoints: start analysis, poll status, fetch summary.
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from notifier import subscribe, unsubscribe

from auth import get_current_user
from database import get_db
from models import Job, JobStatus, Result, User

router = APIRouter(tags=["jobs"])


class AnalyseRequest(BaseModel):
    job_id: str


def _get_owned_job(job_id: str, user: User, db: Session) -> Job:
    """Fetch a job and verify it belongs to the requesting user."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job


@router.post("/analyse")
def start_analysis(
    body: AnalyseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(body.job_id, current_user, db)

    if job.status not in (JobStatus.uploaded, JobStatus.error):
        raise HTTPException(
            status_code=400,
            detail=f"Job is already in status '{job.status.value}'. Cannot re-queue.",
        )

    job.status = JobStatus.queued
    db.commit()
    return {"message": "Job queued for analysis", "job_id": job.id}


@router.get("/get_status/{job_id}")
def get_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, current_user, db)
    response = {"job_id": job.id, "status": job.status.value}
    if job.status == JobStatus.error and job.error_msg:
        response["error"] = job.error_msg
    return response


@router.get("/get_summary")
def get_summary(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, current_user, db)

    if job.status != JobStatus.done:
        raise HTTPException(
            status_code=400,
            detail=f"Summary not ready. Current status: '{job.status.value}'",
        )

    result = db.query(Result).filter(Result.job_id == job_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result record missing")

    summary = {}
    if result.summary_json:
        try:
            summary = json.loads(result.summary_json)
        except json.JSONDecodeError:
            summary = {}

    return {
        "job_id": job_id,
        "transcript": result.transcript or "",
        "summary": summary,
    }


@router.get("/job-stream/{job_id}")
async def job_stream(
    job_id: str,
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    """
    SSE endpoint — blocks until the worker calls notify(job_id).
    No DB polling. Worker pushes directly into an asyncio.Queue.
    """
    from auth import decode_token
    from models import User as UserModel
    payload = decode_token(token)
    email = payload.get("sub")
    current_user = db.query(UserModel).filter(UserModel.email == email).first()
    if not current_user:
        from fastapi.responses import Response
        return Response("Unauthorized", status_code=401)

    job = _get_owned_job(job_id, current_user, db)

    # Already finished — return immediately, no need to wait
    if job.status == JobStatus.done:
        async def _done():
            yield f"event: done\ndata: {job_id}\n\n"
        return StreamingResponse(_done(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    if job.status == JobStatus.error:
        async def _error():
            yield f"event: error\ndata: {job.error_msg or 'Processing failed'}\n\n"
        return StreamingResponse(_error(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def event_generator():
        q = subscribe(job_id)
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    # Block here — no loop, no polling. Worker wakes us up.
                    msg = await asyncio.wait_for(q.get(), timeout=5)
                    if msg["status"] == "done":
                        yield f"event: done\ndata: {job_id}\n\n"
                    else:
                        yield f"event: error\ndata: {msg.get('detail', 'Processing failed')}\n\n"
                    return
                except asyncio.TimeoutError:
                    # Send current status as keepalive so dashboard stays in sync
                    current = db.query(Job).filter(Job.id == job_id).first()
                    status_val = current.status.value if current else "processing"
                    yield f"event: status\ndata: {status_val}\n\n"
        finally:
            unsubscribe(job_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@router.get("/job-metadata/{job_id}")
def get_job_metadata(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns file metadata for a specific job.
    Duration is populated after the worker completes the FFmpeg probe.
    """
    job = _get_owned_job(job_id, current_user, db)

    duration = job.file_duration_seconds
    return {
        "job_id": job.id,
        "original_filename": job.original_filename,
        "file_size_bytes": job.file_size_bytes,
        "file_size_human": _fmt_bytes(job.file_size_bytes),
        "file_format": job.file_format,
        "file_duration_seconds": duration,
        "file_duration_human": _fmt_duration(duration),
        "status": job.status.value,
        "uploaded_at": job.created_at.isoformat(),
    }


def _fmt_bytes(size: Optional[int]) -> str:
    if not size:
        return "unknown"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _fmt_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "unknown"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


@router.get("/file-metadata/{job_id}")
def get_file_metadata(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns stored metadata for the uploaded file associated with a job.
    Available immediately after upload — no need to wait for processing.
    """
    job = _get_owned_job(job_id, current_user, db)

    def fmt_size(b):
        if b is None:
            return None
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    def fmt_duration(s):
        if s is None:
            return None
        mins = int(s // 60)
        secs = int(s % 60)
        hours = mins // 60
        mins = mins % 60
        if hours:
            return f"{hours}h {mins}m {secs}s"
        return f"{mins}m {secs}s"

    return {
        "job_id": job_id,
        "filename":          job.original_filename,
        "extension":         job.file_extension,
        "size_bytes":        job.file_size_bytes,
        "size_human":        fmt_size(job.file_size_bytes),
        "duration_seconds":  job.file_duration_seconds,
        "duration_human":    fmt_duration(job.file_duration_seconds),
        "uploaded_at":       job.created_at.isoformat(),
        "status":            job.status.value,
    }


@router.get("/jobs")
def list_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all jobs for the current user."""
    jobs = (
        db.query(Job)
        .filter(Job.user_id == current_user.id)
        .order_by(Job.created_at.desc())
        .all()
    )
    return [
        {
            "job_id": j.id,
            "status": j.status.value,
            "original_filename": j.original_filename,
            "file_path": j.file_path,
            "created_at": j.created_at.isoformat(),
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in jobs
    ]