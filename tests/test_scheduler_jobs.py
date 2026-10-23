"""스케줄러 잡 유닛 테스트.

크롤러/스코어러/DB를 모두 mock으로 대체해
잡 함수의 분기 로직(중복 제외, 실패 격리 등)만 검증한다.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from crawler.base import RawJob
from db.orm import (
    Application, ApplicationQuestion, Experience, GeminiUsage, Job, RawPosting, ResumeDraft, User, JobScore,
)
from scheduler.jobs import (
    _GEMINI_DAILY_CAP, _is_scoring_target, _retry_window_start, cleanup_job, crawl_job, extract_job,
    analyze_job, run_crawl_pipeline, run_daily_pipeline,
)


# ── crawl_job ────────────────────────────────────────────────────────────
#
# get_session()을 실제 in-memory SQLite 세션(conftest의 session 픽스처)으로 바꿔치기해서,
# 목 체인을 일일이 맞추지 않고 실제 조회 결과로 검증한다.

def _crawler_class(
    jobs=None, source: str = "a", error: Exception | None = None, on_call=None,
    seen: int | None = None, detail_failed: int = 0,
):
    """crawl_job()이 호출하는 크롤러 클래스를 흉내낸다.

    on_call: (source, known_urls)를 기록하고 싶을 때 넘기는 콜백 — known_urls 전달 검증용.
    seen/detail_failed: 수집 기록용 카운터. seen 기본값은 돌려주는 공고 수.
    """

    class _FakeCrawler:
        def __init__(self):
            self.seen_count = 0
            self.detail_failed_count = 0

        def fetch_jobs(self, limit=None, known_urls=None):
            if on_call is not None:
                on_call(source, known_urls)
            if error is not None:
                raise error
            self.seen_count = len(jobs or []) if seen is None else seen
            self.detail_failed_count = detail_failed
            return jobs or []

    _FakeCrawler.source = source
    return _FakeCrawler


def _raw(
    url: str,
    *,
    source: str = "inthiswork",
    title: str = "Backend Dev",
    company: str = "Acme",
    jd_text: str = "본문",
    deadline=None,
    apply_url: str | None = None,
    payload: dict | None = None,
    hints: dict | None = None,
    image_urls: list[str] | None = None,
) -> RawJob:
    return RawJob(
        source=source, url=url, title=title, company=company, jd_text=jd_text,
        deadline=deadline, apply_url=apply_url, payload=payload or {},
        hints=hints or {}, image_urls=image_urls or [],
    )


def test_enabled_crawlers_are_inthiswork_jasoseol_jobkorea():
    """수집 대상 사이트는 _enabled_crawlers() 한 곳에서 켜고 끈다. 슈퍼루키는 비활성화(삭제)."""
    from crawler.inthiswork import InthisworkCrawler
    from crawler.jasoseol import JasoseolCrawler
    from crawler.jobkorea import JobkoreaCrawler
    from scheduler.jobs import _enabled_crawlers

    assert _enabled_crawlers() == (InthisworkCrawler, JasoseolCrawler, JobkoreaCrawler)


def test_crawl_job_saves_new_deduplicates(session):
    """신규 URL만 INSERT하고 중복은 스킵한다."""
    session.add(Job(source="inthiswork", url="https://dup.com/2", title="old", company="Acme"))
    session.commit()

    raw_new = _raw("https://new.com/1")
    raw_dup = _raw("https://dup.com/2")

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(_crawler_class([raw_new, raw_dup]),)):
        crawl_job()

    assert {j.url for j in session.query(Job).all()} == {"https://new.com/1", "https://dup.com/2"}
    assert session.query(Job).filter_by(url="https://dup.com/2").count() == 1  # 중복 INSERT 안 됨


def test_crawl_job_continues_if_one_crawler_fails(session):
    """한 크롤러가 실패해도 나머지 크롤러는 정상 실행된다."""
    failing = _crawler_class(error=RuntimeError("네트워크 오류"), source="a")
    working = _crawler_class([_raw("https://b.com/1", source="b")], source="b")

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(failing, working)):
        crawl_job()  # 예외 없이 완료되어야 함

    assert session.query(Job).filter_by(url="https://b.com/1").first() is not None


def test_crawl_job_no_new_jobs(session):
    """수집된 공고가 전부 중복이면 새로 추가되지 않는다."""
    session.add(Job(source="inthiswork", url="https://dup.com/1", title="old", company="Acme"))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers",
               return_value=(_crawler_class([_raw("https://dup.com/1")]),)):
        crawl_job()

    assert session.query(Job).filter_by(url="https://dup.com/1").count() == 1


def test_crawl_job_passes_known_urls_to_crawlers(session):
    """DB에 이미 있는 URL 전체를 한 번에 조회해 각 크롤러에 known_urls로 넘긴다."""
    session.add(Job(source="inthiswork", url="https://existing.com/1", title="old", company="Acme"))
    session.commit()

    calls = []
    first = _crawler_class(source="a", on_call=lambda s, u: calls.append((s, u)))
    second = _crawler_class(source="b", on_call=lambda s, u: calls.append((s, u)))

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(first, second)):
        crawl_job()

    expected = {"https://existing.com/1"}
    assert calls == [("a", expected), ("b", expected)]


# ── crawl_job: raw_postings 1차 적재 ────────────────────────────────────────

def test_crawl_job_saves_raw_posting_with_full_payload(session):
    """RawJob의 apply_url/payload/hints/image_urls까지 raw_postings에 그대로 보존한다."""
    raw = _raw(
        "https://new.com/1",
        apply_url="https://company.com/apply",
        payload={"raw": "data"},
        hints={"positions": [{"career_level": "신입"}]},
        image_urls=["https://img.example.com/1.png"],
    )
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(_crawler_class([raw]),)):
        crawl_job()

    saved = session.query(RawPosting).filter_by(source="inthiswork", url="https://new.com/1").first()
    assert saved is not None
    assert saved.apply_url == "https://company.com/apply"
    assert saved.payload == {"raw": "data"}
    assert saved.hints == {"positions": [{"career_level": "신입"}]}
    assert saved.image_urls == ["https://img.example.com/1.png"]


def test_crawl_job_raw_posting_insert_is_idempotent(session):
    """이미 raw_postings에 있는 (source, url)은 재실행해도 다시 쌓이지 않는다."""
    raw = _raw("https://new.com/1")
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(_crawler_class([raw]),)):
        crawl_job()
        crawl_job()  # 같은 결과를 다시 반환해도(테스트 크롤러는 known_urls를 무시함)

    assert session.query(RawPosting).filter_by(source="inthiswork", url="https://new.com/1").count() == 1


def test_save_raw_posting_skips_add_when_already_exists(session):
    """_save_raw_posting()은 DB의 UniqueConstraint에 기대지 않고, 조회로 먼저 걸러 add 자체를 안 한다.

    (add를 또 하면 같은 세션·트랜잭션에서 커밋할 때 다른 신규 공고까지 함께 롤백될 위험이 있다.)
    """
    from scheduler.jobs import _save_raw_posting

    raw = _raw("https://new.com/1")
    _save_raw_posting(session, raw)
    session.commit()

    _save_raw_posting(session, raw)
    assert len(session.new) == 0  # 두 번째 호출은 session.add() 자체를 호출하지 않는다


def test_crawl_job_keeps_raw_posting_for_url_already_known_as_job(session):
    """jobs에 이미 있는 URL도 raw_postings엔 없으면 보존한다 — 두 테이블은 독립적으로 채워진다."""
    session.add(Job(source="inthiswork", url="https://dup.com/2", title="old", company="Acme"))
    session.commit()

    raw_dup = _raw("https://dup.com/2", payload={"raw": "data"})
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(_crawler_class([raw_dup]),)):
        crawl_job()

    assert session.query(RawPosting).filter_by(source="inthiswork", url="https://dup.com/2").first() is not None


# ── crawl_job: extract_status 자동 분류 ─────────────────────────────────────

@pytest.mark.parametrize(
    "jd_text, image_urls, expected_status",
    [
        ("담당업무: API 개발", [], "pending"),
        ("", ["https://img.example.com/1.png"], "needs_ocr"),
        ("", [], "empty"),
        ("   \n\t  ", ["https://img.example.com/1.png"], "needs_ocr"),  # 공백뿐인 텍스트는 "없음"
        ("   \n\t  ", [], "empty"),
    ],
)
def test_crawl_job_classifies_extract_status(session, jd_text, image_urls, expected_status):
    raw = _raw("https://new.com/1", jd_text=jd_text, image_urls=image_urls)
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(_crawler_class([raw]),)):
        crawl_job()

    job = session.query(Job).filter_by(url="https://new.com/1").first()
    assert job.extract_status == expected_status


def test_job_extract_status_defaults_to_pending_outside_crawl_job(session):
    """crawl_job을 거치지 않고 Job을 직접 만들어도(기존 코드 경로) 기본값은 pending이다."""
    job = Job(source="inthiswork", url="https://direct.com/1", title="t", company="c")
    session.add(job)
    session.commit()

    assert session.query(Job).filter_by(url="https://direct.com/1").first().extract_status == "pending"


# ── crawl_job: 사이트별 수집 요약(로그·웹훅용) ───────────────────────────────────────

def test_crawl_job_returns_per_site_summaries(session):
    session.add(Job(source="a", url="https://a.com/known", title="old", company="c"))
    session.commit()
    site_a = _crawler_class(
        [_raw("https://a.com/1", source="a"),
         _raw("https://a.com/2", source="a", jd_text=""),          # 본문·이미지 없음 → 빈 공고
         _raw("https://a.com/known", source="a")],                 # 안전망에 걸리는 중복 → 신규 아님
        source="a", seen=7, detail_failed=1,
    )
    site_b = _crawler_class(source="b", error=RuntimeError("네트워크 오류"))

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(site_a, site_b)):
        summaries = crawl_job()

    assert summaries == [
        {"source": "a", "status": "ok", "seen": 7, "new": 2, "empty": 1, "detail_failed": 1, "error": None},
        {"source": "b", "status": "failed", "seen": 0, "new": 0, "empty": 0, "detail_failed": 0,
         "error": "RuntimeError: 네트워크 오류"},
    ]


def test_crawl_job_raises_after_rollback_on_unexpected_error(session):
    """사이트 하나의 에러가 아닌 저장 단계 에러는 파이프라인이 알 수 있게 다시 올린다."""
    site = _crawler_class([_raw("https://a.com/1", source="a")], source="a")
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._enabled_crawlers", return_value=(site,)), \
         patch("scheduler.jobs._save_raw_posting", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError, match="disk full"):
            crawl_job()

    assert session.query(Job).count() == 0


# ── extract_job: 재시도 기간(수집한 날 + 다음 날) ─────────────────────────────

_KST = timezone(timedelta(hours=9))


def test_retry_window_starts_at_midnight_of_yesterday_kst():
    now = datetime(2026, 10, 14, 2, 0, tzinfo=_KST)
    assert _retry_window_start(now) == datetime(2026, 10, 13, 0, 0, tzinfo=_KST)


def test_extract_job_retries_failed_only_until_the_day_after_crawl(session):
    """첫 실패 포함 2일: 수집한 날과 다음 날까지만 재시도하고, 그 뒤엔 failed로 남겨 둔다."""
    now = datetime.now(timezone.utc)
    recent = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문",
                 extract_status="failed", crawled_at=now)
    old = Job(source="a", url="https://a.com/2", title="t", company="c", jd_text="본문",
              extract_status="failed", crawled_at=_retry_window_start(now) - timedelta(minutes=1))
    session.add_all([recent, old])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    assert mock_sum.call_count == 1
    statuses = {j.url: j.extract_status for j in session.query(Job).all()}
    assert statuses == {"https://a.com/1": "ok", "https://a.com/2": "failed"}


def test_extract_job_gives_up_needs_ocr_past_window_as_failed_without_ocr(session):
    now = datetime.now(timezone.utc)
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="",
                    extract_status="needs_ocr", crawled_at=now - timedelta(days=3)))
    session.add(RawPosting(source="a", url="https://a.com/1", jd_text="", image_urls=["https://img/1.png"]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image") as ocr, \
         patch("scheduler.jobs.summarize_jd") as mock_sum:
        stats = extract_job()

    ocr.assert_not_called()
    mock_sum.assert_not_called()
    assert session.query(Job).first().extract_status == "failed"
    assert stats["ocr_expired"] == 1


def test_extract_job_processes_pending_regardless_of_age(session):
    """pending은 실패가 아니라 대기(예: 하루 한도에 걸려 밀림)라 기간 제한을 걸지 않는다."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문",
                    extract_status="pending", crawled_at=datetime.now(timezone.utc) - timedelta(days=10)))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    assert session.query(Job).first().extract_status == "ok"


