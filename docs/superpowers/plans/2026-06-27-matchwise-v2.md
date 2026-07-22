# MatchWise v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매일 채용 공고를 수집·분석하여 Discord로 알림하고, 사용자 승인 시 지원서 초안을 생성하는 멀티유저 시스템 구축

**Architecture:** EC2 t2.micro 위에 Docker Compose로 5개 서비스(postgres, scheduler, crawler, web-api, discord-bot) 운영. 기존 LocalStack/S3/DynamoDB/Pinecone 의존성 제거. 분석·초안 생성은 Gemini Flash API(이미 config에 존재) + LangChain.

**Tech Stack:** Python 3.12, discord.py 2.x, FastAPI+Jinja2, Playwright+BS4, langchain-google-genai (Gemini Flash), PostgreSQL+SQLAlchemy 2.x, APScheduler 3.x, Docker Compose, GitHub Actions

---

## File Map

| 파일 | 작업 | 역할 |
|------|------|------|
| `docker-compose.yml` | replace | postgres/web-api/discord-bot/scheduler/crawler |
| `.env.example` | create | 환경변수 템플릿 |
| `pyproject.toml` | modify | discord.py/sqlalchemy/apscheduler 추가, slack/boto3/pinecone 제거 |
| `shared/config.py` | modify | DiscordSettings + DatabaseSettings 추가, SlackSettings 교체 |
| `shared/db.py` | create | SQLAlchemy engine + session factory |
| `db/__init__.py` | create | 패키지 |
| `db/orm.py` | create | users/jobs/job_scores/application_questions/resume_drafts ORM |
| `db/init_db.py` | create | 테이블 생성 스크립트 |
| `crawler/__init__.py` | create | 패키지 |
| `crawler/base.py` | create | RawJob 데이터클래스 |
| `crawler/inthiswork.py` | create | inthiswork.com/it 크롤러 |
| `crawler/superookie.py` | create | superookie.com 크롤러 |
| `analysis/__init__.py` | create | 패키지 |
| `analysis/gemini_client.py` | create | Gemini Flash 래퍼 |
| `analysis/scorer.py` | create | fit_score + company_score 계산 |
| `scheduler/__init__.py` | create | 패키지 |
| `scheduler/jobs.py` | create | crawl/analyze/notify 함수 |
| `scheduler/main.py` | create | APScheduler 진입점 |
| `bot/__init__.py` | create | 패키지 |
| `bot/main.py` | create | discord.py Bot 진입점 |
| `bot/notifications.py` | create | 일일 DM 발송 로직 |
| `bot/interactions.py` | create | 버튼 핸들러 |
| `web/__init__.py` | create | 패키지 |
| `web/deps.py` | create | FastAPI 공용 의존성 (get_db) |
| `web/main.py` | create | FastAPI 앱 |
| `web/routers/__init__.py` | create | 패키지 |
| `web/routers/dashboard.py` | create | /dashboard, /jobs/{id} 라우터 |
| `web/routers/profile.py` | create | /profile 라우터 |
| `web/routers/draft.py` | create | /draft/{job_id} 라우터 |
| `web/templates/base.html` | create | 기본 레이아웃 |
| `web/templates/dashboard.html` | create | 공고 목록 |
| `web/templates/jobs_detail.html` | create | 공고 상세 + 질문 등록 |
| `web/templates/profile.html` | create | 프로필 관리 |
| `web/templates/draft.html` | create | 초안 조회 |
| `draft/__init__.py` | create | 패키지 |
| `draft/agent.py` | create | LangChain 기반 초안 생성 |
| `scripts/add_user.py` | create | 관리자용 유저 추가 CLI |
| `tests/conftest.py` | create | pytest fixtures (SQLite in-memory) |
| `tests/test_orm.py` | create | ORM CRUD 테스트 |
| `tests/test_crawler.py` | create | 크롤러 파싱 테스트 (mock HTML) |
| `tests/test_scorer.py` | create | 점수 계산 테스트 (mock Gemini) |
| `tests/test_notifications.py` | create | 알림 포매팅 테스트 |
| `tests/test_draft.py` | create | 초안 생성 테스트 (mock LLM) |
| `tests/test_web.py` | create | FastAPI 엔드포인트 테스트 |
| `.github/workflows/deploy.yml` | create | EC2 SSH 자동 배포 |
| `Dockerfile` | replace | 멀티스테이지 (web/bot/scheduler/crawler) |

---

## Task 1: 의존성 + Docker Compose + Dockerfile 재구성

**Files:**
- Modify: `pyproject.toml`
- Replace: `docker-compose.yml`
- Replace: `Dockerfile`
- Create: `.env.example`

- [ ] **Step 1: pyproject.toml 의존성 업데이트**

`pyproject.toml`의 `dependencies` 배열을 다음으로 교체:

```toml
[project]
name = "matchwise"
version = "0.2.0"
description = "Job hunting AI agent — multi-user, Discord-based"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "jinja2>=3.1.0",
    "python-multipart>=0.0.9",
    "sqlalchemy>=2.0.0",
    "psycopg2-binary>=2.9.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
    "langchain>=0.3.0",
    "langchain-google-genai>=2.0.0",
    "playwright>=1.48.0",
    "beautifulsoup4>=4.12.0",
    "apscheduler>=3.10.0",
    "discord.py>=2.4.0",
    "python-dotenv>=1.0.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.14.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: docker-compose.yml 교체**

`docker-compose.yml` 전체를 다음으로 교체:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: matchwise
      POSTGRES_USER: matchwise
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-matchwise_dev}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U matchwise"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - matchwise-net

  web-api:
    build:
      context: .
      target: web
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://matchwise:${POSTGRES_PASSWORD:-matchwise_dev}@postgres:5432/matchwise
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - matchwise-net

  discord-bot:
    build:
      context: .
      target: bot
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://matchwise:${POSTGRES_PASSWORD:-matchwise_dev}@postgres:5432/matchwise
      - WEB_BASE_URL=http://${EC2_HOST:-localhost}:8000
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - matchwise-net

  scheduler:
    build:
      context: .
      target: scheduler
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://matchwise:${POSTGRES_PASSWORD:-matchwise_dev}@postgres:5432/matchwise
      - WEB_BASE_URL=http://${EC2_HOST:-localhost}:8000
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - matchwise-net

  crawler:
    build:
      context: .
      target: crawler
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://matchwise:${POSTGRES_PASSWORD:-matchwise_dev}@postgres:5432/matchwise
    depends_on:
      postgres:
        condition: service_healthy
    profiles:
      - crawl
    deploy:
      resources:
        limits:
          memory: 1500m
    networks:
      - matchwise-net

volumes:
  postgres-data:

networks:
  matchwise-net:
    driver: bridge
```

