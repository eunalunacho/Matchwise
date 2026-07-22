"""Discord 버튼 인터랙션 핸들러 유닛 테스트.

discord.Interaction과 DB 세션을 모두 mock으로 대체한다.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.interactions import handle_interaction, parse_custom_id
from db.orm import ApplicationQuestion, Job, JobScore, User


# ── helpers ────────────────────────────────────────────────────────────────

def _make_interaction(
    custom_id: str,
    discord_user_id: int = 111222333,
    itype: discord.InteractionType = discord.InteractionType.component,
) -> MagicMock:
    interaction = MagicMock()
    interaction.type = itype
    interaction.data = {"custom_id": custom_id}
    interaction.user.id = discord_user_id
    interaction.response.send_message = AsyncMock()
    return interaction


def _make_session(
    job=None, score=None, user=None, questions: list | None = None
) -> MagicMock:
    session = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model is Job:
            q.filter_by.return_value.first.return_value = job
        elif model is JobScore:
            q.filter_by.return_value.first.return_value = score
        elif model is User:
            q.filter_by.return_value.first.return_value = user
        elif model is ApplicationQuestion:
            q.filter_by.return_value.order_by.return_value.all.return_value = (
                questions or []
            )
        return q

    session.query.side_effect = query_side_effect
    return session


def _make_settings(web_base_url: str = "http://localhost:8000") -> MagicMock:
    settings = MagicMock()
    settings.db.web_base_url = web_base_url
    return settings


def _sent_text(interaction: MagicMock) -> str:
    """send_message로 보낸 첫 positional 인자(텍스트)를 반환."""
    args, kwargs = interaction.response.send_message.call_args
    return args[0] if args else ""


def _sent_embed(interaction: MagicMock) -> discord.Embed:
    _, kwargs = interaction.response.send_message.call_args
    return kwargs["embed"]


# ── parse_custom_id ────────────────────────────────────────────────────────

def test_parse_valid_custom_id():
    assert parse_custom_id("detail:7:42") == ("detail", 7, 42)
    assert parse_custom_id("draft:1:2") == ("draft", 1, 2)
    assert parse_custom_id("dislike:99:1") == ("dislike", 99, 1)


def test_parse_rejects_unknown_action():
    assert parse_custom_id("foo:1:2") is None


def test_parse_rejects_bad_format():
    assert parse_custom_id("") is None
    assert parse_custom_id("detail:1") is None
    assert parse_custom_id("detail:x:y") is None


# ── 라우팅 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_component_interaction_ignored():
    interaction = _make_interaction(
        "detail:1:1", itype=discord.InteractionType.application_command
    )
    await handle_interaction(interaction)
    interaction.response.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_custom_id_ignored():
    interaction = _make_interaction("somethingelse")
    await handle_interaction(interaction)
    interaction.response.send_message.assert_not_called()


# ── dislike ────────────────────────────────────────────────────────────────

def _make_mock_job(job_id: int = 7, jd_summary: dict | None = None) -> MagicMock:
    job = MagicMock(spec=Job)
    job.id = job_id
    job.title = "AI Engineer"
    job.company = "Acme"
    job.url = "https://example.com/job/7"
    job.jd_summary = jd_summary
    return job


@pytest.mark.asyncio
async def test_dislike_sets_dismissed_and_names_the_job():
    score = MagicMock(spec=JobScore)
    score.dismissed = False
    score.job = _make_mock_job()
    session = _make_session(score=score)
    interaction = _make_interaction("dislike:7:42")

    with patch("bot.interactions.get_session", return_value=session):
        await handle_interaction(interaction)

    assert score.dismissed is True
    session.commit.assert_called_once()
    text = _sent_text(interaction)
    assert "반영" in text
    assert "[Acme] AI Engineer" in text  # 어떤 공고인지 응답에 명시
    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_dislike_score_not_found():
    session = _make_session(score=None)
    interaction = _make_interaction("dislike:7:42")

    with patch("bot.interactions.get_session", return_value=session):
        await handle_interaction(interaction)

    session.commit.assert_not_called()
    assert "찾을 수 없" in _sent_text(interaction)


# ── detail ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detail_job_not_found():
    session = _make_session(job=None)
    interaction = _make_interaction("detail:7:42")

    with patch("bot.interactions.get_session", return_value=session):
        await handle_interaction(interaction)

    assert "찾을 수 없" in _sent_text(interaction)


@pytest.mark.asyncio
async def test_detail_embed_has_qualifications_preferred_and_stack():
    job = _make_mock_job(jd_summary={
        "tasks": ["Agent 설계"],
        "stack": ["Python", "LangGraph"],
        "employment_type": "정규직",
        "qualifications": ["경력 0~3년", "Python 능숙"],
        "preferred": ["LangGraph 경험"],
    })
    score = MagicMock(spec=JobScore)
    score.company_score = 72

    session = _make_session(job=job, score=score)
    interaction = _make_interaction("detail:7:42")

    with patch("bot.interactions.get_session", return_value=session), \
         patch("bot.interactions.get_settings", return_value=_make_settings()):
        await handle_interaction(interaction)

    embed = _sent_embed(interaction)
    assert "[Acme] AI Engineer (정규직)" in embed.title
    fields = {f.name: f.value for f in embed.fields}
    assert "경력 0~3년" in fields["✅ 자격요건"]
    assert "LangGraph 경험" in fields["⭐ 우대사항"]
    assert "Python, LangGraph" in fields["🛠 스택"]
    assert "72점" in fields["🏢 회사 점수"]
    assert "/jobs/7" in fields["🔗 더 보기"]
    # 매칭 이유 전문은 상세에서 제외됨 (자격요건/우대사항으로 대체)
    assert "✨ 매칭 이유 (전문)" not in fields


# ── draft ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_unregistered_user_rejected():
    session = _make_session(user=None)
    interaction = _make_interaction("draft:7:42")

    with patch("bot.interactions.get_session", return_value=session):
        await handle_interaction(interaction)

    assert "등록된 사용자가 아니" in _sent_text(interaction)


@pytest.mark.asyncio
async def test_draft_clicker_mismatch_rejected():
    """custom_id의 user_id와 실제 클릭한 Discord 유저가 다르면 거부한다."""
    user = MagicMock(spec=User)
    user.discord_id = "999999999"  # 클릭한 사람(111222333)과 다름
    session = _make_session(user=user)
    interaction = _make_interaction("draft:7:42", discord_user_id=111222333)

    with patch("bot.interactions.get_session", return_value=session):
        await handle_interaction(interaction)

    assert "등록된 사용자가 아니" in _sent_text(interaction)


@pytest.mark.asyncio
async def test_draft_no_questions_links_dashboard_and_names_job():
    user = MagicMock(spec=User)
    user.discord_id = "111222333"
    session = _make_session(job=_make_mock_job(), user=user, questions=[])
    interaction = _make_interaction("draft:7:42", discord_user_id=111222333)

    with patch("bot.interactions.get_session", return_value=session), \
         patch("bot.interactions.get_settings", return_value=_make_settings()):
        await handle_interaction(interaction)

    text = _sent_text(interaction)
    assert "[Acme] AI Engineer" in text  # 어떤 공고인지 응답에 명시
    assert "질문" in text
    assert "/jobs/7" in text


@pytest.mark.asyncio
async def test_draft_with_questions_returns_stub_and_names_job():
    user = MagicMock(spec=User)
    user.discord_id = "111222333"
    q1 = MagicMock(spec=ApplicationQuestion)
    q2 = MagicMock(spec=ApplicationQuestion)
    session = _make_session(job=_make_mock_job(), user=user, questions=[q1, q2])
    interaction = _make_interaction("draft:7:42", discord_user_id=111222333)

    with patch("bot.interactions.get_session", return_value=session), \
         patch("bot.interactions.get_settings", return_value=_make_settings()):
        await handle_interaction(interaction)

    text = _sent_text(interaction)
    assert "[Acme] AI Engineer" in text
    assert "2개" in text
    assert "준비 중" in text
