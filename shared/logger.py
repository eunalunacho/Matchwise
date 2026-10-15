"""MatchWise 로깅 모듈 — 실행 프로그램(scheduler·crawler·bot)마다 콘솔 + 로그 파일.

각 진입점(main)이 시작할 때 setup_logging("<프로그램명>")을 1회 호출한다. 이후 모듈은
평소처럼 logging.getLogger(__name__)를 쓰면 콘솔과 logs/<프로그램명>.log에 함께 남는다.

로그 파일은 자정마다 날짜가 붙은 파일(예: crawler.log.2026-10-14)로 넘어가고, 최근
LOG_RETENTION_DAYS일치만 남기고 오래된 것부터 자동 삭제된다. 실행 중에 바로바로 기록하므로
프로그램이 중간에 죽어도 그 시점까지의 로그가 남는다. 프로그램마다 파일을 나누는 이유:
Windows에서는 다른 프로세스가 연 파일을 날짜 파일로 이름 바꾸다 실패할 수 있다.

Usage::

    from shared.logger import setup_logging

    setup_logging("crawler")
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.config import Settings

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_RETENTION_DAYS = 7

_lock = threading.Lock()

# PROD JSON에 포함할 extra 필드 화이트리스트
_EXTRA_FIELDS = ("agent", "job_id", "company", "target", "duration_ms")


class JsonFormatter(logging.Formatter):
    """PROD용 JSON 포맷터. CloudWatch Logs Insights 호환."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in _EXTRA_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def _build_dev_formatter() -> logging.Formatter:
    """DEV용 사람이 읽기 좋은 포맷터. 로그 파일은 환경과 무관하게 항상 이 포맷을 쓴다."""
    return logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _remove_our_handlers(root: logging.Logger) -> None:
    """이 모듈이 붙인 핸들러만 떼고 닫는다 — pytest 등 다른 곳이 붙인 핸들러는 건드리지 않는다."""
    for handler in [h for h in root.handlers if getattr(h, "_matchwise", False)]:
        root.removeHandler(handler)
        handler.close()


def setup_logging(app_name: str, log_dir: Path | None = None, settings: Settings | None = None) -> None:
    """루트 로거에 콘솔 출력과 logs/<app_name>.log 파일 저장(최근 7일 보관)을 붙인다.

    다시 호출하면 이전에 붙인 핸들러를 교체한다(중복 출력 없음).

    Args:
        app_name: 실행 프로그램 이름 — 로그 파일명이 된다 (scheduler / crawler / bot).
        log_dir: 로그 폴더. None이면 프로젝트 루트의 logs/. 없으면 만든다.
        settings: 설정 객체. None이면 get_settings()로 자동 로드.
    """
    # 순환 임포트 방지: 함수 내부에서 lazy import
    if settings is None:
        from shared.config import get_settings

        settings = get_settings()

    log_dir = log_dir or LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    with _lock:
        root = logging.getLogger()
        root.setLevel(settings.log_level.upper())
        _remove_our_handlers(root)

        console = logging.StreamHandler()
        console.setFormatter(JsonFormatter() if settings.is_prod else _build_dev_formatter())

        file_handler = TimedRotatingFileHandler(
            log_dir / f"{app_name}.log",
            when="midnight",
            backupCount=LOG_RETENTION_DAYS,
            encoding="utf-8",
        )
        file_handler.setFormatter(_build_dev_formatter())

        for handler in (console, file_handler):
            handler._matchwise = True  # _remove_our_handlers가 알아보는 표시
            root.addHandler(handler)


def _reset_logging() -> None:
    """테스트용 상태 리셋. 프로덕션 코드에서 사용 금지."""
    with _lock:
        _remove_our_handlers(logging.getLogger())
