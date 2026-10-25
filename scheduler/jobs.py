"""스케줄러 잡 함수.

각 함수는 APScheduler에 의해 독립적으로 호출된다.
예외가 발생해도 다음 잡 실행에 영향 없도록 내부에서 처리한다.
"""
import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
from io import BytesIO
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote_to_bytes, urlsplit

import httpx
from google.cloud import documentai
from google.oauth2 import service_account
from PIL import Image
from sqlalchemy import and_, or_

from shared.db import get_session
from shared.config import get_settings
from bot import ingest_report
from db.orm import (
    Application, ApplicationEvent, ApplicationQuestion, GeminiUsage, Job, RawPosting, ResumeDraft, User, JobScore,
)
from crawler.base import BaseCrawler, RawJob
from crawler.inthiswork import InthisworkCrawler
from crawler.jasoseol import JasoseolCrawler
from crawler.jobkorea import JobkoreaCrawler
from analysis.profile import build_profile_text
from analysis.scorer import SCORER_VERSION, score_fit, summarize_jd

logger = logging.getLogger(__name__)

# Gemini free tier: 10 RPM → 잡 간 최소 대기
_GEMINI_SLEEP_SEC = 6

# extract_job(summarize_jd)·analyze_job(score_fit)이 공유하는 하루 Gemini 호출 상한.
# gemini-3.5-flash-lite 무료 티어 RPD 500(2026-08-09 AI Studio 실측 —
# E:\00_llm_wiki\wiki\지식\기술\Gemini_API_무료티어_Rate_Limits.md) 중 matchwise 몫으로
# 안전 마진을 두고 400으로 제한한다(2026-10-01 결정).
_GEMINI_DAILY_CAP = 400


def _gemini_calls_today(session) -> int:
    """오늘 두 배치(extract_job, analyze_job)가 합쳐서 호출한 Gemini 횟수."""
    row = session.query(GeminiUsage).filter_by(usage_date=date.today()).first()
    return row.call_count if row else 0


def _record_gemini_call(session) -> None:
    """Gemini 호출 1회를 오늘 날짜 카운터에 반영한다."""
    today = date.today()
    row = session.query(GeminiUsage).filter_by(usage_date=today).first()
    if row is None:
        row = GeminiUsage(usage_date=today, call_count=0)
        session.add(row)
    row.call_count += 1
    session.flush()


def _gemini_budget_exhausted(session) -> bool:
    """오늘 하루 Gemini 호출 한도(_GEMINI_DAILY_CAP)를 다 썼는지."""
    return _gemini_calls_today(session) >= _GEMINI_DAILY_CAP

_OCR_HEADERS = {"User-Agent": "Matchwise/0.2 (personal job-search tool)"}
_CLAUDE_OCR_TIMEOUT_SEC = 120
# Document AI는 가로·세로 중 한 변이라도 10,000px를 넘는 이미지를 거부한다(400 INVALID_ARGUMENT).
# 인디스워크 캡처 공고는 세로 13,000px대도 있어, 넘으면 세로로 잘라 조각마다 OCR한다.
# 조각 경계에 걸린 글줄이 잘리지 않게 조각끼리 _OCR_SLICE_OVERLAP_PX만큼 겹친다(겹친 줄은 중복될 수 있음).
_DOCUMENT_AI_MAX_SIDE_PX = 10_000
_OCR_SLICE_HEIGHT_PX = 9_000
_OCR_SLICE_OVERLAP_PX = 200


# ── Job 0: 정리 ──────────────────────────────────────────────────────────

