"""크롤러 단독 실행 진입점 — 스케줄러를 기다리지 않고 수집을 한 번 돌리고 끝난다.

docker-compose의 crawler 서비스(profile: crawl)가 이 모듈을 실행한다.
  docker compose --profile crawl run --rm crawler
  CMD ["uv", "run", "python", "-m", "crawler.main"]

스케줄러 02시 잡과 같은 run_crawl_pipeline(crawl_job → extract_job)을 호출한다 —
수집만 하고 추출을 빼면 새 공고가 다음 02시까지 extract_status='pending'으로 남기 때문이다.
"""
import logging

from scheduler.jobs import run_crawl_pipeline
from shared.logger import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """수집 → 추출 파이프라인을 1회 실행한다. 로그는 콘솔과 logs/crawler.log(최근 7일 보관)에 남는다."""
    setup_logging("crawler")
    logger.info("크롤러 수동 실행 시작")
    run_crawl_pipeline()
    logger.info("크롤러 수동 실행 종료")


if __name__ == "__main__":
    main()
