"""Auth middleware and FastAPI dependency.

Protected path prefixes — everything under these requires a valid session:
  /api/jobs, /api/upload, /api/inputs/ (write ops), /api/policy/,
  /api/rehydrate, /api/gateway/, /api/server-sources/,
  /api/llm-export/, /api/import, /api/user/, /api/auth/logout

Always public:
  /api/health, /api/config, /api/models, /api/auth/login,
  /api/inputs  (read-only list), /api/demo/  (read-only demo info)
  GET /  (the SPA shell — JS checks auth itself)

STRICT_AUTH_MODE=true extends protection to everything except
  /api/health and /api/auth/login.

TODO (production): add rate limiting on /api/auth/login; add IP allowlist.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.sessions import COOKIE_NAME, get_username
from app.config import settings

# Paths that always require auth (prefix match)
_PROTECTED: tuple[str, ...] = (
    "/api/jobs",
    "/api/upload",
    "/api/inputs/",       # individual file ops (list is public)
    "/api/policy/",
    "/api/rehydrate",
    "/api/gateway/",
    "/api/server-sources/",
    "/api/llm-export/",
    "/api/import",
    "/api/user/",
    "/api/auth/logout",
)

# Paths that are always public regardless of STRICT_AUTH_MODE
_ALWAYS_PUBLIC: tuple[str, ...] = (
    "/api/health",
    "/api/auth/login",
)


def _is_protected(path: str) -> bool:
    if not settings.auth_enabled:
        return False
    if any(path.startswith(p) for p in _ALWAYS_PUBLIC):
        return False
    if settings.strict_auth_mode:
        return path.startswith("/api/")
    return any(path.startswith(p) for p in _PROTECTED)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_protected(request.url.path):
            token = request.cookies.get(COOKIE_NAME)
            if not token or not get_username(token):
                return JSONResponse(
                    {"detail": "Authentication required"},
                    status_code=401,
                )
        return await call_next(request)


# FastAPI dependency for routes that need the current username
def current_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    username = get_username(token) if token else None
    if not username:
        raise HTTPException(401, "Authentication required")
    return username


def current_user_optional(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    return get_username(token) if token else None