def cleanup_job() -> None:
    """01:00 KST — 마감(deadline) 지난 공고와 연관 데이터(점수·지원질문·초안) 삭제.

    deadline이 없는 공고(크롤러·추출 모두 마감일을 못 찾은 경우)는 만료 여부를 판단할 수 없어 건드리지 않는다.
    지원 이력(applications)이 있는 공고는 사용자가 추적 중인 기록이라 마감이 지나도 보존한다.
    단 패스(dismissed)만 있는 공고는 추적이 아니므로 지우고, 그 패스 기록(applications·application_events)도 함께 지운다.
    """
    logger.info("[cleanup] 시작")
    session = get_session()
    try:
        now = datetime.now(timezone.utc)
        tracked_job_ids = session.query(Application.job_id).filter(Application.status != "dismissed")
        expired_ids = [
            job_id for (job_id,) in session.query(Job.id)
            .filter(Job.deadline.isnot(None), Job.deadline < now)
            .filter(~Job.id.in_(tracked_job_ids))
            .all()
        ]

        if not expired_ids:
            logger.info("[cleanup] 삭제 대상 없음")
            return

        session.query(JobScore).filter(JobScore.job_id.in_(expired_ids)) \
            .delete(synchronize_session=False)
        dismissed_app_ids = session.query(Application.id).filter(Application.job_id.in_(expired_ids))
        session.query(ApplicationEvent).filter(ApplicationEvent.application_id.in_(dismissed_app_ids)) \
            .delete(synchronize_session=False)
        session.query(Application).filter(Application.job_id.in_(expired_ids)) \
            .delete(synchronize_session=False)
        session.query(ApplicationQuestion).filter(ApplicationQuestion.job_id.in_(expired_ids)) \
            .delete(synchronize_session=False)
        session.query(ResumeDraft).filter(ResumeDraft.job_id.in_(expired_ids)) \
            .delete(synchronize_session=False)
        session.query(Job).filter(Job.id.in_(expired_ids)) \
            .delete(synchronize_session=False)

        session.commit()
        logger.info("[cleanup] 완료 — 마감 지난 공고 %d건 삭제", len(expired_ids))

    except Exception:
        logger.exception("[cleanup] 예기치 않은 오류")
        session.rollback()
    finally:
        session.close()


# ── Job 1: 크롤링 ────────────────────────────────────────────────────────

def _enabled_crawlers() -> tuple[type[BaseCrawler], ...]:
    """수집 대상 사이트. 사이트를 켜고 끄는 곳은 여기 한 곳이다.

    자소설·잡코리아는 2차 저장(extract_job)이 needs_ocr까지 처리하게 된 뒤(2026-09-30)
    등록했다 — 이전엔 이미지 JD가 방치돼 등록을 보류했었다(설계문서 13-2/13절 참고).
    """
    return (InthisworkCrawler, JasoseolCrawler, JobkoreaCrawler)


def _save_raw_posting(session, raw: RawJob) -> None:
    """RawJob을 raw_postings에 해석 없이 그대로 보존한다. (source, url) 기준 insert-if-absent — 멱등.

    jobs와 별개로 채워진다 — jobs에 이미 있는 URL이라도 raw_postings엔 없으면 새로 넣는다.
    """
    exists = session.query(RawPosting).filter_by(source=raw.source, url=raw.url).first()
    if exists:
        return
    session.add(RawPosting(
        source=raw.source,
        url=raw.url,
        apply_url=raw.apply_url,
        title=raw.title,
        company=raw.company,
        deadline=raw.deadline,
        jd_text=raw.jd_text,
        payload=raw.payload,
        hints=raw.hints,
        image_urls=raw.image_urls,
    ))


def _classify_extract_status(raw: RawJob) -> str:
    """jd_text/image_urls 유무로 2차 저장 단계가 처리할 상태를 미리 분류한다.

    공백만 있는 jd_text는 "텍스트 없음"으로 본다.
    """
    if (raw.jd_text or "").strip():
        return "pending"
    if raw.image_urls:
        return "needs_ocr"
    return "empty"


def run_crawl_pipeline() -> None:
    """크롤링 파이프라인: crawl_job → extract_job을 순서대로 실행하고 진행 상황을 웹훅으로 알린다.

    고정 시간에 각각 따로 도는 대신, 이전 단계가 끝나면 곧바로 다음 단계를 함수 호출로 잇는다
    (워크플로우 체인). 스케줄러는 이 함수 하나만 트리거하면 된다 — 새 잡을 추가할 땐 여기에
    호출을 이어붙인다.

    웹훅(bot/ingest_report.py): 시작 → 수집 완료(사이트별 건수) → 추출 완료. 단계가 예외로 멈추면
    에러 메시지를 보내고 종료한다 — 예외는 스케줄러까지 올리지 않는다(다음 날 실행에 영향 없게).
    사이트 하나의 실패는 멈춤이 아니라 수집 완료 메시지에 ⚠️로 표시된다.
    """
    ingest_report.send(ingest_report.format_start(datetime.now(timezone.utc)))

    try:
        runs = crawl_job()
    except Exception as exc:
        ingest_report.send(ingest_report.format_error("수집", exc))
        return
    ingest_report.send(ingest_report.format_crawl_result(runs))

    try:
        stats = extract_job()
    except Exception as exc:
        ingest_report.send(ingest_report.format_error("정리", exc))
        return
    ingest_report.send(ingest_report.format_extract_result(stats))


