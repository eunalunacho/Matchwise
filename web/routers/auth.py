"""구글 로그인 + 디스코드 계정 연결 라우터.

흐름:
  1. /login → 구글 로그인 → /auth/google/callback (없는 계정이면 새로 생성, 세션에 user_id 저장)
  2. /connect/discord → 디스코드 인증 → /auth/discord/callback (현재 세션 유저에 discord_id 연결)
  3. /logout → 세션 삭제
"""
import logging
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import User
from shared.config import get_settings
from web.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

_settings = get_settings()

oauth = OAuth()
oauth.register(
    name="google",
    client_id=_settings.google.client_id,
    client_secret=(
        _settings.google.client_secret.get_secret_value()
        if _settings.google.client_secret else None
    ),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
oauth.register(
    name="discord",
    client_id=_settings.discord.client_id,
    client_secret=(
        _settings.discord.client_secret.get_secret_value()
        if _settings.discord.client_secret else None
    ),
    access_token_url="https://discord.com/api/oauth2/token",
    authorize_url="https://discord.com/api/oauth2/authorize",
    api_base_url="https://discord.com/api/",
    client_kwargs={"scope": "identify"},
)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/")
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/auth/google/login")
async def google_login(request: Request):
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        logger.warning("[auth] 구글 콜백에 userinfo 없음")
        return RedirectResponse("/login")

    google_sub = userinfo["sub"]
    email = userinfo.get("email")
    name = userinfo.get("name") or email or "user"

    user = db.query(User).filter_by(google_sub=google_sub).first()
    if not user:
        user = User(google_sub=google_sub, email=email, username=name)
        db.add(user)
        db.commit()
        logger.info("[auth] 신규 유저 생성 google_sub=%s", google_sub)

    request.session["user_id"] = user.id
    return RedirectResponse("/")


@router.get("/connect/discord")
async def discord_connect(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login")
    redirect_uri = request.url_for("discord_callback")
    return await oauth.discord.authorize_redirect(request, redirect_uri)


@router.get("/auth/discord/callback", name="discord_callback")
async def discord_callback(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")

    token = await oauth.discord.authorize_access_token(request)
    resp = await oauth.discord.get("users/@me", token=token)
    discord_user = resp.json()

    user = db.query(User).filter_by(id=user_id).first()
    if user:
        user.discord_id = discord_user["id"]
        db.commit()
        logger.info("[auth] discord 연결 완료 user_id=%s discord_id=%s", user.id, user.discord_id)

    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")
