"""수집 파이프라인(run_crawl_pipeline) 진행 상황을 Discord 비공개 채널 웹훅으로 보낸다.

봇 로그인 없이 웹훅 주소에 POST 한 번만 한다. 주소(DISCORD_INGEST_WEBHOOK_URL)가 없으면 보내지 않고,
전송이 실패해도 예외를 올리지 않는다 — 알림 때문에 수집이 멈추면 안 된다.

메시지 4종: 시작 → 수집 완료(사이트별 건수) → 추출 완료, 중간에 멈추면 에러.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from shared.config import get_settings

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
_DISCORD_CONTENT_LIMIT = 2000
_TIMEOUT_SEC = 10

SOURCE_NAMES = {"inthiswork": "인디스워크", "jasoseol": "자소설", "jobkorea": "잡코리아"}


def _name(source: str) -> str:
    return SOURCE_NAMES.get(source, source)


def format_start(now: datetime) -> str:
    """수집 시작 메시지. now는 시간대가 있는 datetime(KST로 표시)."""
    kst = now.astimezone(_KST)
    return f"📥{kst.month}월 {kst.day}일자 공고 수집 시작 ({kst:%H:%M})"


def format_crawl_result(runs: list[dict]) -> str:
    """crawl_job()이 돌려준 사이트별 기록 → 수집 완료 메시지.

    크롤러 에러, 또는 목록에서 본 공고가 0건이면(신규 0건과 달리 정상일 가능성이 낮음) ⚠️로 표시한다.
    """
    lines = ["🔎 공고 수집 완료", f"신규 {sum(r['new'] for r in runs)}건"]
    for r in runs:
        name = _name(r["source"])
        if r["status"] != "ok":
            lines.append(f"⚠️ {name} — 에러: {r['error']}")
        elif r["seen"] == 0:
            lines.append(f"⚠️ {name} — 목록 0건 (사이트 구조 변경 의심)")
        else:
            lines.append(
                f"{name} — 목록 {r['seen']} · 신규 {r['new']} · 빈 공고 {r['empty']} · 상세 실패 {r['detail_failed']}"
            )
    return "\n".join(lines)


def format_extract_result(stats: dict) -> str:
    """extract_job()이 돌려준 건수 → 추출 완료 메시지.

    OCR 재시도 실패 = 재시도 기간(첫 실패 포함 2일)이 지나 OCR을 포기하고 failed로 넘긴 공고(ocr_expired).
    """
    lines = [
        "🔧 공고 추출 완료",
        f"성공 {stats['ok']} · 실패 {stats['failed']} · OCR 실패 {stats['ocr_failed']}",
        f"OCR 재시도 실패 {stats['ocr_expired']}",
    ]
    if stats["remaining"]:
        lines.append(f"하루 AI 한도로 남은 대기 {stats['remaining']}")
    return "\n".join(lines)


def format_error(stage: str, exc: BaseException) -> str:
    """파이프라인을 멈춘 에러 메시지. Discord 본문 길이 제한(2000자)에 맞춰 자른다."""
    text = f"❌ {stage} 중 에러로 중단 — {type(exc).__name__}: {exc}"
    return text[:_DISCORD_CONTENT_LIMIT]


def send(content: str) -> None:
    """웹훅으로 메시지 1건을 보낸다. 주소가 없거나 전송이 실패하면 로그만 남긴다."""
    url = get_settings().discord.ingest_webhook_url
    if url is None or not url.get_secret_value().strip():  # .env에 `KEY=`로 비워 둔 경우 포함
        logger.info("[ingest-report] 웹훅 주소 미설정 — 전송 생략")
        return
    try:
        response = httpx.post(url.get_secret_value(), json={"content": content}, timeout=_TIMEOUT_SEC)
        response.raise_for_status()
    except Exception:
        logger.exception("[ingest-report] 웹훅 전송 실패")
