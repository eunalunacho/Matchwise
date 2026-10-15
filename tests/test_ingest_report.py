"""bot/ingest_report.py — 수집 파이프라인 Discord 웹훅 메시지 테스트."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
from pydantic import SecretStr

from bot import ingest_report

KST = timezone(timedelta(hours=9))


def _run(source="jasoseol", status="ok", seen=12, new=3, empty=1, detail_failed=0, error=None):
    return {
        "source": source, "status": status, "seen": seen, "new": new,
        "empty": empty, "detail_failed": detail_failed, "error": error,
    }


def _settings(url: str | None):
    settings = MagicMock()
    settings.discord.ingest_webhook_url = SecretStr(url) if url else None
    return settings


# ── 메시지 형식 ─────────────────────────────────────────────────────────────

def test_format_start_shows_kst_date_and_time():
    text = ingest_report.format_start(datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc))
    assert text == "📥10월 14일자 공고 수집 시작 (02:00)"


def test_format_crawl_result_lists_each_site_with_korean_name_and_total_new():
    text = ingest_report.format_crawl_result([
        _run("inthiswork", seen=24, new=9, empty=0),
        _run("jasoseol", seen=12, new=3, empty=1),
    ])
    lines = text.splitlines()
    assert lines[0] == "🔎 공고 수집 완료"
    assert lines[1] == "신규 12건"
    assert lines[2] == "인디스워크 — 목록 24 · 신규 9 · 빈 공고 0 · 상세 실패 0"
    assert lines[3] == "자소설 — 목록 12 · 신규 3 · 빈 공고 1 · 상세 실패 0"


def test_format_crawl_result_flags_crawler_error_and_empty_listing():
    text = ingest_report.format_crawl_result([
        _run("jobkorea", status="failed", seen=0, new=0, empty=0,
             error="RuntimeError: 필터 라벨 '서울'을 찾을 수 없음"),
        _run("inthiswork", seen=0, new=0, empty=0),
    ])
    assert "⚠️ 잡코리아 — 에러: RuntimeError: 필터 라벨 '서울'을 찾을 수 없음" in text
    assert "⚠️ 인디스워크 — 목록 0건 (사이트 구조 변경 의심)" in text


def test_format_extract_result_shows_counts_and_budget_leftover_only_when_present():
    base = {"ok": 11, "failed": 1, "ocr_failed": 0, "ocr_expired": 2, "remaining": 0}
    assert ingest_report.format_extract_result(base) == (
        "🔧 공고 추출 완료\n성공 11 · 실패 1 · OCR 실패 0\nOCR 재시도 실패 2"
    )
    with_left = ingest_report.format_extract_result({**base, "remaining": 5})
    assert with_left.endswith("\nOCR 재시도 실패 2\n하루 AI 한도로 남은 대기 5")


def test_format_error_names_stage_and_truncates_to_discord_limit():
    text = ingest_report.format_error("수집", RuntimeError("x" * 5000))
    assert text.startswith("❌ 수집 중 에러로 중단 — RuntimeError: ")
    assert len(text) <= 2000


# ── 전송 ───────────────────────────────────────────────────────────────────

def test_send_posts_content_to_webhook():
    with patch("bot.ingest_report.get_settings", return_value=_settings("https://discord.test/hook")), \
         patch("bot.ingest_report.httpx.post") as post:
        ingest_report.send("안녕")

    post.assert_called_once()
    assert post.call_args.args[0] == "https://discord.test/hook"
    assert post.call_args.kwargs["json"] == {"content": "안녕"}


def test_send_skips_when_webhook_not_configured():
    with patch("bot.ingest_report.get_settings", return_value=_settings(None)), \
         patch("bot.ingest_report.httpx.post") as post:
        ingest_report.send("안녕")

    post.assert_not_called()


def test_send_skips_when_webhook_left_blank_in_env():
    settings = MagicMock()
    settings.discord.ingest_webhook_url = SecretStr("  ")
    with patch("bot.ingest_report.get_settings", return_value=settings), \
         patch("bot.ingest_report.httpx.post") as post:
        ingest_report.send("안녕")

    post.assert_not_called()


def test_send_never_raises_on_http_failure():
    """웹훅이 실패해도 수집 파이프라인은 계속 돌아야 한다."""
    with patch("bot.ingest_report.get_settings", return_value=_settings("https://discord.test/hook")), \
         patch("bot.ingest_report.httpx.post", side_effect=httpx.ConnectError("down")):
        ingest_report.send("안녕")  # 예외 없이 완료
