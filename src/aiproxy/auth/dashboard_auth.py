"""Dashboard auth: master-key login + signed session cookie.

Design:
- Login endpoint accepts POST {"master_key": "..."} and compares against
  settings.proxy_master_key using hmac.compare_digest (constant-time).
- On success, issues a JWT (HS256) signed with settings.session_secret.
- The JWT payload contains only `iat` and `exp`. It is stored in an
  httpOnly, SameSite=Strict cookie named "aiproxy_session".
- Protected routes use a FastAPI dependency that reads the cookie,
  verifies the signature + expiry, and returns (or raises 401).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

COOKIE_NAME = "aiproxy_session"
ALGORITHM = "HS256"


@dataclass
class VerifyResult:
    valid: bool
    expired: bool = False
    payload: dict | None = None


def issue_session_token(*, secret: str, ttl_seconds: int) -> str:
    """Create a signed JWT with iat + exp claims."""
    now = int(time.time())
    payload = {"iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_session_token(token: str | None, *, secret: str) -> VerifyResult:
    """Verify a token's signature and expiry. Never raises."""
    if not token:
        return VerifyResult(valid=False)
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return VerifyResult(valid=False, expired=True)
    except jwt.InvalidTokenError:
        return VerifyResult(valid=False)
    return VerifyResult(valid=True, payload=payload)


def require_dashboard_session(session_secret: str):
    """FastAPI dependency factory: returns a dependency that validates the
    session cookie. Raises HTTPException(401) on missing/invalid token.
    """
    async def _dep(request: Request) -> dict:
        token = request.cookies.get(COOKIE_NAME)
        result = verify_session_token(token, secret=session_secret)
        if not result.valid:
            raise HTTPException(status_code=401, detail="unauthenticated")
        return result.payload or {}
    return _dep


def make_session_cookie_response(
    token: str,
    *,
    secure: bool,
    max_age_seconds: int,
) -> JSONResponse:
    """Return a JSONResponse with the session cookie set."""
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        secure=secure,
        max_age=max_age_seconds,
        path="/",
    )
    return resp
