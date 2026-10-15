"""공고 대시보드 + 상세 + 지원 상태 변경 + 재채점 요청 + 지원 질문 등록 라우터."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from analysis.scorer import is_stale_score
from db.orm import Application, ApplicationQuestion, Job, JobScore, User
from tracker.applications import STATUS_LABELS, STATUSES, change_status
from web.deps import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["status_labels"] = STATUS_LABELS


def _stale_unrequested(db: Session, user: User) -> list[JobScore]:
    """아직 재채점을 요청하지 않은 이 사용자의 구 점수."""
    rows = db.query(JobScore).filter(
        JobScore.user_id == user.id, JobScore.rescore_requested_at.is_(None),
    ).all()
    return [row for row in rows if is_stale_score(row, user.profile_version)]


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    source: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Job, JobScore, Application)
        .outerjoin(
            JobScore,
            (Job.id == JobScore.job_id) & (JobScore.user_id == current_user.id),
        )
        .outerjoin(
            Application,
            (Job.id == Application.job_id) & (Application.user_id == current_user.id),
        )
    )
    if source:
        query = query.filter(Job.source == source)

    rows = [
        (job, score, app, score is not None and is_stale_score(score, current_user.profile_version))
        for job, score, app in query.order_by(JobScore.fit_score.desc().nullslast()).all()
    ]
    sources = [s for (s,) in db.query(Job.source).distinct().all()]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request, "jobs": rows, "sources": sources,
            "active_source": source, "current_user": current_user,
            "stale_count": len(_stale_unrequested(db, current_user)),
        },
    )


@router.post("/scores/rescore-stale")
def request_rescore_stale(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """내 구 점수 전체에 재채점을 요청한다. 실제 채점은 다음 analyze_job(05시)이 하루 한도 안에서 한다."""
    now = datetime.now(timezone.utc)
    for row in _stale_unrequested(db, current_user):
        row.rescore_requested_at = now
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/jobs/{job_id}/rescore")
def request_rescore(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """공고 하나의 재채점을 요청한다(구 점수가 아니어도 가능)."""
    row = db.query(JobScore).filter_by(job_id=job_id, user_id=current_user.id).first()
    if row is not None and row.rescore_requested_at is None:
        row.rescore_requested_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/status")
def update_status(
    job_id: int,
    status: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """지원 상태 변경([제출 완료] 등). Discord 버튼과 같은 change_status를 거쳐 기록이 남는다."""
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="알 수 없는 상태")
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="공고 없음")
    change_status(db, current_user.id, job_id, status, source="web")
    db.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


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
    application = db.query(Application).filter_by(job_id=job_id, user_id=current_user.id).first()
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
            "stale": score is not None and is_stale_score(score, current_user.profile_version),
            "application": application,
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
