"""이력서 초안 조회 라우터. 로그인한 본인 초안만 보여준다."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import ApplicationQuestion, Job, ResumeDraft, User
from web.deps import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/draft/{job_id}", response_class=HTMLResponse)
def draft_page(
    request: Request,
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter_by(id=job_id).first()
    questions = (
        db.query(ApplicationQuestion)
        .filter_by(job_id=job_id)
        .order_by(ApplicationQuestion.order_num)
        .all()
    )

    drafts_by_q = {}
    for q in questions:
        draft = (
            db.query(ResumeDraft)
            .filter_by(question_id=q.id, job_id=job_id, user_id=current_user.id)
            .first()
        )
        drafts_by_q[q.id] = {"question": q.question, "draft": draft}

    return templates.TemplateResponse(
        "draft.html",
        {
            "request": request, "job": job, "drafts_by_q": drafts_by_q,
            "current_user": current_user,
        },
    )
