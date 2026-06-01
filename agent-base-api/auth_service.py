"""
Multi-tenant auth service — orgs, members, roles, API keys.

This module is the org/role boundary that the rest of the runtime composes
against. It does NOT change the existing user_id-centric APIs — those keep
working unchanged. It ADDS an org context that the request-handler layer
can resolve from an X-API-Key header or from user_id fallback.

Key design choices:

  - API keys are issued with `secrets.token_urlsafe(32)` and prefixed
    `aios_` so they're identifiable in logs. The raw key is returned
    EXACTLY ONCE at creation; only the SHA-256 hash is persisted.

  - resolve_org_for_user picks the user's first org by joined_at. Users
    can belong to multiple orgs; choosing an "active" one is a future
    concern (session-scoped switcher). For now: deterministic first-org.

  - get_org_user_ids is the join used by orchestration_api to filter
    workflows/tasks/approvals to the whole org instead of just one user.

  - Test-mode preservation: a user (e.g. user_id=1) with zero org
    memberships resolves to org_id=None. Endpoints fall back to
    legacy single-user filtering. The new layer is opt-in.

  - No role enforcement at the auth level. Roles are stored. Future
    middleware can read them; this module exposes the lookup.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from db import DEFAULT_USER_ID, db_connection, execute_query, execute_write, now_iso


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


VALID_PLANS = ("free", "pro", "enterprise")
VALID_ROLES = ("owner", "admin", "editor", "viewer")
DEFAULT_ROLE = "viewer"

VALID_SCOPES = ("read", "write", "admin")
DEFAULT_SCOPE = "read"

API_KEY_PREFIX = "aios_"
API_KEY_BYTES = 32


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OrgNotFound(LookupError):
    """Raised when an org id has no matching row."""


class MemberAlreadyExists(ValueError):
    """Raised when add_member is called on an existing (org, user) tuple."""


class InvalidRoleError(ValueError):
    """Raised on an unknown role name."""


class InvalidPlanError(ValueError):
    """Raised on an unknown plan name."""


class InvalidScopeError(ValueError):
    """Raised on an unknown scope name."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Org:
    id: int
    name: str
    slug: str
    plan: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "slug": self.slug,
            "plan": self.plan, "created_at": self.created_at,
        }


@dataclass
class OrgMember:
    id: int
    org_id: int
    user_id: int
    role: str
    joined_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id, "org_id": self.org_id, "user_id": self.user_id,
            "role": self.role, "joined_at": self.joined_at,
        }


@dataclass
class ApiKey:
    id: int
    org_id: int
    name: str
    scope: str
    status: str
    last_used_at: str | None
    expires_at: str | None
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id, "org_id": self.org_id, "name": self.name,
            "scope": self.scope, "status": self.status,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at, "created_at": self.created_at,
        }


@dataclass
class IssuedApiKey:
    """One-shot result from create_api_key. `raw_key` is shown ONCE."""
    metadata: ApiKey
    raw_key: str

    def to_dict(self) -> dict:
        return {**self.metadata.to_dict(), "raw_key": self.raw_key}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return (s or "org")[:40]


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_raw_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_BYTES)


def _validate_role(role: str) -> str:
    r = (role or "").strip().lower()
    if r not in VALID_ROLES:
        raise InvalidRoleError(
            f"invalid role {role!r}; expected one of {VALID_ROLES}"
        )
    return r


def _validate_plan(plan: str) -> str:
    p = (plan or "").strip().lower()
    if p not in VALID_PLANS:
        raise InvalidPlanError(
            f"invalid plan {plan!r}; expected one of {VALID_PLANS}"
        )
    return p


def _validate_scope(scope: str) -> str:
    s = (scope or "").strip().lower()
    if s not in VALID_SCOPES:
        raise InvalidScopeError(
            f"invalid scope {scope!r}; expected one of {VALID_SCOPES}"
        )
    return s


def _row_to_org(row) -> Org:
    d = dict(row)
    return Org(
        id=int(d["id"]), name=d["name"], slug=d["slug"],
        plan=d["plan"], created_at=d["created_at"],
    )


def _row_to_member(row) -> OrgMember:
    d = dict(row)
    return OrgMember(
        id=int(d["id"]), org_id=int(d["org_id"]), user_id=int(d["user_id"]),
        role=d["role"], joined_at=d["joined_at"],
    )


def _row_to_api_key(row) -> ApiKey:
    d = dict(row)
    return ApiKey(
        id=int(d["id"]), org_id=int(d["org_id"]), name=d["name"],
        scope=d["scope"], status=d.get("status") or "active",
        last_used_at=d.get("last_used_at"),
        expires_at=d.get("expires_at"),
        created_at=d["created_at"],
    )


# ---------------------------------------------------------------------------
# Org CRUD
# ---------------------------------------------------------------------------


