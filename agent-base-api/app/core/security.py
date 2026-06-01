"""JWT ve bcrypt parola islemleri (passlib yerine bcrypt 5.x ile uyumlu)."""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.env_settings import env_settings


def _password_bytes(password: str) -> bytes:
    b = password.encode("utf-8")
    return b[:72] if len(b) > 72 else b


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(plain_password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str | int, extra_claims: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=env_settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(subject), "exp": expire}
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, env_settings.SECRET_KEY, algorithm=env_settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, env_settings.SECRET_KEY, algorithms=[env_settings.ALGORITHM])
    except JWTError:
        return None
