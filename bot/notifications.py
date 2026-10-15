"""Discord 일일 매칭 알림 모듈 — 뉴스레터 성격이라 사용자마다 자기 매칭 채널(users.match_channel_id)로 보낸다.

스케줄러의 notify_job()이 asyncio.run(send_daily_notifications())로 호출한다.
봇 토큰으로 임시 로그인 → 채널 전송 → 로그아웃 방식으로 동작한다. 버튼(목록에 등록·패스)이 있어
웹훅이 아니라 봇이 보낸다. 지원 현황·리마인드 같은 '비서' 메시지는 DM으로 따로 보낸다(bot/assistant.py).

보내는 대상: 아직 안 보낸(notified_at 비어 있음) 점수 중 MIN_FIT_SCORE 이상이고 지원 상태(등록·패스 등)가 없는 것.
보낸 뒤 notified_at·notified_as를 기록하므로 재채점해도 같은 공고가 다시 나가지 않는다(2026-10-15).
상위 CARD_COUNT건은 카드로, 나머지는 스레드에 한 줄 링크로 넣는다.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord

from sqlalchemy import exists

from db.orm import Application, Job, JobScore, User
from shared.config import get_settings
from shared.db import get_session
from tracker.lists import kst_date

logger = logging.getLogger(__name__)

MIN_FIT_SCORE = 50  # 이 점수 미만 공고는 추천에서 제외
CARD_COUNT = 3  # 카드로 보여줄 상위 공고 수. 나머지는 스레드에 한 줄씩
_FIT_SCORE_TIER = 10  # 이 폭 안의 점수는 "비슷함"으로 보고 마감일로 재정렬
_KST = timezone(timedelta(hours=9))
_DISCORD_CONTENT_LIMIT = 2000
_THREAD_NAME_LIMIT = 100

FIT_REASONS_PREVIEW_LEN = 150  # 프롬프트는 50자 한 줄을 요청한다 — 길게 온 경우를 대비한 카드 표시 상한


def _position(score: JobScore | None) -> str:
    """채점에서 고른 직무명(fit_detail.position). 없으면 빈 문자열."""
    detail = getattr(score, "fit_detail", None) if score is not None else None
    return str(detail.get("position") or "").strip() if isinstance(detail, dict) else ""


def format_job_title(job: Job, score: JobScore | None = None) -> str:
    """공고 표시 제목: '[회사명] 직무명 (고용형태)'. 고용형태 미상이면 괄호 생략.

    직무명은 채점에서 고른 직무(fit_detail.position) — 공고 제목은 "2027 신입 공개채용"처럼 직무가 안 보이고,
    직무가 여러 개인 공고에서는 어느 직무 기준 점수인지 알아야 하기 때문. 없으면 공고 제목을 쓴다.
    """
    base = f"[{job.company}] {_position(score) or job.title}"
    employment_type = job.jd_employment_type
    return f"{base} ({employment_type})" if employment_type else base


def format_job_message(
    job: Job, score: JobScore
) -> tuple[discord.Embed, discord.ui.View]:
    """공고 1건의 Discord Embed와 버튼 View를 반환한다.

    카드 본문은 하는 일 + 왜 나랑 맞나만 둔다(2026-10-15 결정). 자격요건·우대사항·스택은
    [자세히 보기]로 원본을 본다.
    """
    fit = score.fit_score or 0

    if fit >= 80:
        color = discord.Color.green()
    elif fit >= 60:
        color = discord.Color.gold()
    else:
        color = discord.Color.light_grey()

    embed = discord.Embed(
        title=format_job_title(job, score),
        url=job.url,
        color=color,
    )

    desc_parts = [f"매칭 **{fit}점**"]
    if job.deadline:
        delta = (kst_date(job.deadline) - kst_date(datetime.now(timezone.utc))).days
        desc_parts.append(f"마감 D-{delta}" if delta >= 0 else "마감 지남")
    embed.description = " · ".join(desc_parts)

    tasks = job.jd_tasks or []
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
        label="자세히 보기",
        style=discord.ButtonStyle.link,
        url=job.url,
    ))
    view.add_item(discord.ui.Button(
        label="목록에 등록",
        style=discord.ButtonStyle.primary,
        custom_id=f"will_apply:{job.id}:{score.user_id}",
    ))
    view.add_item(discord.ui.Button(
        label="패스",
        style=discord.ButtonStyle.secondary,
        custom_id=f"dislike:{job.id}:{score.user_id}",
    ))
    return embed, view


def format_more_line(score: JobScore) -> str:
    """스레드용 한 줄: '• [회사 — 직무](공고 링크) · 82점'."""
    job = score.job
    name = f"{job.company} — {_position(score) or job.title}".replace("[", "(").replace("]", ")")
    return f"• [{name}]({job.url}) · {score.fit_score}점"


def chunk_lines(lines: list[str], limit: int = _DISCORD_CONTENT_LIMIT) -> list[str]:
    """줄 목록을 Discord 메시지 길이 제한 안의 덩어리로 나눈다."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _unsent_scores(session, user_id: int) -> list[JobScore]:
    """아직 안 보낸 추천 대상 점수 — fit_score 우선, 점수가 비슷한(10점 단위) 것끼리는 마감 가까운 순."""
    has_application = exists().where(
        Application.job_id == JobScore.job_id, Application.user_id == JobScore.user_id,
    )
    return (
        session.query(JobScore)
        .join(Job)
        .filter(JobScore.user_id == user_id)
        .filter(JobScore.fit_score >= MIN_FIT_SCORE)
        .filter(JobScore.notified_at.is_(None))
        .filter(~has_application)
        .order_by(
            # SQLAlchemy의 `/`는 실수 나눗셈으로 강제 변환되어(예: 91/10=9.1) 타이어링이
            # 깨지므로, .op("/")로 SQLite의 정수 나눗셈(91/10=9)을 그대로 쓴다.
            JobScore.fit_score.op("/")(_FIT_SCORE_TIER).desc(),
            Job.deadline.asc().nullslast(),
            JobScore.fit_score.desc(),
        )
        .all()
    )


