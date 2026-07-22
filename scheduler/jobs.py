"""스케줄러 잡 함수.

각 함수는 APScheduler에 의해 독립적으로 호출된다.
예외가 발생해도 다음 잡 실행에 영향 없도록 내부에서 처리한다.
"""
import asyncio
import logging
import time

from shared.config import get_settings
from shared.db import get_session
from db.orm import Job, User, JobScore
from crawler.inthiswork import InthisworkCrawler
from crawler.superookie import SuperookieCrawler
from analysis.scorer import score_job_for_user, summarize_jd

logger = logging.getLogger(__name__)

# Gemini free tier: 10 RPM → 잡 간 최소 대기
_GEMINI_SLEEP_SEC = 6


def _make_dart_client():
    """DART API 키가 있으면 DartClient 반환, 없으면 None."""
    settings = get_settings()
    if not settings.dart.api_key:
        return None
    from analysis.dart_client import DartClient
    return DartClient(
        api_key=settings.dart.api_key.get_secret_value(),
        cache_dir=settings.dart.cache_dir,
    )


# ── Job 1: 크롤링 ────────────────────────────────────────────────────────

def crawl_job() -> None:
    """02:00 KST — 신규 공고 수집 후 DB에 저장 (URL 기준 중복 제외)."""
    logger.info("[crawl] 시작")
    session = get_session()
    try:
        raw_jobs = []

        for CrawlerCls in (InthisworkCrawler, SuperookieCrawler):
            try:
                crawler = CrawlerCls()
                jobs = crawler.fetch_jobs()
                raw_jobs.extend(jobs)
                logger.info("[crawl] %s: %d건 수집", CrawlerCls.source, len(jobs))
            except Exception:
                logger.exception("[crawl] %s 실패", CrawlerCls.source)

        new_count = 0
        for raw in raw_jobs:
            if session.query(Job).filter_by(url=raw.url).first():
                continue
            session.add(Job(
                source=raw.source,
                url=raw.url,
                title=raw.title,
                company=raw.company,
                jd_text=raw.jd_text,
                deadline=raw.deadline,
            ))
            new_count += 1

        session.commit()
        logger.info("[crawl] 완료 — 신규 %d건 저장 (전체 수집 %d건)", new_count, len(raw_jobs))

    except Exception:
        logger.exception("[crawl] 예기치 않은 오류")
        session.rollback()
    finally:
        session.close()


# ── Job 2: 분석 ──────────────────────────────────────────────────────────

def analyze_job() -> None:
    """05:00 KST — JD 요약(공고당 1회) + 미분석 (Job × User) 조합 점수 계산."""
    logger.info("[analyze] 시작")
    session = get_session()
    dart_client = _make_dart_client()

    try:
        # 1) JD 요약 — 알림 카드용, 공고당 1회
        unsummarized = session.query(Job).filter(Job.jd_summary.is_(None)).all()
        for job in unsummarized:
            try:
                job.jd_summary = summarize_jd(job.jd_text or "")
                session.flush()
                time.sleep(_GEMINI_SLEEP_SEC)
            except Exception:
                logger.exception("[analyze] JD 요약 실패 job_id=%s", job.id)
        if unsummarized:
            session.commit()
            logger.info("[analyze] JD 요약 %d건 생성", len(unsummarized))

        # 2) 유저별 fit/company 점수
        users = session.query(User).filter_by(is_active=True).all()
        if not users:
            logger.info("[analyze] 활성 유저 없음, 종료")
            return

        all_jobs = session.query(Job).all()
        total, skipped, failed = 0, 0, 0

        for job in all_jobs:
            for user in users:
                already = session.query(JobScore).filter_by(
                    job_id=job.id, user_id=user.id
                ).first()
                if already:
                    skipped += 1
                    continue

                try:
                    result = score_job_for_user(
                        jd_text=job.jd_text or "",
                        company=job.company or "",
                        resume_text=user.resume_text or "",
                        experience_text=user.experience_text or "",
                        dart_client=dart_client,
                    )
                    session.add(JobScore(
                        job_id=job.id,
                        user_id=user.id,
                        fit_score=result["fit_score"],
                        company_score=result["company_score"],
                        fit_reasons=result.get("fit_reasons", ""),
                    ))
                    session.flush()
                    total += 1
                    time.sleep(_GEMINI_SLEEP_SEC)

                except Exception:
                    logger.exception(
                        "[analyze] 점수 계산 실패 job_id=%s user_id=%s",
                        job.id, user.id,
                    )
                    failed += 1

        session.commit()
        logger.info(
            "[analyze] 완료 — 신규 %d건, 스킵 %d건, 실패 %d건",
            total, skipped, failed,
        )

    except Exception:
        logger.exception("[analyze] 예기치 않은 오류")
        session.rollback()
    finally:
        session.close()


# ── Job 3: Discord 알림 ──────────────────────────────────────────────────

def notify_job() -> None:
    """09:00 KST — 유저별 상위 3개 공고 Discord DM 발송."""
    logger.info("[notify] 시작")
    try:
        from bot.notifications import send_daily_notifications
        asyncio.run(send_daily_notifications())
        logger.info("[notify] 완료")
    except Exception:
        logger.exception("[notify] 실패")
