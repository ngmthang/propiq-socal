"""
    PropIQ - Authentication
    Real per-user auth via JWT bearer tokens (see routers/auth.py for
    register/login). The old shared API-key check is kept as a fallback for
    service-to-service callers, but JWT is the primary gate now.

    @author Minh Thang Nguyen
    @version July 24, 2026
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from data_layer.models.database import User, UserRole

from .config import settings
from .db import get_db
from .security import decode_access_token

_api_key_header = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)
_bearer = HTTPBearer(auto_error=False)

# Paths that don't require auth at all (health checks, docs, and the two
# auth endpoints a not-yet-logged-in user needs to reach).
PUBLIC_PATHS = {
    "/", "/health", "/docs", "/redoc", "/openapi.json",
    "/api/auth/register", "/api/auth/login",
}


def is_valid_key(key: str | None) -> bool:
    return key is not None and key in settings.api_keys_set


async def get_current_user(
        request: Request,
        db: Session = Depends(get_db),
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        api_key: str | None = Depends(_api_key_header),
) -> User:
    """
    FastAPI dependency enforcing real per-user auth on protected routes.
    Accepts either an 'Authorization: Bearer <jwt>' header (the normal path
    for the frontend) or the legacy 'X-API-Key' header, mapped to a
    synthetic system user, for any existing service-to-service callers.
    """
    if credentials is not None:
        user_id = decode_access_token(credentials.credentials)
        if user_id is not None:
            user = db.query(User).filter(User.id == user_id).first()
            if user is not None and user.is_active:
                return user

    if is_valid_key(api_key):
        # Legacy path: API-key callers act as the first admin user, if one
        # exists, so existing service-to-service integrations keep working
        # without needing a real account. Not intended for end users.
        # Must be an ACTIVE admin: ingest scripts create disabled system
        # bot accounts, and those must never become the identity behind
        # API-key calls.
        system_user = (
            db.query(User)
            .filter(User.role == UserRole.ADMIN, User.is_active.is_(True))
            .first()
        )
        if system_user is not None:
            return system_user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Log in via /api/auth/login and pass the "
               "token as 'Authorization: Bearer <token>'.",
    )

async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency for admin-only routes: authenticated AND role == ADMIN."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


# Kept for any code that still imports it directly; new routers should use
# get_current_user instead.
async def require_api_key(
        request: Request,
        api_key: str | None = Depends(_api_key_header),
) -> str:
    if not is_valid_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key. Provide it via the "
                   f"'{settings.API_KEY_HEADER}' header.",
        )
    return api_key


class JWTAuthMiddleware:
    """
    ASGI middleware that gate-keeps every request except PUBLIC_PATHS.
    Used in addition to (not instead of) the 'get_current_user' dependency,
    so that even routers that forget to add the dependency stay protected.
    Accepts either a valid JWT bearer token or the legacy API key.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode()
        raw_key = headers.get(settings.API_KEY_HEADER.lower().encode(), b"").decode()

        authenticated = False
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            authenticated = decode_access_token(token) is not None
        if not authenticated:
            authenticated = is_valid_key(raw_key)

        if not authenticated:
            response_body = (
                b'{"detail":"Not authenticated. Log in via /api/auth/login '
                b'and pass the token as \'Authorization: Bearer <token>\'."}'
            )
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": response_body})
            return

        await self.app(scope, receive, send)