- [ ] **Step 3: Dockerfile 교체**

`Dockerfile` 전체를 다음으로 교체:

```dockerfile
FROM python:3.12-slim AS base
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

FROM base AS web
COPY . .
CMD ["uv", "run", "uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS bot
COPY . .
CMD ["uv", "run", "python", "-m", "bot.main"]

FROM base AS scheduler
COPY . .
CMD ["uv", "run", "python", "-m", "scheduler.main"]

FROM base AS crawler
RUN uv run playwright install chromium --with-deps
COPY . .
CMD ["uv", "run", "python", "-m", "crawler.main"]
```

- [ ] **Step 4: .env.example 생성**

```bash
# Database
POSTGRES_PASSWORD=matchwise_dev
DATABASE_URL=postgresql://matchwise:matchwise_dev@localhost:5432/matchwise

# Discord
DISCORD_BOT_TOKEN=your-discord-bot-token

# Gemini
GEMINI_API_KEY=your-gemini-api-key

# App
EC2_HOST=your-ec2-public-ip
WEB_BASE_URL=http://your-ec2-public-ip:8000
ENV=DEV
```

- [ ] **Step 5: 커밋**

```bash
git add pyproject.toml docker-compose.yml Dockerfile .env.example
git commit -m "feat: restructure to postgres + discord + docker compose v2"
```

---

## Task 2: Config 업데이트

**Files:**
- Modify: `shared/config.py`

- [ ] **Step 1: DiscordSettings + DatabaseSettings 추가**

`shared/config.py`에서 `SlackSettings` 클래스 전체를 다음으로 교체:

```python
class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DISCORD_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = SecretStr("dummy")
    guild_id: int | None = None


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./matchwise_dev.db"
    web_base_url: str = "http://localhost:8000"
```

`Settings` 클래스를 다음으로 교체 (AWS/Pinecone/OpenAI/Slack/Sentry/Gmail 제거):

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.DEV
    debug: bool = False
    log_level: str = "INFO"
    app_name: str = "matchwise"

    gemini: GeminiSettings = GeminiSettings()
    discord: DiscordSettings = DiscordSettings()
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
```

- [ ] **Step 2: 커밋**

```bash
git add shared/config.py
git commit -m "feat: replace Slack/AWS config with Discord + PostgreSQL settings"
```

---

## Task 3: ORM 모델 + DB 초기화

**Files:**
- Create: `db/__init__.py`
- Create: `db/orm.py`
- Create: `db/init_db.py`
- Create: `shared/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_orm.py`

- [ ] **Step 1: 테스트 작성**

`tests/conftest.py`:
```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.orm import Base

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)

@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
```

`tests/test_orm.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError
from db.orm import User, Job, JobScore

def test_create_user(session):
    user = User(discord_id="123456789", username="tester")
    session.add(user)
    session.commit()
    found = session.query(User).filter_by(discord_id="123456789").first()
    assert found is not None
    assert found.username == "tester"

def test_create_job(session):
    job = Job(source="inthiswork", url="https://example.com/job/1", title="Backend Dev", company="Acme")
    session.add(job)
    session.commit()
    found = session.query(Job).filter_by(url="https://example.com/job/1").first()
    assert found is not None

def test_job_score_unique_constraint(session):
    user = User(discord_id="111", username="u1")
    job = Job(source="inthiswork", url="https://example.com/job/2", title="Dev", company="Co")
    session.add_all([user, job])
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=80))
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=90))
    with pytest.raises(IntegrityError):
        session.flush()
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_orm.py -v
```
Expected: `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: ORM 구현**

`db/__init__.py`: 빈 파일

`db/orm.py`:
```python
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
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
```

`shared/db.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from shared.config import get_settings

def get_engine():
    return create_engine(get_settings().db.database_url)

def get_session() -> Session:
    Session = sessionmaker(bind=get_engine())
    return Session()
```

`db/init_db.py`:
```python
from db.orm import Base
from shared.db import get_engine

def init():
    Base.metadata.create_all(get_engine())
    print("Tables created.")

if __name__ == "__main__":
    init()
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_orm.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add db/ shared/db.py tests/conftest.py tests/test_orm.py
git commit -m "feat: add SQLAlchemy ORM models and DB session"
```

---

## Task 4: Inthiswork 크롤러

**Files:**
- Create: `crawler/__init__.py`
- Create: `crawler/base.py`
- Create: `crawler/inthiswork.py`
- Create: `tests/test_crawler.py`

- [ ] **Step 1: 테스트 작성**

`tests/test_crawler.py`:
```python
from crawler.inthiswork import parse_job_list, parse_job_detail

SAMPLE_LIST_HTML = """
<ul>
  <li class="job-item">
    <a href="/jobs/123">
      <span class="company">Acme Corp</span>
      <span class="title">백엔드 개발자</span>
    </a>
  </li>
</ul>
"""

SAMPLE_DETAIL_HTML = """
<div class="jd-content">Python, FastAPI 경험자 우대. AWS Lambda 사용.</div>
"""

def test_parse_job_list_extracts_url_and_meta():
    results = parse_job_list(SAMPLE_LIST_HTML, base_url="https://inthiswork.com")
    assert len(results) == 1
    assert results[0]["url"] == "https://inthiswork.com/jobs/123"
    assert results[0]["company"] == "Acme Corp"
    assert results[0]["title"] == "백엔드 개발자"

def test_parse_job_detail_extracts_jd_text():
    result = parse_job_detail(SAMPLE_DETAIL_HTML)
    assert "Python" in result["jd_text"]
    assert "FastAPI" in result["jd_text"]
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_crawler.py -v
```
Expected: `ImportError: cannot import name 'parse_job_list'`

- [ ] **Step 3: 구현**

`crawler/__init__.py`: 빈 파일

`crawler/base.py`:
```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class RawJob:
    source: str
    url: str
    title: str
    company: str
    jd_text: str
    deadline: datetime | None = None
```

