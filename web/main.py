"""FastAPI 앱 진입점.

docker-compose의 web-api 서비스가 실행한다:
  uvicorn web.main:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from shared.config import get_settings
from web.deps import NotAuthenticated
from web.routers import auth, dashboard, draft, experiences, profile

app = FastAPI(title="MatchWise")
app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret_key)


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login")


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(profile.router)
app.include_router(draft.router)
app.include_router(experiences.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return RedirectResponse("/dashboard")
