"""Discord 버튼 인터랙션 핸들러 유닛 테스트.

discord.Interaction은 mock, DB는 SQLite 인메모리 세션(conftest.session)을 쓴다 —
상태 변경(change_status)이 applications·application_events에 실제로 남는지 확인하기 위해서다.
"""
from unittest.mock import MagicMock, patch

import discord
import pytest

from bot.interactions import handle_interaction, parse_custom_id
from db.orm import Application, ApplicationEvent, Job, JobScore, User


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


def _make_settings(web_base_url: str = "http://localhost:8000") -> MagicMock:
    settings = MagicMock()
    settings.db.web_base_url = web_base_url
    return settings


def _sent_text(interaction: MagicMock) -> str:
    """send_message로 보낸 첫 positional 인자(텍스트)를 반환."""
    args, kwargs = interaction.response.send_message.call_args
    return args[0] if args else ""


def _seed(session, status: str | None = None) -> tuple[int, int]:
    """공고·사용자(+선택적 지원 상태)를 만들고 (job_id, user_id)를 반환한다."""
    user = User(username="tester", discord_id="111222333")
    job = Job(source="a", url="https://example.com/job/7", title="AI Engineer", company="Acme")
    session.add_all([user, job])
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=80, fit_detail={"position": "ML 엔지니어"}))
    if status:
        session.add(Application(job_id=job.id, user_id=user.id, status=status))
    session.commit()
    return job.id, user.id


async def _click(session, action: str, job_id: int, user_id: int) -> MagicMock:
    interaction = _make_interaction(f"{action}:{job_id}:{user_id}")
    # 핸들러가 세션을 닫아도 테스트에서 계속 조회할 수 있게 close만 막는다.
    with patch("bot.interactions.get_session", return_value=session),          patch.object(session, "close"),          patch("bot.interactions.get_settings", return_value=_make_settings()):
        await handle_interaction(interaction)
    return interaction


def _app(session, job_id: int, user_id: int) -> Application | None:
    return session.query(Application).filter_by(job_id=job_id, user_id=user_id).first()


# ── parse_custom_id ────────────────────────────────────────────────────────

def test_parse_valid_custom_id():
    assert parse_custom_id("dislike:99:1") == ("dislike", 99, 1)
    assert parse_custom_id("will_apply:7:42") == ("will_apply", 7, 42)
    assert parse_custom_id("submitted:7:42") == ("submitted", 7, 42)


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
async def test_dislike_records_dismissed_status_with_event_and_names_the_job(session):
    job_id, user_id = _seed(session)
    interaction = await _click(session, "dislike", job_id, user_id)

    assert _app(session, job_id, user_id).status == "dismissed"
    event = session.query(ApplicationEvent).one()
    assert (event.from_status, event.to_status, event.source) == (None, "dismissed", "discord")
    text = _sent_text(interaction)
    assert "반영" in text
    assert "[Acme] ML 엔지니어" in text  # 어떤 공고인지 — 채점에서 고른 직무명으로


@pytest.mark.asyncio
async def test_dislike_job_not_found(session):
    interaction = await _click(session, "dislike", 999, 1)

    assert session.query(Application).count() == 0
    assert "찾을 수 없" in _sent_text(interaction)


# ── will_apply (목록에 등록) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_will_apply_creates_application_and_confirms_with_web_link(session):
    job_id, user_id = _seed(session)
    interaction = await _click(session, "will_apply", job_id, user_id)

    app = _app(session, job_id, user_id)
    assert app.status == "will_apply"
    assert app.will_apply_at is not None
    assert session.query(ApplicationEvent).one().to_status == "will_apply"
    text = _sent_text(interaction)
    assert "목록에 등록" in text
    assert f"http://localhost:8000/jobs/{job_id}" in text
    assert "/apply" not in text  # 작성 페이지(B5) 전까지는 공고 상세로 안내


@pytest.mark.asyncio
async def test_will_apply_job_not_found(session):
    interaction = await _click(session, "will_apply", 999, 1)

    assert "찾을 수 없" in _sent_text(interaction)
    assert session.query(Application).count() == 0


@pytest.mark.asyncio
async def test_will_apply_already_registered_is_idempotent_and_resends_link(session):
    job_id, user_id = _seed(session, status="will_apply")
    interaction = await _click(session, "will_apply", job_id, user_id)

    assert session.query(ApplicationEvent).count() == 0
    text = _sent_text(interaction)
    assert "이미" in text
    assert f"http://localhost:8000/jobs/{job_id}" in text


@pytest.mark.asyncio
async def test_will_apply_after_pass_changes_mind(session):
    """패스했던 공고도 다시 목록에 등록할 수 있다 — 기록에 dismissed → will_apply가 남는다."""
    job_id, user_id = _seed(session, status="dismissed")
    await _click(session, "will_apply", job_id, user_id)

    event = session.query(ApplicationEvent).one()
    assert (event.from_status, event.to_status) == ("dismissed", "will_apply")


# ── submitted (리마인드 DM의 제출함) ────────────────────────────────────

@pytest.mark.asyncio
async def test_submitted_marks_submission(session):
    job_id, user_id = _seed(session, status="will_apply")
    interaction = await _click(session, "submitted", job_id, user_id)

    app = _app(session, job_id, user_id)
    assert app.status == "submitted"
    assert app.submitted_at is not None
    assert "제출 완료로 표시했어요" in _sent_text(interaction)


@pytest.mark.asyncio
async def test_submitted_twice_says_already(session):
    job_id, user_id = _seed(session, status="submitted")
    interaction = await _click(session, "submitted", job_id, user_id)

    assert session.query(ApplicationEvent).count() == 0
    assert "이미" in _sent_text(interaction)