def _crawl_one_site(session, CrawlerCls: type[BaseCrawler], known_urls: set[str]) -> dict:
    """사이트 1곳을 수집·저장하고 로그·웹훅용 요약을 반환한다.

    크롤러(fetch_jobs) 실패는 이 사이트만 failed로 요약하고 넘어간다. 저장 단계 실패는 다시 올린다.
    seen(목록에서 본 공고, 이미 아는 것 포함)이 0이면 사이트 구조 변경을 의심한다 — new 0은 정상일 수 있다.
    """
    summary = {
        "source": CrawlerCls.source, "status": "ok", "seen": 0, "new": 0,
        "empty": 0, "detail_failed": 0, "error": None,
    }
    try:
        crawler = CrawlerCls()
        raw_jobs = crawler.fetch_jobs(known_urls=known_urls)
        summary["seen"] = crawler.seen_count
        summary["detail_failed"] = crawler.detail_failed_count
    except Exception as exc:
        logger.exception("[crawl] %s 실패", CrawlerCls.source)
        summary["status"] = "failed"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raw_jobs = []

    # known_urls로 이미 걸러졌지만, 동일 크롤링 사이클 내 중복 등 예외 상황에 대한 안전망으로 유지
    for raw in raw_jobs:
        _save_raw_posting(session, raw)

        if session.query(Job).filter_by(url=raw.url).first():
            continue
        status = _classify_extract_status(raw)
        session.add(Job(
            source=raw.source,
            url=raw.url,
            title=raw.title,
            company=raw.company,
            jd_text=raw.jd_text,
            deadline=raw.deadline,
            extract_status=status,
        ))
        summary["new"] += 1
        if status == "empty":
            summary["empty"] += 1

    session.flush()
    logger.info(
        "[crawl] %s: %s · 목록 %d · 신규 %d · 빈 공고 %d · 상세 실패 %d",
        summary["source"], summary["status"], summary["seen"], summary["new"],
        summary["empty"], summary["detail_failed"],
    )
    return summary


def crawl_job() -> list[dict]:
    """신규 공고 수집 후 DB에 저장 (URL 기준 중복 제외). run_crawl_pipeline이 호출한다(08:00 KST 체인 1단계).

    크롤링 전에 기존 URL 전체를 한 번만 조회해 크롤러에 넘긴다 — 각 크롤러는
    이미 아는 URL의 상세 페이지 요청 자체를 생략해 불필요한 스크래핑을 줄인다.

    수집된 공고는 두 곳에 저장된다:
    - raw_postings: 원본을 해석 없이 그대로 보존 (1차 적재)
    - jobs: 기존처럼 6개 필드 + 2차 저장 대상 여부를 나타내는 extract_status
    사이트별 결과는 로그(logs/*.log)와 반환값(웹훅 메시지용)으로 남는다.

    Returns:
        사이트별 요약 목록 — {source, status, seen, new, empty, detail_failed, error}.

    Raises:
        Exception: 사이트 1곳의 크롤러 실패가 아닌 예기치 않은 오류(DB 등). 롤백 후 다시 올린다.
    """
    logger.info("[crawl] 시작")
    session = get_session()
    try:
        known_urls = {url for (url,) in session.query(Job.url).all()}
        summaries = [
            _crawl_one_site(session, CrawlerCls, known_urls) for CrawlerCls in _enabled_crawlers()
        ]
        session.commit()
        logger.info("[crawl] 완료 — 신규 %d건 저장", sum(s["new"] for s in summaries))
        return summaries

    except Exception:
        logger.exception("[crawl] 예기치 않은 오류")
        session.rollback()
        raise
    finally:
        session.close()


# ── Job 1.5: 2차 저장(추출) ─────────────────────────────────────────────

def _ocr_image(image_url: str) -> str:
    """이미지 1장을 OCR해 텍스트를 반환한다. 백엔드는 settings.ocr_provider로 고른다.

    - "document_ai"(기본): Google Cloud Document AI, 이미지 바이트를 바로 전달
    - "claude_code": Claude Code 헤드리스(-p) 모드, 구독 재사용. sonnet 고정(haiku는 환각 확인됨)
    """
    provider = get_settings().ocr_provider
    if provider == "document_ai":
        return _ocr_image_document_ai(image_url)
    if provider == "claude_code":
        return _ocr_image_claude_code(image_url)
    raise ValueError(f"알 수 없는 ocr_provider: {provider!r}")


