"""shared/logger.py — 프로그램별 로그 파일(최근 7일 보관) 설정 테스트."""
import logging
from logging.handlers import TimedRotatingFileHandler

import pytest

from shared.logger import LOG_RETENTION_DAYS, _reset_logging, setup_logging


@pytest.fixture(autouse=True)
def _clean_root_logger():
    yield
    _reset_logging()  # 파일 핸들을 닫아야 Windows에서 tmp_path 정리가 된다


def _file_handlers():
    return [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]


def test_writes_to_per_program_file_rotated_at_midnight_and_kept_seven_days(tmp_path):
    setup_logging("crawler", log_dir=tmp_path)

    [handler] = _file_handlers()
    assert handler.baseFilename == str(tmp_path / "crawler.log")
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == LOG_RETENTION_DAYS == 7


def test_log_lines_reach_the_file_in_utf8(tmp_path):
    setup_logging("scheduler", log_dir=tmp_path)
    logging.getLogger("scheduler.jobs").info("[crawl] 자소설: 목록 12 · 신규 3")
    for handler in _file_handlers():
        handler.flush()

    text = (tmp_path / "scheduler.log").read_text(encoding="utf-8")
    assert "[crawl] 자소설: 목록 12 · 신규 3" in text


def test_still_prints_to_console(tmp_path):
    setup_logging("bot", log_dir=tmp_path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_matchwise", False)]
    assert any(type(h) is logging.StreamHandler for h in ours)


def test_calling_twice_does_not_duplicate_handlers(tmp_path):
    setup_logging("crawler", log_dir=tmp_path)
    setup_logging("crawler", log_dir=tmp_path)
    assert len(_file_handlers()) == 1


def test_creates_log_dir_if_missing(tmp_path):
    setup_logging("crawler", log_dir=tmp_path / "logs")
    assert (tmp_path / "logs").is_dir()