def test_extract_job_returns_counts_for_report(session):
    session.add_all([
        Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="pending"),
        Job(source="a", url="https://a.com/2", title="t", company="c", jd_text="본문", extract_status="pending"),
        Job(source="a", url="https://a.com/3", title="t", company="c", jd_text="", extract_status="needs_ocr"),
    ])
    session.add(RawPosting(source="a", url="https://a.com/3", jd_text="", image_urls=["https://img/3.png"]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", side_effect=[_EMPTY_SUMMARY, RuntimeError("Gemini 오류")]), \
         patch("scheduler.jobs._ocr_image", side_effect=RuntimeError("이미지 없음")), \
         patch("scheduler.jobs.time.sleep"):
        stats = extract_job()

    assert stats == {"ok": 1, "failed": 1, "ocr_failed": 1, "ocr_expired": 0, "remaining": 0}


def test_extract_job_reports_targets_left_by_daily_budget(session):
    session.add(GeminiUsage(usage_date=date.today(), call_count=_GEMINI_DAILY_CAP))
    session.add_all([
        Job(source="a", url=f"https://a.com/{i}", title="t", company="c", jd_text="본문", extract_status="pending")
        for i in range(3)
    ])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd"):
        stats = extract_job()

    assert stats["remaining"] == 3


# ── extract_job (2차 저장: pending/failed 추출) ─────────────────────────────

_EMPTY_SUMMARY = {
    "tasks": [], "stack": [], "employment_type": "", "qualifications": [], "preferred": [],
    "eligibility": {"new_grad_ok": None, "min_years": None, "evidence": ""},
    "locations": [],
}


def test_extract_job_processes_pending_and_sets_ok(session):
    session.add(Job(
        source="inthiswork", url="https://a.com/1", title="t", company="c",
        jd_text="본문", extract_status="pending",
    ))
    session.commit()

    summary = {
        "tasks": ["RAG 구축"], "stack": ["Python"], "employment_type": "정규직",
        "qualifications": ["경력 무관"], "preferred": [],
        "eligibility": {"new_grad_ok": True, "min_years": None, "evidence": "신입 지원 가능"},
        "locations": ["서울 강남구"],
    }
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=summary), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    updated = session.query(Job).filter_by(url="https://a.com/1").first()
    assert updated.jd_tasks == ["RAG 구축"]
    assert updated.jd_stack == ["Python"]
    assert updated.jd_employment_type == "정규직"
    assert updated.eligibility == {"new_grad_ok": True, "min_years": None, "evidence": "신입 지원 가능"}
    assert updated.work_locations == ["서울 강남구"]
    assert updated.extract_status == "ok"


def test_extract_job_skips_empty_and_ok(session):
    """empty(텍스트·이미지 둘 다 없음)와 ok(이미 처리됨)는 건드리지 않는다."""
    session.add_all([
        Job(source="a", url="https://a.com/2", title="t", company="c", jd_text="", extract_status="empty"),
        Job(source="a", url="https://a.com/3", title="t", company="c", jd_text="본문", extract_status="ok"),
    ])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd") as mock_sum, \
         patch("scheduler.jobs._ocr_image") as mock_ocr, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_sum.assert_not_called()
    mock_ocr.assert_not_called()
    statuses = {j.url: j.extract_status for j in session.query(Job).all()}
    assert statuses == {"https://a.com/2": "empty", "https://a.com/3": "ok"}


# ── extract_job: needs_ocr(B 채택 — Document AI) ────────────────────────────

def test_extract_job_ocrs_needs_ocr_then_extracts(session):
    """needs_ocr 공고는 이미지를 OCR해 jd_text를 채운 뒤, 그 텍스트로 곧바로 추출까지 진행한다."""
    session.add(Job(
        source="jasoseol", url="https://j.com/1", title="t", company="c",
        jd_text="", extract_status="needs_ocr",
    ))
    session.add(RawPosting(
        source="jasoseol", url="https://j.com/1", jd_text="",
        image_urls=["https://img.example.com/jd.png"],
    ))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", return_value="OCR로 뽑은 담당업무 텍스트") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_ocr.assert_called_once_with("https://img.example.com/jd.png")
    mock_sum.assert_called_once_with("OCR로 뽑은 담당업무 텍스트", hints=None)
    updated = session.query(Job).filter_by(url="https://j.com/1").first()
    assert updated.jd_text == "OCR로 뽑은 담당업무 텍스트"  # raw_postings는 안 건드리고 jobs만 채움
    assert updated.extract_status == "ok"


def test_extract_job_ocr_uses_first_image_when_several(session):
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="", extract_status="needs_ocr"))
    session.add(RawPosting(
        source="a", url="https://a.com/1", jd_text="",
        image_urls=["https://img.example.com/1.png", "https://img.example.com/2.png"],
    ))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", return_value="텍스트") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_ocr.assert_called_once_with("https://img.example.com/1.png")