def _is_svg(image_url: str) -> bool:
    """SVG(도형 코드)인지. Document AI가 받지 않고 보통 아이콘·로고라 OCR 대상에서 뺀다."""
    if image_url.startswith("data:"):
        return image_url[5:].split(",", 1)[0].lower().startswith("image/svg")
    return urlsplit(image_url).path.lower().endswith(".svg")


def _ocr_target(image_urls: list[str] | None) -> str | None:
    """OCR할 이미지 — SVG를 건너뛴 첫 이미지. 없으면 None."""
    return next((url for url in image_urls or [] if not _is_svg(url)), None)


def _fetch_image(image_url: str) -> tuple[bytes, str]:
    """이미지 바이트와 MIME 타입을 구한다.

    본문에 이미지 데이터를 통째로 넣은 data: 주소(`data:[MIME][;base64],[내용]`)는 내려받지 않고 바로 푼다 —
    주소로 요청하면 'URL too long'으로 실패한다(2026-10-25 실제 사례). ;base64가 있으면 base64, 없으면 퍼센트
    인코딩으로 푼다. 일반 주소는 내려받고 리다이렉트를 따라간다. MIME을 모르면 image/png로 본다.
    """
    if image_url.startswith("data:"):
        header, _, data = image_url[5:].partition(",")
        mime_type = header.split(";", 1)[0] or "image/png"
        if ";base64" in header.lower():
            return base64.b64decode(data), mime_type
        return unquote_to_bytes(data), mime_type

    response = httpx.get(image_url, headers=_OCR_HEADERS, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.content, response.headers.get("content-type") or "image/png"


def _image_parts_for_document_ai(content: bytes, mime_type: str) -> list[tuple[bytes, str]]:
    """Document AI 크기 제한(한 변 10,000px)에 맞게 이미지를 (바이트, MIME) 조각 목록으로 만든다.

    제한 안이면 원본 그대로 1조각. 넘으면 가로를 제한 안으로 줄인 뒤 세로로 잘라 PNG 조각들로 만든다.
    이미지로 열 수 없는 바이트(예: PDF)는 판단하지 않고 그대로 넘긴다 — 거부 여부는 Document AI가 정한다.
    """
    try:
        image = Image.open(BytesIO(content))
        width, height = image.size
    except Exception:
        return [(content, mime_type)]
    if max(width, height) <= _DOCUMENT_AI_MAX_SIDE_PX:
        return [(content, mime_type)]

    if width > _DOCUMENT_AI_MAX_SIDE_PX:
        height = round(height * _DOCUMENT_AI_MAX_SIDE_PX / width)
        width = _DOCUMENT_AI_MAX_SIDE_PX
        image = image.resize((width, height))

    parts = []
    top = 0
    while True:
        bottom = min(top + _OCR_SLICE_HEIGHT_PX, height)
        buffer = BytesIO()
        image.crop((0, top, width, bottom)).save(buffer, format="PNG")
        parts.append((buffer.getvalue(), "image/png"))
        if bottom == height:
            return parts
        top = bottom - _OCR_SLICE_OVERLAP_PX


def _document_ai_client() -> documentai.DocumentProcessorServiceClient:
    """Document AI 클라이언트를 만든다.

    GCP_SA_KEY_JSON(GSA 키 JSON 내용)이 있으면 그 키로 인증한다 — GCP 밖이라 ADC가 없는 Railway 운영용
    (2026-10-23). 없거나 비어 있으면 자격증명을 넘기지 않아 SDK가 ADC(로컬 위장 로그인)를 찾는다.
    """
    key_json = get_settings().gcp.sa_key_json
    if key_json is None or not key_json.get_secret_value().strip():
        return documentai.DocumentProcessorServiceClient()
    info = json.loads(key_json.get_secret_value())
    credentials = service_account.Credentials.from_service_account_info(info)
    return documentai.DocumentProcessorServiceClient(credentials=credentials)


def _ocr_image_document_ai(image_url: str) -> str:
    """이미지 1장을 Google Cloud Document AI로 OCR해 텍스트를 반환한다.

    인증은 _document_ai_client()가 고른다(운영은 GSA 키, 로컬은 ADC). 이미지는 _fetch_image()로 구하고(data: 주소 포함),
    10,000px를 넘는 긴 이미지는 조각으로 나눠 OCR한 뒤 줄바꿈으로 이어 붙인다.
    """
    content, mime_type = _fetch_image(image_url)

    settings = get_settings().gcp
    client = _document_ai_client()
    name = client.processor_path(settings.project_id, settings.project_location, settings.doc_processor_id)
    parts = _image_parts_for_document_ai(content, mime_type)
    texts = []
    for content, mime_type in parts:
        raw_document = documentai.RawDocument(content=content, mime_type=mime_type)
        result = client.process_document(request=documentai.ProcessRequest(name=name, raw_document=raw_document))
        texts.append(result.document.text)
    return "\n".join(texts)


def _ocr_image_claude_code(image_url: str) -> str:
    """이미지 1장을 Claude Code 헤드리스(-p) 모드로 OCR해 텍스트를 반환한다.

    구독 로그인을 재사용한다(--bare 안 씀 — bare는 ANTHROPIC_API_KEY가 필수라 구독 재사용이 안 됨).
    Read 툴은 로컬 파일 경로만 다루므로 이미지를 임시 파일로 받아 그 경로를 프롬프트에 넣는다.
    모델은 claude_ocr_model(기본 sonnet) 고정 — haiku는 2026-09-30 실측에서 회사명을 완전히
    다르게 지어내는 환각을 일으켜 쓰지 않는다.
    """
    content, _ = _fetch_image(image_url)

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    with open(tmp_path, "wb") as f:
        f.write(content)

    try:
        model = get_settings().claude_ocr_model
        prompt = (
            "이 이미지에 있는 텍스트를 최대한 원문 그대로 옮겨 적어줘. "
            "다른 설명 없이 텍스트만 출력해줘.\n\n"
            f"이미지 경로: {tmp_path}"
        )
        proc = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", "Read", "--model", model, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_OCR_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p 실패 (exit {proc.returncode}): {proc.stderr[:500]}")

        data = json.loads(proc.stdout)
        if data.get("is_error"):
            raise RuntimeError(f"claude -p 응답이 is_error=true: {str(data)[:500]}")
        return data.get("result", "")
    finally:
        os.remove(tmp_path)


