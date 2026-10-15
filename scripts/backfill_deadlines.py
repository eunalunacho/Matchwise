"""마감일 없는 기존 공고에 본문(jd_text, OCR 포함)에서 마감일을 뽑아 채우는 1회성 CLI.

2026-10-15 전에 추출한 공고(주로 인디스워크)는 요약에 deadline 항목이 없었다. 이후 공고는 extract_job이 함께 채운다.
요약(summarize_jd)을 다시 부르되 deadline만 쓰고 나머지 요약은 건드리지 않는다. 하루 Gemini 한도를 지킨다.

Usage:
    python -m scripts.backfill_deadlines [--limit N]
"""
import argparse
import time

from analysis.scorer import summarize_jd
from db.orm import Job
from scheduler.jobs import _GEMINI_SLEEP_SEC, _gemini_budget_exhausted, _record_gemini_call
from shared.db import get_session


def backfill_deadlines(session, limit: int | None = None, sleep_sec: float = _GEMINI_SLEEP_SEC) -> dict:
    """Returns: {"checked", "filled", "failed", "left"}. 공고마다 커밋한다."""
    targets = (
        session.query(Job)
        .filter(Job.deadline.is_(None), Job.extract_status == "ok", Job.jd_text.isnot(None), Job.jd_text != "")
        .order_by(Job.id)
        .all()
    )
    if limit is not None:
        targets = targets[:limit]
    stats = {"checked": 0, "filled": 0, "failed": 0, "left": 0}
    for index, job in enumerate(targets):
        if _gemini_budget_exhausted(session):
            stats["left"] = len(targets) - index
            break
        stats["checked"] += 1
        try:
            _record_gemini_call(session)
            deadline = summarize_jd(job.jd_text)["deadline"]
        except Exception:
            stats["failed"] += 1
        else:
            if deadline is not None:
                job.deadline = deadline
                stats["filled"] += 1
        session.commit()
        time.sleep(sleep_sec)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="마감일 없는 공고에 본문의 마감일 채우기")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    session = get_session()
    try:
        result = backfill_deadlines(session, args.limit)
        print(f"확인 {result['checked']}건 · 마감일 채움 {result['filled']}건 · 실패 {result['failed']}건"
              + (f" · 한도로 남음 {result['left']}건" if result["left"] else ""))
    finally:
        session.close()
