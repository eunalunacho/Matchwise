# MatchWise v2 — System Design

**Date**: 2026-06-27  
**Status**: Approved  
**Author**: eunalunacho

---

## 1. 프로젝트 목표

채용 공고를 매일 자동 수집하고, 사용자 이력·경험 기반으로 맞춤 분석하여 Discord로 알림. 사용자가 승인하면 지원서 초안을 자동 생성하는 시스템.

**핵심 요구사항**
- 하루 한 번 공고 수집 → 사용자별 fit 분석 → 상위 3개 Discord 알림
- 사용자 승인(버튼 클릭) 시 이력서/지원서 답변 초안 생성
- 수집된 전체 공고 + 점수를 웹 대시보드에서 열람 가능
- 다수 사용자 지원 (비개발자 포함, 웹 UI로 프로필 관리)
- 비용 최소화: AWS 프리티어 + Gemini Flash 무료 티어

---

## 2. 시스템 아키텍처

```
AWS EC2 t2.micro (Free Tier → 이후 t4g.small)
└── Docker Compose
    ├── postgres          ← 공고 / 점수 / 사용자 데이터
    ├── scheduler         ← APScheduler (나중에 Airflow로 교체)
    │     02:00 KST → crawler 실행
    │     05:00 KST → 분석 실행
    │     09:00 KST → Discord 알림 발송
    ├── crawler           ← Playwright + BS4 (실행 후 종료)
    │     - inthiswork.com/it
    │     - superookie.com
    ├── web-api           ← FastAPI + Jinja2
    │     GET  /dashboard           공고 목록 + 점수 (필터/정렬)
    │     GET  /jobs/{id}           공고 상세 + fit 이유
    │     GET  /draft/{job_id}      이력서 초안 조회
    │     GET  /profile             프로필 관리 페이지
    │     POST /profile/resume      이력서 텍스트 업로드
    │     POST /profile/experience  경험 텍스트 업로드
    │     POST /jobs/{id}/questions 지원 질문 등록
    └── discord-bot       ← discord.py
          - 09:00: 유저별 DM (상위 3개 공고 + 버튼)
          - 버튼: [상세 보기] [이력서 작성하기]
          - 이력서 작성 클릭 → 초안 생성 트리거 → 웹 링크 DM

외부 API
  Gemini Flash (google-generativeai)
    - 공고 fit 분석 (05:00 배치)
    - 이력서 초안 생성 (사용자 요청 시)
```

---

## 3. 데이터 플로우

### 3-1. 일일 파이프라인

```
[02:00] 크롤링
  crawler 컨테이너 실행
  → inthiswork.com/it 크롤링 (Playwright)
  → superookie.com 크롤링 (Playwright)
  → 중복 체크 (URL 기준, postgres)
  → 신규 공고만 jobs 테이블 저장
  → 컨테이너 종료

[05:00] 분석
  scheduler → 분석 스크립트 실행
  → 신규 공고 목록 조회
  → 전체 활성 유저 목록 조회
  → 각 (공고, 유저) 조합:
      JD 텍스트 + 유저 이력서 + 경험 텍스트
      → Gemini Flash 호출
      → fit_score (0-100), fit_reasons, company_score 반환
      → job_scores 테이블 저장

[09:00] 알림
  scheduler → Discord 알림 스크립트 실행
  → 유저별 신규 공고 중 fit_score 상위 3개 조회
  → Discord DM 발송:
      공고 제목 / 회사 / fit_score / 핵심 이유 (2줄)
      [상세 보기 →] [이력서 작성하기]
```

### 3-2. 이력서 초안 생성 플로우

```
사용자: Discord DM에서 [이력서 작성하기] 클릭
  → discord-bot: 해당 job_id + user_id 확인
  → web-api: POST /draft/generate 호출
  → LangGraph Agent 실행:
      Step 1. JD에서 핵심 요구사항 추출
      Step 2. 유저 프로필에서 관련 경험 매칭
      Step 3. 지원 질문별 답변 초안 생성
              (JD + 이력서 + 경험 텍스트 + 질문 → Gemini Flash)
      Step 4. 전체 일관성 검토
      Step 5. 결과 저장 (resume_drafts 테이블)
  → discord-bot: 웹 링크 DM 전송
      "초안이 완성됐어요 → http://<ec2-ip>/draft/{job_id}"
  → 사용자: 브라우저에서 질문별 답변 확인 + 복사
```

---

## 4. 데이터베이스 스키마 (PostgreSQL)