`crawler/inthiswork.py`:
```python
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from crawler.base import RawJob

BASE_URL = "https://inthiswork.com"
LIST_URL = f"{BASE_URL}/it"

def parse_job_list(html: str, base_url: str) -> list[dict]:
    """HTML에서 공고 목록을 파싱. CSS 셀렉터는 실제 사이트 구조 확인 후 조정."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select(".job-item"):
        link = item.find("a")
        if not link:
            continue
        href = link.get("href", "")
        company_el = item.select_one(".company")
        title_el = item.select_one(".title")
        url = f"{base_url}{href}" if href.startswith("/") else href
        results.append({
            "url": url,
            "company": company_el.get_text(strip=True) if company_el else "",
            "title": title_el.get_text(strip=True) if title_el else "",
        })
    return results

def parse_job_detail(html: str) -> dict:
    """HTML에서 JD 본문을 파싱. CSS 셀렉터는 실제 사이트 구조 확인 후 조정."""
    soup = BeautifulSoup(html, "html.parser")
    jd_el = soup.select_one(".jd-content")
    return {"jd_text": jd_el.get_text(strip=True) if jd_el else ""}

async def crawl() -> list[RawJob]:
    jobs = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(LIST_URL, timeout=30000)
        listings = parse_job_list(await page.content(), BASE_URL)
        for item in listings:
            await page.goto(item["url"], timeout=30000)
            detail = parse_job_detail(await page.content())
            jobs.append(RawJob(
                source="inthiswork",
                url=item["url"],
                title=item["title"],
                company=item["company"],
                jd_text=detail["jd_text"],
            ))
            await asyncio.sleep(1)
        await browser.close()
    return jobs
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_crawler.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add crawler/ tests/test_crawler.py
git commit -m "feat: add Inthiswork crawler with HTML parsers"
```

---

## Task 5: Superookie 크롤러

**Files:**
- Create: `crawler/superookie.py`

- [ ] **Step 1: 테스트 추가** (`tests/test_crawler.py` 하단에 추가)

```python
from crawler.superookie import parse_job_list as superookie_parse

SUPEROOKIE_LIST_HTML = """
<ul class="job-list">
  <li class="job-card">
    <a class="job-link" href="/jobs/456">
      <span class="company-name">Beta Inc</span>
      <span class="job-title">프론트엔드 개발자</span>
    </a>
  </li>
</ul>
"""

def test_superookie_parse_job_list():
    results = superookie_parse(SUPEROOKIE_LIST_HTML, base_url="https://www.superookie.com")
    assert len(results) == 1
    assert results[0]["url"] == "https://www.superookie.com/jobs/456"
    assert results[0]["company"] == "Beta Inc"
    assert results[0]["title"] == "프론트엔드 개발자"
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_crawler.py::test_superookie_parse_job_list -v
```

- [ ] **Step 3: 구현**

`crawler/superookie.py`:
```python
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from crawler.base import RawJob

BASE_URL = "https://www.superookie.com"
# 전체 URL은 실제 사이트에서 신입/주니어 필터 적용 후 주소창에서 복사할 것
LIST_URL = (
    f"{BASE_URL}/jobs/search"
    "?job_level[]=579f18168b129f673b4efebe"
    "&job_level[]=579f18168b129f673b4efebf"
    "&job_type=job"
)

def parse_job_list(html: str, base_url: str) -> list[dict]:
    """CSS 셀렉터는 실제 사이트 구조 확인 후 조정."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select(".job-card"):
        link = card.select_one(".job-link")
        if not link:
            continue
        href = link.get("href", "")
        company_el = card.select_one(".company-name")
        title_el = card.select_one(".job-title")
        results.append({
            "url": f"{base_url}{href}" if href.startswith("/") else href,
            "company": company_el.get_text(strip=True) if company_el else "",
            "title": title_el.get_text(strip=True) if title_el else "",
        })
    return results

def parse_job_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    jd_el = soup.select_one(".job-description") or soup.select_one(".description")
    return {"jd_text": jd_el.get_text(strip=True) if jd_el else ""}

async def crawl() -> list[RawJob]:
    jobs = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(LIST_URL, timeout=30000)
        listings = parse_job_list(await page.content(), BASE_URL)
        for item in listings:
            await page.goto(item["url"], timeout=30000)
            detail = parse_job_detail(await page.content())
            jobs.append(RawJob(
                source="superookie",
                url=item["url"],
                title=item["title"],
                company=item["company"],
                jd_text=detail["jd_text"],
            ))
            await asyncio.sleep(1)
        await browser.close()
    return jobs
```

- [ ] **Step 4: 전체 크롤러 테스트 통과 확인**

```
uv run pytest tests/test_crawler.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add crawler/superookie.py
git commit -m "feat: add Superookie crawler"
```

---

## Task 6: Gemini 클라이언트 + Fit 스코어러

**Files:**
- Create: `analysis/__init__.py`
- Create: `analysis/gemini_client.py`
- Create: `analysis/scorer.py`
- Create: `tests/test_scorer.py`

- [ ] **Step 1: 테스트 작성**

`tests/test_scorer.py`:
```python
from unittest.mock import MagicMock, patch
from analysis.scorer import score_job_for_user

def test_score_returns_valid_range():
    mock_response = MagicMock()
    mock_response.content = '{"fit_score": 82, "company_score": 70, "fit_reasons": "Python 경험 일치"}'

    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_response

    with patch("analysis.scorer.build_chain", return_value=mock_chain):
        result = score_job_for_user(
            jd_text="Python 백엔드 개발자. AWS 우대.",
            resume_text="Python 3년, FastAPI, AWS Lambda 경험.",
            experience_text="스타트업 결제 시스템 개발.",
        )

    assert 0 <= result["fit_score"] <= 100
    assert 0 <= result["company_score"] <= 100
    assert isinstance(result["fit_reasons"], str)
    assert len(result["fit_reasons"]) > 0

def test_score_handles_malformed_json():
    mock_response = MagicMock()
    mock_response.content = "분석할 수 없습니다."

    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_response

    with patch("analysis.scorer.build_chain", return_value=mock_chain):
        result = score_job_for_user(jd_text="", resume_text="", experience_text="")

    assert result["fit_score"] == 0
    assert result["company_score"] == 0
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_scorer.py -v
```

- [ ] **Step 3: 구현**

`analysis/__init__.py`: 빈 파일