# ── extract_job: 텍스트가 조금 있지만 본문은 이미지인 공고 → OCR 보완 (2026-10-14 실제 사례) ──

_TASK_SUMMARY = {**_EMPTY_SUMMARY, "tasks": ["RAG 구축"], "qualifications": ["학사 이상"]}


def _pending_with_images(session, jd_text="신우이엔지㈜", image_urls=("https://img.example.com/jd.png",)):
    session.add(Job(source="jobkorea", url="https://k.com/1", title="t", company="c",
                    jd_text=jd_text, extract_status="pending"))
    session.add(RawPosting(source="jobkorea", url="https://k.com/1", jd_text=jd_text,
                           image_urls=list(image_urls)))
    session.commit()


def test_extract_job_ocrs_image_when_text_summary_has_no_tasks_or_qualifications(session):
    """본문 텍스트가 회사명·접수기한 한 줄뿐이라 요약이 비면, 이미지를 OCR해 본문 뒤에 붙이고 다시 요약한다."""
    _pending_with_images(session)

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", return_value="담당업무: RAG 구축") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd", side_effect=[_EMPTY_SUMMARY, _TASK_SUMMARY]) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        stats = extract_job()

    mock_ocr.assert_called_once_with("https://img.example.com/jd.png")
    assert mock_sum.call_args_list[1].args[0] == "신우이엔지㈜\n\n담당업무: RAG 구축"
    job = session.query(Job).filter_by(url="https://k.com/1").first()
    assert job.jd_text == "신우이엔지㈜\n\n담당업무: RAG 구축"
    assert job.jd_tasks == ["RAG 구축"]
    assert job.extract_status == "ok"
    assert stats["ok"] == 1
    assert session.query(GeminiUsage).first().call_count == 2


def test_extract_job_does_not_ocr_when_text_summary_has_tasks(session):
    _pending_with_images(session, jd_text="담당업무: RAG 구축 ...")

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd", return_value=_TASK_SUMMARY) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_ocr.assert_not_called()
    assert mock_sum.call_count == 1


def test_extract_job_empty_summary_without_images_stays_ok_without_ocr(session):
    _pending_with_images(session, image_urls=())

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_ocr.assert_not_called()
    assert session.query(Job).first().extract_status == "ok"


def test_extract_job_supplement_ocr_failure_keeps_text_summary(session):
    """보완 OCR이 실패해도 텍스트 요약 결과로 ok 처리한다 — 하는 일이 비어 있어 채점 대상에서는 빠진다."""
    _pending_with_images(session)

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", side_effect=RuntimeError("OCR down")), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        stats = extract_job()

    assert mock_sum.call_count == 1
    job = session.query(Job).first()
    assert job.extract_status == "ok"
    assert job.jd_text == "신우이엔지㈜"
    assert stats["ocr_failed"] == 1


