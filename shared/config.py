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

from pydantic import BaseModel, SecretStr, field_validator
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



class GeminiSettings(BaseSettings):
    """Google Gemini 설정 (선택, 대체 LLM)."""

    model_config = SettingsConfigDict(
        env_prefix="GEMINI_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr | None = None
    model: str = "gemini-3.5-flash-lite"


class DartSettings(BaseSettings):
    """금융감독원 DART OpenAPI 설정."""

    model_config = SettingsConfigDict(
        env_prefix="DART_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr | None = None
    cache_dir: str = ".dart_cache"


class DiscordSettings(BaseSettings):
    """Discord Bot + OAuth2(계정 연결) 설정."""

    model_config = SettingsConfigDict(
        env_prefix="DISCORD_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = SecretStr("dummy")
    guild_id: int | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    # 매칭 공고 채널은 사용자마다 다르므로 env가 아니라 users.match_channel_id에 둔다(2026-10-15).
    # 수집 파이프라인 진행 알림을 보낼 비공개 채널 웹훅 (DISCORD_INGEST_WEBHOOK_URL). 없으면 전송 안 함.
    ingest_webhook_url: SecretStr | None = None


class GoogleSettings(BaseSettings):
    """구글 로그인(OAuth2) 설정."""

    model_config = SettingsConfigDict(
        env_prefix="GOOGLE_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None


class GCPSettings(BaseSettings):
    """Google Cloud 설정 — GCP_ 접두사 하나로 통합 (project_id 공통값 + Document AI 리소스 값).

    GOOGLE_APPLICATION_CREDENTIALS는 Google SDK가 직접 읽는 표준 환경변수라 여기 필드로 안 두고
    .env에만 둔다. 지금은 ADC(위장 로그인, `gcloud auth application-default login
    --impersonate-service-account=...`)를 쓰므로 비워둔다.
    """

    model_config = SettingsConfigDict(
        env_prefix="GCP_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_id: str | None = None
    project_location: str = "us"
    doc_processor_id: str | None = None


class DatabaseSettings(BaseSettings):
    """PostgreSQL + 앱 URL 설정."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./matchwise_dev.db"
    web_base_url: str = "http://localhost:8000"



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

    cleanup_hour: int = 1
    pipeline_hour: int = 8  # 수집 → 추출 → 채점 → 알림을 이어서 실행 (run_daily_pipeline)
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
    """MatchWise 전체 설정."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.DEV
    debug: bool = False
    log_level: str = "INFO"
    app_name: str = "matchwise"
    session_secret_key: str = "dev-insecure-session-key"  # 운영에선 .env로 반드시 덮어쓸 것

    # OCR 백엔드 선택. 아래 값은 .env에 없을 때만 쓰는 기본값 — 실제로는 .env의
    # OCR_PROVIDER / CLAUDE_OCR_MODEL이 항상 우선한다(session_secret_key와 동일한 패턴).
    # ocr_provider: "document_ai"(기본) | "claude_code" — extract_job()의 _ocr_image()가 이 값으로 분기.
    # claude_ocr_model: claude_code를 쓸 때의 모델. haiku는 환각을 일으켜(2026-09-30 실측, 회사명
    # 오독) sonnet 고정 — .env로 더 싼 모델로 바꾸지 말 것.
    ocr_provider: str = "document_ai"
    claude_ocr_model: str = "sonnet"

    @field_validator("ocr_provider", "claude_ocr_model", mode="before")
    @classmethod
    def _blank_env_falls_back_to_default(cls, value, info):
        """`.env`에 `KEY=`처럼 빈 값으로만 남겨둔 경우, pydantic-settings는 이를 "명시적으로 빈
        문자열"로 보고 기본값을 안 쓴다 — 그러면 이 값이 잘못된 provider로 취급돼 조용히 에러를
        낸다. 빈 문자열/공백만 있는 값은 "설정 안 함"으로 보고 기본값을 쓰게 한다.
        """
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    gemini: GeminiSettings = GeminiSettings()
    dart: DartSettings = DartSettings()
    gcp: GCPSettings = GCPSettings()
    discord: DiscordSettings = DiscordSettings()
    google: GoogleSettings = GoogleSettings()
    db: DatabaseSettings = DatabaseSettings()
    crawling: CrawlingConfig = CrawlingConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    analysis: AnalysisConfig = AnalysisConfig()

    @property
    def is_prod(self) -> bool:
        return self.env == Environment.PROD

    @property
    def is_dev(self) -> bool:
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
