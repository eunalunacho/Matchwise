"""공고 대시보드 + 상세 + 지원 질문 등록 라우터."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import ApplicationQuestion, Job, JobScore, User
from web.deps import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    source: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Job, JobScore)
        .outerjoin(
            JobScore,
            (Job.id == JobScore.job_id) & (JobScore.user_id == current_user.id),
        )
    )
    if source:
        query = query.filter(Job.source == source)

    rows = query.order_by(JobScore.fit_score.desc().nullslast()).all()
    sources = [s for (s,) in db.query(Job.source).distinct().all()]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request, "jobs": rows, "sources": sources,
            "active_source": source, "current_user": current_user,
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter_by(id=job_id).first()
    score = (
        db.query(JobScore)
        .filter_by(job_id=job_id, user_id=current_user.id)
        .first()
    )
    questions = (
        db.query(ApplicationQuestion)
        .filter_by(job_id=job_id)
        .order_by(ApplicationQuestion.order_num)
        .all()
    )
    summary = {
        "tasks": job.jd_tasks,
        "stack": job.jd_stack,
        "employment_type": job.jd_employment_type,
        "qualifications": job.jd_qualifications,
        "preferred": job.jd_preferred,
    } if job else {}
    return templates.TemplateResponse(
        "jobs_detail.html",
        {
            "request": request,
            "job": job,
            "summary": summary,
            "score": score,
            "questions": questions,
            "current_user": current_user,
        },
    )


@router.post("/jobs/{job_id}/questions")
def save_questions(
    job_id: int,
    questions: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """textarea 한 줄 = 질문 하나. 저장 시 기존 질문을 덮어쓴다."""
    db.query(ApplicationQuestion).filter_by(job_id=job_id).delete()
    for i, line in enumerate(questions.strip().splitlines()):
        line = line.strip()
        if line:
            db.add(ApplicationQuestion(job_id=job_id, question=line, order_num=i))
    db.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
