from datetime import datetime, timezone
from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey,
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
    discord_id = Column(String(64), unique=True, nullable=False)
    username = Column(String(128), nullable=False)
    resume_text = Column(Text)
    experience_text = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    scores = relationship("JobScore", back_populates="user")
    drafts = relationship("ResumeDraft", back_populates="user")


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    source = Column(String(64), nullable=False)
    url = Column(Text, unique=True, nullable=False)
    title = Column(String(512))
    company = Column(String(256))
    jd_text = Column(Text)
    jd_summary = Column(JSON)  # {"tasks": [...], "stack": [...], "requirements": "..."} — 분석 배치에서 공고당 1회 생성
    deadline = Column(DateTime(timezone=True))
    crawled_at = Column(DateTime(timezone=True), default=_utcnow)
    scores = relationship("JobScore", back_populates="job")
    questions = relationship("ApplicationQuestion", back_populates="job")
    drafts = relationship("ResumeDraft", back_populates="job")


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (UniqueConstraint("job_id", "user_id"),)
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    fit_score = Column(SmallInteger)
    fit_reasons = Column(Text)
    company_score = Column(SmallInteger)
    dismissed = Column(Boolean, default=False)  # "별로임" 피드백 — 이후 추천에서 제외
    scored_at = Column(DateTime(timezone=True), default=_utcnow)
    job = relationship("Job", back_populates="scores")
    user = relationship("User", back_populates="scores")


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
