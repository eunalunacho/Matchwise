"""Discord 일일 알림 모듈.

스케줄러의 notify_job()이 asyncio.run(send_daily_notifications())로 호출한다.
봇 토큰으로 임시 로그인 → DM 전송 → 로그아웃 방식으로 동작한다.
"""
import logging
from datetime import date

import discord

from db.orm import Job, JobScore, User
from shared.config import get_settings
from shared.db import get_session

logger = logging.getLogger(__name__)

MIN_FIT_SCORE = 50  # 이 점수 미만 공고는 추천에서 제외


FIT_REASONS_PREVIEW_LEN = 150  # 카드에서는 매칭 이유를 짧게


def format_job_title(job: Job) -> str:
    """공고 표시 제목: '[회사명] 직군명 (고용형태)'. 고용형태 미상이면 괄호 생략."""
    summary = job.jd_summary if isinstance(job.jd_summary, dict) else {}
    base = f"[{job.company}] {job.title}"
    employment_type = summary.get("employment_type")
    return f"{base} ({employment_type})" if employment_type else base


def format_job_message(
    job: Job, score: JobScore
) -> tuple[discord.Embed, discord.ui.View]:
    """공고 1건의 Discord Embed와 버튼 View를 반환한다.

    카드는 '하는 일'(jd_summary.tasks) 중심 — 별로임/관심 판단은 여기서 하고,
    스택·요건·회사 점수·매칭 이유 전문은 [상세 분석] 버튼으로 넘긴다.
    """
    fit = score.fit_score or 0

    if fit >= 80:
        color = discord.Color.green()
    elif fit >= 60:
        color = discord.Color.gold()
    else:
        color = discord.Color.light_grey()

    embed = discord.Embed(
        title=format_job_title(job),
        url=job.url,
        color=color,
    )

    desc_parts = [f"매칭 **{fit}점**"]
    if job.deadline:
        delta = (job.deadline.date() - date.today()).days
        desc_parts.append(f"마감 D-{delta}" if delta >= 0 else "마감 지남")
    embed.description = " · ".join(desc_parts)

    summary = job.jd_summary if isinstance(job.jd_summary, dict) else {}
    tasks = summary.get("tasks") or []
    if tasks:
        embed.add_field(
            name="💼 하는 일",
            value="\n".join(f"• {t}" for t in tasks[:4]),
            inline=False,
        )

    if score.fit_reasons:
        reasons = score.fit_reasons
        if len(reasons) > FIT_REASONS_PREVIEW_LEN:
            reasons = reasons[:FIT_REASONS_PREVIEW_LEN] + "…"
        embed.add_field(name="✨ 왜 나랑 맞나", value=reasons, inline=False)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="상세 분석",
        style=discord.ButtonStyle.primary,
        custom_id=f"detail:{job.id}:{score.user_id}",
    ))
    view.add_item(discord.ui.Button(
        label="이력서 작성하기",
        style=discord.ButtonStyle.success,
        custom_id=f"draft:{job.id}:{score.user_id}",
    ))
    view.add_item(discord.ui.Button(
        label="별로임",
        style=discord.ButtonStyle.secondary,
        custom_id=f"dislike:{job.id}:{score.user_id}",
    ))
    return embed, view


async def send_daily_notifications() -> None:
    """활성 유저별 fit_score 상위 3개 공고를 Discord DM으로 전송한다."""
    settings = get_settings()
    token = settings.discord.bot_token.get_secret_value()

    client = discord.Client(intents=discord.Intents.default())
    await client.login(token)

    session = get_session()
    try:
        users = session.query(User).filter_by(is_active=True).all()

        for user in users:
            scores = (
                session.query(JobScore)
                .join(Job)
                .filter(JobScore.user_id == user.id)
                .filter(JobScore.fit_score >= MIN_FIT_SCORE)
                .filter(JobScore.dismissed.is_(False))
                .order_by(JobScore.fit_score.desc())
                .limit(3)
                .all()
            )

            if not scores:
                logger.info("[notify] user=%s 추천 공고 없음", user.discord_id)
                continue

            try:
                discord_user = await client.fetch_user(int(user.discord_id))
                dm = await discord_user.create_dm()
                await dm.send(content=f"📬 오늘의 추천 공고 ({len(scores)}건)")

                for score in scores:
                    embed, view = format_job_message(score.job, score)
                    await dm.send(embed=embed, view=view)

                logger.info("[notify] user=%s %d건 전송 완료", user.discord_id, len(scores))
            except Exception:
                logger.exception("[notify] user=%s DM 전송 실패", user.discord_id)

    finally:
        session.close()
        await client.close()
