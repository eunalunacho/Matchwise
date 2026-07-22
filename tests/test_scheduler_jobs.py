"""스케줄러 잡 유닛 테스트.

크롤러/스코어러/DB를 모두 mock으로 대체해
잡 함수의 분기 로직(중복 제외, 실패 격리 등)만 검증한다.
"""
from unittest.mock import MagicMock, patch

from db.orm import Job, User, JobScore
from scheduler.jobs import crawl_job, analyze_job


# ── helpers ──────────────────────────────────────────────────────────────

def _make_raw_job(url: str, source: str = "inthiswork"):
    j = MagicMock()
    j.source = source
    j.url = url
    j.title = "Backend Dev"
    j.company = "Acme"
    j.jd_text = "Python 개발자"
    j.deadline = None
    return j


def _make_db_job(job_id: int):
    j = MagicMock(spec=Job)
    j.id = job_id
    j.jd_text = "Python 백엔드"
    j.company = "Acme"
    return j


def _make_db_user(user_id: int):
    u = MagicMock(spec=User)
    u.id = user_id
    u.resume_text = "Python 3년"
    u.experience_text = "스타트업 경험"
    return u


def _make_session(users, jobs, job_score_exists: bool, unsummarized=None):
    """
    analyze_job이 사용하는 쿼리 체인을 올바르게 모사한다.

    session.query(Job).filter(jd_summary.is_(None)).all() → unsummarized (기본 [])
    session.query(User).filter_by(is_active=True).all()   → users
    session.query(Job).all()                              → jobs
    session.query(JobScore).filter_by(...).first()        → existing | None
    """
    session = MagicMock()

    def query_side_effect(model):
        mock_q = MagicMock()
        if model is User:
            mock_q.filter_by.return_value.all.return_value = users
        elif model is Job:
            mock_q.all.return_value = jobs
            mock_q.filter.return_value.all.return_value = unsummarized or []
        elif model is JobScore:
            existing = MagicMock() if job_score_exists else None
            mock_q.filter_by.return_value.first.return_value = existing
        return mock_q

    session.query.side_effect = query_side_effect
    return session


# ── crawl_job ────────────────────────────────────────────────────────────

def test_crawl_job_saves_new_deduplicates():
    """신규 URL만 INSERT하고 중복은 스킵한다."""
    raw_new = _make_raw_job("https://new.com/1")
    raw_dup = _make_raw_job("https://dup.com/2")

    session = MagicMock()
    # 신규 URL → None, 중복 URL → 기존 Job
    session.query.return_value.filter_by.return_value.first.side_effect = [
        None, MagicMock()
    ]

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.InthisworkCrawler") as MockITW, \
         patch("scheduler.jobs.SuperookieCrawler") as MockSR:

        MockITW.return_value.fetch_jobs.return_value = [raw_new, raw_dup]
        MockSR.return_value.fetch_jobs.return_value = []

        crawl_job()

    session.commit.assert_called_once()
    # 신규 1건만 add
    assert session.add.call_count == 1


def test_crawl_job_continues_if_one_crawler_fails():
    """한 크롤러가 실패해도 나머지 크롤러는 정상 실행된다."""
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = None

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.InthisworkCrawler") as MockITW, \
         patch("scheduler.jobs.SuperookieCrawler") as MockSR:

        MockITW.return_value.fetch_jobs.side_effect = RuntimeError("네트워크 오류")
        MockSR.return_value.fetch_jobs.return_value = [_make_raw_job("https://sr.com/1", "superookie")]

        crawl_job()  # 예외 없이 완료되어야 함

    session.commit.assert_called_once()
    # Superookie 결과 1건 저장
    assert session.add.call_count == 1


def test_crawl_job_no_new_jobs():
    """수집된 공고가 전부 중복이면 commit만 하고 add는 없다."""
    session = MagicMock()
    # 항상 기존 Job 반환 (중복)
    session.query.return_value.filter_by.return_value.first.return_value = MagicMock()

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.InthisworkCrawler") as MockITW, \
         patch("scheduler.jobs.SuperookieCrawler") as MockSR:

        MockITW.return_value.fetch_jobs.return_value = [_make_raw_job("https://dup.com/1")]
        MockSR.return_value.fetch_jobs.return_value = []

        crawl_job()

    session.add.assert_not_called()
    session.commit.assert_called_once()


