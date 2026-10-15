"""scripts/backfill_deadlines.py 테스트 — summarize_jd는 mock."""
from datetime import datetime, timezone
from unittest.mock import patch

from db.orm import Job
from scripts.backfill_deadlines import backfill_deadlines

_FOUND = datetime(2026, 10, 28, 14, 59, tzinfo=timezone.utc)


def test_backfill_fills_only_jobs_without_deadline_and_keeps_other_fields(session):
    kept = datetime(2026, 11, 1, 14, 59, tzinfo=timezone.utc)
    session.add_all([
        Job(source="inthiswork", url="https://a/1", jd_text="~10/28", extract_status="ok", jd_tasks=["업무"]),
        Job(source="inthiswork", url="https://a/2", jd_text="상시채용", extract_status="ok"),
        Job(source="jobkorea", url="https://a/3", jd_text="본문", extract_status="ok", deadline=kept),
        Job(source="inthiswork", url="https://a/4", jd_text="", extract_status="ok"),
    ])
    session.commit()

    def fake(jd_text, hints=None):
        return {"tasks": ["바뀌면 안 됨"], "deadline": _FOUND if "10/28" in jd_text else None}

    with patch("scripts.backfill_deadlines.summarize_jd", side_effect=fake) as mock_summary:
        stats = backfill_deadlines(session, sleep_sec=0)

    assert mock_summary.call_count == 2  # 마감일 있는 공고·본문 없는 공고는 부르지 않음
    assert stats == {"checked": 2, "filled": 1, "failed": 0, "left": 0}
    jobs = {j.url: j for j in session.query(Job).all()}
    assert jobs["https://a/1"].deadline == _FOUND
    assert jobs["https://a/1"].jd_tasks == ["업무"]
    assert jobs["https://a/2"].deadline is None
    assert jobs["https://a/3"].deadline == kept