async def _post_recommendations(channel, scores: list[JobScore], day: datetime) -> None:
    """헤더 → 상위 카드 → (나머지가 있으면) 스레드에 한 줄 링크 목록."""
    label = f"{day.month}월 {day.day}일"
    if not scores:
        await channel.send(content=f"📭 {label} — 새 추천 공고 없음")
        return

    await channel.send(content=f"📬 {label} 추천 공고 — {len(scores)}건")
    for score in scores[:CARD_COUNT]:
        embed, view = format_job_message(score.job, score)
        await channel.send(embed=embed, view=view)

    rest = scores[CARD_COUNT:]
    if not rest:
        return
    anchor = await channel.send(content=f"➕ 나머지 {len(rest)}건 보기")
    thread = await anchor.create_thread(name=f"{label} 나머지 추천 {len(rest)}건"[:_THREAD_NAME_LIMIT])
    for chunk in chunk_lines([format_more_line(s) for s in rest]):
        await thread.send(content=chunk, suppress_embeds=True)


def _mark_sent(scores: list[JobScore], now: datetime) -> None:
    """보낸 점수에 시각과 방식(카드/스레드)을 남긴다."""
    for index, score in enumerate(scores):
        score.notified_at = now
        score.notified_as = "card" if index < CARD_COUNT else "thread"


async def send_daily_notifications() -> None:
    """활성 유저마다 자기 매칭 채널로 아직 안 보낸 추천 공고를 보낸다.

    채널(users.match_channel_id)이 없는 유저는 건너뛴다 — 공유 채널로 보내면 남의 점수가 보이므로 기본 채널을 두지 않는다.
    보낼 유저가 하나도 없으면 로그인하지 않고 끝낸다. 한 유저의 전송 실패는 그 유저만 되돌리고(다음 날 다시 보냄) 넘어간다.
    """
    session = get_session()
    client = None
    try:
        users = session.query(User).filter_by(is_active=True).all()
        targets = [user for user in users if user.match_channel_id]
        for user in users:
            if not user.match_channel_id:
                logger.warning("[notify] user_id=%s 매칭 채널 미설정 — 건너뜀 (users.match_channel_id)", user.id)
        if not targets:
            return

        client = discord.Client(intents=discord.Intents.default())
        await client.login(get_settings().discord.bot_token.get_secret_value())
        now = datetime.now(timezone.utc)

        for user in targets:
            try:
                channel = await client.fetch_channel(int(user.match_channel_id))
                scores = _unsent_scores(session, user.id)
                await _post_recommendations(channel, scores, now.astimezone(_KST))
                _mark_sent(scores, now)
                session.commit()
                logger.info("[notify] user_id=%s 추천 %d건 전송", user.id, len(scores))
            except Exception:
                session.rollback()
                logger.exception("[notify] user_id=%s 채널 전송 실패", user.id)

    finally:
        session.close()
        if client is not None:
            await client.close()
