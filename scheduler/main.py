"""APScheduler 진입점.

docker-compose의 scheduler 서비스가 이 모듈을 실행한다.
  CMD ["uv", "run", "python", "-m", "scheduler.main"]
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from shared.config import get_settings
from scheduler.jobs import cleanup_job, run_daily_pipeline
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
    # 수집·추출 → 채점 → 매칭 알림 → 비서 DM을 한 잡으로 이어서 실행 (scheduler/jobs.py::run_daily_pipeline)
    scheduler.add_job(
        run_daily_pipeline,
        # 평일만 — 월요일은 크롤러가 조회 기간을 토·일·월로 넓힌다 (crawler/base.py::default_lookback_days)
        CronTrigger(day_of_week="mon-fri", hour=settings.schedule.pipeline_hour, minute=0, timezone=tz),
        id="daily_pipeline",
        name="수집 → 추출 → 채점 → 알림",
        max_instances=1,        # 중복 실행 방지
        misfire_grace_time=600, # 10분 내 지연 실행 허용
    )

    logger.info(
        "스케줄러 시작 (timezone=%s) — cleanup=%d:00, daily_pipeline=평일 %d:00",
        tz,
        settings.schedule.cleanup_hour,
        settings.schedule.pipeline_hour,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
