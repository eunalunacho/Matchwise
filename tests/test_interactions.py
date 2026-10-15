"""Discord 버튼 인터랙션 핸들러 유닛 테스트.

discord.Interaction과 DB 세션을 모두 mock으로 대체한다.
"""
from unittest.mock import MagicMock, patch

import discord
import pytest

from bot.interactions import handle_interaction, parse_custom_id
from db.orm import Application, Job, JobScore


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
    from unittest.mock import AsyncMock
    interaction.response.send_message = AsyncMock()
    return interaction


def _make_session(job=None, score=None, application=None) -> MagicMock:
    session = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model is Job:
            q.filter_by.return_value.first.return_value = job
        elif model is JobScore:
            q.filter_by.return_value.first.return_value = score
        elif model is Application:
            q.filter_by.return_value.first.return_value = application
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


def _make_mock_job(job_id: int = 7, jd_summary: dict | None = None) -> MagicMock:
    job = MagicMock(spec=Job)
    job.id = job_id
    job.title = "AI Engineer"
    job.company = "Acme"
    job.url = "https://example.com/job/7"
    summary = jd_summary or {}
    job.jd_tasks = summary.get("tasks")
    job.jd_stack = summary.get("stack")
    job.jd_employment_type = summary.get("employment_type")
    job.jd_qualifications = summary.get("qualifications")
    job.jd_preferred = summary.get("preferred")
    return job


# ── parse_custom_id ────────────────────────────────────────────────────────

def test_parse_valid_custom_id():
    assert parse_custom_id("dislike:99:1") == ("dislike", 99, 1)
    assert parse_custom_id("will_apply:7:42") == ("will_apply", 7, 42)


def test_parse_rejects_unknown_action():
    assert parse_custom_id("foo:1:2") is None
    # "자세히 보기"는 링크 버튼이라 애초에 인터랙션이 안 옴 — action 자체가 사라짐
    assert parse_custom_id("detail:1:2") is None


def test_parse_rejects_bad_format():
    assert parse_custom_id("") is None
    assert parse_custom_id("dislike:1") is None
    assert parse_custom_id("dislike:x:y") is None


# ── 라우팅 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_component_interaction_ignored():
    interaction = _make_interaction(
        "will_apply:1:1", itype=discord.InteractionType.application_command
    )
    await handle_interaction(interaction)
    interaction.response.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_custom_id_ignored():
    interaction = _make_interaction("somethingelse")
    await handle_interaction(interaction)
    interaction.response.send_message.assert_not_called()


# ── dislike (패스) ────────────────────────────────────────────────────────

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


# ── will_apply (목록에 등록) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_will_apply_creates_application_and_confirms_with_web_link():
    job = _make_mock_job()
    session = _make_session(job=job, application=None)
    interaction = _make_interaction("will_apply:7:42")

    with patch("bot.interactions.get_session", return_value=session), \
         patch("bot.interactions.get_settings", return_value=_make_settings()):
        await handle_interaction(interaction)

    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert isinstance(added, Application)
    assert added.status == "will_apply"
    assert added.will_apply_at is not None
    session.commit.assert_called_once()
    text = _sent_text(interaction)
    assert "목록에 등록" in text
    assert "http://localhost:8000/jobs/7" in text
    assert "/apply" not in text  # 작성 페이지(B5) 전까지는 공고 상세로 안내


@pytest.mark.asyncio
async def test_will_apply_job_not_found():
    session = _make_session(job=None)
    interaction = _make_interaction("will_apply:7:42")

    with patch("bot.interactions.get_session", return_value=session):
        await handle_interaction(interaction)

    assert "찾을 수 없" in _sent_text(interaction)
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_will_apply_already_registered_is_idempotent_and_resends_link():
    job = _make_mock_job()
    existing_app = MagicMock(spec=Application)
    existing_app.status = "will_apply"

    session = _make_session(job=job, application=existing_app)
    interaction = _make_interaction("will_apply:7:42")

    with patch("bot.interactions.get_session", return_value=session), \
         patch("bot.interactions.get_settings", return_value=_make_settings()):
        await handle_interaction(interaction)

    session.commit.assert_not_called()
    text = _sent_text(interaction)
    assert "이미" in text
    assert "http://localhost:8000/jobs/7" in text
    assert "/apply" not in text
