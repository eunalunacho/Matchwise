from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, HttpUrl, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContentType(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    PDF = "PDF"

# ──────────────────────────────────────────────
# 공고
# ──────────────────────────────────────────────

class Job(BaseModel):
    # 기본 정보
    id: str = Field(..., description="공고 고유 식별자(UUID 또는 사이트 제공 ID)")
    source: str = Field(..., description="사이트 출처 (Wanted, Jumpit 등)")
    company_name: str
    title: str
    url: HttpUrl
    deadline: datetime | None = None

    # 데이터 가공 정보
    content_type: ContentType
    raw_content_url: str = Field(..., description="S3에 저장된 원본 데이터 경로")
    cleaned_content: str = Field(..., description="분석용으로 정제된 텍스트 (OCR 결과 포함)")

    # 메타데이터 (에이전트가 추출)
    location: str | None = None
    tech_stack: list[str] = Field(default_factory=list)

    # 분석 결과 (나중에 업데이트됨)
    match_score: float | None = Field(None, ge=0, le=5.0, description="적합도 점수 (0.0~5.0)")
    scraped_at: datetime = Field(default_factory=_utcnow)

    # 리포팅 관리용 필드
    report_count: int = Field(0, description="리포트에 포함된 횟수")
    last_reported_at: datetime | None = Field(None, description="마지막 리포트 발송 일시")

    @property
    def is_new(self) -> bool:
        """한 번도 리포트되지 않은 신규 공고인지 확인"""
        return self.report_count == 0

# ──────────────────────────────────────────────
# 분석
# ──────────────────────────────────────────────

# 항목별 상세 점수를 위한 서브 모델
class CategoryScore(BaseModel):
    score: float = Field(..., ge=0, le=5.0, description="0.0 ~ 5.0 사이의 점수")
    reason: str = Field(..., description="해당 점수를 산정한 구체적 근거")

class MatchCategoryScores(BaseModel):
    tech_stack: CategoryScore = Field(..., description="기술 스택 일치도")
    engineering: CategoryScore = Field(..., description="엔지니어링 난이도")
    domain: CategoryScore = Field(..., description="도메인 연관성")
    pass_probability: CategoryScore = Field(..., description="서류 통과 확률")

# Fit/Gap 분석을 위한 서브 모델
class AnalysisPoint(BaseModel):
    title: str
    description: str

# 최종 매칭 결과 모델
class MatchResult(BaseModel):
    # 관계 설정
    job_id: str = Field(..., description="연결된 공고 ID")
    resume_version: str = Field(..., description="분석에 사용된 이력서 버전/날짜")

    # 종합 점수 및 요약
    total_score: float = Field(..., ge=0, le=5.0, description="전체 평균 매칭 점수")
    summary: str = Field(..., description="전체 분석 내용을 아우르는 한 줄 요약")
    reasoning: str = Field(..., description="전체적인 점수 산정 근거")

    # 항목별 상세 분석 결과
    category_scores: MatchCategoryScores = Field(..., description="4가지 카테고리별 상세 점수와 이유")

    # 상세 포인트 및 액션 가이드
    fit_points: list[AnalysisPoint] = Field(default_factory=list, description="강점 및 일치 항목")
    gap_points: list[AnalysisPoint] = Field(default_factory=list, description="약점 및 부족 항목")
    resume_tips: list[str] = Field(default_factory=list, description="이력서 커스터마이징 팁")
    learning_roadmap: list[str] = Field(default_factory=list, description="보완이 필요한 기술 학습 로드맵")

    # 메타데이터
    analyzed_at: datetime = Field(default_factory=_utcnow)


# ──────────────────────────────────────────────
# 사용자 성향 및 이력 정보
# ──────────────────────────────────────────────

# 태스크별 부하 가중치 (에이전트 판단 근거)
class TaskWeight(int, Enum):
    RESUME_WRITING = 2   # 이력서 작성: 중간 부하
    CODING_TEST = 3      # 코딩 테스트: 높은 집중력 필요
    INTERVIEW_PREP = 5   # 면접 준비: 최고 부하 (정신적/시간적)
    TEST_EXAM = 4        # 일반 시험/코테: 높은 부하

class ActiveTask(BaseModel):
    task_type: TaskWeight
    company_name: str
    deadline: datetime
    description: str | None = None

# 사용자 성향 및 이력 정보
class UserProfile(BaseModel):
    # 기본 정보 및 직무
    name: str
    target_positions: list[str] = Field(..., description="선호 직무명 (예: 백엔드 개발자, 데이터 엔지니어)")
    years_of_experience: int = 0

    # 역량 정보
    resume_summary: str = Field(..., description="이력서 핵심 요약 텍스트")
    tech_stack: list[str] = Field(..., description="보유 기술 스택 리스트")

    # 선호도 (Preference Learning Agent가 업데이트할 영역)
    preferences: dict[str, str] = Field(
        default_factory=lambda: {"work_type": "hybrid", "min_salary": "5000"},
        description="재택여부, 연봉, 도메인 등 선호 환경"
    )
    disliked_points: list[str] = Field(default_factory=list, description="기피하는 공고 특징")

    # 현재 진행 중인 태스크 (부하도 계산용)
    active_tasks: list[ActiveTask] = Field(default_factory=list)

    # 설정
    max_load_capacity: int = 10  # 내가 감당 가능한 최대 부하 점수
    last_updated: datetime = Field(default_factory=_utcnow)

    @property
    def current_load_score(self) -> int:
        """현재 진행 중인 모든 태스크의 부하 합산"""
        return sum(task.task_type.value for task in self.active_tasks)

    @property
    def alert_mode(self) -> str:
        """부하도에 따른 알림 모드 결정"""
        load = self.current_load_score
        if load >= self.max_load_capacity:
            return "EXTREME_BUSY"  # 알림 중단 혹은 초긴급 건만 발송
        if load >= self.max_load_capacity * 0.7:
            return "BUSY"          # 발송 주기 늘림 (예: 1일 1회 -> 2일 1회)
        return "NORMAL"            # 실시간 혹은 정기 발송

# ──────────────────────────────────────────────
# 지원 현황
# ──────────────────────────────────────────────

class ApplicationStep(str, Enum):
    TODO = "TODO"
    APPLIED = "APPLIED"
    TEST = "TEST"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"

class StatusLog(BaseModel):
    step: ApplicationStep
    changed_at: datetime = Field(default_factory=_utcnow)
    comment: str | None = None

class ApplicationRecord(BaseModel):
    # 관계 정보
    job_id: str
    company_name: str  # 조인 없이 바로 알 수 있게 편의상 추가

    # 상태 관리
    current_step: ApplicationStep = ApplicationStep.TODO
    priority: int = Field(3, ge=1, le=5) # 1(낮음) ~ 5(매우 높음)

    # 일정 관리
    deadline: datetime | None = None  # 현재 단계의 마감일
    last_notified_at: datetime | None = None # 에이전트가 마지막으로 말 건 시간

    # 히스토리 및 노트
    history: list[StatusLog] = Field(default_factory=list)
    notes: str = "" # 내가 쓴 메모
    agent_tips: list[str] = Field(default_factory=list) # 에이전트가 조사해준 기술 면접 팁 등

    @property
    def days_since_last_update(self) -> int:
        """마지막 업데이트 후 며칠 지났는지 계산"""
        if not self.history:
            return 0
        last_date = self.history[-1].changed_at
        return (datetime.now(timezone.utc) - last_date).days


# ──────────────────────────────────────────────
# 기업 정보
# ──────────────────────────────────────────────

class CompanyType(str, Enum):
    CONGLOMERATE = "대기업"
    MEDIUM = "중견기업"
    SME = "중소기업"
    STARTUP = "스타트업"
    PUBLIC = "공공기관"
    FOREIGN = "외국계"

class NewsItem(BaseModel):
    title: str
    url: HttpUrl
    published_at: datetime | None = None

class CompanyDetail(BaseModel):
    # 1. 기본 프로필
    name: str = Field(..., description="기업명")
    industry: str = Field(..., description="산업 분야 (예: 핀테크, 커머스)")
    company_type: CompanyType = Field(..., description="기업 형태")
    established_date: str | None = Field(None, description="설립일")
    homepage_url: HttpUrl | None = None
    location: str | None = Field(None, description="본사 소재지")

    # 2. 규모 및 재무 (Scale)
    revenue: str | None = Field(None, description="최근 매출액 (예: 500억)")
    employee_count: int | None = Field(None, description="전체 직원 수")
    funding_stage: str | None = Field(None, description="투자 단계 (예: Series B, 상장)")

    # 3. 평판 (Reputation)
    average_rating: float = Field(0.0, ge=0, le=5.0, description="전체 별점 리뷰 (5점 만점)")
    revenue_model: str = Field(..., description="수익 구조 및 비즈니스 모델 요약")

    # 4. 최신 동향
    core_products: list[str] = Field(default_factory=list, description="주요 서비스 및 제품 리스트")
    recent_news: list[NewsItem] = Field(default_factory=list, description="최근 주요 뉴스나 투자 소식")

    # 5. 메타데이터
    last_updated: datetime = Field(default_factory=_utcnow)