```sql
-- 사용자
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    discord_id      VARCHAR(64) UNIQUE NOT NULL,
    username        VARCHAR(128) NOT NULL,
    resume_text     TEXT,           -- 이력서 원문
    experience_text TEXT,           -- 경험 텍스트 뭉치
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 수집 공고
CREATE TABLE jobs (
    id          SERIAL PRIMARY KEY,
    source      VARCHAR(64) NOT NULL,   -- 'inthiswork' | 'superookie'
    url         TEXT UNIQUE NOT NULL,
    title       VARCHAR(512),
    company     VARCHAR(256),
    jd_text     TEXT,
    deadline    DATE,
    crawled_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 유저별 공고 점수
CREATE TABLE job_scores (
    id             SERIAL PRIMARY KEY,
    job_id         INTEGER REFERENCES jobs(id),
    user_id        INTEGER REFERENCES users(id),
    fit_score      SMALLINT,           -- 0-100
    fit_reasons    TEXT,               -- Gemini 생성 요약
    company_score  SMALLINT,           -- 0-100
    scored_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (job_id, user_id)
);

-- 지원 질문
CREATE TABLE application_questions (
    id          SERIAL PRIMARY KEY,
    job_id      INTEGER REFERENCES jobs(id),
    question    TEXT NOT NULL,
    order_num   SMALLINT DEFAULT 0
);

-- 이력서 초안
CREATE TABLE resume_drafts (
    id          SERIAL PRIMARY KEY,
    job_id      INTEGER REFERENCES jobs(id),
    user_id     INTEGER REFERENCES users(id),
    question_id INTEGER REFERENCES application_questions(id),
    answer      TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 5. 기술 스택

| 역할 | 기술 | 비고 |
|------|------|------|
| 언어 | Python 3.12 | |
| Discord 봇 | discord.py | |
| 웹 | FastAPI + Jinja2 | 대시보드 + 프로필 관리 |
| 크롤링 | Playwright + BeautifulSoup4 | JS 렌더링 대응 |
| LLM | Gemini Flash (google-generativeai) | 무료 티어 |
| Agent | LangGraph | 이력서 초안 생성 단계에만 적용 |
| DB | PostgreSQL 16 | Docker 컨테이너 |
| ORM | SQLAlchemy 2.x | |
| 스케줄링 | APScheduler | → Airflow DAG으로 교체 예정 |
| 컨테이너 | Docker Compose | |
| CI/CD | GitHub Actions | EC2 SSH 배포 |
| 인프라 | AWS EC2 + EBS | t2.micro (프리티어) |

---

## 6. 크롤링 소스

| 사이트 | URL | 비고 |
|--------|-----|------|
| 인디스워크 | https://inthiswork.com/it | IT 분야 공고 |
| 슈퍼루키 | https://www.superookie.com/jobs/search?... | 신입/주니어 필터 적용 (전체 URL 구현 시 확인 필요) |

두 사이트 모두 JS 렌더링 여부 확인 후, 정적이면 requests+BS4로 대체하여 Playwright 메모리 부담 최소화.

---

## 7. 비용 추정

| 항목 | 월 비용 | 비고 |
|------|--------|------|
| EC2 t2.micro | $0 | 12개월 프리티어 |
| EBS 30GB | $0 | 프리티어 |
| Gemini Flash | $0 | 무료 티어 (1,500 req/일) |
| Elastic IP | $0 | 인스턴스 실행 중 무료 |
| **합계 (Year 1)** | **~$0** | |
| EC2 t4g.small (Year 2+) | $15.2 | 2GB RAM, 서울 리전 |

---

## 8. Airflow 마이그레이션 경로

현재 APScheduler가 호출하는 함수를 그대로 유지하되, 트리거만 교체.

```
현재: APScheduler → crawl() / analyze() / notify()

이후:
  crawl_dag.py   → PythonOperator → crawl()
  analyze_dag.py → PythonOperator → analyze()
  notify_dag.py  → PythonOperator → notify()
```

Docker Compose에 Airflow 서비스 추가 후 APScheduler 제거. 함수 시그니처 변경 없음.

---

## 9. 개발 단계

| Phase | 내용 | 목표 |
|-------|------|------|
| 1 | 인프라 기초 | EC2 + Docker Compose + PostgreSQL + GitHub Actions |
| 2 | 크롤러 | Playwright 크롤링 + 중복 제거 + DB 저장 |
| 3 | 분석 파이프라인 | Gemini Flash fit 분석 + 스케줄링 |
| 4 | Discord 봇 | 일일 알림 + 버튼 인터랙션 |
| 5 | 웹 대시보드 | FastAPI 대시보드 + 프로필 관리 |
| 6 | 이력서 초안 | LangGraph Agent + 질문별 답변 생성 |
| 7 | Airflow 전환 | APScheduler → Airflow DAG (선택) |

---

## 10. 알려진 제약 및 대응

| 제약 | 대응 |
|------|------|
| t2.micro 1GB RAM | 2GB swap 추가, 크롤러는 02:00 단독 실행 |
| Playwright 메모리 | JS 불필요 시 requests+BS4로 대체 |
| Gemini 무료 티어 (15 req/min) | 분석 배치를 분산 처리 (sleep 추가) |
| 크롤링 차단/CAPTCHA | User-Agent 로테이션, 실패 시 재시도 로직 |
| Discord 버튼 인터랙션 3초 제한 | 즉시 ACK 후 비동기 처리 |
