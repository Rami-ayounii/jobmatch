"""FastAPI dependencies — Clerk JWT auth and tenant context."""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db

# ── JWKS cache ────────────────────────────────────────────────────────────────

_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600.0  # 1 hour


async def _get_jwks() -> dict[str, Any]:
    global _jwks_fetched_at, _jwks_cache
    now = time.monotonic()
    if now - _jwks_fetched_at < _JWKS_TTL and _jwks_cache:
        return _jwks_cache

    if not settings.clerk_publishable_key:
        # Dev mode: skip JWKS fetch; validate via clerk_jwt_public_key if set
        return {}

    # Clerk JWKS URL derived from publishable key domain
    # pk_live_xxx or pk_test_xxx → domain is embedded after stripping prefix + base64-decoding
    import base64

    raw = settings.clerk_publishable_key.split("_", 2)[-1].rstrip("$")
    try:
        domain = base64.b64decode(raw + "==").decode().rstrip("\x00")
    except Exception:
        domain = ""

    if not domain:
        return {}

    jwks_url = f"https://{domain}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
        return _jwks_cache


# ── Auth context ──────────────────────────────────────────────────────────────


class AuthContext:
    def __init__(self, tenant_id: uuid.UUID, user_id: uuid.UUID, clerk_user_id: str) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.clerk_user_id = clerk_user_id


async def _resolve_tenant_and_user(
    clerk_user_id: str, clerk_org_id: str, db: AsyncSession
) -> AuthContext:
    """Resolve Clerk IDs to internal tenant/user UUIDs. Bypasses RLS (service query)."""
    result = await db.execute(
        text("SELECT id FROM tenants WHERE clerk_org_id = :org_id"),
        {"org_id": clerk_org_id},
    )
    tenant_row = result.fetchone()
    if not tenant_row:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant not found")

    tenant_id: uuid.UUID = tenant_row[0]

    result = await db.execute(
        text(
            "SELECT id FROM users WHERE clerk_user_id = :uid AND tenant_id = :tid"
        ),
        {"uid": clerk_user_id, "tid": str(tenant_id)},
    )
    user_row = result.fetchone()
    if not user_row:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")

    return AuthContext(tenant_id=tenant_id, user_id=user_row[0], clerk_user_id=clerk_user_id)


async def get_current_user(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Validate Clerk JWT and return tenant + user context.

    Also sets app.tenant_id as a transaction-scoped Postgres variable so RLS
    policies activate for every subsequent query in this request's transaction.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not authorization.startswith("Bearer "):
        raise credentials_exc

    token = authorization.removeprefix("Bearer ")

    # Dev shortcut: if no Clerk keys configured, accept a raw tenant_id UUID header
    if not settings.clerk_secret_key and not settings.clerk_jwt_public_key:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Auth not configured — set CLERK_SECRET_KEY or CLERK_JWT_PUBLIC_KEY",
        )

    try:
        jwks = await _get_jwks()
        public_key: str | dict[str, Any]
        if jwks:
            public_key = jwks
        else:
            public_key = settings.clerk_jwt_public_key

        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except JWTError:
        raise credentials_exc

    clerk_user_id: str = payload.get("sub", "")
    # Clerk puts org_id in `org_id` claim (Session Token v2)
    clerk_org_id: str = payload.get("org_id", "")

    if not clerk_user_id or not clerk_org_id:
        raise credentials_exc

    ctx = await _resolve_tenant_and_user(clerk_user_id, clerk_org_id, db)

    # Activate RLS for this transaction
    await db.execute(
        text("SET LOCAL app.tenant_id = :tid"),
        {"tid": str(ctx.tenant_id)},
    )

    return ctx


async def get_tenant_id(ctx: AuthContext = Depends(get_current_user)) -> uuid.UUID:
    """Convenience dependency returning just the tenant UUID."""
    return ctx.tenant_id
