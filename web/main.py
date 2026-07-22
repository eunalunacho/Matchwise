"""FastAPI 앱 진입점.

docker-compose의 web-api 서비스가 실행한다:
  uvicorn web.main:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from web.routers import dashboard, draft, profile

app = FastAPI(title="MatchWise")

app.include_router(dashboard.router)
app.include_router(profile.router)
app.include_router(draft.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return RedirectResponse("/dashboard")
