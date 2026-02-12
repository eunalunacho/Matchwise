"""MatchWise 설정 모듈.

모든 Agent/Tool/Storage 모듈이 공유하는 설정의 단일 진입점.
환경변수를 읽고 검증하며, 서비스별 설정을 구조화한다.

Usage::

    from shared.config import get_settings

    settings = get_settings()
    settings.aws.region          # "ap-northeast-2"
    settings.openai.api_key.get_secret_value()  # 실제 키 값
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = str(_PROJECT_ROOT / ".env")


class Environment(str, Enum):
    """실행 환경."""

    DEV = "DEV"
    PROD = "PROD"


# ──────────────────────────────────────────────
# 외부 서비스 설정 (env_prefix로 환경변수 매핑)
# ──────────────────────────────────────────────


class AWSSettings(BaseSettings):
    """AWS 관련 설정.

    Lambda/ECS에서는 IAM Role을 사용하므로 키가 선택사항.
    """

    model_config = SettingsConfigDict(
        env_prefix="AWS_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    region: str = "ap-northeast-2"
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    s3_bucket: str = "matchwise-data"
    dynamodb_table_prefix: str = "matchwise"


class PineconeSettings(BaseSettings):
    """Pinecone Vector DB 설정."""

    model_config = SettingsConfigDict(
        env_prefix="PINECONE_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr
    environment: str = "us-east-1"
    index_name: str = "matchwise-jobs"


class OpenAISettings(BaseSettings):
    """OpenAI API 설정."""

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    max_retries: int = 3
    request_timeout: int = 60


class GeminiSettings(BaseSettings):
    """Google Gemini 설정 (선택, 대체 LLM)."""

    model_config = SettingsConfigDict(
        env_prefix="GEMINI_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr | None = None


class SlackSettings(BaseSettings):
    """Slack Bot 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SLACK_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr
    app_token: SecretStr
    signing_secret: SecretStr
    channel: str = "matchwise"


class SentrySettings(BaseSettings):
    """Sentry 모니터링 설정 (선택)."""

    model_config = SettingsConfigDict(
        env_prefix="SENTRY_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dsn: str | None = None
    traces_sample_rate: float = 0.1


# ──────────────────────────────────────────────
# 내부 설정 (환경변수 불필요, 코드 기본값)
# ──────────────────────────────────────────────


class CrawlingConfig(BaseModel):
    """크롤링 Agent 설정."""

    headless: bool = True
    timeout_ms: int = 30_000
    max_concurrent_pages: int = 3
    retry_count: int = 2
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )


class ScheduleConfig(BaseModel):
    """스케줄 설정 (KST 기준)."""

    crawl_hour: int = 2
    analyze_hour: int = 5
    brief_hour: int = 9
    timezone: str = "Asia/Seoul"


class AnalysisConfig(BaseModel):
    """분석 Agent 설정."""

    match_score_threshold: float = 0.7
    max_jobs_per_batch: int = 50
    gap_analysis_enabled: bool = True


# ──────────────────────────────────────────────
# 최상위 Settings
# ──────────────────────────────────────────────


class Settings(BaseSettings):
    """MatchWise 전체 설정.

    `.env` 파일에서 환경변수를 자동으로 읽고 검증한다.
    하위 서비스 설정은 각각의 env_prefix로 매핑된다.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 환경/디버그
    env: Environment = Environment.DEV
    debug: bool = False
    log_level: str = "INFO"
    app_name: str = "matchwise"

    # 외부 서비스
    aws: AWSSettings = AWSSettings()
    pinecone: PineconeSettings = PineconeSettings()
    openai: OpenAISettings = OpenAISettings()
    gemini: GeminiSettings = GeminiSettings()
    slack: SlackSettings = SlackSettings()
    sentry: SentrySettings = SentrySettings()

    # 내부 설정
    crawling: CrawlingConfig = CrawlingConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    analysis: AnalysisConfig = AnalysisConfig()

    @property
    def is_prod(self) -> bool:
        """프로덕션 환경 여부."""
        return self.env == Environment.PROD

    @property
    def is_dev(self) -> bool:
        """개발 환경 여부."""
        return self.env == Environment.DEV


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 싱글톤을 반환한다.

    첫 호출시 `.env` 로드 + 검증, 이후 캐시 반환.
    Lambda 컨테이너당 1회 초기화 후 재사용.
    """
    return Settings()


def clear_settings_cache() -> None:
    """설정 캐시를 초기화한다.

    테스트에서 환경변수를 변경한 후 설정을 다시 로드할 때 사용.
    """
    get_settings.cache_clear()
