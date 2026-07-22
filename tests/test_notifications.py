"""Discord 알림 유닛 테스트.

format_job_message: 실제 discord.Embed/View 객체를 생성해 필드를 검증한다.
send_daily_notifications: discord.Client와 DB를 모두 mock으로 대체한다.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from db.orm import Job, JobScore, User
from bot.notifications import MIN_FIT_SCORE, format_job_message, send_daily_notifications


# ── helpers ────────────────────────────────────────────────────────────────

def _make_job(
    job_id: int = 1,
    deadline: datetime | None = None,
    jd_summary: dict | None = None,
) -> MagicMock:
    job = MagicMock(spec=Job)
    job.id = job_id
    job.title = "Backend Dev"
    job.company = "Acme"
    job.url = "https://example.com/job/1"
    job.deadline = deadline
    job.jd_summary = jd_summary
    return job


def _make_score(
    fit_score: int = 80,
    company_score: int = 70,
    user_id: int = 42,
    fit_reasons: str | None = "Python 경험",
) -> MagicMock:
    score = MagicMock(spec=JobScore)
    score.fit_score = fit_score
    score.company_score = company_score
    score.user_id = user_id
    score.fit_reasons = fit_reasons
    return score


def _make_notify_session(users: list, scores: list) -> MagicMock:
    """send_daily_notifications의 두 쿼리 체인을 모사한다."""
    session = MagicMock()

    def query_side_effect(model):
        mock_q = MagicMock()
        if model is User:
            mock_q.filter_by.return_value.all.return_value = users
        elif model is JobScore:
            (
                mock_q.join.return_value
                .filter.return_value
                .filter.return_value
                .filter.return_value
                .order_by.return_value
                .limit.return_value
                .all.return_value
            ) = scores
        return mock_q

    session.query.side_effect = query_side_effect
    return session


def _make_discord_mock() -> tuple[AsyncMock, AsyncMock]:
    """(mock_client, mock_dm) 반환. fetch_user → dm 체인 설정."""
    client = AsyncMock()
    dm = AsyncMock()
    discord_user = AsyncMock()
    discord_user.create_dm.return_value = dm
    client.fetch_user.return_value = discord_user
    return client, dm


# ── format_job_message ─────────────────────────────────────────────────────

def test_high_fit_score_green_color():
    embed, _ = format_job_message(_make_job(), _make_score(fit_score=85))
    assert embed.color == discord.Color.green()


def test_mid_fit_score_gold_color():
    embed, _ = format_job_message(_make_job(), _make_score(fit_score=65))
    assert embed.color == discord.Color.gold()


def test_low_fit_score_grey_color():
    embed, _ = format_job_message(_make_job(), _make_score(fit_score=40))
    assert embed.color == discord.Color.light_grey()


def test_embed_title_bracket_format():
    embed, _ = format_job_message(_make_job(), _make_score())
    assert embed.title == "[Acme] Backend Dev"


def test_embed_title_includes_employment_type_when_present():
    job = _make_job(jd_summary={
        "tasks": [], "stack": [], "employment_type": "인턴 (6개월)",
        "qualifications": [], "preferred": [],
    })
    embed, _ = format_job_message(job, _make_score())
    assert embed.title == "[Acme] Backend Dev (인턴 (6개월))"


def test_embed_url_set_to_job_url():
    job = _make_job()
    embed, _ = format_job_message(job, _make_score())
    assert embed.url == job.url


def test_deadline_shown_in_description_when_set():
    deadline = datetime.now(timezone.utc) + timedelta(days=5)
    embed, _ = format_job_message(_make_job(deadline=deadline), _make_score())
    assert "마감 D-" in embed.description


def test_deadline_omitted_from_description_when_none():
    embed, _ = format_job_message(_make_job(deadline=None), _make_score())
    assert "마감" not in embed.description


def test_fit_score_always_in_description():
    embed, _ = format_job_message(_make_job(), _make_score(fit_score=87))
    assert "87점" in embed.description


def test_fit_reasons_field_omitted_when_none():
    embed, _ = format_job_message(_make_job(), _make_score(fit_reasons=None))
    field_names = [f.name for f in embed.fields]
    assert "✨ 왜 나랑 맞나" not in field_names


def test_fit_reasons_truncated_in_card():
    long_reasons = "가" * 300
    embed, _ = format_job_message(_make_job(), _make_score(fit_reasons=long_reasons))
    field = next(f for f in embed.fields if f.name == "✨ 왜 나랑 맞나")
    assert len(field.value) < 300
    assert field.value.endswith("…")


def test_tasks_field_shown_when_summary_present():
    job = _make_job(jd_summary={
        "tasks": ["RAG 파이프라인 구축", "Agent Tool 설계"],
        "stack": ["Python"],
        "requirements": "경력 무관",
    })
    embed, _ = format_job_message(job, _make_score())
    field = next(f for f in embed.fields if f.name == "💼 하는 일")
    assert "RAG 파이프라인 구축" in field.value
    assert field.value.startswith("• ")


def test_tasks_field_omitted_without_summary():
    embed, _ = format_job_message(_make_job(jd_summary=None), _make_score())
    field_names = [f.name for f in embed.fields]
    assert "💼 하는 일" not in field_names


def test_company_score_not_in_card():
    """회사 점수는 카드가 아니라 [상세 분석]에서만 보여준다."""
    embed, _ = format_job_message(_make_job(), _make_score(company_score=72))
    field_names = [f.name for f in embed.fields]
    assert "회사 점수" not in field_names
    assert "72" not in (embed.description or "")


def test_buttons_have_correct_custom_ids():
    job = _make_job(job_id=7)
    score = _make_score(user_id=42)
    _, view = format_job_message(job, score)
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert len(buttons) == 3
    custom_ids = {b.custom_id for b in buttons}
    assert "detail:7:42" in custom_ids
    assert "draft:7:42" in custom_ids
    assert "dislike:7:42" in custom_ids


# ── send_daily_notifications ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_no_active_users_sends_no_dm():
    """활성 유저 없으면 fetch_user를 호출하지 않는다."""
    session = _make_notify_session(users=[], scores=[])
    client, dm = _make_discord_mock()

    with patch("bot.notifications.get_session", return_value=session), \
         patch("bot.notifications.discord.Client") as MockClient:
        MockClient.return_value = client
        await send_daily_notifications()

    client.fetch_user.assert_not_called()
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_notify_no_qualifying_scores_sends_no_dm():
    """fit_score 미충족 공고만 있는 유저에게 DM을 보내지 않는다."""
    user = MagicMock(spec=User)
    user.id = 1
    user.discord_id = "111222333"

    session = _make_notify_session(users=[user], scores=[])
    client, dm = _make_discord_mock()

    with patch("bot.notifications.get_session", return_value=session), \
         patch("bot.notifications.discord.Client") as MockClient:
        MockClient.return_value = client
        await send_daily_notifications()

    client.fetch_user.assert_not_called()


@pytest.mark.asyncio
async def test_notify_sends_header_plus_one_embed_per_score():
    """헤더 1번 + 공고 N건만큼 dm.send를 호출한다."""
    user = MagicMock(spec=User)
    user.id = 1
    user.discord_id = "111222333"

    job = _make_job(job_id=10)
    score1 = _make_score(fit_score=90, user_id=1)
    score1.job = job
    score2 = _make_score(fit_score=80, user_id=1)
    score2.job = job

    session = _make_notify_session(users=[user], scores=[score1, score2])
    client, dm = _make_discord_mock()

    with patch("bot.notifications.get_session", return_value=session), \
         patch("bot.notifications.discord.Client") as MockClient:
        MockClient.return_value = client
        await send_daily_notifications()

    # 헤더 1회 + 공고 2건 = 3회
    assert dm.send.call_count == 3
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_notify_continues_if_one_user_dm_fails():
    """한 유저의 DM 전송 실패가 다른 유저 처리를 막지 않는다."""
    user1 = MagicMock(spec=User)
    user1.id = 1
    user1.discord_id = "111"

    user2 = MagicMock(spec=User)
    user2.id = 2
    user2.discord_id = "222"

    job = _make_job(job_id=10)
    score = _make_score(fit_score=90, user_id=1)
    score.job = job

    session = _make_notify_session(users=[user1, user2], scores=[score])
    client, dm = _make_discord_mock()

    fetch_count = {"n": 0}

    async def fetch_user_side_effect(discord_id: int):
        fetch_count["n"] += 1
        if fetch_count["n"] == 1:
            raise RuntimeError("봇 차단됨")
        discord_user = AsyncMock()
        discord_user.create_dm.return_value = dm
        return discord_user

    client.fetch_user.side_effect = fetch_user_side_effect

    with patch("bot.notifications.get_session", return_value=session), \
         patch("bot.notifications.discord.Client") as MockClient:
        MockClient.return_value = client
        await send_daily_notifications()

    # 두 유저 모두 시도
    assert client.fetch_user.call_count == 2
    # 두 번째 유저는 성공 → 헤더 + 공고 1건 = 2회 send
    assert dm.send.call_count == 2
    client.close.assert_called_once()
