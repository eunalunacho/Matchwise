"""APScheduler 진입점.

docker-compose의 scheduler 서비스가 이 모듈을 실행한다.
  CMD ["uv", "run", "python", "-m", "scheduler.main"]
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from shared.config import get_settings
from scheduler.jobs import assistant_job, cleanup_job, run_crawl_pipeline, analyze_job, notify_job
from shared.logger import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging("scheduler")  # 콘솔 + logs/scheduler.log(최근 7일 보관)
    settings = get_settings()
    tz = settings.schedule.timezone

    scheduler = BlockingScheduler(timezone=tz)

    scheduler.add_job(
        cleanup_job,
        CronTrigger(hour=settings.schedule.cleanup_hour, minute=0, timezone=tz),
        id="cleanup",
        name="마감 공고 정리",
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        run_crawl_pipeline,
        CronTrigger(hour=settings.schedule.crawl_hour, minute=0, timezone=tz),
        id="crawl",
        name="공고 수집 → 추출 파이프라인",
        max_instances=1,        # 중복 실행 방지
        misfire_grace_time=600, # 10분 내 지연 실행 허용
    )
    scheduler.add_job(
        analyze_job,
        CronTrigger(hour=settings.schedule.analyze_hour, minute=0, timezone=tz),
        id="analyze",
        name="공고 분석",
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        notify_job,
        CronTrigger(hour=settings.schedule.brief_hour, minute=0, timezone=tz),
        id="notify",
        name="Discord 알림",
        max_instances=1,
        misfire_grace_time=300,
    )
    # 매칭 알림 10분 뒤 — 마감 리마인드(매일)·3일 현황 요약을 DM으로 (bot/assistant.py)
    scheduler.add_job(
        assistant_job,
        CronTrigger(hour=settings.schedule.brief_hour, minute=10, timezone=tz),
        id="assistant",
        name="비서 DM",
        max_instances=1,
        misfire_grace_time=600,
    )

    logger.info(
        "스케줄러 시작 (timezone=%s) — cleanup=%d:00, crawl=%d:00, analyze=%d:00, notify=%d:00, assistant=%d:10",
        tz,
        settings.schedule.cleanup_hour,
        settings.schedule.crawl_hour,
        settings.schedule.analyze_hour,
        settings.schedule.brief_hour,
        settings.schedule.brief_hour,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
