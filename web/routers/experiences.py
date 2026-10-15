"""경험 조각 저장 페이지 (DESIGN.md 5-8). 로그인한 본인 것만 보고 고친다.

문서(이력서/자소서)를 통째로 쓰는 게 아니라, 나중에 지원서 초안 생성 시
근거로 재사용할 경험을 조각(경력/프로젝트/에피소드) 단위로 저장한다.
기본 정보(이름·학력 등)는 LLM을 거치지 않고 폼 입력 그대로 저장한다.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.orm import Experience, User
from web.deps import get_current_user, get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/experiences", response_class=HTMLResponse)
def experiences_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    my_experiences = (
        db.query(Experience)
        .filter_by(user_id=current_user.id)
        .order_by(Experience.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "experiences.html",
        {"request": request, "current_user": current_user, "experiences": my_experiences},
    )


@router.post("/experiences/basic-info")
def update_basic_info(
    name: str = Form(""),
    education: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """이름·학력 등 기본 정보 — LLM 안 거치고 그대로 저장.

    폼에 없는 항목(scripts/import_profile.py로 넣은 병역·자격증 등)은 지우지 않고 두 칸만 바꾼다.
    새 dict로 바꿔 넣어야 SQLAlchemy가 JSON 컬럼 변경을 감지한다.
    """
    current_user.basic_info = {**(current_user.basic_info or {}), "이름": name, "학력": education}
    db.commit()
    return RedirectResponse("/experiences", status_code=303)


@router.post("/experiences/add")
def add_experience(
    kind: str = Form(...),
    title: str = Form(""),
    period: str = Form(""),
    content: str = Form(...),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    db.add(Experience(
        user_id=current_user.id,
        kind=kind,
        title=title or None,
        period=period or None,
        content=content,
        tags=tag_list or None,
    ))
    db.commit()
    return RedirectResponse("/experiences", status_code=303)


@router.post("/experiences/{experience_id}/delete")
def delete_experience(
    experience_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """본인 소유 경험만 지울 수 있다 — user_id로 한 번 더 걸러서 남의 것 삭제를 막는다."""
    db.query(Experience).filter_by(id=experience_id, user_id=current_user.id).delete()
    db.commit()
    return RedirectResponse("/experiences", status_code=303)
