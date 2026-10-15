"""scripts/migrate_tracking.py 테스트 — 옛 모양의 데이터를 넣고 마이그레이션 결과와 재실행 안전성을 확인한다."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from db.orm import Application, ApplicationEvent, Base, Job, JobScore, User
from scripts.migrate_tracking import migrate


def _old_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE job_scores ADD COLUMN dismissed BOOLEAN DEFAULT 0"))
        conn.execute(text("INSERT INTO users (id, username, profile_version, is_active) VALUES (1, 'A', 1, 1)"))
        # 크롤러가 KST로 만든 마감일이 KST 벽시계 시각 그대로 저장돼 있던 상태
        for i, deadline in enumerate(["2026-10-28 23:59:00.000000", None, None, None, None]):
            conn.execute(text("INSERT INTO jobs (id, source, url, extract_status, deadline) VALUES (:i, 'a', :u, 'ok', :d)"),
                         {"i": i + 1, "u": f"https://ex.com/{i}", "d": deadline})
        for job_id, fit, dismissed in [(1, 90, 0), (2, 85, 0), (3, 80, 0), (4, 70, 0), (5, 40, 1)]:
            conn.execute(text("INSERT INTO job_scores (job_id, user_id, fit_score, dismissed, scored_at) "
                              "VALUES (:j, 1, :f, :d, '2026-10-15 00:10:00.000000')"),
                         {"j": job_id, "f": fit, "d": dismissed})
    return engine


def test_migrate_marks_sent_moves_passes_shifts_deadlines_and_sets_channel():
    engine = _old_db()
    migrate(engine, user_id=1, match_channel_id="555")
    s = sessionmaker(bind=engine)()

    scores = {row.job_id: row for row in s.query(JobScore).all()}
    assert all((r.profile_version, r.scorer_version) == (1, 1) for r in scores.values())
    assert [scores[j].notified_as for j in (1, 2, 3, 4, 5)] == ["card", "card", "card", "thread", None]
    assert scores[1].notified_at == datetime(2026, 10, 15, 0, 10, tzinfo=timezone.utc)

    assert "dismissed" not in {c["name"] for c in inspect(engine).get_columns("job_scores")}
    app = s.query(Application).one()
    assert (app.job_id, app.status) == (5, "dismissed")
    assert s.query(ApplicationEvent).one().source == "system"

    assert s.get(Job, 1).deadline == datetime(2026, 10, 28, 23, 59, tzinfo=timezone(timedelta(hours=9)))
    assert s.get(User, 1).match_channel_id == "555"
    s.close()


def test_migrate_twice_does_not_shift_deadlines_again():
    engine = _old_db()
    migrate(engine, match_channel_id="555")
    assert migrate(engine, match_channel_id="555") == []
    s = sessionmaker(bind=engine)()
    assert s.get(Job, 1).deadline == datetime(2026, 10, 28, 14, 59, tzinfo=timezone.utc)
    s.close()
