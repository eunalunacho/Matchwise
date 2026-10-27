"""Discord 알림 유닛 테스트.

format_job_message: 실제 discord.Embed/View 객체를 생성해 필드를 검증한다.
send_daily_notifications: discord.Client는 mock, DB는 테스트용 SQLite 세션.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from db.orm import Application, Job, JobScore, User
from bot.notifications import format_job_message, send_daily_notifications


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
    summary = jd_summary or {}
    job.jd_tasks = summary.get("tasks")
    job.jd_stack = summary.get("stack")
    job.jd_employment_type = summary.get("employment_type")
    job.jd_qualifications = summary.get("qualifications")
    job.jd_preferred = summary.get("preferred")
    return job


def _make_score(
    fit_score: int | None = 80,
    company_score: int = 70,
    user_id: int = 42,
    fit_reasons: str | None = "Python 경험",
    position: str | None = None,
    fit_detail: dict | None = None,
) -> MagicMock:
    score = MagicMock(spec=JobScore)
    score.fit_score = fit_score
    score.company_score = company_score
    score.user_id = user_id
    score.fit_reasons = fit_reasons
    score.fit_detail = fit_detail if fit_detail is not None else (
        {"position": position} if position is not None else None
    )
    return score


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


def test_embed_title_uses_scored_position_instead_of_posting_title():
    """제목은 공고명이 아니라 채점에서 고른 직무명 — 직무가 여러 개인 공고에서 어느 직무 기준인지 보이게."""
    job = _make_job(jd_summary={"employment_type": "정규직"})
    job.title = "애경그룹｜AK아이에스 2027년 신입사원 공개 채용"
    job.company = "애경그룹"
    embed, _ = format_job_message(job, _make_score(position="IT 서비스 개발"))
    assert embed.title == "[애경그룹] IT 서비스 개발 (정규직)"


def test_embed_title_falls_back_to_posting_title_without_position():
    embed, _ = format_job_message(_make_job(), _make_score(position=""))
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


def test_card_body_is_only_tasks_and_reasons():
    """2026-10-15 결정: 카드 본문은 하는 일 + 왜 나랑 맞나만. 자격요건·우대·스택은 [자세히 보기]로."""
    job = _make_job(jd_summary={
        "tasks": ["RAG 파이프라인 구축"],
        "stack": ["Python", "LangGraph"],
        "qualifications": ["경력 0~3년"],
        "preferred": ["LangGraph 경험"],
    })
    embed, _ = format_job_message(job, _make_score())
    assert [f.name for f in embed.fields] == ["💼 하는 일", "✨ 왜 나랑 맞나"]


def test_company_score_not_in_card():
    """company_score는 비활성화 상태라 카드에 안 나온다."""
    embed, _ = format_job_message(_make_job(), _make_score(company_score=72))
    field_names = [f.name for f in embed.fields]
    assert "회사 점수" not in field_names
    assert "72" not in (embed.description or "")


def test_buttons_are_view_details_add_to_list_and_pass():
    job = _make_job(job_id=7)
    score = _make_score(user_id=42)
    _, view = format_job_message(job, score)
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert len(buttons) == 3

    detail = next(b for b in buttons if b.label == "자세히 보기")
    assert detail.style == discord.ButtonStyle.link
    assert detail.url == job.url
    assert detail.custom_id is None  # 링크 버튼은 인터랙션이 안 옴

    custom_ids = {b.custom_id for b in buttons if b.custom_id}
    assert custom_ids == {"will_apply:7:42", "dislike:7:42"}


# ── send_daily_notifications: 사용자별 매칭 채널 ──────────────────────────────

CHANNEL_ID = "555"


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.discord.bot_token.get_secret_value.return_value = "token"
    return settings


def _discord(sent: list):
    """(client, channel, message). 채널·스레드로 보낸 내용은 sent에 ("channel"|"thread", kwargs)로 쌓인다."""
    thread = MagicMock()

    async def thread_send(content=None, **kwargs):
        sent.append(("thread", {"content": content, **kwargs}))

    thread.send = AsyncMock(side_effect=thread_send)
    message = MagicMock()
    message.create_thread = AsyncMock(return_value=thread)

    async def channel_send(content=None, **kwargs):
        sent.append(("channel", {"content": content, **kwargs}))
        return message

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=channel_send)
    client = AsyncMock()
    client.fetch_channel.return_value = channel
    return client, channel, message


def _seed(session, scores: list[tuple[int, str]], channel_id: str | None = CHANNEL_ID,
          username: str = "tester") -> User:
    """(점수, 회사) 목록으로 공고·점수를 만든다. 직무명은 '직무-회사'."""
    user = User(username=username, is_active=True, match_channel_id=channel_id)
    session.add(user)
    session.flush()
    for fit, company in scores:
        job = Job(source="a", url=f"https://ex.com/{username}/{company}", title=f"공고 {company}", company=company)
        session.add(job)
        session.flush()
        session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=fit, fit_reasons="이유",
                             fit_detail={"position": f"직무-{company}"}))
    session.commit()
    return user


async def _run(session, sent):
    client, channel, message = _discord(sent)
    with patch("bot.notifications.get_session", return_value=session), \
         patch.object(session, "close"), \
         patch("bot.notifications.get_settings", return_value=_settings()), \
         patch("bot.notifications.discord.Client", return_value=client):
        await send_daily_notifications()
    return client, channel, message


def _cards(sent) -> list:
    return [kw["embed"] for where, kw in sent if kw.get("embed")]


@pytest.mark.asyncio
async def test_notify_posts_header_and_top3_cards_to_users_channel(session):
    _seed(session, [(90, "A"), (85, "B"), (80, "C")])
    sent = []
    client, _, message = await _run(session, sent)

    client.fetch_channel.assert_awaited_once_with(int(CHANNEL_ID))
    assert sent[0][0] == "channel" and "추천 공고 — 3건" in sent[0][1]["content"]
    assert [e.title for e in _cards(sent)] == ["[A] 직무-A", "[B] 직무-B", "[C] 직무-C"]
    message.create_thread.assert_not_called()  # 나머지가 없으면 스레드도 없음
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_puts_the_rest_in_a_thread_as_one_line_links(session):
    _seed(session, [(95, "A"), (90, "B"), (85, "C"), (82, "D"), (70, "E")])
    sent = []
    _, _, message = await _run(session, sent)

    assert len(_cards(sent)) == 3
    assert any(where == "channel" and "나머지 2건" in (kw["content"] or "") for where, kw in sent)
    message.create_thread.assert_awaited_once()
    thread_text = "\n".join(kw["content"] for where, kw in sent if where == "thread")
    assert "• [D — 직무-D](https://ex.com/tester/D) · 82점" in thread_text
    assert "• [E — 직무-E](https://ex.com/tester/E) · 70점" in thread_text
    assert all(kw.get("suppress_embeds") for where, kw in sent if where == "thread")


@pytest.mark.asyncio
async def test_notify_records_what_was_sent_and_never_resends(session):
    """보낸 점수에 notified_at·방식이 남고, 다음 날(재채점해도) 다시 나가지 않는다."""
    _seed(session, [(95, "A"), (90, "B"), (85, "C"), (82, "D")])
    await _run(session, [])

    rows = {s.job.company: s for s in session.query(JobScore).all()}
    assert all(r.notified_at is not None for r in rows.values())
    assert [rows[c].notified_as for c in "ABCD"] == ["card", "card", "card", "thread"]

    rows["A"].scored_at = datetime.now(timezone.utc)  # 재채점
    session.commit()
    sent = []
    await _run(session, sent)
    assert _cards(sent) == []
    assert "새 추천 공고 없음" in sent[0][1]["content"]


@pytest.mark.asyncio
async def test_notify_skips_below_minimum_and_jobs_with_a_status(session):
    """권유 기준(70점) 미만, 이미 등록·패스한 공고는 보내지 않는다."""
    user = _seed(session, [(80, "NEW"), (69, "LOW"), (90, "PASSED")])
    passed = session.query(Job).filter_by(company="PASSED").one()
    session.add(Application(job_id=passed.id, user_id=user.id, status="dismissed"))
    session.commit()
    sent = []
    await _run(session, sent)

    assert [e.title for e in _cards(sent)] == ["[NEW] 직무-NEW"]


@pytest.mark.asyncio
async def test_notify_sends_each_user_to_their_own_channel_and_skips_users_without_one(session):
    _seed(session, [(90, "A")], channel_id="555", username="a")
    _seed(session, [(90, "B")], channel_id="777", username="b")
    _seed(session, [(90, "C")], channel_id=None, username="c")
    sent = []
    client, _, _ = await _run(session, sent)

    assert [c.args[0] for c in client.fetch_channel.await_args_list] == [555, 777]
    assert sorted(e.title for e in _cards(sent)) == ["[A] 직무-A", "[B] 직무-B"]
    c_score = session.query(JobScore).join(Job).filter(Job.company == "C").one()
    assert c_score.notified_at is None  # 채널이 생기면 그때 보낸다


@pytest.mark.asyncio
async def test_notify_does_not_log_in_when_nobody_has_a_channel(session):
    _seed(session, [(90, "A")], channel_id=None)
    sent = []
    with patch("bot.notifications.discord.Client") as client_cls, \
         patch("bot.notifications.get_session", return_value=session), \
         patch.object(session, "close"):
        await send_daily_notifications()

    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_notify_failure_leaves_scores_unsent_for_next_day(session):
    _seed(session, [(90, "A")])
    client, channel, _ = _discord([])
    channel.send.side_effect = RuntimeError("discord down")
    with patch("bot.notifications.get_session", return_value=session), \
         patch.object(session, "close"), \
         patch("bot.notifications.get_settings", return_value=_settings()), \
         patch("bot.notifications.discord.Client", return_value=client):
        await send_daily_notifications()

    assert session.query(JobScore).one().notified_at is None


@pytest.mark.asyncio
async def test_notify_orders_by_fit_score_then_deadline_within_similar_tier(session):
    """fit_score가 우선이고, 점수가 비슷한(10점 단위 동일 구간) 것끼리만 마감 가까운 순으로 재정렬한다."""
    now = datetime.now(timezone.utc)
    user = User(username="tester", match_channel_id=CHANNEL_ID)
    session.add(user)
    session.flush()

    # 90점대 둘(마감만 다름) + 70점대 하나(마감이 제일 가까워도 점수 구간이 낮아 후순위)
    high_far = Job(source="inthiswork", url="https://ex.com/high-far", title="High/Far",
                   company="A", deadline=now + timedelta(days=20))
    high_soon = Job(source="inthiswork", url="https://ex.com/high-soon", title="High/Soon",
                    company="B", deadline=now + timedelta(days=1))
    low_soonest = Job(source="inthiswork", url="https://ex.com/low-soonest", title="Low/Soonest",
                      company="C", deadline=now + timedelta(hours=1))
    session.add_all([high_far, high_soon, low_soonest])
    session.flush()
    session.add(JobScore(job_id=high_far.id, user_id=user.id, fit_score=95))
    session.add(JobScore(job_id=high_soon.id, user_id=user.id, fit_score=91))
    session.add(JobScore(job_id=low_soonest.id, user_id=user.id, fit_score=75))
    session.commit()

    sent = []
    await _run(session, sent)

    assert [e.url for e in _cards(sent)] == [
        "https://ex.com/high-soon",   # 90점대끼리는 마감 가까운 쪽이 먼저
        "https://ex.com/high-far",    # 같은 90점대, 마감 먼 쪽
        "https://ex.com/low-soonest", # 마감은 제일 급해도 점수 구간(60점대)이 낮아 마지막
    ]


# ── 등급 기준 알림 (적합도 v3) ───────────────────────────────────────────────

def _set_detail(session, company: str, **detail) -> None:
    row = session.query(JobScore).join(Job).filter(Job.company == company).one()
    row.fit_detail = {**(row.fit_detail or {}), **detail}
    session.commit()


@pytest.mark.asyncio
async def test_mismatch_high_score_is_not_notified(session):
    """점수가 높아도 직무 방향이 mismatch면 비추천 — 알림 대상이 아니다."""
    _seed(session, [(95, "MIS"), (85, "OK")])
    _set_detail(session, "MIS", direction="mismatch")
    sent = []
    await _run(session, sent)
    assert [e.title for e in _cards(sent)] == ["[OK] 직무-OK"]


@pytest.mark.asyncio
async def test_suggest_grade_is_notified(session):
    """70~79점 + 방향 일치(권유)도 알림 대상이고, 카드에 '권유' 라벨이 붙는다."""
    _seed(session, [(75, "SUG")])
    _set_detail(session, "SUG", direction="match")
    sent = []
    await _run(session, sent)
    cards = _cards(sent)
    assert [e.title for e in cards] == ["[SUG] 직무-SUG"]
    assert "75점 · 권유" in cards[0].description


@pytest.mark.asyncio
async def test_ineligible_is_not_notified(session):
    """지원 불가·판단 불가(점수 None)는 알림에 안 나가고 에러도 없다."""
    _seed(session, [(80, "INEL"), (80, "INS"), (85, "OK")])
    for company, status in (("INEL", "ineligible"), ("INS", "insufficient")):
        row = session.query(JobScore).join(Job).filter(Job.company == company).one()
        row.fit_score = None
        row.fit_detail = {**row.fit_detail, "status": status}
    session.commit()
    sent = []
    await _run(session, sent)
    assert [e.title for e in _cards(sent)] == ["[OK] 직무-OK"]


@pytest.mark.asyncio
async def test_user_grade_thresholds_apply(session):
    """유저별 등급 기준(preferences.grade)을 따른다 — 기준을 85로 올리면 80점은 알림 제외."""
    user = _seed(session, [(80, "A"), (90, "B")])
    user.preferences = {"grade": {"recommend_min": 85, "suggest_min": 85}}
    session.commit()
    sent = []
    await _run(session, sent)
    assert [e.title for e in _cards(sent)] == ["[B] 직무-B"]


def test_card_shows_grade_label():
    embed, _ = format_job_message(_make_job(), _make_score(fit_score=85))
    assert "85점 · 추천" in embed.description


def test_card_without_score_shows_label_not_none():
    embed, _ = format_job_message(
        _make_job(), _make_score(fit_score=None, fit_detail={"status": "ineligible"}),
    )
    assert "지원 불가" in embed.description
    assert "None" not in embed.description


def test_card_splits_good_and_weak_points_for_v3_scores():
    score = _make_score(fit_reasons="LLM 경험 일치", fit_detail={"weak_points": "Java 없음"})
    embed, _ = format_job_message(_make_job(), score)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["✨ 맞는 점"] == "LLM 경험 일치"
    assert fields["⚠️ 아쉬운 점"] == "Java 없음"
    assert "✨ 왜 나랑 맞나" not in fields


def test_card_omits_weak_points_field_when_empty():
    score = _make_score(fit_reasons="LLM 경험 일치", fit_detail={"weak_points": ""})
    embed, _ = format_job_message(_make_job(), score)
    assert [f.name for f in embed.fields if "아쉬운" in f.name] == []