`analysis/gemini_client.py`:
```python
from langchain_google_genai import ChatGoogleGenerativeAI
from shared.config import get_settings

def get_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=settings.gemini.model,
        google_api_key=settings.gemini.api_key.get_secret_value(),
        temperature=0.3,
    )
```

`analysis/scorer.py`:
```python
import json
import re
from langchain.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from analysis.gemini_client import get_llm

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a job fit analyzer. Respond ONLY with valid JSON, no markdown."),
    ("human", """
채용 공고 JD:
{jd_text}

지원자 이력서:
{resume_text}

지원자 경험 (협업, 갈등 해결 등):
{experience_text}

다음 JSON 형식으로만 답하세요:
{{"fit_score": <0-100 정수>, "company_score": <0-100 정수>, "fit_reasons": "<한국어 2-3줄 요약>"}}
"""),
])

def build_chain() -> Runnable:
    return _PROMPT | get_llm()

def score_job_for_user(jd_text: str, resume_text: str, experience_text: str) -> dict:
    response = build_chain().invoke({
        "jd_text": jd_text,
        "resume_text": resume_text or "",
        "experience_text": experience_text or "",
    })
    match = re.search(r"\{.*\}", response.content, re.DOTALL)
    if not match:
        return {"fit_score": 0, "company_score": 0, "fit_reasons": "분석 실패"}
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return {"fit_score": 0, "company_score": 0, "fit_reasons": "분석 실패"}
    return {
        "fit_score": max(0, min(100, int(data.get("fit_score", 0)))),
        "company_score": max(0, min(100, int(data.get("company_score", 0)))),
        "fit_reasons": str(data.get("fit_reasons", "")),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_scorer.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add analysis/ tests/test_scorer.py
git commit -m "feat: add Gemini client and fit scorer"
```

---

## Task 7: APScheduler 파이프라인

**Files:**
- Create: `scheduler/__init__.py`
- Create: `scheduler/jobs.py`
- Create: `scheduler/main.py`

- [ ] **Step 1: 구현**

`scheduler/__init__.py`: 빈 파일

`scheduler/jobs.py`:
```python
import asyncio
import time
from crawler.inthiswork import crawl as crawl_inthiswork
from crawler.superookie import crawl as crawl_superookie
from analysis.scorer import score_job_for_user
from shared.db import get_session
from db.orm import Job, User, JobScore

def crawl_job():
    """02:00 KST - 신규 공고 수집 후 중복 제거하여 저장"""
    session = get_session()
    try:
        all_raw = asyncio.run(crawl_inthiswork()) + asyncio.run(crawl_superookie())
        new_count = 0
        for raw in all_raw:
            if session.query(Job).filter_by(url=raw.url).first():
                continue
            session.add(Job(
                source=raw.source, url=raw.url,
                title=raw.title, company=raw.company,
                jd_text=raw.jd_text, deadline=raw.deadline,
            ))
            new_count += 1
        session.commit()
        print(f"[crawl] {new_count} new jobs saved.")
    finally:
        session.close()

def analyze_job():
    """05:00 KST - 미분석 공고×유저 조합에 대해 fit 점수 계산"""
    session = get_session()
    try:
        users = session.query(User).filter_by(is_active=True).all()
        all_jobs = session.query(Job).all()
        for job in all_jobs:
            for user in users:
                already = session.query(JobScore).filter_by(
                    job_id=job.id, user_id=user.id
                ).first()
                if already:
                    continue
                result = score_job_for_user(
                    jd_text=job.jd_text or "",
                    resume_text=user.resume_text or "",
                    experience_text=user.experience_text or "",
                )
                session.add(JobScore(
                    job_id=job.id, user_id=user.id,
                    fit_score=result["fit_score"],
                    company_score=result["company_score"],
                    fit_reasons=result["fit_reasons"],
                ))
                time.sleep(1)  # Gemini 무료 티어 rate limit 대응 (15 req/min)
        session.commit()
        print("[analyze] Done.")
    finally:
        session.close()

def notify_job():
    """09:00 KST - 유저별 상위 3개 Discord DM 발송"""
    from bot.notifications import send_daily_notifications
    asyncio.run(send_daily_notifications())
```

`scheduler/main.py`:
```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from scheduler.jobs import crawl_job, analyze_job, notify_job

scheduler = BlockingScheduler(timezone="Asia/Seoul")
scheduler.add_job(crawl_job, CronTrigger(hour=2, minute=0))
scheduler.add_job(analyze_job, CronTrigger(hour=5, minute=0))
scheduler.add_job(notify_job, CronTrigger(hour=9, minute=0))

if __name__ == "__main__":
    print("Scheduler started.")
    scheduler.start()
```

- [ ] **Step 2: 커밋**

```bash
git add scheduler/
git commit -m "feat: add APScheduler pipeline (crawl → analyze → notify)"
```

---

## Task 8: Discord 봇 + 일일 알림

**Files:**
- Create: `bot/__init__.py`
- Create: `bot/notifications.py`
- Create: `bot/main.py`
- Create: `tests/test_notifications.py`

- [ ] **Step 1: 테스트 작성**

`tests/test_notifications.py`:
```python
from bot.notifications import format_job_message

def test_format_message_contains_key_fields():
    msg = format_job_message(
        rank=1,
        title="백엔드 개발자",
        company="Acme Corp",
        fit_score=85,
        fit_reasons="Python 경험 일치, AWS 우대 조건 해당",
        job_id=42,
        web_base_url="http://localhost:8000",
    )
    assert "백엔드 개발자" in msg["content"]
    assert "Acme Corp" in msg["content"]
    assert "85" in msg["content"]
    assert len(msg["buttons"]) == 2
    assert any(b["custom_id"] == "draft_42" for b in msg["buttons"] if "custom_id" in b)

def test_format_message_rank_appears_in_content():
    msg = format_job_message(
        rank=2, title="Dev", company="Co",
        fit_score=70, fit_reasons="good",
        job_id=1, web_base_url="http://localhost:8000",
    )
    assert "2위" in msg["content"]
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_notifications.py -v
```

- [ ] **Step 3: 구현**

`bot/__init__.py`: 빈 파일

