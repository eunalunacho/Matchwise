"""Discord 버튼 인터랙션 핸들러.

알림 카드·비서 DM의 버튼을 처리한다. custom_id 형식: "{action}:{job_id}:{user_id}"
  will_apply  — 지원 상태 'will_apply'(목록에 등록) + 웹 공고 상세 링크 안내 (작성 페이지는 B5에서)
  dislike     — 지원 상태 'dismissed'(패스) → 이후 추천에서 제외
  submitted   — 지원 상태 'submitted'(제출 완료). 리마인드 DM의 [제출함] 버튼 (DESIGN.md 5-4)

상태는 모두 tracker/applications.py::change_status로 바꿔 application_events에 기록이 남는다.
"자세히 보기"는 링크 버튼(url=job.url)이라 인터랙션이 발생하지 않는다.
"""
import logging

import discord
from discord.ext import commands

from bot.notifications import format_job_title
from db.orm import Application, Job, JobScore
from shared.config import get_settings
from shared.db import get_session
from tracker.applications import change_status

logger = logging.getLogger(__name__)

_ACTIONS = ("dislike", "will_apply", "submitted")


def parse_custom_id(custom_id: str) -> tuple[str, int, int] | None:
    """'action:job_id:user_id' → (action, job_id, user_id). 형식이 다르면 None."""
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] not in _ACTIONS:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


async def handle_interaction(interaction: discord.Interaction) -> None:
    """버튼 클릭 이벤트 라우터. 우리 버튼이 아니면 조용히 무시한다."""
    if interaction.type != discord.InteractionType.component:
        return

    parsed = parse_custom_id((interaction.data or {}).get("custom_id", ""))
    if parsed is None:
        return

    action, job_id, user_id = parsed
    logger.info("[interaction] %s job_id=%s user_id=%s", action, job_id, user_id)

    if action == "dislike":
        await _handle_dislike(interaction, job_id, user_id)
    elif action == "will_apply":
        await _handle_will_apply(interaction, job_id, user_id)
    elif action == "submitted":
        await _handle_submitted(interaction, job_id, user_id)


def _title(session, job: Job, user_id: int) -> str:
    """응답에 쓸 공고 제목 — 채점에서 고른 직무명이 있으면 그것으로."""
    score = session.query(JobScore).filter_by(job_id=job.id, user_id=user_id).first()
    return format_job_title(job, score)


async def _handle_dislike(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """패스 — 지원 상태 'dismissed'. 다음 추천부터 제외. 응답에 어떤 공고인지 명시한다."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            await interaction.response.send_message("공고를 찾을 수 없어요.", ephemeral=True)
            return

        change_status(session, user_id, job_id, "dismissed", source="discord")
        session.commit()
        await interaction.response.send_message(
            f"👌 반영했어요 — **{_title(session, job, user_id)}**은(는) 다시 추천하지 않을게요.",
            ephemeral=True,
        )
    finally:
        session.close()


async def _handle_will_apply(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """목록에 등록 — 지원 상태 'will_apply'(가벼운 상태 마킹) + 웹 공고 상세 링크 안내.

    실제 자소서 작성·저장은 웹에서 한다 (DESIGN.md 5-2 [결정]). 이미 지원 예정·제출이면 상태를 되돌리지 않는다.
    """
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            await interaction.response.send_message("공고를 찾을 수 없어요.", ephemeral=True)
            return

        job_title = _title(session, job, user_id)
        # 작성 페이지(/jobs/{id}/apply, DESIGN.md 5-7·Phase B5)가 생기기 전까지는 공고 상세로 안내한다.
        detail_url = f"{get_settings().db.web_base_url}/jobs/{job_id}"

        app = session.query(Application).filter_by(job_id=job_id, user_id=user_id).first()
        if app and app.status in ("will_apply", "submitted"):
            await interaction.response.send_message(
                f"이미 **{job_title}** — 지원 예정으로 등록돼 있어요.\n공고 상세: {detail_url}",
                ephemeral=True,
            )
            return

        change_status(session, user_id, job_id, "will_apply", source="discord")
        session.commit()
        await interaction.response.send_message(
            f"✅ **{job_title}** — 목록에 등록했어요.\n"
            f"공고 상세에서 지원 질문을 등록하고 초안을 확인할 수 있어요: {detail_url}",
            ephemeral=True,
        )
    finally:
        session.close()


async def _handle_submitted(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """제출함 — 지원 상태 'submitted'. 이후 마감 리마인드에서 빠진다."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            await interaction.response.send_message("공고를 찾을 수 없어요.", ephemeral=True)
            return

        _, changed = change_status(session, user_id, job_id, "submitted", source="discord")
        session.commit()
        job_title = _title(session, job, user_id)
        text = (f"📮 **{job_title}** — 제출 완료로 표시했어요. 이제 리마인드하지 않을게요."
                if changed else f"이미 **{job_title}** — 제출 완료로 표시돼 있어요.")
        await interaction.response.send_message(text, ephemeral=True)
    finally:
        session.close()


def setup_interactions(bot: commands.Bot) -> None:
    """bot에 인터랙션 핸들러를 등록한다."""

    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        await handle_interaction(interaction)