_KST = timezone(timedelta(hours=9))


def _retry_window_start(now: datetime) -> datetime:
    """failed·needs_ocr 재시도 기간의 시작 — 어제 0시(KST)를 UTC로.

    "첫 실패 포함 2일"(2026-10-14 결정): 추출은 수집 직후 같은 실행에서 돌기 때문에 수집한 날이 곧 첫 시도일이다.
    그래서 실패 날짜를 따로 저장하지 않고 jobs.crawled_at이 어제 0시 이후인 공고만 다시 시도한다
    (수집한 날 + 다음 날). 같은 날 수동 실행을 여러 번 해도 시도 일수는 늘지 않는다.
    """
    today_kst = now.astimezone(_KST).date()
    start = datetime(today_kst.year, today_kst.month, today_kst.day, tzinfo=_KST) - timedelta(days=1)
    return start.astimezone(timezone.utc)


def extract_job() -> dict:
    """crawl_job 직후 실행 — pending/failed/needs_ocr 공고를 추출해 jd_* 필드·eligibility·근무지를 채운다.

    needs_ocr는 SVG를 뺀 첫 이미지(_ocr_target)를 OCR(_ocr_image — settings.ocr_provider로 백엔드 선택)해
    jobs.jd_text를 채운 뒤(raw_postings는 안 건드림), 곧바로 같은 실행에서 pending과 동일하게 추출까지
    진행한다.
    - OCR 자체가 실패하면(이미지 없음 포함) 상태를 needs_ocr로 그대로 둬 다음 실행에 재시도한다
      (Gemini를 호출하지 않았으므로 레이트리밋 sleep도 안 함).
    - OCR은 됐는데 그 뒤 LLM 추출이 실패하면 failed로 넘어간다 — 다음 실행은 이미 채워진
      jd_text로 pending/failed 경로를 타므로 OCR을 다시 하지 않는다.

    재시도 기간: failed·needs_ocr는 수집한 날과 다음 날까지만 대상이다(_retry_window_start). 기간이 지난
    needs_ocr는 failed로 바꿔 포기하고, 기간이 지난 failed는 그대로 둔다. pending은 실패가 아니라 대기
    (하루 Gemini 한도에 밀린 것 등)라 기간 제한 없이 처리한다.

    Returns:
        웹훅용 건수 — {ok, failed, ocr_failed, ocr_expired, remaining(하루 한도로 남은 대상)}.

    Raises:
        Exception: 공고 1건의 처리 실패가 아닌 예기치 않은 오류(DB 등). 롤백 후 다시 올린다.
    """
    logger.info("[extract] 시작")
    stats = {"ok": 0, "failed": 0, "ocr_failed": 0, "ocr_expired": 0, "remaining": 0}
    session = get_session()
    try:
        window_start = _retry_window_start(datetime.now(timezone.utc))
        stats["ocr_expired"] = session.query(Job).filter(
            Job.extract_status == "needs_ocr",
            or_(Job.crawled_at < window_start, Job.crawled_at.is_(None)),
        ).update({Job.extract_status: "failed"}, synchronize_session=False)
        if stats["ocr_expired"]:
            logger.info("[extract] 재시도 기간이 지난 needs_ocr %d건 → failed", stats["ocr_expired"])

        targets = session.query(Job).filter(or_(
            Job.extract_status == "pending",
            and_(Job.extract_status.in_(("failed", "needs_ocr")), Job.crawled_at >= window_start),
        )).all()
        if not targets:
            session.commit()
            logger.info("[extract] 대상 없음")
            return stats

        for index, job in enumerate(targets):
            if _gemini_budget_exhausted(session):
                stats["remaining"] = len(targets) - index
                logger.warning(
                    "[extract] 오늘 Gemini 호출 한도(%d건) 도달 — 남은 %d건은 다음 실행에 처리",
                    _GEMINI_DAILY_CAP, stats["remaining"],
                )
                break

            raw = session.query(RawPosting).filter_by(source=job.source, url=job.url).first()
            hints = raw.hints if raw else None

            ocr_url = _ocr_target(raw.image_urls if raw else None)
            ocr_done = False
            if job.extract_status == "needs_ocr":
                if not ocr_url:
                    logger.warning(
                        "[extract] needs_ocr인데 OCR할 이미지가 없음(SVG 제외) job_id=%s — 다음 실행에 재시도",
                        job.id,
                    )
                    stats["ocr_failed"] += 1
                    continue
                try:
                    job.jd_text = _ocr_image(ocr_url)
                    session.flush()
                    ocr_done = True
                except Exception:
                    logger.exception("[extract] OCR 실패 job_id=%s — 다음 실행에 재시도", job.id)
                    stats["ocr_failed"] += 1
                    continue

            try:
                _record_gemini_call(session)
                summary = summarize_jd(job.jd_text or "", hints=hints)
                # 텍스트가 한 줄(회사명·접수기한 등)이라도 있으면 pending으로 분류되지만 본문은 이미지인 공고가 있다
                # (2026-10-14 실제 사례). 요약에 하는 일·자격요건이 모두 비고 이미지가 있으면 OCR로 보완해 다시 요약한다.
                if (not summary["tasks"] and not summary["qualifications"] and ocr_url
                        and not ocr_done and not _gemini_budget_exhausted(session)):
                    try:
                        ocr_text = _ocr_image(ocr_url)
                    except Exception:
                        logger.exception("[extract] 보완 OCR 실패 job_id=%s — 텍스트 요약 결과로 저장", job.id)
                        stats["ocr_failed"] += 1
                    else:
                        logger.info("[extract] 텍스트 요약이 비어 이미지 OCR로 보완 job_id=%s", job.id)
                        job.jd_text = f"{job.jd_text or ''}\n\n{ocr_text}".strip()
                        time.sleep(_GEMINI_SLEEP_SEC)
                        _record_gemini_call(session)
                        summary = summarize_jd(job.jd_text, hints=hints)
                job.jd_tasks = summary["tasks"]
                job.jd_stack = summary["stack"]
                job.jd_employment_type = summary["employment_type"]
                job.jd_qualifications = summary["qualifications"]
                job.jd_preferred = summary["preferred"]
                job.eligibility = summary["eligibility"]
                job.work_locations = summary["locations"]
                # 목록에 마감일이 없는 사이트(인디스워크)는 본문·OCR에 적힌 마감일로 채운다. 크롤러 값이 있으면 그대로 둔다.
                if job.deadline is None and summary.get("deadline") is not None:
                    job.deadline = summary["deadline"]
                job.extract_status = "ok"
                stats["ok"] += 1
            except Exception:
                logger.exception("[extract] 추출 실패 job_id=%s", job.id)
                job.extract_status = "failed"
                stats["failed"] += 1
            session.flush()
            time.sleep(_GEMINI_SLEEP_SEC)

        session.commit()
        logger.info(
            "[extract] 완료 — 성공 %d건, 실패 %d건, OCR 실패 %d건",
            stats["ok"], stats["failed"], stats["ocr_failed"],
        )
        return stats

    except Exception:
        logger.exception("[extract] 예기치 않은 오류")
        session.rollback()
        raise
    finally:
        session.close()