`bot/notifications.py`:
```python
import discord
from shared.db import get_session
from shared.config import get_settings
from db.orm import User, Job, JobScore

def format_job_message(
    rank: int, title: str, company: str,
    fit_score: int, fit_reasons: str,
    job_id: int, web_base_url: str,
) -> dict:
    """Discord 메시지 데이터를 반환. discord.py 객체와 분리하여 테스트 가능."""
    content = (
        f"**{rank}위. [{company}] {title}**\n"
        f"적합도: **{fit_score}점** | {fit_reasons}\n"
    )
    buttons = [
        {"label": "상세 보기", "url": f"{web_base_url}/jobs/{job_id}", "style": "link"},
        {"label": "이력서 작성하기", "custom_id": f"draft_{job_id}", "style": "primary"},
    ]
    return {"content": content, "buttons": buttons}

async def send_daily_notifications():
    settings = get_settings()
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        session = get_session()
        try:
            users = session.query(User).filter_by(is_active=True).all()
            for user in users:
                top = (
                    session.query(JobScore, Job)
                    .join(Job, Job.id == JobScore.job_id)
                    .filter(JobScore.user_id == user.id)
                    .order_by(JobScore.fit_score.desc())
                    .limit(3)
                    .all()
                )
                if not top:
                    continue
                discord_user = await client.fetch_user(int(user.discord_id))
                await discord_user.send("📬 **오늘의 추천 공고**\n━━━━━━━━━━━━━━━")
                for rank, (score, job) in enumerate(top, 1):
                    msg = format_job_message(
                        rank=rank, title=job.title or "",
                        company=job.company or "",
                        fit_score=score.fit_score or 0,
                        fit_reasons=score.fit_reasons or "",
                        job_id=job.id,
                        web_base_url=settings.db.web_base_url,
                    )
                    view = discord.ui.View()
                    for btn in msg["buttons"]:
                        if btn["style"] == "link":
                            view.add_item(discord.ui.Button(
                                label=btn["label"],
                                style=discord.ButtonStyle.link,
                                url=btn["url"],
                            ))
                        else:
                            view.add_item(discord.ui.Button(
                                label=btn["label"],
                                style=discord.ButtonStyle.primary,
                                custom_id=btn["custom_id"],
                            ))
                    await discord_user.send(msg["content"], view=view)
        finally:
            session.close()
            await client.close()

    await client.start(settings.discord.bot_token.get_secret_value())
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_notifications.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 봇 메인 구현**

`bot/main.py`:
```python
import discord
from discord.ext import commands
from shared.config import get_settings
from bot.interactions import setup_interactions

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user}")
    await bot.tree.sync()

setup_interactions(bot)

if __name__ == "__main__":
    bot.run(get_settings().discord.bot_token.get_secret_value())
```

- [ ] **Step 6: 커밋**

```bash
git add bot/main.py bot/notifications.py tests/test_notifications.py
git commit -m "feat: add Discord bot and daily notification sender"
```

---

## Task 9: Discord 버튼 인터랙션 핸들러

**Files:**
- Create: `bot/interactions.py`

- [ ] **Step 1: 구현**

`bot/interactions.py`:
```python
import discord
from discord.ext import commands
from draft.agent import generate_draft
from shared.db import get_session
from shared.config import get_settings
from db.orm import User, Job, ApplicationQuestion, ResumeDraft

def setup_interactions(bot: commands.Bot):

    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id: str = interaction.data.get("custom_id", "")
        if not custom_id.startswith("draft_"):
            return

        job_id = int(custom_id.split("_")[1])
        await interaction.response.defer(ephemeral=True)

        session = get_session()
        try:
            user = session.query(User).filter_by(discord_id=str(interaction.user.id)).first()
            if not user:
                await interaction.followup.send("등록되지 않은 사용자입니다. 관리자에게 문의하세요.")
                return

            job = session.query(Job).filter_by(id=job_id).first()
            if not job:
                await interaction.followup.send("공고를 찾을 수 없습니다.")
                return

            questions = (
                session.query(ApplicationQuestion)
                .filter_by(job_id=job_id)
                .order_by(ApplicationQuestion.order_num)
                .all()
            )
            if not questions:
                settings = get_settings()
                await interaction.followup.send(
                    f"지원 질문이 등록되어 있지 않습니다.\n"
                    f"웹 대시보드에서 먼저 입력해주세요: "
                    f"{settings.db.web_base_url}/jobs/{job_id}"
                )
                return

            await interaction.followup.send("초안 생성 중... ⏳")
            answers = generate_draft(
                jd_text=job.jd_text or "",
                resume_text=user.resume_text or "",
                experience_text=user.experience_text or "",
                questions=[q.question for q in questions],
            )
            for q, answer in zip(questions, answers):
                existing = session.query(ResumeDraft).filter_by(
                    job_id=job_id, user_id=user.id, question_id=q.id
                ).first()
                if existing:
                    existing.answer = answer
                else:
                    session.add(ResumeDraft(
                        job_id=job_id, user_id=user.id,
                        question_id=q.id, answer=answer,
                    ))
            session.commit()
        finally:
            session.close()

        settings = get_settings()
        await interaction.followup.send(
            f"✅ 초안 완성!\n{settings.db.web_base_url}/draft/{job_id}"
        )
```

- [ ] **Step 2: 커밋**

```bash
git add bot/interactions.py
git commit -m "feat: add Discord button handler for draft generation trigger"
```

---

## Task 10: FastAPI 앱 + 대시보드

**Files:**
- Create: `web/__init__.py`
- Create: `web/deps.py`
- Create: `web/main.py`
- Create: `web/routers/__init__.py`
- Create: `web/routers/dashboard.py`
- Create: `web/templates/base.html`
- Create: `web/templates/dashboard.html`
- Create: `web/templates/jobs_detail.html`
- Create: `tests/test_web.py`

- [ ] **Step 1: 테스트 작성**

`tests/test_web.py`:
```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.orm import Base, Job, User, JobScore

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

def override_get_db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()

from web.main import app
from web.deps import get_db
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def test_dashboard_returns_200():
    assert client.get("/dashboard").status_code == 200

def test_dashboard_shows_job_title():
    s = TestSession()
    s.add(Job(source="inthiswork", url="https://ex.com/1", title="Python Dev", company="Co"))
    s.commit()
    s.close()
    assert "Python Dev" in client.get("/dashboard").text

def test_health_check():
    assert client.get("/health").status_code == 200
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_web.py -v
```

- [ ] **Step 3: 구현**

`web/__init__.py`: 빈 파일
`web/routers/__init__.py`: 빈 파일

`web/deps.py`:
```python
from sqlalchemy.orm import Session
from shared.db import get_session

