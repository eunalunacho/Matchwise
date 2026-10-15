"""crawler/main.py — 크롤러 단독 실행 진입점 테스트."""
from unittest.mock import patch

from crawler import main as crawler_main


def test_main_sets_up_crawler_log_file_then_runs_pipeline_once():
    """logs/crawler.log 로깅을 먼저 켜고, 스케줄러 02시 잡과 같은 run_crawl_pipeline을 한 번 실행한다."""
    calls = []
    with patch("crawler.main.setup_logging", side_effect=lambda name: calls.append(("log", name))), \
         patch("crawler.main.run_crawl_pipeline", side_effect=lambda: calls.append("pipeline")):
        crawler_main.main()

    assert calls == [("log", "crawler"), "pipeline"]
