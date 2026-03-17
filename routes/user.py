"""
routes/user.py
User signup and login endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db
from models import User
from auth import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/user", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


AVAILABLE_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
    "moonshotai/kimi-k2-instruct-0905",
    "meta-llama/llama-prompt-guard-2-86m",
    "meta-llama/llama-prompt-guard-2-22m",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SettingsRequest(BaseModel):
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    selected_model: str = "llama-3.3-70b-versatile"


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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token({"sub": user.email})
    return TokenResponse(access_token=token)


@router.get("/settings")
def get_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "groq_api_key": current_user.groq_api_key or "",
        "anthropic_api_key": current_user.anthropic_api_key or "",
        "selected_model": current_user.selected_model or "llama-3.3-70b-versatile",
        "available_models": AVAILABLE_MODELS,
    }


@router.put("/settings")
def update_settings(
    body: SettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.selected_model and body.selected_model not in AVAILABLE_MODELS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Unknown model: {body.selected_model}")

    current_user.groq_api_key = body.groq_api_key or None
    current_user.anthropic_api_key = body.anthropic_api_key or None
    current_user.selected_model = body.selected_model or "llama-3.3-70b-versatile"
    db.commit()
    return {"message": "Settings saved"}