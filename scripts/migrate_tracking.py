"""2026-10-15 지원 관리 구조 변경을 기존 DB에 반영하는 1회성 마이그레이션.

SQLite 전용. create_all(db/init_db.py)은 새 테이블(application_events)만 만들고 기존 테이블에 컬럼을 추가하지 못해 여기서 보강한다.
단계마다 _migrations 테이블에 이름을 남겨, 다시 실행해도 같은 단계를 두 번 하지 않는다(특히 마감일 시간대 보정).

  1. 컬럼 추가 — users(profile_version, match_channel_id), job_scores(버전·재채점 요청·전송 기록)
  2. 기존 점수 = 프로필 1 · 채점 규칙 1로 매긴 것(구 점수), 50점 이상은 이미 보낸 것으로 기록
     (2026-10-15 알림과 같은 순서로 상위 3건 card, 나머지 thread)
  3. job_scores.dismissed(패스) → applications.status='dismissed' 로 옮기고 컬럼 삭제
  4. 마감일 시간대 보정 — 크롤러가 KST로 만든 마감일이 SQLite에 KST 벽시계 시각으로 저장돼 있어 UTC로 바꾼다(-9시간)
  5. 매칭 채널 — --match-channel-id(없으면 .env의 옛 DISCORD_MATCH_CHANNEL_ID)를 그 유저의 users.match_channel_id로

Usage:
    python -m scripts.migrate_tracking [--user-id 1] [--match-channel-id ID]
"""
import argparse
from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from db.orm import Base

_NEW_COLUMNS = {
    "users": {"profile_version": "INTEGER NOT NULL DEFAULT 1", "match_channel_id": "VARCHAR(64)"},
    "job_scores": {
        "profile_version": "INTEGER", "scorer_version": "INTEGER", "rescore_requested_at": "DATETIME",
        "notified_at": "DATETIME", "notified_as": "VARCHAR(16)",
    },
}
_CARD_COUNT = 3
_MIN_FIT_SCORE = 50


def _done(conn, name: str) -> bool:
    return conn.execute(text("SELECT 1 FROM _migrations WHERE name = :n"), {"n": name}).first() is not None


def _mark(conn, name: str) -> None:
    conn.execute(text("INSERT INTO _migrations (name, applied_at) VALUES (:n, :t)"),
                 {"n": name, "t": datetime.now(timezone.utc).isoformat()})


def migrate(engine: Engine, user_id: int = 1, match_channel_id: str | None = None) -> list[str]:
    """실제로 한 단계 이름 목록을 반환한다."""
    applied = []
    Base.metadata.create_all(engine)  # application_events 등 없는 테이블
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS _migrations (name VARCHAR(64) PRIMARY KEY, applied_at VARCHAR(40))"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_applications_user_status ON applications (user_id, status)"))

        for table, columns in _NEW_COLUMNS.items():
            existing = {c["name"] for c in inspect(conn).get_columns(table)}
            for name, col_type in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))
                    applied.append(f"add {table}.{name}")

        if not _done(conn, "score_versions_and_sent"):
            conn.execute(text("UPDATE job_scores SET profile_version = 1 WHERE profile_version IS NULL"))
            conn.execute(text("UPDATE job_scores SET scorer_version = 1 WHERE scorer_version IS NULL"))
            # 2026-10-15 알림(bot/notifications.py::_unsent_scores)과 같은 순서 — 점수 10점 구간, 마감, 점수
            rows = conn.execute(text(
                "SELECT s.id, s.user_id, s.scored_at FROM job_scores s JOIN jobs j ON j.id = s.job_id "
                "WHERE s.fit_score >= :min AND s.notified_at IS NULL "
                "ORDER BY s.user_id, s.fit_score / 10 DESC, j.deadline IS NULL, j.deadline, s.fit_score DESC"
            ), {"min": _MIN_FIT_SCORE}).all()
            rank: dict[int, int] = {}
            for score_id, owner, scored_at in rows:
                rank[owner] = rank.get(owner, 0) + 1
                conn.execute(text("UPDATE job_scores SET notified_at = :t, notified_as = :k WHERE id = :i"), {
                    "t": scored_at, "k": "card" if rank[owner] <= _CARD_COUNT else "thread", "i": score_id,
                })
            _mark(conn, "score_versions_and_sent")
            applied.append(f"mark {len(rows)} scores as sent")

        if "dismissed" in {c["name"] for c in inspect(conn).get_columns("job_scores")}:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            passes = conn.execute(text("SELECT job_id, user_id FROM job_scores WHERE dismissed = 1")).all()
            for job_id, owner in passes:
                app = conn.execute(text("SELECT id, status FROM applications WHERE job_id = :j AND user_id = :u"),
                                   {"j": job_id, "u": owner}).first()
                if app is None:
                    conn.execute(text("INSERT INTO applications (job_id, user_id, status, created_at) "
                                      "VALUES (:j, :u, 'dismissed', :t)"), {"j": job_id, "u": owner, "t": now})
                    app_id, before = conn.execute(text("SELECT id, NULL FROM applications WHERE job_id = :j AND user_id = :u"),
                                                  {"j": job_id, "u": owner}).first()
                elif app.status != "dismissed":
                    conn.execute(text("UPDATE applications SET status = 'dismissed' WHERE id = :i"), {"i": app.id})
                    app_id, before = app.id, app.status
                else:
                    continue
                conn.execute(text("INSERT INTO application_events (application_id, user_id, from_status, to_status, source, created_at) "
                                  "VALUES (:a, :u, :f, 'dismissed', 'system', :t)"),
                             {"a": app_id, "u": owner, "f": before, "t": now})
            conn.execute(text("ALTER TABLE job_scores DROP COLUMN dismissed"))
            applied.append(f"move {len(passes)} passes to applications, drop job_scores.dismissed")

        if not _done(conn, "deadline_kst_to_utc"):
            for table in ("jobs", "raw_postings"):
                conn.execute(text(f"UPDATE {table} SET deadline = strftime('%Y-%m-%d %H:%M:%f000', deadline, '-9 hours') "
                                  "WHERE deadline IS NOT NULL"))
            _mark(conn, "deadline_kst_to_utc")
            applied.append("shift deadlines KST → UTC")

        if match_channel_id:
            updated = conn.execute(text("UPDATE users SET match_channel_id = :c WHERE id = :u AND match_channel_id IS NULL"),
                                   {"c": str(match_channel_id), "u": user_id}).rowcount
            if updated:
                applied.append(f"set match channel for user {user_id}")
    return applied


def _legacy_match_channel_id() -> str | None:
    """옛 설정 DISCORD_MATCH_CHANNEL_ID를 .env에서 읽는다(값은 출력하지 않는다)."""
    from dotenv import dotenv_values

    from shared.config import _ENV_FILE
    return dotenv_values(_ENV_FILE).get("DISCORD_MATCH_CHANNEL_ID") or None


if __name__ == "__main__":
    from shared.db import get_engine

    parser = argparse.ArgumentParser(description="지원 관리 구조 변경 마이그레이션")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--match-channel-id")
    args = parser.parse_args()
    steps = migrate(get_engine(), args.user_id, args.match_channel_id or _legacy_match_channel_id())
    print("\n".join(steps) if steps else "할 일 없음 (이미 최신)")