# ── Job 2: 분석 ──────────────────────────────────────────────────────────

# 채점 프롬프트에 넣지 않는 basic_info 항목 — 적합도와 무관하고 토큰만 쓴다(2026-10-14 결정).
_SCORING_EXCLUDED_INFO = ("이름", "링크")

# 채점할 근무지. work_locations 중 하나라도 이 키워드를 포함하면 대상이다.
# 판교는 분당구 안이라 "분당"으로도 잡히지만, "성남시 판교로"처럼 구 이름 없이 적힌 주소가 있어 따로 둔다.
_TARGET_REGION_KEYWORDS = ("서울", "과천", "판교", "분당")


def _is_scoring_target(job: Job) -> bool:
    """채점(Gemini 호출) 전에 신입 지원 가능 여부·근무지로 공고를 거른다.

    2026-10-14 결정: 판단 정보가 없으면 채점한다 — 명시적으로 안 맞는 공고만 뺀다.
    - 하는 일(jd_tasks): 비어 있으면 제외. 공고 본문이 없어(이미지만 있는데 OCR 실패 등) 판단 근거가 없는 경우로,
      그대로 채점하면 LLM이 "명시된 요건이 없으니 다 충족"으로 보고 만점을 준다(2026-10-14 실제 사례).
    - 신입: eligibility.new_grad_ok가 False(예: "경력 3년 이상 필수")일 때만 제외. 경력무관은 True로 추출된다.
    - 근무지: work_locations가 있는데 서울·과천·판교·분당이 하나도 없을 때만 제외. 미기재([]/None)는 채점.
    """
    if not job.jd_tasks:
        return False
    if (job.eligibility or {}).get("new_grad_ok") is False:
        return False
    locations = job.work_locations or []
    if locations and not any(kw in loc for loc in locations for kw in _TARGET_REGION_KEYWORDS):
        return False
    return True