def get_db() -> Session:
    session = get_session()
    try:
        yield session
    finally:
        session.close()
```

`web/main.py`:
```python
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from web.routers import dashboard, profile, draft

app = FastAPI(title="MatchWise")

app.include_router(dashboard.router)
app.include_router(profile.router)
app.include_router(draft.router)

@app.get("/health")
def health():
    return {"status": "ok"}
```

`web/routers/dashboard.py`:
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from db.orm import Job, JobScore, User, ApplicationQuestion
from web.deps import get_db

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    rows = (
        db.query(Job, JobScore)
        .outerjoin(JobScore, Job.id == JobScore.job_id)
        .order_by(JobScore.fit_score.desc().nullslast())
        .all()
    )
    return templates.TemplateResponse("dashboard.html", {"request": request, "jobs": rows})

@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter_by(id=job_id).first()
    scores = db.query(JobScore, User).join(User).filter(JobScore.job_id == job_id).all()
    questions = db.query(ApplicationQuestion).filter_by(job_id=job_id).all()
    return templates.TemplateResponse("jobs_detail.html", {
        "request": request, "job": job, "scores": scores, "questions": questions
    })

@router.post("/jobs/{job_id}/questions")
def save_questions(job_id: int, questions: str = Form(...), db: Session = Depends(get_db)):
    db.query(ApplicationQuestion).filter_by(job_id=job_id).delete()
    for i, q in enumerate(questions.strip().splitlines()):
        q = q.strip()
        if q:
            db.add(ApplicationQuestion(job_id=job_id, question=q, order_num=i))
    db.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
```

`web/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MatchWise</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, sans-serif; max-width: 1100px;
           margin: 0 auto; padding: 1rem; color: #1a1a1a; }
    nav { display: flex; gap: 1rem; margin-bottom: 1rem; }
    nav a { text-decoration: none; font-weight: 600; color: #4f46e5; }
    table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    th, td { padding: 0.5rem 0.75rem; border: 1px solid #e5e7eb; text-align: left; }
    th { background: #f9fafb; font-weight: 600; }
    tr:hover td { background: #f5f5ff; }
    .btn { display: inline-block; padding: 0.4rem 0.8rem;
           background: #4f46e5; color: white; border-radius: 4px;
           text-decoration: none; border: none; cursor: pointer; font-size: 0.85rem; }
    .score-hi { color: #16a34a; font-weight: 700; }
    .score-md { color: #d97706; font-weight: 600; }
    .score-lo { color: #dc2626; }
    details { margin-top: 0.5rem; }
    summary { cursor: pointer; font-weight: 600; }
    textarea { width: 100%; font-family: monospace; }
  </style>
</head>
<body>
  <nav>
    <a href="/dashboard">대시보드</a>
    <a href="/profile">프로필 관리</a>
  </nav>
  {% block content %}{% endblock %}
</body>
</html>
```

`web/templates/dashboard.html`:
```html
{% extends "base.html" %}
{% block content %}
<h2>공고 대시보드</h2>
<table>
  <thead>
    <tr><th>회사</th><th>포지션</th><th>출처</th><th>적합도</th><th>기업점수</th><th></th></tr>
  </thead>
  <tbody>
    {% for job, score in jobs %}
    <tr>
      <td>{{ job.company or '-' }}</td>
      <td>{{ job.title or '-' }}</td>
      <td>{{ job.source }}</td>
      <td class="
        {%- if score and score.fit_score >= 80 %}score-hi
        {%- elif score and score.fit_score >= 60 %}score-md
        {%- else %}score-lo{%- endif %}">
        {{ score.fit_score if score else '-' }}
      </td>
      <td>{{ score.company_score if score else '-' }}</td>
      <td><a class="btn" href="/jobs/{{ job.id }}">상세</a></td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`web/templates/jobs_detail.html`:
```html
{% extends "base.html" %}
{% block content %}
<h2>[{{ job.company }}] {{ job.title }}</h2>
<p><a href="{{ job.url }}" target="_blank">공고 원문 →</a></p>

