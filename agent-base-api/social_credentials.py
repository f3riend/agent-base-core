"""
Encrypted social/commerce credential store.

This is the layer that lets the runtime hold real OAuth tokens / API keys
for connected accounts (Instagram, Facebook, Trendyol, Shopify, …) without
storing plaintext anywhere.

Encryption:
    - Tokens are encrypted with cryptography.fernet.Fernet.
    - The key comes from APP_SECRET_KEY env var. The expected shape is a
      base64-encoded 32-byte key (the value `Fernet.generate_key()` returns).
    - If the env var is missing or malformed, save/get raise
      MissingSecretKeyError. List and revoke do NOT require the key —
      they operate on metadata + row state only — so the dashboard can
      always show "what accounts are connected" and let the operator
      revoke even if the secret was rotated.

Threading:
    - The Fernet instance is cached at module level. The cache is
      invalidated automatically on key change because we re-read the
      env var lazily on each load.

Multi-tenant:
    - Every row carries user_id. Every public function takes user_id;
      there is no cross-tenant lookup path.

Status:
    - status='active' is the default. revoke flips to 'revoked' (soft
      delete). A revoked row keeps the encrypted blob so audit / rotation
      logic can still see "an Instagram credential existed and was
      revoked at T". To purge entirely, delete the row directly.

This module deliberately exposes ONLY high-level operations. There is no
"export raw blob" path — getting plaintext requires going through
get_credential() and an in-process call site.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from db import DEFAULT_USER_ID, db_connection, execute_query, execute_write, now_iso


CREDENTIAL_ACTIVE = "active"
CREDENTIAL_REVOKED = "revoked"

SUPPORTED_PROVIDERS = (
    "instagram",
    "facebook",
    "trendyol",
    "shopify",
    "woocommerce",
    "amazon",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MissingSecretKeyError(RuntimeError):
    """Raised when an encryption operation is attempted without APP_SECRET_KEY."""


class CredentialNotFound(LookupError):
    """Raised when a get_credential lookup returns no active row."""


class UnsupportedProviderError(ValueError):
    """Raised when an unknown provider name is passed in."""


# ---------------------------------------------------------------------------
# Fernet — lazy + revalidated
# ---------------------------------------------------------------------------


_FERNET_CACHE: dict[str, Fernet] = {}


def _resolve_fernet() -> Fernet:
    """Lazy-load a Fernet from APP_SECRET_KEY. Cached per key value so a
    rotated env var picks up automatically on next call without restart.
    """
    key = os.environ.get("APP_SECRET_KEY", "").strip()
    if not key:
        raise MissingSecretKeyError(
            "APP_SECRET_KEY is not set; cannot encrypt or decrypt social "
            "credentials. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and add it to .env."
        )
    cached = _FERNET_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        f = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise MissingSecretKeyError(
            f"APP_SECRET_KEY is set but malformed (expected base64 32-byte key): {exc}"
        )
    _FERNET_CACHE[key] = f
    return f


def has_secret_key() -> bool:
    """Diagnostic: is the encryption layer usable right now?"""
    try:
        _resolve_fernet()
        return True
    except MissingSecretKeyError:
        return False


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class SocialCredential:
    """In-memory representation of a connected account.

    `token` is the DECRYPTED token, populated only by get_credential().
    Other entry points (list, revoke) never set it.
    """
    id: int
    user_id: int
    provider: str
    account_handle: str
    scope: str | None
    expires_at: str | None
    status: str
    created_at: str
    updated_at: str
    token: str | None = None             # plaintext after decryption only
    encrypted_token_blob: str | None = None  # base64 fernet blob (DB form)

    def as_public(self) -> dict:
        """Safe shape for API responses: NEVER includes token or blob."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "provider": self.provider,
            "account_handle": self.account_handle,
            "scope": self.scope,
            "expires_at": self.expires_at,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise UnsupportedProviderError(
            f"unsupported provider {provider!r}; "
            f"expected one of {SUPPORTED_PROVIDERS}"
        )
    return p


def _row_to_cred(row, *, include_blob: bool = False) -> SocialCredential:
    d = dict(row)
    return SocialCredential(
        id=int(d["id"]),
        user_id=int(d["user_id"]),
        provider=d["provider"],
        account_handle=d["account_handle"],
        scope=d.get("scope"),
        expires_at=d.get("token_expires_at"),
        status=d.get("status") or CREDENTIAL_ACTIVE,
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        encrypted_token_blob=d["encrypted_token_blob"] if include_blob else None,
    )


