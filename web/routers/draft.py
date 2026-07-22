"""이력서 초안 조회 라우터."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import ApplicationQuestion, Job, ResumeDraft, User
from web.deps import get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/draft/{job_id}", response_class=HTMLResponse)
def draft_page(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter_by(id=job_id).first()
    questions = (
        db.query(ApplicationQuestion)
        .filter_by(job_id=job_id)
        .order_by(ApplicationQuestion.order_num)
        .all()
    )

    drafts_by_q = {}
    for q in questions:
        user_drafts = (
            db.query(ResumeDraft, User)
            .join(User, User.id == ResumeDraft.user_id)
            .filter(ResumeDraft.question_id == q.id, ResumeDraft.job_id == job_id)
            .all()
        )
        drafts_by_q[q.id] = {"question": q.question, "drafts": user_drafts}

    return templates.TemplateResponse(
        "draft.html",
        {"request": request, "job": job, "drafts_by_q": drafts_by_q},
    )