<h3>유저별 적합도</h3>
<table>
  <thead><tr><th>유저</th><th>적합도</th><th>기업점수</th><th>이유</th></tr></thead>
  <tbody>
    {% for score, user in scores %}
    <tr>
      <td>{{ user.username }}</td>
      <td>{{ score.fit_score }}</td>
      <td>{{ score.company_score }}</td>
      <td>{{ score.fit_reasons }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<h3>JD 원문</h3>
<pre style="white-space:pre-wrap; border:1px solid #e5e7eb; padding:1rem; border-radius:4px;">{{ job.jd_text }}</pre>

<h3>지원 질문 등록</h3>
{% if questions %}
<ul>{% for q in questions %}<li>{{ q.question }}</li>{% endfor %}</ul>
{% endif %}
<form method="post" action="/jobs/{{ job.id }}/questions">
  <textarea name="questions" rows="6"
    placeholder="질문을 한 줄에 하나씩 입력하세요. 저장 시 기존 질문은 덮어씁니다.">
    {%- for q in questions %}{{ q.question }}
{% endfor -%}
  </textarea>
  <button class="btn" type="submit">저장</button>
</form>
{% endblock %}
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_web.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add web/ tests/test_web.py
git commit -m "feat: add FastAPI app with dashboard and job detail"
```

---

## Task 11: 프로필 관리 페이지

**Files:**
- Create: `web/routers/profile.py`
- Create: `web/templates/profile.html`

- [ ] **Step 1: 테스트 추가** (`tests/test_web.py` 하단에 추가)

```python
def test_profile_page_returns_200():
    assert client.get("/profile").status_code == 200

def test_profile_resume_update():
    s = TestSession()
    s.add(User(discord_id="777", username="profileuser"))
    s.commit()
    s.close()
    resp = client.post("/profile/777/resume", data={"resume_text": "Python 3년 경력"})
    assert resp.status_code in (200, 303)
    s = TestSession()
    u = s.query(User).filter_by(discord_id="777").first()
    assert u.resume_text == "Python 3년 경력"
    s.close()
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_web.py::test_profile_page_returns_200 -v
```

- [ ] **Step 3: 구현**

`web/routers/profile.py`:
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from db.orm import User
from web.deps import get_db

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).filter_by(is_active=True).all()
    return templates.TemplateResponse("profile.html", {"request": request, "users": users})

@router.post("/profile/{discord_id}/resume")
def update_resume(discord_id: str, resume_text: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(discord_id=discord_id).first()
    if user:
        user.resume_text = resume_text
        db.commit()
    return RedirectResponse("/profile", status_code=303)

@router.post("/profile/{discord_id}/experience")
def update_experience(discord_id: str, experience_text: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(discord_id=discord_id).first()
    if user:
        user.experience_text = experience_text
        db.commit()
    return RedirectResponse("/profile", status_code=303)
```

`web/templates/profile.html`:
```html
{% extends "base.html" %}
{% block content %}
<h2>프로필 관리</h2>
{% if not users %}
<p>등록된 사용자가 없습니다. 관리자가 <code>scripts/add_user.py</code>로 추가해주세요.</p>
{% endif %}
{% for user in users %}
<div style="border:1px solid #e5e7eb; padding:1rem; margin-bottom:1.5rem; border-radius:8px;">
  <h3>{{ user.username }} <small style="color:#6b7280; font-size:0.8rem;">Discord: {{ user.discord_id }}</small></h3>
  <details>
    <summary>이력서 수정</summary>
    <form method="post" action="/profile/{{ user.discord_id }}/resume" style="margin-top:0.5rem;">
      <textarea name="resume_text" rows="10" placeholder="이력서 텍스트를 붙여넣으세요">{{ user.resume_text or '' }}</textarea>
      <button class="btn" type="submit">저장</button>
    </form>
  </details>
  <details>
    <summary>경험 텍스트 수정 (협업, 갈등 해결, 학창시절 등)</summary>
    <form method="post" action="/profile/{{ user.discord_id }}/experience" style="margin-top:0.5rem;">
      <textarea name="experience_text" rows="10" placeholder="자유 형식으로 경험을 작성하세요">{{ user.experience_text or '' }}</textarea>
      <button class="btn" type="submit">저장</button>
    </form>
  </details>
</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_web.py -v
```
Expected: 5 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add web/routers/profile.py web/templates/profile.html
git commit -m "feat: add profile management page for resume and experience upload"
```

---

## Task 12: 초안 생성 Agent

**Files:**
- Create: `draft/__init__.py`
- Create: `draft/agent.py`
- Create: `tests/test_draft.py`

- [ ] **Step 1: 테스트 작성**

`tests/test_draft.py`:
```python
from unittest.mock import MagicMock, patch
from draft.agent import generate_draft

def test_generate_draft_returns_one_answer_per_question():
    mock_resp = MagicMock()
    mock_resp.content = "저는 Python을 3년간 활용하여 결제 시스템을 개발했습니다."
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_resp

    with patch("draft.agent.build_chain", return_value=mock_chain):
        results = generate_draft(
            jd_text="Python 백엔드 개발자",
            resume_text="Python 3년",
            experience_text="스타트업 경험",
            questions=["자기소개를 해주세요.", "지원 동기는 무엇인가요?"],
        )

    assert len(results) == 2
    assert all(isinstance(r, str) and len(r) > 0 for r in results)

def test_generate_draft_calls_chain_per_question():
    mock_resp = MagicMock()
    mock_resp.content = "답변"
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_resp

    with patch("draft.agent.build_chain", return_value=mock_chain):
        generate_draft(
            jd_text="JD", resume_text="이력서", experience_text="경험",
            questions=["Q1", "Q2", "Q3"],
        )

    assert mock_chain.invoke.call_count == 3
```

- [ ] **Step 2: 테스트 실행 (FAIL 확인)**

```
uv run pytest tests/test_draft.py -v
```

- [ ] **Step 3: 구현**

`draft/__init__.py`: 빈 파일

`draft/agent.py`:
```python
from langchain.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from analysis.gemini_client import get_llm

_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "당신은 취업 지원서 전문가입니다. "
     "지원자의 이력과 경험을 바탕으로 진솔하고 설득력 있는 답변을 작성해주세요. "
     "600자 이내로 작성하세요."),
    ("human", """
채용 공고 JD:
{jd_text}

지원자 이력서:
{resume_text}

지원자 경험 (협업, 갈등 해결, 성장 스토리 등):
{experience_text}

지원서 질문:
{question}

위 정보를 바탕으로 이 질문에 대한 답변 초안을 작성하세요.
"""),
])

def build_chain() -> Runnable:
    return _PROMPT | get_llm()

def generate_draft(
    jd_text: str,
    resume_text: str,
    experience_text: str,
    questions: list[str],
) -> list[str]:
    chain = build_chain()
    return [
        chain.invoke({
            "jd_text": jd_text,
            "resume_text": resume_text,
            "experience_text": experience_text,
            "question": q,
        }).content
        for q in questions
    ]
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_draft.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 커밋**

```bash
git add draft/ tests/test_draft.py
git commit -m "feat: add LangChain resume draft generator"
```

---

## Task 13: 초안 조회 페이지

**Files:**
- Create: `web/routers/draft.py`
- Create: `web/templates/draft.html`

- [ ] **Step 1: 구현**

`web/routers/draft.py`:
```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from db.orm import Job, ApplicationQuestion, ResumeDraft, User
from web.deps import get_db

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

@router.get("/draft/{job_id}", response_class=HTMLResponse)
def draft_page(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter_by(id=job_id).first()
    questions = (
        db.query(ApplicationQuestion)
        .filter_by(job_id=job_id)
        .order_by(ApplicationQuestion.order_num)
        .all()
    )
    drafts_by_q = {}
    for q in questions:
        user_drafts = (
            db.query(ResumeDraft, User)
            .join(User, User.id == ResumeDraft.user_id)
            .filter(ResumeDraft.question_id == q.id, ResumeDraft.job_id == job_id)
            .all()
        )
        drafts_by_q[q.id] = {"question": q.question, "drafts": user_drafts}
    return templates.TemplateResponse("draft.html", {
        "request": request, "job": job, "drafts_by_q": drafts_by_q
    })
```

