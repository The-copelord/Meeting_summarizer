"""
models.py
SQLAlchemy ORM models: User, Job, Result.
"""

import enum
from datetime import datetime, timezone

def _now():
    """Current local time with timezone info."""
    return datetime.now(timezone.utc).astimezone()

from sqlalchemy import (
    Column, String, Integer, Float, Text,
    DateTime,
    ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import relationship

from database import Base


class JobStatus(str, enum.Enum):
    uploaded   = "uploaded"
    queued     = "queued"
    processing = "processing"
    done       = "done"
    error      = "error"


class User(Base):
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String(255), unique=True, index=True, nullable=False)
    password_hash     = Column(String(255), nullable=False)
    groq_api_key      = Column(String(512), nullable=True)
    anthropic_api_key = Column(String(512), nullable=True)
    openai_api_key    = Column(String(512), nullable=True)
    together_api_key  = Column(String(512), nullable=True)
    mistral_api_key   = Column(String(512), nullable=True)
    selected_model    = Column(String(256), nullable=True, default="llama-3.3-70b-versatile")
    selected_provider = Column(String(64),  nullable=True, default="groq")
    created_at        = Column(DateTime(timezone=True), default=_now)

    jobs = relationship("Job", back_populates="user", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id         = Column(String(36), primary_key=True, index=True)   # UUID
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_path             = Column(String(512), nullable=False)
    original_filename     = Column(String(512), nullable=True)
    file_size_bytes       = Column(Integer, nullable=True)
    file_duration_seconds = Column(Float, nullable=True)
    file_extension        = Column(String(20), nullable=True)
    status     = Column(SAEnum(JobStatus), default=JobStatus.uploaded, nullable=False)
    error_msg  = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user   = relationship("User", back_populates="jobs")
    result = relationship("Result", back_populates="job", uselist=False,
                          cascade="all, delete-orphan")


class ModelCache(Base):
    """Caches available models per user per provider. Refreshed weekly."""
    __tablename__ = "model_cache"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider   = Column(String(64), nullable=False)
    models_json = Column(Text, nullable=False)   # JSON array of model id strings
    cached_at  = Column(DateTime(timezone=True), default=_now)

    user = relationship("User")


class Result(Base):
    __tablename__ = "results"

    job_id       = Column(String(36), ForeignKey("jobs.id"), primary_key=True)
    transcript   = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)   # JSON-encoded final summary
    created_at   = Column(DateTime(timezone=True), default=_now)

    job = relationship("Job", back_populates="result")