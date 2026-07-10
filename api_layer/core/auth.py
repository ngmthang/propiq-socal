"""
    PropIQ - API Key Authentication
    Simple header-based API key check. Swap 'is_valid_key' for a DB/Redis-backed
    Lookup (with per-key rate limits, scopes, etc.) when moving past MVP.

    @author Minh Thang Nguyen
    @version: July 9, 2026
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from .config import settings

_api_key_header = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)

# Paths that don't require an API key (health checks, docs).
PUBLIC_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}

def is_valid_key(key: str | None) -> bool:
    return key is not None and key in settings.api_keys_set

async def require_api_key(
        request: Request,
        api_key: str | None = Depends(_api_key_header),
) -> str:
    """
    FastAPI dependency enforcing the 'X-API-Key' header on protected routes.
    Raise 401 if missing/invalid.
    """
    if not is_valid_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key. Provide it via the "
                   f"'{settings.API_KEY_HEADER}' header.",
        )
    return api_key

class APIKeyMiddleware:
    """
    ASGI middleware that gate-keeps every request except PUBLIC_PATHS.
    Used in addition to (not instead of) the 'require_api_key' dependency,
    so that even routers that forget to add the dependency stay protected.
    """

    def __init__(self, app):
        self.app = app

    async def __calL__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw_key = headers.get(settings.API_KEY_HEADER.lower().encode(), b"").decode()

        if not is_valid_key(raw_key):
            response_body = (
                b'{"detail":"Missing or invalid API key. Provide it via the'
                + settings.API_KEY_HEADER.encode()
                + b' header."}'
            )
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": response_body})
            return

        await self.app(scope, receive, send)