def create_org(
    name: str,
    owner_user_id: int,
    *,
    slug: str | None = None,
    plan: str = "free",
) -> Org:
    """Create an org and immediately add the owner as a member with role=owner.

    Slug is derived from name if not provided. A numeric suffix is appended
    on slug collision so this never raises on a duplicate name (clients
    should not depend on slug stability across creates).
    """
    if not name or not str(name).strip():
        raise ValueError("org name is required")
    plan = _validate_plan(plan)

    base_slug = _slugify(slug or name)
    final_slug = base_slug
    n = 1
    while execute_query(
        "SELECT id FROM orgs WHERE slug=?",
        (final_slug,),
        one=True,
    ):
        n += 1
        final_slug = f"{base_slug}-{n}"

    ts = now_iso()
    org_id = execute_write(
        """
        INSERT INTO orgs (name, slug, plan, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (str(name).strip(), final_slug, plan, ts),
    )

    execute_write(
        """
        INSERT INTO org_members (org_id, user_id, role, joined_at)
        VALUES (?, ?, 'owner', ?)
        """,
        (org_id, int(owner_user_id), ts),
    )

    try:
        from observability import _emit
        _emit(
            "ORG_CREATED",
            {
                "org_id": org_id, "name": name, "slug": final_slug, "plan": plan,
                "owner_user_id": int(owner_user_id),
                "summary": f"yeni organizasyon: {name} ({final_slug})",
            },
            persist=True, user_id=int(owner_user_id),
        )
    except Exception:
        pass

    return get_org(org_id)


def get_org(org_id: int) -> Org:
    row = execute_query(
        "SELECT * FROM orgs WHERE id=?", (int(org_id),), one=True,
    )
    if not row:
        raise OrgNotFound(f"org id {org_id} not found")
    return _row_to_org(row)


def list_orgs_for_user(user_id: int) -> list[Org]:
    rows = execute_query(
        """
        SELECT o.* FROM orgs o
        JOIN org_members m ON m.org_id = o.id
        WHERE m.user_id=?
        ORDER BY m.joined_at ASC
        """,
        (int(user_id),),
    )
    return [_row_to_org(r) for r in rows]


def resolve_org_for_user(user_id: int) -> int | None:
    """Return the user's primary (oldest membership) org_id, or None.

    Returns None for users who are members of zero orgs — this is the
    test-mode path that preserves legacy `user_id=1` behavior: the
    request handler sees no org and falls back to single-user filtering.
    """
    row = execute_query(
        """
        SELECT org_id FROM org_members
        WHERE user_id=?
        ORDER BY joined_at ASC, id ASC LIMIT 1
        """,
        (int(user_id),),
        one=True,
    )
    return int(row["org_id"]) if row else None


# ---------------------------------------------------------------------------
# Membership CRUD
# ---------------------------------------------------------------------------


def add_member(org_id: int, user_id: int, role: str = DEFAULT_ROLE) -> OrgMember:
    role = _validate_role(role)
    # Ensure the org exists (raises OrgNotFound otherwise).
    get_org(org_id)

    existing = execute_query(
        "SELECT * FROM org_members WHERE org_id=? AND user_id=?",
        (int(org_id), int(user_id)),
        one=True,
    )
    if existing:
        raise MemberAlreadyExists(
            f"user {user_id} is already a member of org {org_id} "
            f"with role={existing['role']!r}"
        )

    ts = now_iso()
    try:
        execute_write(
            """
            INSERT INTO org_members (org_id, user_id, role, joined_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(org_id), int(user_id), role, ts),
        )
    except sqlite3.IntegrityError as exc:
        raise MemberAlreadyExists(
            f"user {user_id} already in org {org_id} (race)"
        ) from exc

    row = execute_query(
        "SELECT * FROM org_members WHERE org_id=? AND user_id=?",
        (int(org_id), int(user_id)),
        one=True,
    )
    return _row_to_member(row)


def get_member_role(org_id: int, user_id: int) -> str | None:
    """Return the role string ('owner'|'admin'|'editor'|'viewer') or None
    if the user is not a member of the org.
    """
    row = execute_query(
        "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
        (int(org_id), int(user_id)),
        one=True,
    )
    return row["role"] if row else None


def list_members(org_id: int) -> list[OrgMember]:
    rows = execute_query(
        "SELECT * FROM org_members WHERE org_id=? ORDER BY joined_at ASC",
        (int(org_id),),
    )
    return [_row_to_member(r) for r in rows]


def get_org_user_ids(org_id: int) -> list[int]:
    """Return all user_ids in an org. Used to widen queries from per-user
    to org-wide. Empty list if the org has no members or doesn't exist."""
    rows = execute_query(
        "SELECT user_id FROM org_members WHERE org_id=?",
        (int(org_id),),
    )
    return [int(r["user_id"]) for r in rows]


