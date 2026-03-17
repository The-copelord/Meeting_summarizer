"""
database.py
SQLAlchemy engine, session factory, and base model.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/meeting_analysis"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables and add any missing columns. Called once at startup."""
    from models import User, Job, Result  # noqa: F401 — imported for side effects
    Base.metadata.create_all(bind=engine)
    _migrate_add_columns()


def _migrate_add_columns():
    """
    Idempotent migration — adds new metadata columns to jobs table if missing.
    Handles upgrades from older schema versions without losing data.
    """
    new_columns = [
        ("original_filename",     "VARCHAR(512)"),
        ("file_size_bytes",       "BIGINT"),
        ("file_duration_seconds", "FLOAT"),
        ("file_extension",        "VARCHAR(20)"),
    ]
    user_columns = [
        ("groq_api_key",      "VARCHAR(512)"),
        ("anthropic_api_key", "VARCHAR(512)"),
        ("selected_model",    "VARCHAR(128)"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )
                )
                conn.commit()
            except Exception:
                conn.rollback()
    for col_name, col_type in user_columns:
        with engine.connect() as conn:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )
                )
                conn.commit()
            except Exception:
                conn.rollback()