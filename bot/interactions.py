"""Discord 버튼 인터랙션 핸들러.

알림 카드의 버튼 3종을 처리한다. custom_id 형식: "{action}:{job_id}:{user_id}"
  detail  — 스택·요건·회사 점수·매칭 이유 전문 (ephemeral)
  draft   — 지원 질문 확인 후 초안 생성 (Draft Agent 연결 전까지 스텁 응답)
  dislike — job_scores.dismissed 마킹 → 이후 추천에서 제외
"""
import logging

import discord
from discord.ext import commands

from bot.notifications import format_job_title
from db.orm import ApplicationQuestion, Job, JobScore, User
from shared.config import get_settings
from shared.db import get_session

logger = logging.getLogger(__name__)

_ACTIONS = ("detail", "draft", "dislike")


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

    if action == "detail":
        await _handle_detail(interaction, job_id, user_id)
    elif action == "draft":
        await _handle_draft(interaction, job_id, user_id)
    elif action == "dislike":
        await _handle_dislike(interaction, job_id, user_id)


async def _handle_detail(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """카드에서 뺀 정보: 자격요건, 우대사항, 스택, 회사 점수."""
    session = get_session()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            await interaction.response.send_message(
                "공고를 찾을 수 없어요.", ephemeral=True
            )
            return

        score = (
            session.query(JobScore)
            .filter_by(job_id=job_id, user_id=user_id)
            .first()
        )

        embed = discord.Embed(
            title=f"🔍 {format_job_title(job)}",
            url=job.url,
            color=discord.Color.blurple(),
        )

        summary = job.jd_summary if isinstance(job.jd_summary, dict) else {}
        if summary.get("qualifications"):
            embed.add_field(
                name="✅ 자격요건",
                value="\n".join(f"• {q}" for q in summary["qualifications"][:5]),
                inline=False,
            )
        if summary.get("preferred"):
            embed.add_field(
                name="⭐ 우대사항",
                value="\n".join(f"• {p}" for p in summary["preferred"][:5]),
                inline=False,
            )
        if summary.get("stack"):
            embed.add_field(
                name="🛠 스택", value=", ".join(summary["stack"][:12]), inline=False
            )

        if score and score.company_score is not None:
            embed.add_field(
                name="🏢 회사 점수", value=f"{score.company_score}점", inline=True
            )

        web_base = get_settings().db.web_base_url
        embed.add_field(
            name="🔗 더 보기",
            value=f"[공고 원문]({job.url}) · [대시보드]({web_base}/jobs/{job.id})",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


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
            f"👌 반영했어요 — **{format_job_title(score.job)}**은(는) 다시 추천하지 않을게요.",
            ephemeral=True,
        )
    finally:
        session.close()


async def _handle_draft(
    interaction: discord.Interaction, job_id: int, user_id: int
) -> None:
    """지원 질문 확인 → 초안 생성. Draft Agent(Phase E) 연결 전까지는 스텁."""
    session = get_session()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if not user or str(interaction.user.id) != user.discord_id:
            await interaction.response.send_message(
                "등록된 사용자가 아니에요. 관리자에게 등록을 요청해주세요.",
                ephemeral=True,
            )
            return

        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            await interaction.response.send_message(
                "공고를 찾을 수 없어요.", ephemeral=True
            )
            return

        questions = (
            session.query(ApplicationQuestion)
            .filter_by(job_id=job_id)
            .order_by(ApplicationQuestion.order_num)
            .all()
        )
        web_base = get_settings().db.web_base_url
        job_title = format_job_title(job)

        if not questions:
            await interaction.response.send_message(
                f"**{job_title}** — 등록된 지원 질문이 아직 없어요.\n"
                f"대시보드에서 먼저 질문을 등록해주세요: {web_base}/jobs/{job_id}",
                ephemeral=True,
            )
            return

        # TODO(Phase E): Draft Agent(LangGraph + RAG + Claude) 연결 지점
        await interaction.response.send_message(
            f"📝 **{job_title}** — 질문 {len(questions)}개 확인했어요.\n"
            "초안 생성 기능(Draft Agent)은 준비 중이에요 — 곧 여기서 바로 생성됩니다.",
            ephemeral=True,
        )
    finally:
        session.close()


def setup_interactions(bot: commands.Bot) -> None:
    """bot에 인터랙션 핸들러를 등록한다."""

    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        await handle_interaction(interaction)