# ── analyze_job ──────────────────────────────────────────────────────────

def test_analyze_job_skips_already_scored():
    """이미 JobScore가 있는 (job, user) 조합은 스코어러를 호출하지 않는다."""
    user = _make_db_user(1)
    job = _make_db_job(10)
    session = _make_session(users=[user], jobs=[job], job_score_exists=True)

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_job_for_user") as mock_score, \
         patch("scheduler.jobs._make_dart_client", return_value=None):

        analyze_job()

    mock_score.assert_not_called()
    session.commit.assert_called_once()


def test_analyze_job_calls_scorer_for_new_pairs():
    """JobScore 없는 조합에 대해 스코어러를 호출하고 결과를 저장한다."""
    user = _make_db_user(1)
    job = _make_db_job(10)
    session = _make_session(users=[user], jobs=[job], job_score_exists=False)

    score_result = {
        "fit_score": 80,
        "company_score": 70,
        "fit_reasons": "Python 경험 일치",
        "company_summary": "안정적 기업",
    }

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_job_for_user", return_value=score_result) as mock_score, \
         patch("scheduler.jobs._make_dart_client", return_value=None), \
         patch("scheduler.jobs.time.sleep"):

        analyze_job()

    mock_score.assert_called_once()
    session.add.assert_called_once()
    session.commit.assert_called_once()


def test_analyze_job_generates_jd_summary_once_per_job():
    """jd_summary 없는 공고는 요약을 생성해 저장한다 (공고당 1회)."""
    user = _make_db_user(1)
    job = _make_db_job(10)
    job.jd_summary = None
    session = _make_session(
        users=[user], jobs=[job], job_score_exists=True, unsummarized=[job]
    )
    summary = {"tasks": ["RAG 구축"], "stack": ["Python"], "requirements": "경력 무관"}

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", return_value=summary) as mock_sum, \
         patch("scheduler.jobs.score_job_for_user"), \
         patch("scheduler.jobs._make_dart_client", return_value=None), \
         patch("scheduler.jobs.time.sleep"):

        analyze_job()

    mock_sum.assert_called_once()
    assert job.jd_summary == summary


def test_analyze_job_continues_if_summary_fails():
    """요약 생성 실패가 점수 계산을 막지 않는다."""
    user = _make_db_user(1)
    job = _make_db_job(10)
    job.jd_summary = None
    session = _make_session(
        users=[user], jobs=[job], job_score_exists=True, unsummarized=[job]
    )

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.summarize_jd", side_effect=RuntimeError("Gemini 오류")), \
         patch("scheduler.jobs.score_job_for_user") as mock_score, \
         patch("scheduler.jobs._make_dart_client", return_value=None), \
         patch("scheduler.jobs.time.sleep"):

        analyze_job()  # 예외 없이 완료

    # 요약 실패해도 이후 단계(유저 조회 등)는 진행됨
    session.close.assert_called_once()


def test_analyze_job_no_active_users():
    """활성 유저가 없으면 스코어러를 호출하지 않고 즉시 종료한다."""
    session = _make_session(users=[], jobs=[_make_db_job(1)], job_score_exists=False)

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_job_for_user") as mock_score, \
         patch("scheduler.jobs._make_dart_client", return_value=None):

        analyze_job()

    mock_score.assert_not_called()


def test_analyze_job_continues_if_scorer_fails():
    """개별 점수 계산이 실패해도 다른 조합은 계속 처리한다."""
    users = [_make_db_user(1), _make_db_user(2)]
    job = _make_db_job(10)

    # 두 유저 모두 JobScore 없음
    session = _make_session(users=users, jobs=[job], job_score_exists=False)

    call_count = {"n": 0}

    def scorer_side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Gemini 오류")
        return {
            "fit_score": 70,
            "company_score": 60,
            "fit_reasons": "ok",
            "company_summary": "ok",
        }

    with patch("scheduler.jobs.get_session", return_value=session), \
         patch("scheduler.jobs.score_job_for_user", side_effect=scorer_side_effect), \
         patch("scheduler.jobs._make_dart_client", return_value=None), \
         patch("scheduler.jobs.time.sleep"):

        analyze_job()  # 예외 없이 완료

    # user 2명 모두 시도
    assert call_count["n"] == 2
    # 성공한 1건만 add
    assert session.add.call_count == 1
    session.commit.assert_called_once()
