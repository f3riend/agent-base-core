import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,64}$")
security_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Oturum gerekli.")
    payload = decode_access_token(creds.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Gecersiz veya suresi dolmus oturum.")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Gecersiz oturum.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Kullanici bulunamadi.")
    return user


def _normalize_username(raw: str) -> str:
    return raw.strip().lower()


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    un = _normalize_username(body.username)
    if not _USERNAME_RE.fullmatch(un):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Kullanici adi 3-64 karakter; sadece harf, rakam ve alt cizgi (_).",
        )
    existing = db.scalar(select(User).where(User.username == un))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu kullanici adi zaten alinmis.")
    ws = secrets.token_hex(16)
    user = User(username=un, password_hash=hash_password(body.password), workspace_uid=ws)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, username=user.username, uid=user.workspace_uid),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    un = _normalize_username(body.username)
    user = db.scalar(select(User).where(User.username == un))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Kullanici adi veya sifre hatali.")
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, username=user.username, uid=user.workspace_uid),
    )


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=user.id, username=user.username, uid=user.workspace_uid)
