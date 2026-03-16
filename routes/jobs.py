"""
routes/jobs.py
Job management endpoints: start analysis, poll status, fetch summary.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

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
            "file_path": j.file_path,
            "created_at": j.created_at.isoformat(),
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in jobs
    ]