def _apply_score(row: JobScore, result: dict, user: User, now: datetime) -> None:
    """score_fit 결과와 채점 버전을 점수 줄에 쓴다. 새 줄·재채점 모두 이 함수로 쓴다."""
    row.fit_score = result["fit_score"]
    row.fit_reasons = result.get("fit_reasons", "")
    row.fit_detail = result.get("fit_detail")
    row.profile_version = user.profile_version
    row.scorer_version = SCORER_VERSION
    row.scored_at = now
    row.rescore_requested_at = None


def analyze_job() -> None:
    """08:00 KST 체인 2단계 — (Job × User) 조합 fit_score 계산.

    1) 신규: extract_job이 추출을 끝낸(extract_status='ok') 공고 중 _is_scoring_target을 통과했고 아직 점수가 없는
       (공고, 유저) 조합을 채점한다. pending·failed는 카드 내용과 신입 여부를 몰라, needs_ocr/empty는 jd_text가 비어 제외.
    2) 재채점 요청: 사용자가 웹에서 요청한(rescore_requested_at) 점수를 요청 순서대로 다시 채점한다. 줄은 그대로 두고
       값만 덮어쓰므로 notified_at이 유지돼 알림이 다시 나가지 않는다. 오늘 새 추천이 먼저라 신규 다음에 처리한다.
    둘 다 하루 Gemini 한도 안에서만 하고, 남은 것은 다음 실행에 이어서 한다.
    프로필이 바뀌어도 기존 점수를 자동으로 다시 채점하지 않는다 — 구 점수로 표시만 된다(2026-10-15 결정).
    유저 쪽 근거는 users.basic_info + experiences(analysis/profile.py) — 둘 다 비어 있으면 그 유저는 건너뛴다.
    company_score는 당분간 비활성화 (analysis/scorer.py 참고).
    """
    logger.info("[analyze] 시작")
    session = get_session()

    try:
        users = session.query(User).filter_by(is_active=True).all()
        if not users:
            logger.info("[analyze] 활성 유저 없음, 종료")
            return

        # 이력(basic_info·experiences)이 하나도 없는 유저는 채점하지 않는다 — 공고만 보고 매긴 점수가
        # 저장되면 이미 채점한 조합으로 취급돼 이력을 넣은 뒤에도 다시 채점되지 않기 때문.
        profiles = {}
        for user in users:
            text = build_profile_text(user.basic_info, user.experiences, _SCORING_EXCLUDED_INFO)
            if text:
                profiles[user.id] = text
            else:
                logger.warning("[analyze] user_id=%s 이력 없음 — 채점 건너뜀 (scripts/import_profile.py)", user.id)
        users = [user for user in users if user.id in profiles]
        if not users:
            logger.info("[analyze] 이력이 있는 유저 없음, 종료")
            return

        extracted = session.query(Job).filter(Job.extract_status == "ok").all()
        all_jobs = [job for job in extracted if _is_scoring_target(job)]
        logger.info(
            "[analyze] 추출 완료 %d건 중 신입·근무지 조건 통과 %d건", len(extracted), len(all_jobs),
        )
        total, skipped, failed = 0, 0, 0
        budget_exhausted = False

        for job in all_jobs:
            if budget_exhausted:
                break
            for user in users:
                already = session.query(JobScore).filter_by(
                    job_id=job.id, user_id=user.id
                ).first()
                if already:
                    skipped += 1
                    continue

                if _gemini_budget_exhausted(session):
                    logger.warning(
                        "[analyze] 오늘 Gemini 호출 한도(%d건) 도달 — 남은 조합은 다음 실행에 처리",
                        _GEMINI_DAILY_CAP,
                    )
                    budget_exhausted = True
                    break

                try:
                    _record_gemini_call(session)
                    result = score_fit(jd_text=job.jd_text or "", profile_text=profiles[user.id])
                    row = JobScore(job_id=job.id, user_id=user.id)
                    _apply_score(row, result, user, datetime.now(timezone.utc))
                    session.add(row)
                    session.flush()
                    total += 1
                    time.sleep(_GEMINI_SLEEP_SEC)

                except Exception:
                    logger.exception(
                        "[analyze] 점수 계산 실패 job_id=%s user_id=%s",
                        job.id, user.id,
                    )
                    failed += 1

        rescored = 0
        users_by_id = {user.id: user for user in users}
        requests = (
            session.query(JobScore)
            .filter(JobScore.rescore_requested_at.isnot(None), JobScore.user_id.in_(users_by_id))
            .order_by(JobScore.rescore_requested_at.asc())
            .all()
        )
        for row in requests:
            if budget_exhausted or _gemini_budget_exhausted(session):
                logger.warning("[analyze] Gemini 한도 도달 — 재채점 요청 %d건은 다음 실행에 처리", len(requests) - rescored)
                break
            user = users_by_id[row.user_id]
            try:
                _record_gemini_call(session)
                result = score_fit(jd_text=row.job.jd_text or "", profile_text=profiles[user.id])
                _apply_score(row, result, user, datetime.now(timezone.utc))
                session.flush()
                rescored += 1
                time.sleep(_GEMINI_SLEEP_SEC)
            except Exception:
                logger.exception("[analyze] 재채점 실패 job_id=%s user_id=%s", row.job_id, row.user_id)
                failed += 1

        session.commit()
        logger.info(
            "[analyze] 완료 — 신규 %d건, 재채점 %d건, 스킵 %d건, 실패 %d건",
            total, rescored, skipped, failed,
        )

    except Exception:
        logger.exception("[analyze] 예기치 않은 오류")
        session.rollback()
    finally:
        session.close()


