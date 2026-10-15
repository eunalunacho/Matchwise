from datetime import datetime, timezone
from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey,
    Integer, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    # 구글 로그인으로 먼저 가입하고 디스코드는 나중에 연결할 수 있어 discord_id는 nullable.
    discord_id = Column(String(64), unique=True, nullable=True)
    google_sub = Column(String(255), unique=True, nullable=True)  # 구글 계정 고유 ID
    email = Column(String(255), nullable=True)
    username = Column(String(128), nullable=False)
    resume_text = Column(Text)
    experience_text = Column(Text)
    basic_info = Column(JSON)  # 이름·병역·학력·어학·자격증 등 자유 key-value (DESIGN.md 5-8, scripts/import_profile.py)
    # resume_text/experience_text는 basic_info+experiences로 대체됨(2026-10-14) — 채점에 쓰지 않는 옛 칸
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    scores = relationship("JobScore", back_populates="user")
    drafts = relationship("ResumeDraft", back_populates="user")
    applications = relationship("Application", back_populates="user")
    experiences = relationship("Experience", back_populates="user")


class RawPosting(Base):
    """크롤러가 넘긴 RawJob을 해석 없이 그대로 보존하는 원본 창고.

    (source, url) 기준으로 한 번만 쌓이고(insert-if-absent) 이후 수정·삭제하지 않는다 —
    cleanup_job도 이 테이블은 건드리지 않는다. 2차 저장 단계(추출)가 이 원본을 입력으로 쓴다.
    """
    __tablename__ = "raw_postings"
    __table_args__ = (UniqueConstraint("source", "url"),)
    id = Column(Integer, primary_key=True)
    source = Column(String(64), nullable=False)
    url = Column(Text, nullable=False)
    apply_url = Column(Text)
    title = Column(String(512))
    company = Column(String(256))
    deadline = Column(DateTime(timezone=True))
    jd_text = Column(Text)
    payload = Column(JSON)  # 원본 응답/HTML 조각. 해석 없이 그대로.
    hints = Column(JSON)  # 크롤러가 정규화한 힌트 (career_level 등)
    image_urls = Column(JSON)  # JD 본문의 이미지 주소 목록
    fetched_at = Column(DateTime(timezone=True), default=_utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    source = Column(String(64), nullable=False)
    url = Column(Text, unique=True, nullable=False)
    title = Column(String(512))
    company = Column(String(256))
    # extract_status: pending(추출 대기) | needs_ocr(이미지 JD, OCR 대기) | empty(텍스트·이미지 둘 다 없음)
    #                 | ok(추출 완료) | failed(추출·OCR 실패)
    # crawl_job이 저장 시 앞의 셋 중 하나로 자동 분류하고, extract_job이 ok/failed로 전환한다.
    # failed·needs_ocr는 수집한 날(crawled_at)과 다음 날까지만 재시도한다(extract_job 참고).
    extract_status = Column(String(16), nullable=False, default="pending")
    jd_text = Column(Text)  # needs_ocr 공고는 extract_job이 OCR 결과로 채운다
    # 아래 5개는 추출 배치(extract_job)가 jd_text에서 공고당 1회 추출해 채운다.
    jd_tasks = Column(JSON)  # list[str]
    jd_stack = Column(JSON)  # list[str]
    jd_employment_type = Column(String(64))
    jd_qualifications = Column(JSON)  # list[str]
    jd_preferred = Column(JSON)  # list[str]
    # eligibility: {"new_grad_ok": bool|None, "min_years": int|None, "evidence": str}
    # jd_tasks 등과 함께 extract_job()이 채운다. 미추출이면 None.
    eligibility = Column(JSON)
    # work_locations: list[str] — JD 원문의 근무지(예: "서울 강남구"). extract_job이 채운다. 미기재면 [].
    work_locations = Column(JSON)
    deadline = Column(DateTime(timezone=True))
    crawled_at = Column(DateTime(timezone=True), default=_utcnow)
    scores = relationship("JobScore", back_populates="job")
    questions = relationship("ApplicationQuestion", back_populates="job")
    drafts = relationship("ResumeDraft", back_populates="job")
    applications = relationship("Application", back_populates="job")


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (UniqueConstraint("job_id", "user_id"),)
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    fit_score = Column(SmallInteger)
    fit_reasons = Column(Text)
    # 항목별 점수·관문·고른 직무·일치/부족 요건 (analysis/scorer.py::score_fit). fit_score는 이걸로 코드가 계산한 총점
    fit_detail = Column(JSON)
    company_score = Column(SmallInteger)
    dismissed = Column(Boolean, default=False)  # "별로임" 피드백 — 이후 추천에서 제외
    scored_at = Column(DateTime(timezone=True), default=_utcnow)
    job = relationship("Job", back_populates="scores")
    user = relationship("User", back_populates="scores")


class GeminiUsage(Base):
    """날짜별 Gemini 호출 횟수 카운터.

    extract_job(summarize_jd)과 analyze_job(score_fit)이 같은 무료 할당량(gemini-3.5-flash-lite)을
    나눠 쓰므로, 하루 상한(scheduler/jobs.py::_GEMINI_DAILY_CAP)을 지키기 위해 두 배치가
    이 카운터를 공유한다.
    """
    __tablename__ = "gemini_usage"
    usage_date = Column(Date, primary_key=True)
    call_count = Column(Integer, nullable=False, default=0)


class Application(Base):
    """지원 상태 추적. status: interested | will_apply | submitted | rejected | passed."""
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", "user_id"),)
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(32), nullable=False, default="interested")
    will_apply_at = Column(DateTime(timezone=True))
    submitted_at = Column(DateTime(timezone=True))
    interview_at = Column(DateTime(timezone=True))  # 사용자가 직접 입력
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    job = relationship("Job", back_populates="applications")
    user = relationship("User", back_populates="applications")


class Experience(Base):
    """자소서·초안 생성에 재사용할 경험 조각. DESIGN.md 5-8 — pgvector 없는 경량판.

    kind: 'career' | 'project' | 'award' | 'campus' | 'club' | 'team_project' | 'education' | 'volunteer' | 'story'
    (표시 이름은 analysis/profile.py::KIND_LABELS). 채점(analyze_job)과 초안 생성의 근거 데이터.
    """
    __tablename__ = "experiences"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(32), nullable=False)
    title = Column(String(256))
    period = Column(String(64))
    content = Column(Text, nullable=False)
    tags = Column(JSON)  # list[str]
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    user = relationship("User", back_populates="experiences")


class ApplicationQuestion(Base):
    __tablename__ = "application_questions"
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    question = Column(Text, nullable=False)
    order_num = Column(SmallInteger, default=0)
    job = relationship("Job", back_populates="questions")
    drafts = relationship("ResumeDraft", back_populates="question")


class ResumeDraft(Base):
    __tablename__ = "resume_drafts"
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("application_questions.id"), nullable=False)
    answer = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    job = relationship("Job", back_populates="drafts")
    user = relationship("User", back_populates="drafts")
    question = relationship("ApplicationQuestion", back_populates="drafts")
