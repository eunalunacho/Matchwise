"""유저 프로필(이력서·경험 텍스트) 관리 라우터. 로그인한 본인 것만 보고 고친다."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import User
from web.deps import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "profile.html", {"request": request, "current_user": current_user}
    )


@router.post("/profile/resume")
def update_resume(
    resume_text: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.resume_text = resume_text
    db.commit()
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/experience")
def update_experience(
    experience_text: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.experience_text = experience_text
    db.commit()
    return RedirectResponse("/profile", status_code=303)