def test_extract_job_needs_ocr_without_raw_posting_stays_needs_ocr(session):
    """raw_postings에 대응 행이 없으면(1차 적재 이전 데이터 등) OCR을 시도하지 않고 다음 실행에 맡긴다."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="", extract_status="needs_ocr"))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd") as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_ocr.assert_not_called()
    mock_sum.assert_not_called()
    assert session.query(Job).filter_by(url="https://a.com/1").first().extract_status == "needs_ocr"


def test_extract_job_needs_ocr_with_empty_image_list_stays_needs_ocr(session):
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="", extract_status="needs_ocr"))
    session.add(RawPosting(source="a", url="https://a.com/1", jd_text="", image_urls=[]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image") as mock_ocr, \
         patch("scheduler.jobs.summarize_jd") as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_ocr.assert_not_called()
    mock_sum.assert_not_called()
    assert session.query(Job).filter_by(url="https://a.com/1").first().extract_status == "needs_ocr"


def test_extract_job_ocr_failure_keeps_needs_ocr_for_retry(session):
    """OCR 자체가 실패하면 상태를 needs_ocr로 그대로 둬서 다음 실행에 다시 시도한다(failed로 안 빠짐)."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="", extract_status="needs_ocr"))
    session.add(RawPosting(source="a", url="https://a.com/1", jd_text="", image_urls=["https://img.example.com/1.png"]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", side_effect=RuntimeError("Document AI 오류")), \
         patch("scheduler.jobs.summarize_jd") as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()  # 예외 없이 완료

    mock_sum.assert_not_called()
    job = session.query(Job).filter_by(url="https://a.com/1").first()
    assert job.extract_status == "needs_ocr"
    assert job.jd_text == ""  # OCR 실패했으니 원래 값 그대로


def test_extract_job_ocr_success_but_llm_extraction_failure_sets_failed(session):
    """OCR은 됐는데 그다음 LLM 추출이 실패하면, OCR 결과(jd_text)는 남기고 상태만 failed로."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="", extract_status="needs_ocr"))
    session.add(RawPosting(source="a", url="https://a.com/1", jd_text="", image_urls=["https://img.example.com/1.png"]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", return_value="OCR 텍스트"), \
         patch("scheduler.jobs.summarize_jd", side_effect=RuntimeError("Gemini 오류")), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    job = session.query(Job).filter_by(url="https://a.com/1").first()
    assert job.jd_text == "OCR 텍스트"
    assert job.extract_status == "failed"  # 다음 실행에서 pending/failed 경로로 재추출(OCR은 재시도 안 함)


def test_ocr_image_document_ai_downloads_and_calls_processor():
    """_ocr_image_document_ai()는 이미지를 내려받아 Document AI 프로세서에 넘기고 결과 텍스트를 반환한다."""
    from scheduler.jobs import _ocr_image_document_ai

    fake_response = MagicMock()
    fake_response.content = b"fake-image-bytes"
    fake_response.headers = {"content-type": "image/png"}

    fake_client = MagicMock()
    fake_client.processor_path.return_value = "projects/p/locations/us/processors/x"
    fake_client.process_document.return_value.document.text = "추출된 텍스트"

    with patch("scheduler.jobs.httpx.get", return_value=fake_response) as mock_get, \
         patch("scheduler.jobs.documentai.DocumentProcessorServiceClient", return_value=fake_client), \
         patch("scheduler.jobs.get_settings") as mock_get_settings:
        mock_get_settings.return_value.gcp.project_id = "proj"
        mock_get_settings.return_value.gcp.project_location = "us"
        mock_get_settings.return_value.gcp.doc_processor_id = "proc123"
        mock_get_settings.return_value.gcp.sa_key_json = None

        result = _ocr_image_document_ai("https://img.example.com/jd.png")

    assert result == "추출된 텍스트"
    mock_get.assert_called_once()
    assert mock_get.call_args[0][0] == "https://img.example.com/jd.png"
    fake_response.raise_for_status.assert_called_once()
    fake_client.processor_path.assert_called_once_with("proj", "us", "proc123")

    raw_document = fake_client.process_document.call_args.kwargs["request"].raw_document
    assert raw_document.content == b"fake-image-bytes"
    assert raw_document.mime_type == "image/png"


def test_ocr_image_document_ai_defaults_mime_type_when_header_missing():
    from scheduler.jobs import _ocr_image_document_ai

    fake_response = MagicMock()
    fake_response.content = b"bytes"
    fake_response.headers = {}

    fake_client = MagicMock()
    fake_client.processor_path.return_value = "projects/p/locations/us/processors/x"
    fake_client.process_document.return_value.document.text = "텍스트"

    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.documentai.DocumentProcessorServiceClient", return_value=fake_client), \
         patch("scheduler.jobs.get_settings") as mock_get_settings:
        mock_get_settings.return_value.gcp.project_id = "proj"
        mock_get_settings.return_value.gcp.project_location = "us"
        mock_get_settings.return_value.gcp.doc_processor_id = "proc123"
        mock_get_settings.return_value.gcp.sa_key_json = None

        _ocr_image_document_ai("https://img.example.com/jd")

    raw_document = fake_client.process_document.call_args.kwargs["request"].raw_document
    assert raw_document.mime_type == "image/png"


# ── _ocr_image_claude_code: Claude Code 헤드리스(-p) 백엔드 ──────────────────
#
# haiku는 실측에서 환각(회사명 오독)을 일으켜 sonnet 고정 — GCPSettings처럼 별도 모델 파라미터를
# 안 주고 shared.config의 claude_ocr_model(기본 "sonnet")을 그대로 쓴다.

def _fake_claude_result(text: str, is_error: bool = False) -> str:
    import json
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text})


def _png_bytes(width: int, height: int) -> bytes:
    from io import BytesIO
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _fake_docai(texts):
    client = MagicMock()
    client.processor_path.return_value = "projects/p/locations/us/processors/x"
    client.process_document.side_effect = [MagicMock(document=MagicMock(text=t)) for t in texts]
    return client


def _adc_settings():
    """GSA 키 없이 ADC로 인증하는 설정(로컬과 같은 상태)."""
    settings = MagicMock()
    settings.gcp.sa_key_json = None
    return settings


def test_document_ai_client_uses_adc_without_sa_key():
    """GCP_SA_KEY_JSON이 없으면 자격증명을 넘기지 않아 SDK가 ADC(로컬 위장 로그인)를 찾게 한다."""
    from scheduler.jobs import _document_ai_client

    with patch("scheduler.jobs.documentai.DocumentProcessorServiceClient") as client_cls, \
         patch("scheduler.jobs.get_settings", return_value=_adc_settings()):
        _document_ai_client()

    client_cls.assert_called_once_with()


def test_document_ai_client_treats_blank_sa_key_as_unset():
    from pydantic import SecretStr
    from scheduler.jobs import _document_ai_client

    settings = MagicMock()
    settings.gcp.sa_key_json = SecretStr("  ")
    with patch("scheduler.jobs.documentai.DocumentProcessorServiceClient") as client_cls, \
         patch("scheduler.jobs.get_settings", return_value=settings):
        _document_ai_client()

    client_cls.assert_called_once_with()


def test_document_ai_client_uses_sa_key_json_when_set():
    """GCP_SA_KEY_JSON(GSA 키 JSON 내용)이 있으면 그 키로 만든 자격증명으로 클라이언트를 만든다 — Railway 운영용."""
    from pydantic import SecretStr
    from scheduler.jobs import _document_ai_client

    settings = MagicMock()
    settings.gcp.sa_key_json = SecretStr('{"type": "service_account", "client_email": "ocr@p.iam.gserviceaccount.com"}')
    with patch("scheduler.jobs.documentai.DocumentProcessorServiceClient") as client_cls, \
         patch("scheduler.jobs.service_account.Credentials.from_service_account_info") as from_info, \
         patch("scheduler.jobs.get_settings", return_value=settings):
        _document_ai_client()

    from_info.assert_called_once_with(
        {"type": "service_account", "client_email": "ocr@p.iam.gserviceaccount.com"}
    )
    client_cls.assert_called_once_with(credentials=from_info.return_value)


def test_ocr_image_document_ai_follows_redirects():
    """이미지 서버가 308 등으로 다른 주소를 알려주면 따라간다 (2026-10-14 kribb.fairy.im 실패 사례)."""
    from scheduler.jobs import _ocr_image_document_ai

    fake_response = MagicMock(content=b"fake-image-bytes", headers={"content-type": "image/png"})
    with patch("scheduler.jobs.httpx.get", return_value=fake_response) as mock_get, \
         patch("scheduler.jobs.documentai.DocumentProcessorServiceClient", return_value=_fake_docai(["t"])), \
         patch("scheduler.jobs.get_settings", return_value=_adc_settings()):
        _ocr_image_document_ai("https://img.example.com/jd.png")

    assert mock_get.call_args.kwargs["follow_redirects"] is True


def test_ocr_image_document_ai_splits_image_taller_than_10k_pixels():
    """Document AI는 한 변이 10,000px를 넘는 이미지를 거부한다 — 세로로 잘라 조각마다 OCR하고 이어 붙인다."""
    from io import BytesIO
    from PIL import Image
    from scheduler.jobs import _ocr_image_document_ai

    fake_response = MagicMock(content=_png_bytes(958, 25000), headers={"content-type": "image/webp"})
    client = _fake_docai(["첫 조각", "둘째 조각", "셋째 조각"])
    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.documentai.DocumentProcessorServiceClient", return_value=client), \
         patch("scheduler.jobs.get_settings", return_value=_adc_settings()):
        result = _ocr_image_document_ai("https://img.example.com/long.webp")

    assert result == "첫 조각\n둘째 조각\n셋째 조각"
    sent = [c.kwargs["request"].raw_document for c in client.process_document.call_args_list]
    assert len(sent) == 3
    for raw_document in sent:
        assert raw_document.mime_type == "image/png"
        width, height = Image.open(BytesIO(raw_document.content)).size
        assert width == 958 and height <= 10000


def test_ocr_image_document_ai_sends_image_within_limit_unchanged():
    from scheduler.jobs import _ocr_image_document_ai

    original = _png_bytes(1220, 4206)
    fake_response = MagicMock(content=original, headers={"content-type": "image/png"})
    client = _fake_docai(["본문"])
    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.documentai.DocumentProcessorServiceClient", return_value=client), \
         patch("scheduler.jobs.get_settings", return_value=_adc_settings()):
        assert _ocr_image_document_ai("https://img.example.com/jd.png") == "본문"

    assert client.process_document.call_args.kwargs["request"].raw_document.content == original


def test_ocr_image_claude_code_follows_redirects():
    from scheduler.jobs import _ocr_image_claude_code

    fake_response = MagicMock(content=b"img")
    proc = MagicMock(returncode=0, stdout='{"is_error": false, "result": "ok"}', stderr="")
    with patch("scheduler.jobs.httpx.get", return_value=fake_response) as mock_get, \
         patch("scheduler.jobs.subprocess.run", return_value=proc), \
         patch("scheduler.jobs.get_settings"):
        _ocr_image_claude_code("https://img.example.com/jd.png")

    assert mock_get.call_args.kwargs["follow_redirects"] is True


def test_ocr_image_claude_code_downloads_image_and_calls_headless_claude():
    from scheduler.jobs import _ocr_image_claude_code

    fake_response = MagicMock()
    fake_response.content = b"fake-image-bytes"

    fake_proc = MagicMock()
    fake_proc.stdout = _fake_claude_result("추출된 텍스트")
    fake_proc.returncode = 0

    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.subprocess.run", return_value=fake_proc) as mock_run, \
         patch("scheduler.jobs.get_settings") as mock_get_settings, \
         patch("scheduler.jobs.tempfile.mkstemp", return_value=(999, "C:/tmp/fake.png")), \
         patch("scheduler.jobs.os.close"), \
         patch("scheduler.jobs.open", MagicMock()), \
         patch("scheduler.jobs.os.remove") as mock_remove:
        mock_get_settings.return_value.claude_ocr_model = "sonnet"

        result = _ocr_image_claude_code("https://img.example.com/jd.png")

    assert result == "추출된 텍스트"
    args = mock_run.call_args[0][0]
    assert args[0] == "claude"
    assert "-p" in args
    assert "--allowedTools" in args
    assert "Read" in args
    assert "--model" in args and args[args.index("--model") + 1] == "sonnet"
    assert "--output-format" in args and args[args.index("--output-format") + 1] == "json"
    # 프롬프트 어딘가에 임시 파일 경로가 포함돼야 Read 툴이 그 이미지를 찾는다
    assert "C:/tmp/fake.png" in " ".join(args)
    mock_remove.assert_called_once_with("C:/tmp/fake.png")  # 임시 파일 정리


def test_ocr_image_claude_code_cleans_up_temp_file_even_on_failure():
    """subprocess가 실패해도(예외) 임시 이미지 파일은 지운다."""
    from scheduler.jobs import _ocr_image_claude_code

    fake_response = MagicMock()
    fake_response.content = b"bytes"

    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.subprocess.run", side_effect=RuntimeError("claude 실행 실패")), \
         patch("scheduler.jobs.get_settings") as mock_get_settings, \
         patch("scheduler.jobs.tempfile.mkstemp", return_value=(999, "C:/tmp/fake.png")), \
         patch("scheduler.jobs.os.close"), \
         patch("scheduler.jobs.open", MagicMock()), \
         patch("scheduler.jobs.os.remove") as mock_remove:
        mock_get_settings.return_value.claude_ocr_model = "sonnet"

        with pytest.raises(RuntimeError):
            _ocr_image_claude_code("https://img.example.com/jd.png")

    mock_remove.assert_called_once_with("C:/tmp/fake.png")


def test_ocr_image_claude_code_raises_on_nonzero_exit():
    from scheduler.jobs import _ocr_image_claude_code

    fake_response = MagicMock()
    fake_response.content = b"bytes"
    fake_proc = MagicMock()
    fake_proc.stdout = "{}"
    fake_proc.returncode = 1
    fake_proc.stderr = "권한 오류"

    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.subprocess.run", return_value=fake_proc), \
         patch("scheduler.jobs.get_settings") as mock_get_settings, \
         patch("scheduler.jobs.tempfile.mkstemp", return_value=(999, "C:/tmp/fake.png")), \
         patch("scheduler.jobs.os.close"), \
         patch("scheduler.jobs.open", MagicMock()), \
         patch("scheduler.jobs.os.remove"):
        mock_get_settings.return_value.claude_ocr_model = "sonnet"

        with pytest.raises(RuntimeError, match="claude"):
            _ocr_image_claude_code("https://img.example.com/jd.png")


def test_ocr_image_claude_code_raises_when_response_marks_is_error():
    """호출은 성공(exit 0)해도 응답 안 is_error가 true면 결과를 신뢰하지 않고 실패로 취급한다."""
    from scheduler.jobs import _ocr_image_claude_code

    fake_response = MagicMock()
    fake_response.content = b"bytes"
    fake_proc = MagicMock()
    fake_proc.stdout = _fake_claude_result("", is_error=True)
    fake_proc.returncode = 0

    with patch("scheduler.jobs.httpx.get", return_value=fake_response), \
         patch("scheduler.jobs.subprocess.run", return_value=fake_proc), \
         patch("scheduler.jobs.get_settings") as mock_get_settings, \
         patch("scheduler.jobs.tempfile.mkstemp", return_value=(999, "C:/tmp/fake.png")), \
         patch("scheduler.jobs.os.close"), \
         patch("scheduler.jobs.open", MagicMock()), \
         patch("scheduler.jobs.os.remove"):
        mock_get_settings.return_value.claude_ocr_model = "sonnet"

        with pytest.raises(RuntimeError, match="claude"):
            _ocr_image_claude_code("https://img.example.com/jd.png")


# ── _ocr_image: 백엔드 디스패처 ──────────────────────────────────────────────

def test_ocr_image_dispatches_to_document_ai_by_default():
    from scheduler.jobs import _ocr_image

    with patch("scheduler.jobs.get_settings") as mock_get_settings, \
         patch("scheduler.jobs._ocr_image_document_ai", return_value="doc-ai 결과") as mock_doc_ai, \
         patch("scheduler.jobs._ocr_image_claude_code") as mock_claude:
        mock_get_settings.return_value.ocr_provider = "document_ai"

        result = _ocr_image("https://img.example.com/jd.png")

    assert result == "doc-ai 결과"
    mock_doc_ai.assert_called_once_with("https://img.example.com/jd.png")
    mock_claude.assert_not_called()


def test_ocr_image_dispatches_to_claude_code_when_configured():
    from scheduler.jobs import _ocr_image

    with patch("scheduler.jobs.get_settings") as mock_get_settings, \
         patch("scheduler.jobs._ocr_image_document_ai") as mock_doc_ai, \
         patch("scheduler.jobs._ocr_image_claude_code", return_value="claude 결과") as mock_claude:
        mock_get_settings.return_value.ocr_provider = "claude_code"

        result = _ocr_image("https://img.example.com/jd.png")

    assert result == "claude 결과"
    mock_claude.assert_called_once_with("https://img.example.com/jd.png")
    mock_doc_ai.assert_not_called()


def test_ocr_image_dispatch_rejects_unknown_provider():
    from scheduler.jobs import _ocr_image

    with patch("scheduler.jobs.get_settings") as mock_get_settings:
        mock_get_settings.return_value.ocr_provider = "invalid_provider"
        with pytest.raises(ValueError, match="invalid_provider"):
            _ocr_image("https://img.example.com/jd.png")


def test_extract_job_ocr_failure_does_not_call_gemini_sleep(session):
    """OCR만 실패한 건 Gemini를 호출하지 않았으니 레이트리밋 sleep도 안 한다."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="", extract_status="needs_ocr"))
    session.add(RawPosting(source="a", url="https://a.com/1", jd_text="", image_urls=["https://img.example.com/1.png"]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs._ocr_image", side_effect=RuntimeError("boom")), \
         patch("scheduler.jobs.summarize_jd"), \
         patch("scheduler.jobs.time.sleep") as mock_sleep:
        extract_job()

    mock_sleep.assert_not_called()


def test_extract_job_retries_failed_jobs(session):
    session.add(Job(
        source="a", url="https://a.com/1", title="t", company="c",
        jd_text="본문", extract_status="failed",
    ))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    assert session.query(Job).filter_by(url="https://a.com/1").first().extract_status == "ok"


def test_extract_job_sets_failed_on_exception_and_continues(session):
    """한 건 추출이 실패해도 나머지는 계속 처리된다."""
    session.add_all([
        Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="pending"),
        Job(source="a", url="https://a.com/2", title="t", company="c", jd_text="본문", extract_status="pending"),
    ])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", side_effect=RuntimeError("Gemini 오류")), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()  # 예외 없이 완료

    statuses = {j.url: j.extract_status for j in session.query(Job).all()}
    assert statuses == {"https://a.com/1": "failed", "https://a.com/2": "failed"}


def test_extract_job_passes_hints_from_matching_raw_posting(session):
    session.add(Job(
        source="jasoseol", url="https://j.com/1", title="t", company="c",
        jd_text="본문", extract_status="pending",
    ))
    session.add(RawPosting(
        source="jasoseol", url="https://j.com/1", jd_text="본문",
        hints={"positions": [{"career_level": "신입"}]},
    ))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_sum.assert_called_once_with("본문", hints={"positions": [{"career_level": "신입"}]})


def test_extract_job_passes_none_hints_when_no_matching_raw_posting(session):
    """1차 적재 이전에 생성된 Job 등, raw_postings에 대응 행이 없으면 힌트 없이 진행한다."""
    session.add(Job(
        source="a", url="https://a.com/1", title="t", company="c",
        jd_text="본문", extract_status="pending",
    ))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY) as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_sum.assert_called_once_with("본문", hints=None)


def test_extract_job_stops_when_daily_gemini_budget_exhausted(session):
    """오늘 Gemini 호출이 이미 하루 한도에 도달했으면 summarize_jd를 호출하지 않고 pending을 그대로 둔다."""
    session.add(GeminiUsage(usage_date=date.today(), call_count=_GEMINI_DAILY_CAP))
    job = Job(source="a", url="https://a.com/1", title="t", company="c",
              jd_text="본문", extract_status="pending")
    session.add(job)
    session.commit()
    job_id = job.id

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd") as mock_sum, \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    mock_sum.assert_not_called()
    assert session.query(Job).filter_by(id=job_id).first().extract_status == "pending"


def test_extract_job_records_gemini_call_on_success(session):
    """summarize_jd 호출 1회마다 오늘 날짜 GeminiUsage 카운터가 1씩 증가한다."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c",
                     jd_text="본문", extract_status="pending"))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=_EMPTY_SUMMARY), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    usage = session.query(GeminiUsage).filter_by(usage_date=date.today()).first()
    assert usage.call_count == 1


def test_extract_job_no_targets_does_nothing(session):
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd") as mock_sum:
        extract_job()  # 예외 없이 완료

    mock_sum.assert_not_called()


# ── run_crawl_pipeline (워크플로우 체인) ─────────────────────────────────────

_STATS = {"ok": 1, "failed": 0, "ocr_failed": 0, "ocr_expired": 0, "remaining": 0}
_RUNS = [{"source": "inthiswork", "status": "ok", "seen": 3, "new": 1, "empty": 0,
          "detail_failed": 0, "error": None}]


def test_run_crawl_pipeline_calls_crawl_then_extract_and_reports_each_step():
    """crawl이 끝나면 곧바로 extract로 이어지고, 웹훅은 시작 → 수집 완료 → 추출 완료 순서로 간다."""
    calls = []

    def _crawl():
        calls.append("crawl")
        return _RUNS

    def _extract():
        calls.append("extract")
        return _STATS

    with patch("scheduler.jobs.crawl_job", side_effect=_crawl), \
         patch("scheduler.jobs.extract_job", side_effect=_extract), \
         patch("scheduler.jobs.ingest_report.send", side_effect=calls.append):
        run_crawl_pipeline()

    assert len(calls) == 5
    assert calls[0].startswith("📥") and "공고 수집 시작" in calls[0]
    assert calls[1] == "crawl"
    assert calls[2].startswith("🔎 공고 수집 완료\n신규 1건")
    assert calls[3] == "extract"
    assert calls[4].startswith("🔧 공고 추출 완료")


def test_run_crawl_pipeline_reports_error_and_stops_when_crawl_fails():
    sent = []
    with patch("scheduler.jobs.crawl_job", side_effect=RuntimeError("DB 연결 끊김")), \
         patch("scheduler.jobs.extract_job") as extract, \
         patch("scheduler.jobs.ingest_report.send", side_effect=sent.append):
        run_crawl_pipeline()  # 예외를 스케줄러까지 올리지 않는다

    extract.assert_not_called()
    assert len(sent) == 2
    assert sent[-1] == "❌ 수집 중 에러로 중단 — RuntimeError: DB 연결 끊김"


def test_run_crawl_pipeline_reports_error_when_extract_fails():
    sent = []
    with patch("scheduler.jobs.crawl_job", return_value=_RUNS), \
         patch("scheduler.jobs.extract_job", side_effect=RuntimeError("boom")), \
         patch("scheduler.jobs.ingest_report.send", side_effect=sent.append):
        run_crawl_pipeline()

    assert len(sent) == 3
    assert sent[-1] == "❌ 정리 중 에러로 중단 — RuntimeError: boom"


# ── run_daily_pipeline (08시 하루 1회 체인) ──────────────────────────────────

def test_run_daily_pipeline_runs_collect_analyze_notify_assistant_in_order():
    """수집·추출 → 채점 → 매칭 알림 → 비서 DM 순서로 이어서 실행한다."""
    calls = []
    with patch("scheduler.jobs.run_crawl_pipeline", side_effect=lambda: calls.append("collect")), \
         patch("scheduler.jobs.analyze_job", side_effect=lambda: calls.append("analyze")), \
         patch("scheduler.jobs.notify_job", side_effect=lambda: calls.append("notify")), \
         patch("scheduler.jobs.assistant_job", side_effect=lambda: calls.append("assistant")):
        run_daily_pipeline()

    assert calls == ["collect", "analyze", "notify", "assistant"]


def test_run_daily_pipeline_still_scores_and_notifies_when_collect_stops():
    """수집이 에러로 멈춰도(run_crawl_pipeline은 예외를 삼킨다) 이미 쌓인 공고로 채점·알림은 진행한다."""
    with patch("scheduler.jobs.crawl_job", side_effect=RuntimeError("사이트 다운")), \
         patch("scheduler.jobs.ingest_report.send"), \
         patch("scheduler.jobs.analyze_job") as analyze, \
         patch("scheduler.jobs.notify_job") as notify, \
         patch("scheduler.jobs.assistant_job") as assistant:
        run_daily_pipeline()

    analyze.assert_called_once()
    notify.assert_called_once()
    assistant.assert_called_once()


# ── analyze_job ──────────────────────────────────────────────────────────

_PROFILE = {"병역": "비대상"}


def test_analyze_job_sends_profile_text_built_from_basic_info_and_experiences(session):
    user = User(discord_id="1", username="u", is_active=True,
                basic_info={"이름": "조은아", "병역": "비대상", "링크": {"GitHub": "https://github.com/x"}})
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job])
    session.flush()
    session.add(Experience(user_id=user.id, kind="career", title="여기어때컴퍼니", content="GCP 이관"))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session),          patch("scheduler.jobs.score_fit", return_value={"fit_score": 1, "fit_reasons": ""}) as mock_score,          patch("scheduler.jobs.time.sleep"):
        analyze_job()

    kwargs = mock_score.call_args.kwargs
    assert kwargs["jd_text"] == "본문"
    assert "병역: 비대상" in kwargs["profile_text"]
    assert "■ 여기어때컴퍼니\nGCP 이관" in kwargs["profile_text"]
    assert "조은아" not in kwargs["profile_text"]  # 이름·링크는 채점에서 제외
    assert "github" not in kwargs["profile_text"]


def test_analyze_job_skips_user_without_profile(session):
    """이력이 하나도 없는 유저는 채점하지 않는다 — 공고만 보고 매긴 점수가 저장되면 나중에 다시 채점되지 않는다."""
    empty = User(discord_id="1", username="empty", is_active=True)
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([empty, job])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session),          patch("scheduler.jobs.score_fit") as mock_score,          patch("scheduler.jobs.time.sleep"):
        analyze_job()

    mock_score.assert_not_called()
    assert session.query(JobScore).count() == 0


def test_analyze_job_skips_already_scored(session):
    """이미 JobScore가 있는 (job, user) 조합은 스코어러를 호출하지 않는다."""
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job])
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=1, fit_reasons=""))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit") as mock_score:

        analyze_job()

    mock_score.assert_not_called()


def test_analyze_job_calls_scorer_for_new_pairs(session):
    """JobScore 없는 조합에 대해 스코어러를 호출하고 결과를 저장한다."""
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job])
    session.commit()
    job_id, user_id = job.id, user.id

    score_result = {"fit_score": 80, "fit_reasons": "Python 경험 일치",
                    "fit_detail": {"position": "AI 서비스 개발", "missing": ["Java"]}}

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit", return_value=score_result) as mock_score, \
         patch("scheduler.jobs.time.sleep"):

        analyze_job()

    mock_score.assert_called_once()
    saved = session.query(JobScore).filter_by(job_id=job_id, user_id=user_id).first()
    assert saved is not None
    assert saved.fit_score == 80
    assert saved.fit_reasons == "Python 경험 일치"
    assert saved.fit_detail == {"position": "AI 서비스 개발", "missing": ["Java"]}


def test_analyze_job_scores_only_extracted_ok_jobs(session):
    """2차 저장(extract_job)이 끝난 ok 공고만 채점한다.

    pending(추출 전)·failed(추출 실패)는 카드에 하는 일·자격요건이 비고 신입 여부도 몰라 제외하고,
    needs_ocr/empty는 jd_text가 비어 의미 없는 점수가 나오므로 제외한다.
    """
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    jobs = {
        status: Job(source="a", url=f"https://a.com/{status}", title="t", company="c",
                    jd_text="본문" if status not in ("needs_ocr", "empty") else "",
                    extract_status=status, jd_tasks=["업무"])
        for status in ("ok", "pending", "failed", "needs_ocr", "empty")
    }
    session.add_all([user, *jobs.values()])
    session.commit()
    ok_id = jobs["ok"].id  # analyze_job이 session.close()하면 만료된 속성 재조회가 실패하므로 미리 확보

    with patch("scheduler.jobs.get_session", return_value=session),          patch("scheduler.jobs.score_fit", return_value={"fit_score": 1, "fit_reasons": ""}) as mock_score,          patch("scheduler.jobs.time.sleep"):
        analyze_job()

    assert mock_score.call_count == 1
    assert {s.job_id for s in session.query(JobScore).all()} == {ok_id}


def test_analyze_job_skips_jobs_failing_eligibility_or_region(session):
    """신입 불가(new_grad_ok=False)·근무지가 서울/과천/판교/분당 밖인 공고는 Gemini를 부르지 않는다."""
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    target = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문",
                 extract_status="ok", jd_tasks=["업무"], eligibility={"new_grad_ok": True}, work_locations=["서울 강남구"])
    senior_only = Job(source="a", url="https://a.com/2", title="t", company="c", jd_text="본문",
                      extract_status="ok", jd_tasks=["업무"], eligibility={"new_grad_ok": False}, work_locations=["서울"])
    busan = Job(source="a", url="https://a.com/3", title="t", company="c", jd_text="본문",
                extract_status="ok", jd_tasks=["업무"], eligibility={"new_grad_ok": True}, work_locations=["부산 해운대구"])
    session.add_all([user, target, senior_only, busan])
    session.commit()
    target_id = target.id

    with patch("scheduler.jobs.get_session", return_value=session),          patch("scheduler.jobs.score_fit", return_value={"fit_score": 1, "fit_reasons": ""}) as mock_score,          patch("scheduler.jobs.time.sleep"):
        analyze_job()

    assert mock_score.call_count == 1
    assert {s.job_id for s in session.query(JobScore).all()} == {target_id}


def test_analyze_job_no_active_users(session):
    """활성 유저가 없으면 스코어러를 호출하지 않고 즉시 종료한다."""
    session.add(Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"]))
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit") as mock_score:

        analyze_job()

    mock_score.assert_not_called()


def test_analyze_job_continues_if_scorer_fails(session):
    """개별 점수 계산이 실패해도 다른 조합은 계속 처리한다."""
    users = [
        User(discord_id="1", username="u1", is_active=True, basic_info=_PROFILE),
        User(discord_id="2", username="u2", is_active=True, basic_info=_PROFILE),
    ]
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all(users + [job])
    session.commit()
    job_id = job.id

    call_count = {"n": 0}

    def scorer_side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Gemini 오류")
        return {"fit_score": 70, "fit_reasons": "ok"}

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit", side_effect=scorer_side_effect), \
         patch("scheduler.jobs.time.sleep"):

        analyze_job()  # 예외 없이 완료

    # user 2명 모두 시도
    assert call_count["n"] == 2
    # 성공한 1건만 저장됨
    assert session.query(JobScore).filter_by(job_id=job_id).count() == 1


def test_analyze_job_stops_when_daily_gemini_budget_exhausted(session):
    """오늘 Gemini 호출이 이미 하루 한도에 도달했으면 score_fit을 호출하지 않는다."""
    session.add(GeminiUsage(usage_date=date.today(), call_count=_GEMINI_DAILY_CAP))
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit") as mock_score, \
         patch("scheduler.jobs.time.sleep"):
        analyze_job()

    mock_score.assert_not_called()
    assert session.query(JobScore).count() == 0


def test_analyze_job_stops_mid_batch_once_budget_hit(session):
    """배치 중간에 하루 한도에 도달하면 남은 조합은 건너뛰고 이미 쓴 것만 반영한다."""
    session.add(GeminiUsage(usage_date=date.today(), call_count=_GEMINI_DAILY_CAP - 1))
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    job1 = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문1", extract_status="ok", jd_tasks=["업무"])
    job2 = Job(source="a", url="https://a.com/2", title="t", company="c", jd_text="본문2", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job1, job2])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit", return_value={"fit_score": 1, "fit_reasons": ""}) as mock_score, \
         patch("scheduler.jobs.time.sleep"):
        analyze_job()

    assert mock_score.call_count == 1
    assert session.query(JobScore).count() == 1


def test_analyze_job_records_gemini_call_on_success(session):
    """score_fit 호출 1회마다 오늘 날짜 GeminiUsage 카운터가 1씩 증가한다."""
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE)
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job])
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit", return_value={"fit_score": 1, "fit_reasons": ""}), \
         patch("scheduler.jobs.time.sleep"):
        analyze_job()

    usage = session.query(GeminiUsage).filter_by(usage_date=date.today()).first()
    assert usage.call_count == 1


# ── _is_scoring_target: 채점 전 신입·지역 필터 ────────────────────────────────

def _job(eligibility=None, work_locations=None, jd_tasks=("RAG 구축",)) -> Job:
    return Job(source="a", url="https://a.com/x", title="t", company="c", jd_text="본문",
               extract_status="ok", eligibility=eligibility, work_locations=work_locations,
               jd_tasks=list(jd_tasks) if jd_tasks is not None else None)


@pytest.mark.parametrize("jd_tasks", [[], None])
def test_is_scoring_target_excludes_job_without_tasks(jd_tasks):
    """하는 일이 비어 있으면 판단할 근거가 없다 — 본문 없는 공고가 만점을 받은 사례(2026-10-14) 방지."""
    assert _is_scoring_target(_job(jd_tasks=jd_tasks)) is False


@pytest.mark.parametrize("eligibility, expected", [
    ({"new_grad_ok": True, "min_years": None, "evidence": "경력무관"}, True),
    ({"new_grad_ok": None, "min_years": None, "evidence": ""}, True),   # 모름 → 채점 (2026-10-14 결정)
    (None, True),                                                       # 추출 이전 공고
    ({"new_grad_ok": False, "min_years": 3, "evidence": "경력 3년 이상"}, False),
])
def test_is_scoring_target_eligibility(eligibility, expected):
    assert _is_scoring_target(_job(eligibility=eligibility)) is expected


@pytest.mark.parametrize("work_locations, expected", [
    (["서울 강남구 테헤란로"], True),
    (["경기 과천시"], True),
    (["경기 성남시 분당구 판교역로 235"], True),
    (["경기 성남시 분당구 정자일로 95"], True),   # 판교 글자 없는 분당 주소도 대상 (2026-10-14)
    (["경기 성남시 중원구"], False),              # 성남이어도 분당 밖은 제외
    (["부산 해운대구", "서울 마포구"], True),      # 여러 근무지 중 하나만 맞아도 채점
    (["부산 해운대구"], False),
    (["경기 수원시", "대전"], False),
    ([], True),                                   # 근무지 미기재 → 채점 (2026-10-14 결정)
    (None, True),
])
def test_is_scoring_target_region(work_locations, expected):
    assert _is_scoring_target(_job(work_locations=work_locations)) is expected


# ── cleanup_job (실제 in-memory DB로 검증 — cascade delete 로직이 핵심이라 mock보다 real DB가 적합) ──

def test_cleanup_job_deletes_expired_job_and_related_rows(session):
    """마감 지난 공고와 연관된 job_scores/application_questions/resume_drafts를 함께 삭제한다."""
    user = User(discord_id="1", username="u1")
    expired = Job(
        source="inthiswork", url="https://example.com/expired", title="Old",
        company="Acme", deadline=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add_all([user, expired])
    session.flush()

    question = ApplicationQuestion(job_id=expired.id, question="왜 지원했나요?")
    session.add(question)
    session.flush()

    session.add(JobScore(job_id=expired.id, user_id=user.id, fit_score=80))
    session.add(ResumeDraft(job_id=expired.id, user_id=user.id, question_id=question.id, answer="초안"))
    session.commit()
    expired_id = expired.id  # commit 후 session이 close()되므로 미리 값만 뽑아둔다

    with patch("scheduler.jobs.get_session", return_value=session):
        cleanup_job()

    assert session.query(Job).filter_by(id=expired_id).first() is None
    assert session.query(JobScore).filter_by(job_id=expired_id).first() is None
    assert session.query(ApplicationQuestion).filter_by(job_id=expired_id).first() is None
    assert session.query(ResumeDraft).filter_by(job_id=expired_id).first() is None


def test_cleanup_job_keeps_jobs_with_tracked_application(session):
    """지원 이력(applications)이 있는 공고는 마감이 지나도 삭제하지 않는다."""
    user = User(discord_id="2", username="u2")
    tracked = Job(
        source="inthiswork", url="https://example.com/tracked", title="Tracked",
        company="Acme", deadline=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add_all([user, tracked])
    session.flush()

    session.add(Application(job_id=tracked.id, user_id=user.id, status="will_apply"))
    session.commit()
    tracked_id = tracked.id

    with patch("scheduler.jobs.get_session", return_value=session):
        cleanup_job()

    assert session.query(Job).filter_by(id=tracked_id).first() is not None
    assert session.query(Application).filter_by(job_id=tracked_id).first() is not None


def test_cleanup_job_keeps_jobs_without_deadline(session):
    """deadline이 없는 공고는 만료 여부를 판단할 수 없으므로 건드리지 않는다."""
    job = Job(source="inthiswork", url="https://example.com/no-deadline", title="No DL", company="Acme")
    session.add(job)
    session.commit()
    job_id = job.id

    with patch("scheduler.jobs.get_session", return_value=session):
        cleanup_job()

    assert session.query(Job).filter_by(id=job_id).first() is not None


def test_cleanup_job_keeps_future_deadline(session):
    """마감이 아직 안 지난 공고는 삭제하지 않는다."""
    job = Job(
        source="inthiswork", url="https://example.com/future", title="Future",
        company="Acme", deadline=datetime.now(timezone.utc) + timedelta(days=3),
    )
    session.add(job)
    session.commit()
    job_id = job.id

    with patch("scheduler.jobs.get_session", return_value=session):
        cleanup_job()

    assert session.query(Job).filter_by(id=job_id).first() is not None


# ── 채점 버전·재채점 요청 (2026-10-15) ───────────────────────────────────

def _scored_pair(session, **score_fields):
    user = User(discord_id="1", username="u", is_active=True, basic_info=_PROFILE, profile_version=3)
    job = Job(source="a", url="https://a.com/1", title="t", company="c", jd_text="본문", extract_status="ok", jd_tasks=["업무"])
    session.add_all([user, job])
    session.flush()
    row = None
    if score_fields:
        row = JobScore(job_id=job.id, user_id=user.id, **score_fields)
        session.add(row)
    session.commit()
    return user, job, row


_RESULT = {"fit_score": 77, "fit_reasons": "새 이유", "fit_detail": {"position": "새 직무"}}


def test_analyze_job_stamps_profile_and_scorer_version_on_new_scores(session):
    from analysis.scorer import SCORER_VERSION
    _scored_pair(session)
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit", return_value=_RESULT), \
         patch("scheduler.jobs.time.sleep"):
        analyze_job()

    saved = session.query(JobScore).one()
    assert (saved.profile_version, saved.scorer_version) == (3, SCORER_VERSION)
    assert saved.notified_at is None


def test_analyze_job_does_not_rescore_stale_scores_without_request(session):
    """프로필이 바뀌어 구 점수가 돼도 자동으로 다시 채점하지 않는다."""
    _scored_pair(session, fit_score=50, profile_version=1, scorer_version=1)
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit") as mock_score:
        analyze_job()

    mock_score.assert_not_called()


def test_analyze_job_rescores_requested_scores_in_place_and_keeps_notified_at(session):
    from analysis.scorer import SCORER_VERSION
    sent_at = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
    _, _, row = _scored_pair(
        session, fit_score=50, fit_reasons="옛 이유", profile_version=1, scorer_version=1,
        notified_at=sent_at, notified_as="card", rescore_requested_at=datetime(2026, 10, 15, 3, 0),
    )
    row_id = row.id
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit", return_value=_RESULT) as mock_score, \
         patch("scheduler.jobs.time.sleep"):
        analyze_job()

    mock_score.assert_called_once()
    saved = session.query(JobScore).one()
    assert saved.id == row_id  # 지우지 않고 덮어씀
    assert (saved.fit_score, saved.fit_reasons) == (77, "새 이유")
    assert (saved.profile_version, saved.scorer_version) == (3, SCORER_VERSION)
    assert saved.rescore_requested_at is None
    assert saved.notified_at == sent_at  # 알림이 다시 나가지 않게 유지


def test_analyze_job_leaves_rescore_requests_when_budget_exhausted(session):
    _scored_pair(session, fit_score=50, rescore_requested_at=datetime(2026, 10, 15, 3, 0))
    session.add(GeminiUsage(usage_date=date.today(), call_count=_GEMINI_DAILY_CAP))
    session.commit()
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_fit") as mock_score:
        analyze_job()

    mock_score.assert_not_called()
    assert session.query(JobScore).one().rescore_requested_at is not None


# ── 본문에서 마감일 채우기 (인디스워크) ──────────────────────────────────

def _pending(session, deadline=None):
    session.add(Job(source="inthiswork", url="https://a.com/d", title="t", company="c",
                    jd_text="서류접수 ~ 10월 28일 23:59", extract_status="pending", deadline=deadline))
    session.commit()


def test_extract_job_fills_missing_deadline_from_summary(session):
    _pending(session)
    found = datetime(2026, 10, 28, 14, 59, tzinfo=timezone.utc)
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value={**_TASK_SUMMARY, "deadline": found}), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    assert session.query(Job).one().deadline == found


def test_extract_job_keeps_crawler_deadline(session):
    crawler_deadline = datetime(2026, 11, 1, 23, 59, tzinfo=_KST)
    _pending(session, deadline=crawler_deadline)
    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd",
               return_value={**_TASK_SUMMARY, "deadline": datetime(2026, 10, 28, tzinfo=timezone.utc)}), \
         patch("scheduler.jobs.time.sleep"):
        extract_job()

    assert session.query(Job).one().deadline == crawler_deadline


# ── cleanup: 패스만 있는 공고 ───────────────────────────────────────────

def test_cleanup_job_deletes_expired_job_with_only_a_pass_and_its_records(session):
    from db.orm import ApplicationEvent
    from tracker.applications import change_status
    user = User(discord_id="3", username="u3")
    expired = Job(source="a", url="https://example.com/passed", title="P", company="C",
                  deadline=datetime.now(timezone.utc) - timedelta(days=1))
    session.add_all([user, expired])
    session.flush()
    change_status(session, user.id, expired.id, "dismissed", source="discord")
    session.commit()

    with patch("scheduler.jobs.get_session", return_value=session):
        cleanup_job()

    assert session.query(Job).count() == 0
    assert session.query(Application).count() == 0
    assert session.query(ApplicationEvent).count() == 0
