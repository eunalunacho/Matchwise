"""비서 DM(bot/assistant.py) 유닛 테스트 — discord.Client는 mock, DB는 SQLite 인메모리 세션."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.assistant import build_summary_lines, format_reminder, is_summary_day, send_assistant_messages
from db.orm import Job, JobScore, User
from tracker.applications import change_status

KST = timezone(timedelta(hours=9))


def _summary_day() -> datetime:
    """is_summary_day가 True인 시각(KST 09:10)."""
    day = datetime(2026, 10, 20, 9, 10, tzinfo=KST)
    while not is_summary_day(day):
        day += timedelta(days=1)
    return day.astimezone(timezone.utc)


def _seed(session, discord_id="111"):
    user = User(username="A", discord_id=discord_id, is_active=True)
    session.add(user)
    session.flush()
    return user


def _job(session, company, deadline=None):
    job = Job(source="a", url=f"https://ex.com/{company}", title="공고", company=company, deadline=deadline)
    session.add(job)
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=1, fit_score=80, fit_detail={"position": f"직무-{company}"}))
    session.flush()
    return job


def test_is_summary_day_every_third_day():
    day = _summary_day()
    assert [is_summary_day(day + timedelta(days=n)) for n in range(6)] == [True, False, False, True, False, False]


def test_format_reminder_has_detail_link_and_submitted_button(session):
    user = _seed(session)
    job = _job(session, "애경", deadline=datetime(2026, 10, 28, 23, 59, tzinfo=KST))
    app, _ = change_status(session, user.id, job.id, "will_apply", "discord")

    embed, view = format_reminder(app, "마감 D-7", session.query(JobScore).first())

    assert embed.title == "[애경] 직무-애경"
    assert "마감 D-7 (10/28 마감)" in embed.description
    link, submitted = view.children
    assert link.url == "https://ex.com/애경"
    assert submitted.custom_id == f"submitted:{job.id}:{user.id}"
    assert submitted.style == discord.ButtonStyle.success


def test_build_summary_lines_lists_changes_unwritten_and_missed(session):
    now = _summary_day()
    user = _seed(session)
    registered = _job(session, "토스", deadline=now + timedelta(days=5))
    change_status(session, user.id, registered.id, "will_apply", "discord", now=now - timedelta(days=1))
    missed = _job(session, "노타")
    score = session.query(JobScore).filter_by(job_id=missed.id).one()
    score.notified_as, score.notified_at = "card", now - timedelta(days=4)
    session.flush()

    text = "\n".join(build_summary_lines(session, user.id, now))

    assert "지원 현황" in text
    assert "최근 3일 변화" in text and "(토스) 직무-토스](https://ex.com/토스) · 새로 → 지원 예정" in text
    assert "**미작성** (1건)" in text and "마감 D-5" in text
    assert "놓치고 있는 추천" in text and "(노타) 직무-노타" in text and "80점 · 마감일 미확인" in text
    assert "작성 중" not in text  # 초안이 있는 공고가 없으면 섹션도 없음


def test_build_summary_lines_is_empty_when_nothing_to_report(session):
    user = _seed(session)
    assert build_summary_lines(session, user.id, _summary_day()) == []


async def _run(session, now):
    target = MagicMock()
    target.send = AsyncMock()
    client = AsyncMock()
    client.fetch_user.return_value = target
    with patch("bot.assistant.get_session", return_value=session), \
         patch.object(session, "close"), \
         patch("bot.assistant.get_settings", return_value=MagicMock()), \
         patch("bot.assistant.datetime") as fake_datetime, \
         patch("bot.assistant.discord.Client", return_value=client):
        fake_datetime.now.return_value = now
        await send_assistant_messages()
    return client, target


@pytest.mark.asyncio
async def test_send_assistant_messages_dms_reminders_with_buttons(session):
    now = _summary_day() + timedelta(days=1)  # 요약 없는 날
    user = _seed(session, discord_id="1490")
    due = _job(session, "애경", deadline=now + timedelta(days=7))
    change_status(session, user.id, due.id, "will_apply", "discord", now=now - timedelta(days=1))
    session.commit()

    client, target = await _run(session, now)

    client.fetch_user.assert_awaited_once_with(1490)
    calls = target.send.await_args_list
    assert "지원 리마인드 — 1건" in calls[0].kwargs["content"]
    assert calls[1].kwargs["embed"].title == "[애경] 직무-애경"
    assert len(calls) == 2  # 요약 날이 아니므로 리마인드만


@pytest.mark.asyncio
async def test_send_assistant_messages_sends_nothing_without_reminders_or_summary(session):
    _seed(session)
    session.commit()

    client, target = await _run(session, _summary_day() + timedelta(days=1))

    client.fetch_user.assert_not_called()
    target.send.assert_not_called()
