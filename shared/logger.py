"""MatchWise 로깅 모듈.

환경별 포맷 전환(DEV: 콘솔, PROD: JSON), Sentry 연동,
로그 레벨 관리를 담당하는 단일 진입점.

Usage::

    from shared.logger import get_logger

    logger = get_logger(__name__)
    logger.info("크롤링 시작")
    logger.info("완료: %d건", count, extra={"agent": "crawling", "duration_ms": 1500})
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.config import Settings

_initialized = False
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
    """DEV용 사람이 읽기 좋은 포맷터."""
    return logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _init_sentry(settings: Settings) -> None:
    """Sentry SDK 초기화. DSN이 없거나 패키지 미설치 시 스킵."""
    dsn = settings.sentry.dsn
    if not dsn or dsn in ("your-dsn", "your-sentry-dsn"):
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logging.getLogger(__name__).warning(
            "sentry-sdk가 설치되지 않아 Sentry 연동을 건너뜁니다"
        )
        return

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=settings.sentry.traces_sample_rate,
        integrations=[
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
            ),
        ],
    )


def setup_logging(settings: Settings | None = None) -> None:
    """루트 로거를 1회 설정한다.

    Args:
        settings: 설정 객체. None이면 ``get_settings()``로 자동 로드.
    """
    global _initialized  # noqa: PLW0603

    if _initialized:
        return

    with _lock:
        if _initialized:
            return

        # 순환 임포트 방지: 함수 내부에서 lazy import
        if settings is None:
            from shared.config import get_settings

            settings = get_settings()

        root = logging.getLogger()
        root.setLevel(settings.log_level.upper())

        # 기존 핸들러 제거 (Lambda 재사용 시 중복 방지)
        root.handlers.clear()

        handler = logging.StreamHandler()
        if settings.is_prod:
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(_build_dev_formatter())

        root.addHandler(handler)

        _init_sentry(settings)

        _initialized = True


def get_logger(name: str) -> logging.Logger:
    """이름 기반 로거를 반환한다.

    첫 호출 시 ``setup_logging()``을 자동 실행한다.

    Args:
        name: 로거 이름. 일반적으로 ``__name__`` 사용.

    Returns:
        설정이 완료된 로거 인스턴스.
    """
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)


def _reset_logging() -> None:
    """테스트용 상태 리셋. 프로덕션 코드에서 사용 금지."""
    global _initialized  # noqa: PLW0603

    with _lock:
        root = logging.getLogger()
        root.handlers.clear()
        _initialized = False
