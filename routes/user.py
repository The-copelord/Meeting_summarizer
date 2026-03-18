"""
routes/user.py
User signup, login, settings, and model cache endpoints.
"""

import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db
from models import User, ModelCache
from auth import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/user", tags=["auth"])

CACHE_TTL_DAYS = 7  # Refresh model list once per week


# ── Pydantic models ───────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class SettingsRequest(BaseModel):
    groq_api_key:      str = ""
    anthropic_api_key: str = ""
    openai_api_key:    str = ""
    together_api_key:  str = ""
    mistral_api_key:   str = ""
    selected_provider: str = "groq"
    selected_model:    str = "llama-3.3-70b-versatile"


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return {"message": "User created successfully", "email": body.email}


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid email or password")
    token = create_access_token({"sub": user.email})
    return TokenResponse(access_token=token)


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return {
        "groq_api_key":      current_user.groq_api_key      or "",
        "anthropic_api_key": current_user.anthropic_api_key or "",
        "openai_api_key":    current_user.openai_api_key    or "",
        "together_api_key":  current_user.together_api_key  or "",
        "mistral_api_key":   current_user.mistral_api_key   or "",
        "selected_provider": current_user.selected_provider or "groq",
        "selected_model":    current_user.selected_model    or "llama-3.3-70b-versatile",
    }


@router.put("/settings")
def update_settings(
    body: SettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.groq_api_key      = body.groq_api_key      or None
    current_user.anthropic_api_key = body.anthropic_api_key or None
    current_user.openai_api_key    = body.openai_api_key    or None
    current_user.together_api_key  = body.together_api_key  or None
    current_user.mistral_api_key   = body.mistral_api_key   or None
    current_user.selected_provider = body.selected_provider or "groq"
    current_user.selected_model    = body.selected_model    or "llama-3.3-70b-versatile"
    db.commit()
    return {"message": "Settings saved"}


# ── Model cache helpers ───────────────────────────────────────────────────────

def _get_user_key(user: User, provider: str) -> str:
    """Get user's API key for a provider, falling back to env var."""
    import os
    key_map = {
        "groq":      (user.groq_api_key,      "GROQ_API_KEY"),
        "anthropic": (user.anthropic_api_key,  "ANTHROPIC_API_KEY"),
        "claude":    (user.anthropic_api_key,  "ANTHROPIC_API_KEY"),
        "openai":    (user.openai_api_key,     "OPENAI_API_KEY"),
        "together":  (user.together_api_key,   "TOGETHER_API_KEY"),
        "mistral":   (user.mistral_api_key,    "MISTRAL_API_KEY"),
    }
    if provider not in key_map:
        return None
    user_key, env_var = key_map[provider]
    return user_key or os.getenv(env_var)


def _is_cache_valid(cached_at: datetime) -> bool:
    """Returns True if cache is less than 7 days old."""
    now = datetime.now(timezone.utc)
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    return (now - cached_at) < timedelta(days=CACHE_TTL_DAYS)


def _get_cached_models(user_id: int, provider: str, db: Session):
    """Return cached models if valid, else None."""
    cache = db.query(ModelCache).filter(
        ModelCache.user_id == user_id,
        ModelCache.provider == provider,
    ).first()
    if cache and _is_cache_valid(cache.cached_at):
        return json.loads(cache.models_json)
    return None


def _save_cache(user_id: int, provider: str, models: list, db: Session):
    """Save or update model cache for a provider."""
    cache = db.query(ModelCache).filter(
        ModelCache.user_id == user_id,
        ModelCache.provider == provider,
    ).first()
    if cache:
        cache.models_json = json.dumps(models)
        cache.cached_at = datetime.now(timezone.utc)
    else:
        db.add(ModelCache(
            user_id=user_id,
            provider=provider,
            models_json=json.dumps(models),
        ))
    db.commit()


# ── Model endpoints ───────────────────────────────────────────────────────────

@router.get("/models/{provider}")
def get_provider_models(
    provider: str,
    force_refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get models for a single provider.
    Uses DB cache (7-day TTL). Only calls provider API if:
    - No cache exists
    - Cache is older than 7 days
    - force_refresh=true query param passed
    """
    valid_providers = {"groq", "anthropic", "claude", "openai", "together", "mistral"}
    # claude is an alias for anthropic
    if provider == "claude": provider = "anthropic"
    if provider not in valid_providers:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Check cache first (unless force refresh)
    if not force_refresh:
        cached = _get_cached_models(current_user.id, provider, db)
        if cached is not None:
            return {
                "provider": provider,
                "models": cached,
                "from_cache": True,
            }

    # No valid cache — check if user has a key for this provider
    api_key = _get_user_key(current_user, provider)
    if not api_key:
        return {
            "provider": provider,
            "models": [],
            "from_cache": False,
            "error": f"No API key configured for {provider}",
        }

    # Fetch live from provider
    from services.llm_clients import (
        fetch_groq_models, fetch_openai_models,
        fetch_together_models, fetch_mistral_models,
        ANTHROPIC_MODELS,
    )

    fetchers = {
        "groq":      lambda: fetch_groq_models(api_key),
        "anthropic": lambda: ANTHROPIC_MODELS,
        "openai":    lambda: fetch_openai_models(api_key),
        "together":  lambda: fetch_together_models(api_key),
        "mistral":   lambda: fetch_mistral_models(api_key),
    }

    try:
        models = fetchers[provider]()
        _save_cache(current_user.id, provider, models, db)
        return {
            "provider": provider,
            "models": models,
            "from_cache": False,
        }
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"Failed to fetch models from {provider}: {e}")


@router.get("/available-models")
def get_available_models(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns models for ALL providers the user has keys for.
    Each provider uses its own cache independently.
    Only providers with configured keys are included.
    """
    providers = ["groq", "anthropic", "openai", "together", "mistral"]  # claude uses anthropic key
    result = {}

    for provider in providers:
        api_key = _get_user_key(current_user, provider)
        if not api_key:
            continue  # Skip providers with no key

        # Try cache first
        cached = _get_cached_models(current_user.id, provider, db)
        if cached is not None:
            result[provider] = cached
            continue

        # Fetch and cache
        from services.llm_clients import (
            fetch_groq_models, fetch_openai_models,
            fetch_together_models, fetch_mistral_models,
            ANTHROPIC_MODELS,
        )
        fetchers = {
            "groq":      lambda k=api_key: fetch_groq_models(k),
            "anthropic": lambda: ANTHROPIC_MODELS,
            "openai":    lambda k=api_key: fetch_openai_models(k),
            "together":  lambda k=api_key: fetch_together_models(k),
            "mistral":   lambda k=api_key: fetch_mistral_models(k),
        }
        try:
            models = fetchers[provider]()
            _save_cache(current_user.id, provider, models, db)
            result[provider] = models
        except Exception as e:
            result[provider] = []

    return {
        "providers": result,
        "selected_provider": current_user.selected_provider or "groq",
        "selected_model":    current_user.selected_model    or "llama-3.3-70b-versatile",
    }


@router.delete("/models/cache")
def clear_model_cache(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Force clear all cached models for the current user."""
    db.query(ModelCache).filter(ModelCache.user_id == current_user.id).delete()
    db.commit()
    return {"message": "Model cache cleared"}