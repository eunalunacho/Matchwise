"""비서 메시지·웹 화면이 함께 쓰는 목록 조회. 목록은 저장하지 않고 필요할 때 조건으로 만든다.

  작성 중 / 미작성   — status='will_apply'를 초안(resume_drafts) 유무로 나눈 것 (DESIGN.md 5-3)
  마감 리마인드      — will_apply 중 마감 D-7·D-2, 마감일이 없으면 등록 후 7일째 한 번 (5-4)
  놓친 추천          — 카드로 보낸 지 MISSED_AFTER_DAYS일이 지났는데 아무 반응(지원 상태)이 없는 공고 (5-3)
  최근 변화          — application_events의 기록 (5-3)

날짜 계산(D-N, 7일째)은 한국 시간 기준이다. SQLite는 시간대 없이 돌려주므로 UTC로 보고 읽는다.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import exists, or_

from db.orm import Application, ApplicationEvent, Job, JobScore, ResumeDraft

KST = timezone(timedelta(hours=9))
REMIND_DAYS_LEFT = (7, 2)  # 마감 D-7, D-2에 리마인드
NO_DEADLINE_REMIND_DAY = 7  # 마감일을 모르는 공고는 등록 후 7일째에 한 번
MISSED_AFTER_DAYS = 3  # 카드로 보낸 뒤 이 일수가 지나도 반응이 없으면 "놓친 추천"
MISSED_MIN_FIT_SCORE = 50


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def kst_date(dt: datetime) -> date:
    """datetime(UTC 또는 시간대 없음=UTC) → 한국 날짜."""
    return _as_utc(dt).astimezone(KST).date()


def _will_apply_query(session, user_id: int):
    return (
        session.query(Application)
        .join(Job)
        .filter(Application.user_id == user_id, Application.status == "will_apply")
        .order_by(Job.deadline.asc().nullslast(), Application.will_apply_at.asc())
    )


def writing_and_unwritten(session, user_id: int) -> tuple[list[Application], list[Application]]:
    """지원 예정 공고를 (작성 중, 미작성)으로 나눈다. 마감 가까운 순, 마감일 없는 공고는 뒤로."""
    has_draft = exists().where(
        ResumeDraft.job_id == Application.job_id, ResumeDraft.user_id == Application.user_id,
    )
    base = _will_apply_query(session, user_id)
    return base.filter(has_draft).all(), base.filter(~has_draft).all()


def deadline_reminders(session, user_id: int, now: datetime) -> list[tuple[Application, str]]:
    """오늘 리마인드할 지원 예정 공고와 안내 문구. 마감이 지난 공고는 넣지 않는다."""
    today = kst_date(now)
    reminders = []
    for app in _will_apply_query(session, user_id).all():
        deadline = app.job.deadline
        if deadline is not None:
            days_left = (kst_date(deadline) - today).days
            if days_left in REMIND_DAYS_LEFT:
                reminders.append((app, f"마감 D-{days_left}"))
        elif app.will_apply_at is not None:
            if (today - kst_date(app.will_apply_at)).days == NO_DEADLINE_REMIND_DAY:
                reminders.append((app, f"등록 {NO_DEADLINE_REMIND_DAY}일째 (마감일 미확인)"))
    return reminders


def missed_recommendations(session, user_id: int, now: datetime, limit: int = 3) -> list[JobScore]:
    """카드로 받은 지 MISSED_AFTER_DAYS일이 지났는데 등록·패스 어느 쪽도 안 한, 마감 전 공고. 점수 높은 순."""
    has_application = exists().where(
        Application.job_id == JobScore.job_id, Application.user_id == JobScore.user_id,
    )
    return (
        session.query(JobScore)
        .join(Job)
        .filter(
            JobScore.user_id == user_id,
            JobScore.notified_as == "card",
            JobScore.notified_at <= now - timedelta(days=MISSED_AFTER_DAYS),
            JobScore.fit_score >= MISSED_MIN_FIT_SCORE,
            ~has_application,
            or_(Job.deadline.is_(None), Job.deadline > now),
        )
        .order_by(JobScore.fit_score.desc())
        .limit(limit)
        .all()
    )


def recent_changes(session, user_id: int, since: datetime) -> list[ApplicationEvent]:
    """since 이후 지원 상태가 바뀐 기록. 오래된 순. 패스는 현황 요약에 의미가 없어 뺀다."""
    return (
        session.query(ApplicationEvent)
        .filter(
            ApplicationEvent.user_id == user_id,
            ApplicationEvent.created_at >= since,
            ApplicationEvent.to_status != "dismissed",
        )
        .order_by(ApplicationEvent.created_at.asc())
        .all()
    )
