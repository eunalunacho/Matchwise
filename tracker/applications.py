"""지원 상태 변경 — applications의 현재 상태를 고치고 application_events에 기록을 한 줄 남긴다.

상태를 바꾸는 곳(Discord 버튼·웹)은 모두 change_status를 거친다. 그래야 기록이 빠지지 않는다.
"""
from datetime import datetime, timezone

from db.orm import Application, ApplicationEvent

STATUS_LABELS = {
    "saved": "관심",
    "will_apply": "지원 예정",
    "submitted": "제출 완료",
    "assignment": "과제",
    "interview": "면접",
    "passed": "합격",
    "rejected": "불합격",
    "dismissed": "패스",
}
STATUSES = tuple(STATUS_LABELS)
SOURCES = ("discord", "web", "system")

# 상태별로 함께 남기는 시각 칸 — 리마인드("등록 후 7일째")와 목록 정렬에 쓴다.
_TIMESTAMP_FIELDS = {"will_apply": "will_apply_at", "submitted": "submitted_at"}


def change_status(
    session,
    user_id: int,
    job_id: int,
    to_status: str,
    source: str,
    now: datetime | None = None,
) -> tuple[Application, bool]:
    """(사용자, 공고)의 지원 상태를 to_status로 바꾼다. 줄이 없으면 만든다. 커밋은 호출자가 한다.

    이미 같은 상태면 아무것도 바꾸지 않고 기록도 남기지 않는다.

    Returns:
        (Application, 실제로 바뀌었는지).

    Raises:
        ValueError: 알 수 없는 상태·출처.
    """
    if to_status not in STATUSES:
        raise ValueError(f"알 수 없는 상태: {to_status!r}")
    if source not in SOURCES:
        raise ValueError(f"알 수 없는 출처: {source!r}")

    app = session.query(Application).filter_by(job_id=job_id, user_id=user_id).first()
    if app is not None and app.status == to_status:
        return app, False

    now = now or datetime.now(timezone.utc)
    from_status = app.status if app is not None else None
    if app is None:
        app = Application(job_id=job_id, user_id=user_id, created_at=now)
        session.add(app)
    app.status = to_status
    if to_status in _TIMESTAMP_FIELDS:
        setattr(app, _TIMESTAMP_FIELDS[to_status], now)
    session.flush()

    session.add(ApplicationEvent(
        application_id=app.id,
        user_id=user_id,
        from_status=from_status,
        to_status=to_status,
        source=source,
        created_at=now,
    ))
    session.flush()
    return app, True
