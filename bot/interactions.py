"""Discord 버튼 인터랙션 핸들러.

알림 카드의 버튼을 처리한다. custom_id 형식: "{action}:{job_id}:{user_id}"
  will_apply  — applications.status='will_apply' 기록 + 웹 공고 상세 링크 안내 (작성 페이지는 B5에서)
  dislike     — job_scores.dismissed 마킹 → 이후 추천에서 제외 ("패스" 버튼)

"자세히 보기"는 링크 버튼(url=job.url)이라 인터랙션이 발생하지 않는다.
제출 완료 표시는 Discord가 아니라 웹 작성 페이지에서만 한다 (DESIGN.md 5-2).
"""
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from bot.notifications import format_job_title
from db.orm import Application, Job, JobScore
from shared.config import get_settings
from shared.db import get_session

logger = logging.getLogger(__name__)

_ACTIONS = ("dislike", "will_apply")


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


async def _handle_dislike(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """dismissed 마킹 — 다음 추천부터 제외. 응답에 어떤 공고인지 명시한다."""
    session = get_session()
    try:
        score = (
            session.query(JobScore)
            .filter_by(job_id=job_id, user_id=user_id)
            .first()
        )
        if not score:
            await interaction.response.send_message(
                "점수 기록을 찾을 수 없어요.", ephemeral=True
            )
            return

        score.dismissed = True
        session.commit()
        await interaction.response.send_message(
            f"👌 반영했어요 — **{format_job_title(score.job, score)}**은(는) 다시 추천하지 않을게요.",
            ephemeral=True,
        )
    finally:
        session.close()


async def _handle_will_apply(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """applications.status='will_apply' 기록(가벼운 상태 마킹) + 웹 공고 상세 링크 안내.

    실제 자소서 작성·저장·제출 완료 표시는 전부 웹에서 한다 (DESIGN.md 5-2 [결정]).
    """
    session = get_session()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            await interaction.response.send_message(
                "공고를 찾을 수 없어요.", ephemeral=True
            )
            return

        job_title = format_job_title(job)
        web_base = get_settings().db.web_base_url
        # 작성 페이지(/jobs/{id}/apply, DESIGN.md 5-7·Phase B5)가 생기기 전까지는 공고 상세로 안내한다.
        detail_url = f"{web_base}/jobs/{job_id}"

        app = (
            session.query(Application)
            .filter_by(job_id=job_id, user_id=user_id)
            .first()
        )
        if app and app.status in ("will_apply", "submitted"):
            await interaction.response.send_message(
                f"이미 **{job_title}** — 지원 예정으로 등록돼 있어요.\n"
                f"공고 상세: {detail_url}",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        if not app:
            app = Application(job_id=job_id, user_id=user_id)
            session.add(app)
        app.status = "will_apply"
        app.will_apply_at = now
        session.commit()

        await interaction.response.send_message(
            f"✅ **{job_title}** — 목록에 등록했어요.\n"
            f"공고 상세에서 지원 질문을 등록하고 초안을 확인할 수 있어요: {detail_url}",
            ephemeral=True,
        )
    finally:
        session.close()


def setup_interactions(bot: commands.Bot) -> None:
    """bot에 인터랙션 핸들러를 등록한다."""

    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        await handle_interaction(interaction)