def remove_member(org_id: int, user_id: int) -> bool:
    n = execute_write(
        "DELETE FROM org_members WHERE org_id=? AND user_id=?",
        (int(org_id), int(user_id)),
    )
    return bool(n)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def create_api_key(
    org_id: int,
    name: str,
    *,
    scope: str = DEFAULT_SCOPE,
    expires_at: str | None = None,
) -> IssuedApiKey:
    """Issue a new API key.

    Returns IssuedApiKey containing the RAW key — this is the only
    moment the raw key is ever revealed. The DB stores only the SHA-256
    hash. Callers MUST surface the raw key to the operator immediately
    and discard it from memory.
    """
    if not name or not str(name).strip():
        raise ValueError("api key name is required")
    scope = _validate_scope(scope)
    # Ensure the org exists.
    get_org(org_id)

    raw = _generate_raw_key()
    key_hash = _hash_key(raw)
    ts = now_iso()

    # Collision avoidance: while sha256 collisions are practically
    # impossible, the unique index makes a re-roll cheap if it ever
    # happens. Loop up to 3 times before giving up.
    for _ in range(3):
        try:
            new_id = execute_write(
                """
                INSERT INTO api_keys (
                    org_id, name, key_hash, scope, status,
                    expires_at, created_at
                )
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (int(org_id), str(name).strip(), key_hash, scope, expires_at, ts),
            )
            break
        except sqlite3.IntegrityError:
            raw = _generate_raw_key()
            key_hash = _hash_key(raw)
    else:
        raise RuntimeError("failed to issue api key (hash collision retries exhausted)")

    row = execute_query(
        "SELECT * FROM api_keys WHERE id=?",
        (new_id,), one=True,
    )

    try:
        from observability import _emit
        _emit(
            "API_KEY_ISSUED",
            {
                "api_key_id": new_id, "org_id": int(org_id),
                "name": name, "scope": scope,
                "summary": f"API anahtarı oluşturuldu: {name} (kapsam: {scope})",
            },
            persist=True,
        )
    except Exception:
        pass

    return IssuedApiKey(metadata=_row_to_api_key(row), raw_key=raw)


def verify_api_key(raw_key: str) -> dict | None:
    """Look up an API key by its hash. Returns metadata dict or None.

    Updates last_used_at on hit. Returns None for:
        - unknown key
        - revoked key (status != 'active')
        - expired key

    The return shape is intentionally small — only what the auth gate
    needs: org_id, scope, key id.
    """
    if not raw_key or not isinstance(raw_key, str):
        return None
    if not raw_key.startswith(API_KEY_PREFIX):
        return None
    key_hash = _hash_key(raw_key.strip())

    row = execute_query(
        """
        SELECT id, org_id, scope, status, expires_at
        FROM api_keys WHERE key_hash=?
        """,
        (key_hash,),
        one=True,
    )
    if not row:
        return None
    if (row["status"] or "active") != "active":
        return None
    if row["expires_at"]:
        # ISO comparison is lexically correct for ISO-8601 strings.
        if row["expires_at"] <= now_iso():
            return None

    # Best-effort last_used_at update; silently swallow any failure so
    # auth never blocks on a write error.
    try:
        execute_write(
            "UPDATE api_keys SET last_used_at=? WHERE id=?",
            (now_iso(), int(row["id"])),
        )
    except Exception:
        pass

    return {
        "api_key_id": int(row["id"]),
        "org_id": int(row["org_id"]),
        "scope": row["scope"],
    }


def list_api_keys(org_id: int) -> list[ApiKey]:
    """List keys for an org. Hash is NEVER returned. Raw key was returned
    only at creation time."""
    rows = execute_query(
        """
        SELECT id, org_id, name, scope, status, last_used_at,
               expires_at, created_at
        FROM api_keys WHERE org_id=?
        ORDER BY id DESC
        """,
        (int(org_id),),
    )
    return [_row_to_api_key(r) for r in rows]


def revoke_api_key(api_key_id: int, *, org_id: int | None = None) -> bool:
    """Soft-delete: flip status='revoked'. Multi-tenant guarded if org_id given."""
    if org_id is not None:
        row = execute_query(
            "SELECT id FROM api_keys WHERE id=? AND org_id=?",
            (int(api_key_id), int(org_id)),
            one=True,
        )
        if not row:
            return False
    n = execute_write(
        "UPDATE api_keys SET status='revoked' WHERE id=?",
        (int(api_key_id),),
    )
    return bool(n)


# ---------------------------------------------------------------------------
# High-level helper for the request layer
# ---------------------------------------------------------------------------


def resolve_auth_from_request(
    *,
    api_key_header: str | None,
    user_id_fallback: int,
) -> dict:
    """Single entry point the FastAPI dependency uses.

    Returns:
        {
          "user_id":  resolved user id (always — falls back to caller's),
          "org_id":   None or int,
          "role":     None or role string (only when resolved via membership),
          "scope":    None or "read|write|admin" (only when via API key),
          "source":   "api_key" | "user_id",
          "api_key_id": int | None,
        }

    The contract: if api_key is valid → org_id is set and source='api_key'.
    Otherwise org_id is None (NOT auto-resolved) so the dependency can
    decide whether to engage org filtering. This preserves the test-mode
    legacy behavior the user explicitly asked us to keep.
    """
    if api_key_header:
        verified = verify_api_key(api_key_header)
        if verified:
            return {
                "user_id": int(user_id_fallback),
                "org_id": verified["org_id"],
                "role": None,
                "scope": verified["scope"],
                "source": "api_key",
                "api_key_id": verified["api_key_id"],
            }

    return {
        "user_id": int(user_id_fallback),
        "org_id": None,
        "role": None,
        "scope": None,
        "source": "user_id",
        "api_key_id": None,
    }