`web/templates/draft.html`:
```html
{% extends "base.html" %}
{% block content %}
<h2>[{{ job.company }}] {{ job.title }} — 이력서 초안</h2>
<p><a href="{{ job.url }}" target="_blank">공고 원문 →</a> | <a href="/jobs/{{ job.id }}">공고 상세</a></p>

{% if not drafts_by_q %}
<p>아직 초안이 없습니다. Discord에서 [이력서 작성하기] 버튼을 눌러주세요.</p>
{% endif %}

{% for q_id, data in drafts_by_q.items() %}
<div style="margin-bottom:2rem; padding:1rem; border:1px solid #e5e7eb; border-radius:8px;">
  <h3>Q. {{ data.question }}</h3>
  {% for draft, user in data.drafts %}
  <div style="margin-top:1rem; padding-left:1rem; border-left:4px solid #4f46e5;">
    <strong>{{ user.username }}</strong>
    <pre id="draft-{{ draft.id }}"
         style="white-space:pre-wrap; font-family:sans-serif; margin:0.5rem 0;">{{ draft.answer }}</pre>
    <button class="btn"
            onclick="navigator.clipboard.writeText(document.getElementById('draft-{{ draft.id }}').textContent)">
      복사
    </button>
  </div>
  {% endfor %}
</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 2: 전체 테스트 실행**

```
uv run pytest -v
```
Expected: 모든 테스트 PASS

- [ ] **Step 3: 커밋**

```bash
git add web/routers/draft.py web/templates/draft.html
git commit -m "feat: add draft display page with per-user answers and copy button"
```

---

## Task 14: 관리자 유저 추가 스크립트

**Files:**
- Create: `scripts/add_user.py`

- [ ] **Step 1: 구현**

`scripts/add_user.py`:
```python
"""관리자용 유저 등록 CLI.

Usage:
    uv run python scripts/add_user.py <discord_id> <username>

Example:
    uv run python scripts/add_user.py 123456789012345678 "홍길동"
"""
import sys
from db.init_db import init
from shared.db import get_session
from db.orm import User

def add_user(discord_id: str, username: str) -> None:
    init()
    session = get_session()
    try:
        if session.query(User).filter_by(discord_id=discord_id).first():
            print(f"이미 존재하는 사용자: {discord_id}")
            return
        user = User(discord_id=discord_id, username=username)
        session.add(user)
        session.commit()
        print(f"사용자 추가 완료: {username} (Discord ID: {discord_id}, DB id: {user.id})")
    finally:
        session.close()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/add_user.py <discord_id> <username>")
        sys.exit(1)
    add_user(sys.argv[1], sys.argv[2])
```

- [ ] **Step 2: 로컬 동작 확인**

```bash
DATABASE_URL=sqlite:///./test_admin.db uv run python scripts/add_user.py 123456789 "테스트유저"
```
Expected: `사용자 추가 완료: 테스트유저 (Discord ID: 123456789, DB id: 1)`

```bash
rm test_admin.db
```

- [ ] **Step 3: 커밋**

```bash
git add scripts/add_user.py
git commit -m "feat: add admin CLI for user registration"
```

---

## Task 15: GitHub Actions CI/CD

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: GitHub Secrets 등록 (수동, 최초 1회)**

GitHub 레포 → Settings → Secrets and variables → Actions → New repository secret:
- `EC2_HOST`: EC2 퍼블릭 IP 주소
- `EC2_SSH_KEY`: EC2 접속 PEM 키 내용 전체 (`cat ~/.ssh/matchwise.pem` 결과)
- `EC2_USER`: `ubuntu` (Amazon Linux면 `ec2-user`)

- [ ] **Step 2: deploy.yml 생성**

```yaml
name: Deploy to EC2

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install uv && uv sync --dev
      - run: uv run pytest -v

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ${{ secrets.EC2_USER }}
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd ~/matchwise
            git pull origin main
            docker compose build --pull
            docker compose up -d --remove-orphans
            echo "Deploy complete at $(date)"
```

- [ ] **Step 3: EC2 초기 세팅 (최초 1회, 수동 SSH)**

```bash
# 1. Docker 설치
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 2. swap 추가 (t2.micro 메모리 보완)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. 레포 클론
git clone https://github.com/<your-username>/matchwise.git ~/matchwise
cd ~/matchwise
cp .env.example .env
# nano .env  → 실제 API 키 값 입력

# 4. DB 초기화
docker compose up postgres -d
sleep 5
DATABASE_URL=postgresql://matchwise:matchwise_dev@localhost:5432/matchwise \
  uv run python -m db.init_db

# 5. 전체 서비스 실행
docker compose up -d
```

- [ ] **Step 4: 커밋 후 자동 배포 확인**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat: add GitHub Actions CI/CD with test gate"
git push origin main
```

GitHub → Actions 탭에서 `Deploy to EC2` 워크플로우 실행 확인. 녹색 체크 확인.

---

## 전체 테스트 실행

```bash
uv run pytest -v
```

Expected:
```
tests/test_orm.py::test_create_user PASSED
tests/test_orm.py::test_create_job PASSED
tests/test_orm.py::test_job_score_unique_constraint PASSED
tests/test_crawler.py::test_parse_job_list_extracts_url_and_meta PASSED
tests/test_crawler.py::test_parse_job_detail_extracts_jd_text PASSED
tests/test_crawler.py::test_superookie_parse_job_list PASSED
tests/test_scorer.py::test_score_returns_valid_range PASSED
tests/test_scorer.py::test_score_handles_malformed_json PASSED
tests/test_notifications.py::test_format_message_contains_key_fields PASSED
tests/test_notifications.py::test_format_message_rank_appears_in_content PASSED
tests/test_draft.py::test_generate_draft_returns_one_answer_per_question PASSED
tests/test_draft.py::test_generate_draft_calls_chain_per_question PASSED
tests/test_web.py::test_dashboard_returns_200 PASSED
tests/test_web.py::test_dashboard_shows_job_title PASSED
tests/test_web.py::test_health_check PASSED
tests/test_web.py::test_profile_page_returns_200 PASSED
tests/test_web.py::test_profile_resume_update PASSED
17 passed
```

---

## 크롤러 CSS 셀렉터 조정 (배포 전 필수)

Task 4, 5에서 구현한 파서의 CSS 셀렉터는 실제 사이트 구조를 확인해야 합니다:

```bash
# 로컬에서 실제 사이트 HTML 확인
uv run python -c "
import asyncio
from playwright.async_api import async_playwright

async def dump():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page()
        await page.goto('https://inthiswork.com/it', timeout=30000)
        print(await page.content())
        await b.close()

asyncio.run(dump())
" | head -200
```

결과를 보고 `crawler/inthiswork.py`의 `parse_job_list`/`parse_job_detail` 셀렉터를 실제 클래스명으로 교체. `crawler/superookie.py`도 동일하게 진행.
