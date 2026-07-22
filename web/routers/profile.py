"""유저 프로필(이력서·경험 텍스트) 관리 라우터."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import User
from web.deps import get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).filter_by(is_active=True).all()
    return templates.TemplateResponse(
        "profile.html", {"request": request, "users": users}
    )


@router.post("/profile/{discord_id}/resume")
def update_resume(
    discord_id: str,
    resume_text: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(discord_id=discord_id).first()
    if user:
        user.resume_text = resume_text
        db.commit()
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/{discord_id}/experience")
def update_experience(
    discord_id: str,
    experience_text: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(discord_id=discord_id).first()
    if user:
        user.experience_text = experience_text
        db.commit()
    return RedirectResponse("/profile", status_code=303)
