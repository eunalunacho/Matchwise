"""tracker 유닛 테스트 — 지원 상태 변경(change_status)과 목록 조회(lists). SQLite 인메모리 세션."""
from datetime import datetime, timedelta, timezone

import pytest

from db.orm import Application, ApplicationEvent, ApplicationQuestion, Job, JobScore, ResumeDraft, User
from tracker.applications import change_status
from tracker.lists import (
    deadline_reminders, missed_recommendations, recent_changes, writing_and_unwritten,
)

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)  # 10/20 09:00 KST


def _user(session, name="A") -> User:
    user = User(username=name)
    session.add(user)
    session.flush()
    return user


def _job(session, company="C", deadline=None) -> Job:
    job = Job(source="a", url=f"https://ex.com/{company}", title="t", company=company, deadline=deadline)
    session.add(job)
    session.flush()
    return job


# ── change_status ───────────────────────────────────────────────────────

def test_change_status_keeps_one_current_row_and_appends_history_per_user(session):
    """A·B가 각각 상태를 바꾸면 applications는 (사람, 공고)당 한 줄, application_events는 바뀔 때마다 한 줄."""
    a, b = _user(session, "A"), _user(session, "B")
    aekyung, toss = _job(session, "애경"), _job(session, "토스")

    t = datetime(2026, 10, 15, 0, 10, tzinfo=timezone.utc)
    change_status(session, a.id, aekyung.id, "will_apply", "discord", now=t)
    change_status(session, b.id, toss.id, "will_apply", "discord", now=t)
    change_status(session, a.id, aekyung.id, "submitted", "web", now=t + timedelta(days=5))
    change_status(session, b.id, toss.id, "submitted", "discord", now=t + timedelta(days=6))
    change_status(session, b.id, toss.id, "passed", "web", now=t + timedelta(days=19))

    current = {(app.user_id, app.job_id): app.status for app in session.query(Application).all()}
    assert current == {(a.id, aekyung.id): "submitted", (b.id, toss.id): "passed"}

    history = [(e.user_id, e.from_status, e.to_status, e.source)
               for e in session.query(ApplicationEvent).order_by(ApplicationEvent.created_at).all()]
    assert history == [
        (a.id, None, "will_apply", "discord"),
        (b.id, None, "will_apply", "discord"),
        (a.id, "will_apply", "submitted", "web"),
        (b.id, "will_apply", "submitted", "discord"),
        (b.id, "submitted", "passed", "web"),
    ]


def test_change_status_records_will_apply_and_submitted_times(session):
    user, job = _user(session), _job(session)
    app, _ = change_status(session, user.id, job.id, "will_apply", "discord", now=NOW)
    assert app.will_apply_at == NOW and app.submitted_at is None
    later = NOW + timedelta(days=1)
    change_status(session, user.id, job.id, "submitted", "web", now=later)
    assert app.submitted_at == later


def test_change_status_same_status_is_a_no_op(session):
    user, job = _user(session), _job(session)
    change_status(session, user.id, job.id, "will_apply", "discord")
    _, changed = change_status(session, user.id, job.id, "will_apply", "web")

    assert changed is False
    assert session.query(ApplicationEvent).count() == 1


@pytest.mark.parametrize("status, source", [("interested", "web"), ("submitted", "slack")])
def test_change_status_rejects_unknown_values(session, status, source):
    user, job = _user(session), _job(session)
    with pytest.raises(ValueError):
        change_status(session, user.id, job.id, status, source)


# ── 작성 중 / 미작성 ────────────────────────────────────────────────────

def test_writing_and_unwritten_split_by_draft_and_sorted_by_deadline(session):
    user = _user(session)
    soon = _job(session, "soon", deadline=NOW + timedelta(days=2))
    late = _job(session, "late", deadline=NOW + timedelta(days=9))
    none = _job(session, "none")
    drafted = _job(session, "drafted", deadline=NOW + timedelta(days=5))
    submitted = _job(session, "submitted")
    for job in (late, none, soon, drafted):
        change_status(session, user.id, job.id, "will_apply", "discord")
    change_status(session, user.id, submitted.id, "submitted", "web")
    question = ApplicationQuestion(job_id=drafted.id, question="지원동기")
    session.add(question)
    session.flush()
    session.add(ResumeDraft(job_id=drafted.id, user_id=user.id, question_id=question.id, answer="초안"))
    session.flush()

    writing, unwritten = writing_and_unwritten(session, user.id)
    assert [a.job.company for a in writing] == ["drafted"]
    assert [a.job.company for a in unwritten] == ["soon", "late", "none"]


