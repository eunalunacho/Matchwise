"""APScheduler 진입점.

docker-compose의 scheduler 서비스가 이 모듈을 실행한다.
  CMD ["uv", "run", "python", "-m", "scheduler.main"]
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from shared.config import get_settings
from scheduler.jobs import crawl_job, analyze_job, notify_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    tz = settings.schedule.timezone

    scheduler = BlockingScheduler(timezone=tz)

    scheduler.add_job(
        crawl_job,
        CronTrigger(hour=settings.schedule.crawl_hour, minute=0, timezone=tz),
        id="crawl",
        name="공고 수집",
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

    logger.info(
        "스케줄러 시작 (timezone=%s) — crawl=%d:00, analyze=%d:00, notify=%d:00",
        tz,
        settings.schedule.crawl_hour,
        settings.schedule.analyze_hour,
        settings.schedule.brief_hour,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