# ── Job 3: Discord 알림 ──────────────────────────────────────────────────

def notify_job() -> None:
    """08:00 KST 체인 3단계 — 유저별 매칭 채널에 아직 안 보낸 추천 공고 발송 (bot/notifications.py)."""
    logger.info("[notify] 시작")
    try:
        from bot.notifications import send_daily_notifications
        asyncio.run(send_daily_notifications())
        logger.info("[notify] 완료")
    except Exception:
        logger.exception("[notify] 실패")


# ── Job 4: 비서 DM ───────────────────────────────────────────────────────

def assistant_job() -> None:
    """매칭 알림 직후 — 유저별 DM으로 마감 리마인드(매일)와 3일 현황 요약(3일마다) 발송 (bot/assistant.py)."""
    logger.info("[assistant] 시작")
    try:
        from bot.assistant import send_assistant_messages
        asyncio.run(send_assistant_messages())
        logger.info("[assistant] 완료")
    except Exception:
        logger.exception("[assistant] 실패")


# ── 하루 1회 체인 ────────────────────────────────────────────────────────

def run_daily_pipeline() -> None:
    """08:00 KST — 수집·추출 → 채점 → 매칭 알림 → 비서 DM을 한 번에 이어서 실행한다(2026-10-23 결정).

    단계마다 고정 시간에 따로 돌던 것(02·05·09시)을 함수 호출 체인으로 합쳤다. 각 단계는 예외를
    안에서 처리하고 돌아오므로, 수집이 멈춰도 이미 쌓인 공고로 채점·알림은 그대로 진행된다.
    """
    run_crawl_pipeline()
    analyze_job()
    notify_job()
    assistant_job()