# ── 마감 리마인드 ───────────────────────────────────────────────────────

def _will_apply(session, user, company, deadline=None, registered=NOW):
    job = _job(session, company, deadline=deadline)
    change_status(session, user.id, job.id, "will_apply", "discord", now=registered)
    return job


def test_deadline_reminders_on_d7_d2_and_day7_without_deadline(session):
    user = _user(session)
    # 마감은 한국 시간 날짜로 센다 — 10/27 23:59 KST는 D-7
    _will_apply(session, user, "d7", deadline=datetime(2026, 10, 27, 23, 59, tzinfo=KST))
    _will_apply(session, user, "d2", deadline=datetime(2026, 10, 22, 10, 0, tzinfo=KST))
    _will_apply(session, user, "d5", deadline=datetime(2026, 10, 25, 10, 0, tzinfo=KST))
    _will_apply(session, user, "nodl-day7", registered=NOW - timedelta(days=7))
    _will_apply(session, user, "nodl-day6", registered=NOW - timedelta(days=6))
    submitted = _will_apply(session, user, "submitted", deadline=datetime(2026, 10, 22, 10, 0, tzinfo=KST))
    change_status(session, user.id, submitted.id, "submitted", "discord")

    found = {app.job.company: label for app, label in deadline_reminders(session, user.id, NOW)}
    assert found == {"d7": "마감 D-7", "d2": "마감 D-2", "nodl-day7": "등록 7일째 (마감일 미확인)"}


# ── 놓친 추천 ───────────────────────────────────────────────────────────

def _sent_score(session, user, company, fit=80, sent_days_ago=4, notified_as="card", deadline=None):
    job = _job(session, company, deadline=deadline)
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=fit, notified_as=notified_as,
                         notified_at=NOW - timedelta(days=sent_days_ago)))
    session.flush()
    return job


def test_missed_recommendations_are_cards_without_reaction_after_3_days(session):
    user = _user(session)
    _sent_score(session, user, "missed", fit=82)
    _sent_score(session, user, "missed-higher", fit=91)
    _sent_score(session, user, "recent", sent_days_ago=1)
    _sent_score(session, user, "thread", notified_as="thread")
    _sent_score(session, user, "low", fit=40)
    _sent_score(session, user, "expired", deadline=NOW - timedelta(days=1))
    registered = _sent_score(session, user, "registered")
    change_status(session, user.id, registered.id, "will_apply", "discord")
    passed = _sent_score(session, user, "passed")
    change_status(session, user.id, passed.id, "dismissed", "discord")

    missed = missed_recommendations(session, user.id, NOW)
    assert [s.job.company for s in missed] == ["missed-higher", "missed"]


# ── 최근 변화 ───────────────────────────────────────────────────────────

def test_recent_changes_since_and_without_passes(session):
    user, other = _user(session, "A"), _user(session, "B")
    old, new, passed, others = (_job(session, c) for c in ("old", "new", "passed", "others"))
    change_status(session, user.id, old.id, "will_apply", "discord", now=NOW - timedelta(days=5))
    change_status(session, user.id, new.id, "will_apply", "discord", now=NOW - timedelta(days=1))
    change_status(session, user.id, passed.id, "dismissed", "discord", now=NOW - timedelta(days=1))
    change_status(session, other.id, others.id, "will_apply", "discord", now=NOW - timedelta(days=1))

    changes = recent_changes(session, user.id, NOW - timedelta(days=3))
    assert [e.application.job.company for e in changes] == ["new"]


def test_change_status_supports_assignment_and_interview_stages(session):
    user, job = _user(session), _job(session)
    for status in ("will_apply", "submitted", "assignment", "interview", "passed"):
        change_status(session, user.id, job.id, status, "web")

    steps = [e.to_status for e in session.query(ApplicationEvent).order_by(ApplicationEvent.id)]
    assert steps == ["will_apply", "submitted", "assignment", "interview", "passed"]
