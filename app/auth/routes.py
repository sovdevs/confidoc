"""Auth API endpoints — login, logout, me."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import JSONResponse

from app.auth.middleware import current_user_optional
from app.auth.sessions import COOKIE_NAME, create_session, destroy_session, get_username
from app.auth.users import verify_password
from app.config import settings

router = APIRouter()

_COOKIE_KWARGS = dict(
    httponly=True,
    samesite="lax",
    secure=False,   # set True in production behind HTTPS
    max_age=settings.session_ttl_hours * 3600,
)


@router.post("/api/auth/login")
def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    if not verify_password(username, password):
        return JSONResponse({"ok": False, "detail": "Invalid username or password"}, status_code=401)
    token = create_session(username)
    response.set_cookie(COOKIE_NAME, token, **_COOKIE_KWARGS)
    return {"ok": True, "username": username}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        destroy_session(token)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    username = get_username(token) if token else None
    return {"authenticated": bool(username), "username": username}
