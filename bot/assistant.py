"""비서 DM — 지원 일정 리마인드(C2, 매일)와 3일 현황 요약(C1, 3일마다). DESIGN.md 5-3·5-4.

스케줄러의 assistant_job()이 asyncio.run(send_assistant_messages())로 호출한다. 매칭 알림(채널)과 달리
'나에게 할 일'이라 users.discord_id로 DM을 보낸다. 리마인드 카드의 [제출함] 버튼은 bot/interactions.py가 처리하므로
버튼이 동작하려면 봇(-m bot.main)이 켜져 있어야 한다.

목록 조건은 tracker/lists.py 한 곳에 있다(웹 화면과 같은 함수를 쓴다).
"""
import logging
from datetime import datetime, timedelta, timezone

import discord

from bot.notifications import chunk_lines, format_job_title
from db.orm import Application, JobScore, User
from shared.config import get_settings
from shared.db import get_session
from tracker.applications import STATUS_LABELS
from tracker.lists import (
    deadline_reminders, kst_date, missed_recommendations, recent_changes, writing_and_unwritten,
)

logger = logging.getLogger(__name__)

SUMMARY_EVERY_DAYS = 3
SUMMARY_SECTION_MAX = 3  # 현황 요약에서 섹션마다 보여줄 최대 건수


def is_summary_day(now: datetime) -> bool:
    """3일 현황 요약을 보내는 날인지. 날짜 번호로 정해 스케줄러를 다시 켜도 주기가 흐트러지지 않는다."""
    return kst_date(now).toordinal() % SUMMARY_EVERY_DAYS == 0


def _score(session, job_id: int, user_id: int) -> JobScore | None:
    return session.query(JobScore).filter_by(job_id=job_id, user_id=user_id).first()


def _deadline_text(deadline: datetime | None, now: datetime) -> str:
    if deadline is None:
        return "마감일 미확인"
    days_left = (kst_date(deadline) - kst_date(now)).days
    return f"마감 D-{days_left}" if days_left >= 0 else "마감 지남"


def format_reminder(app: Application, label: str, score: JobScore | None) -> tuple[discord.Embed, discord.ui.View]:
    """리마인드 공고 1건 — 제목·안내 문구 + [자세히 보기] [제출함] 버튼."""
    job = app.job
    embed = discord.Embed(title=format_job_title(job, score), url=job.url, color=discord.Color.orange())
    deadline = job.deadline
    when = f" ({kst_date(deadline).month}/{kst_date(deadline).day} 마감)" if deadline else ""
    embed.description = f"⏰ {label}{when} — 아직 제출 전이에요"

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="자세히 보기", style=discord.ButtonStyle.link, url=job.url))
    view.add_item(discord.ui.Button(
        label="제출함", style=discord.ButtonStyle.success, custom_id=f"submitted:{job.id}:{app.user_id}",
    ))
    return embed, view


def _link_line(job, score: JobScore | None, extra: str) -> str:
    title = format_job_title(job, score).replace("[", "(").replace("]", ")")
    return f"• [{title}]({job.url}) · {extra}"


def build_summary_lines(session, user_id: int, now: datetime) -> list[str]:
    """3일 현황 요약 줄 목록. 보여줄 게 하나도 없으면 빈 목록."""
    since = now - timedelta(days=SUMMARY_EVERY_DAYS)
    changes = recent_changes(session, user_id, since)
    writing, unwritten = writing_and_unwritten(session, user_id)
    missed = missed_recommendations(session, user_id, now, limit=SUMMARY_SECTION_MAX)
    if not (changes or writing or unwritten or missed):
        return []

    start, end = kst_date(since), kst_date(now)
    lines = [f"🗂 **지원 현황** ({start.month}/{start.day} ~ {end.month}/{end.day})"]

    if changes:
        lines.append(f"\n**최근 {SUMMARY_EVERY_DAYS}일 변화**")
        for event in changes:
            job = event.application.job
            before = STATUS_LABELS.get(event.from_status, "") if event.from_status else "새로"
            after = STATUS_LABELS.get(event.to_status, event.to_status)
            lines.append(_link_line(job, _score(session, job.id, user_id), f"{before} → {after}"))

    for name, apps in (("작성 중", writing), ("미작성", unwritten)):
        if not apps:
            continue
        lines.append(f"\n**{name}** ({len(apps)}건)")
        for app in apps[:SUMMARY_SECTION_MAX]:
            lines.append(_link_line(app.job, _score(session, app.job_id, user_id), _deadline_text(app.job.deadline, now)))

    if missed:
        lines.append("\n**놓치고 있는 추천**")
        for score in missed:
            lines.append(_link_line(score.job, score, f"{score.fit_score}점 · {_deadline_text(score.job.deadline, now)}"))
    return lines


async def _send_to_user(client, session, user: User, now: datetime) -> tuple[int, bool]:
    """한 사용자에게 리마인드·요약을 보낸다. Returns: (리마인드 건수, 요약 보냈는지)."""
    reminders = deadline_reminders(session, user.id, now)
    summary = build_summary_lines(session, user.id, now) if is_summary_day(now) else []
    if not reminders and not summary:
        return 0, False

    target = await client.fetch_user(int(user.discord_id))
    if reminders:
        await target.send(content=f"⏰ 지원 리마인드 — {len(reminders)}건")
        for app, label in reminders:
            embed, view = format_reminder(app, label, _score(session, app.job_id, user.id))
            await target.send(embed=embed, view=view)
    for chunk in chunk_lines(summary):
        await target.send(content=chunk, suppress_embeds=True)
    return len(reminders), bool(summary)


async def send_assistant_messages() -> None:
    """활성 유저마다 DM으로 오늘의 리마인드와(3일마다) 현황 요약을 보낸다. discord_id가 없는 유저는 건너뛴다."""
    session = get_session()
    client = None
    try:
        users = [u for u in session.query(User).filter_by(is_active=True).all() if u.discord_id]
        if not users:
            logger.info("[assistant] DM 보낼 유저 없음")
            return

        client = discord.Client(intents=discord.Intents.default())
        await client.login(get_settings().discord.bot_token.get_secret_value())
        now = datetime.now(timezone.utc)
        for user in users:
            try:
                count, summarized = await _send_to_user(client, session, user, now)
                logger.info("[assistant] user_id=%s 리마인드 %d건, 현황 요약 %s", user.id, count, summarized)
            except Exception:
                logger.exception("[assistant] user_id=%s DM 전송 실패", user.id)
    finally:
        session.close()
        if client is not None:
            await client.close()
