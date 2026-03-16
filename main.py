"""
main.py
FastAPI application entrypoint.
Wires together auth, upload, job routes, DB init, and background scheduler.
"""

# ── PyTorch 2.6 compatibility ─────────────────────────────────────────────────
# Must be before ANY other import that transitively loads torch/pyannote/speechbrain.
import torch as _torch
import functools as _functools
_orig_torch_load = _torch.load
@_functools.wraps(_orig_torch_load)
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _orig_torch_load(*args, **kwargs)
_torch.load = _patched_torch_load
# ─────────────────────────────────────────────────────────────────────────────

import logging
import warnings

from dotenv import load_dotenv
load_dotenv()

warnings.filterwarnings("ignore", message="The MPEG_LAYER_III subtype is unknown")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pathlib import Path

from database import init_db
from routes.user import router as user_router
from routes.upload import router as upload_router
from routes.jobs import router as jobs_router
from workers.scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    logger.info("Initializing database...")
    init_db()
    logger.info("Starting background scheduler...")
    scheduler = start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


app = FastAPI(
    title="MeetingMind Analysis API",
    description="Async meeting audio analysis with job queue, auth, and speaker diarization.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(user_router)
app.include_router(upload_router)
app.include_router(jobs_router)

# Serve static files
STATIC_DIR = Path("static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=FileResponse)
def serve_ui():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}