def _encrypt(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("token must be a non-empty string")
    f = _resolve_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt(blob: str) -> str:
    f = _resolve_fernet()
    try:
        return f.decrypt(blob.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise MissingSecretKeyError(
            "credential decryption failed — APP_SECRET_KEY may have been "
            "rotated. Re-save the credential after rotation."
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_credential(
    *,
    user_id: int,
    provider: str,
    account_handle: str,
    token: str,
    scope: str | None = None,
    expires_at: str | None = None,
) -> SocialCredential:
    """Encrypt + upsert a credential for (user_id, provider, account_handle).

    If an active row already exists for the same tuple, its blob, scope, and
    expires_at are updated in place (treat as a refresh). If the existing row
    is revoked, it is reactivated.
    """
    p = _validate_provider(provider)
    handle = (account_handle or "").strip()
    if not handle:
        raise ValueError("account_handle is required")

    blob = _encrypt(token)
    ts = now_iso()

    existing = execute_query(
        """
        SELECT id, status FROM social_credentials
        WHERE user_id=? AND provider=? AND account_handle=?
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), p, handle),
        one=True,
    )

    if existing:
        execute_write(
            """
            UPDATE social_credentials
            SET encrypted_token_blob=?, scope=?, token_expires_at=?,
                status='active', updated_at=?
            WHERE id=?
            """,
            (blob, scope, expires_at, ts, int(existing["id"])),
        )
        cred_id = int(existing["id"])
    else:
        cred_id = execute_write(
            """
            INSERT INTO social_credentials (
                user_id, provider, account_handle, encrypted_token_blob,
                scope, token_expires_at, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (int(user_id), p, handle, blob, scope, expires_at, ts, ts),
        )

    return get_credential_by_id(cred_id, include_token=False)


def get_credential(
    user_id: int,
    provider: str,
    *,
    account_handle: str | None = None,
) -> SocialCredential:
    """Look up + DECRYPT the active credential for (user_id, provider).

    If account_handle is omitted, the most recently updated active row
    for the provider is returned. Multi-account users should pass handle.

    Raises CredentialNotFound if no active row matches.
    Raises MissingSecretKeyError if APP_SECRET_KEY is unavailable.
    """
    p = _validate_provider(provider)
    if account_handle:
        row = execute_query(
            """
            SELECT * FROM social_credentials
            WHERE user_id=? AND provider=? AND account_handle=?
              AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (int(user_id), p, account_handle),
            one=True,
        )
    else:
        row = execute_query(
            """
            SELECT * FROM social_credentials
            WHERE user_id=? AND provider=? AND status='active'
            ORDER BY updated_at DESC, id DESC LIMIT 1
            """,
            (int(user_id), p),
            one=True,
        )
    if not row:
        raise CredentialNotFound(
            f"no active {p} credential for user_id={user_id}"
            + (f" handle={account_handle}" if account_handle else "")
        )
    cred = _row_to_cred(row, include_blob=True)
    cred.token = _decrypt(cred.encrypted_token_blob)
    cred.encrypted_token_blob = None  # strip the ciphertext from the returned object
    return cred


def try_get_credential(
    user_id: int,
    provider: str,
    *,
    account_handle: str | None = None,
) -> SocialCredential | None:
    """Same as get_credential but returns None instead of raising.

    Useful for tools that check 'do I have real credentials, or do I
    fall back to fake behavior'.
    """
    try:
        return get_credential(user_id, provider, account_handle=account_handle)
    except (CredentialNotFound, MissingSecretKeyError):
        return None


def get_credential_by_id(
    cred_id: int,
    *,
    include_token: bool = False,
) -> SocialCredential:
    row = execute_query(
        "SELECT * FROM social_credentials WHERE id=?",
        (int(cred_id),),
        one=True,
    )
    if not row:
        raise CredentialNotFound(f"credential id {cred_id} not found")
    cred = _row_to_cred(row, include_blob=include_token)
    if include_token:
        cred.token = _decrypt(cred.encrypted_token_blob)
        cred.encrypted_token_blob = None
    return cred


def list_credentials(user_id: int) -> list[SocialCredential]:
    """Return all credential rows for a user — metadata only, no token.

    Safe to call without APP_SECRET_KEY; never decrypts.
    """
    rows = execute_query(
        """
        SELECT * FROM social_credentials
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (int(user_id),),
    )
    return [_row_to_cred(r, include_blob=False) for r in rows]


def revoke_credential(
    cred_id: int,
    *,
    user_id: int | None = None,
    reason: str = "operator_revoked",
) -> SocialCredential:
    """Flip a credential to status='revoked'.

    Soft-delete: row is kept so audit can show "an account was connected
    and was revoked at T". To physically delete, call purge_credential.

    Safe to call without APP_SECRET_KEY; never decrypts.
    """
    existing = execute_query(
        "SELECT * FROM social_credentials WHERE id=?",
        (int(cred_id),),
        one=True,
    )
    if not existing:
        raise CredentialNotFound(f"credential id {cred_id} not found")
    if user_id is not None and int(existing["user_id"]) != int(user_id):
        # Multi-tenant guard: refuse cross-tenant revocation
        raise CredentialNotFound(
            f"credential id {cred_id} not visible to user_id={user_id}"
        )

    ts = now_iso()
    execute_write(
        """
        UPDATE social_credentials
        SET status='revoked', updated_at=?
        WHERE id=?
        """,
        (ts, int(cred_id)),
    )

    try:
        from observability import _emit
        _emit(
            "CREDENTIAL_REVOKED",
            {
                "credential_id": cred_id,
                "provider": existing["provider"],
                "handle": existing["account_handle"],
                "reason": reason,
                "summary": f"{existing['provider']} kimlik bilgisi iptal edildi: {existing['account_handle']}",
            },
            persist=True,
            user_id=int(existing["user_id"]),
        )
    except Exception:
        pass

    return get_credential_by_id(cred_id, include_token=False)


def purge_credential(cred_id: int) -> bool:
    """Hard delete a credential row. Use only for compliance/right-to-erasure."""
    execute_write(
        "DELETE FROM social_credentials WHERE id=?",
        (int(cred_id),),
    )
    return True


def revoke_provider(user_id: int, provider: str) -> int:
    """Revoke all active credentials for a user/provider. Returns count revoked."""
    p = _validate_provider(provider)
    rows = execute_query(
        """
        SELECT id FROM social_credentials
        WHERE user_id=? AND provider=? AND status='active'
        """,
        (int(user_id), p),
    )
    n = 0
    for r in rows:
        try:
            revoke_credential(int(r["id"]), user_id=user_id, reason="provider_revoked")
            n += 1
        except Exception:
            continue
    return n
