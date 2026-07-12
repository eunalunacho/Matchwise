# MatchWise

채용 공고를 매일 자동으로 수집하고, 내 이력서·경험을 기준으로 적합도(fit)를 분석해서 Discord로 알려주는 **개인용 구직 자동화 도구**.

> ⚠️ 개인 사용 목적으로 만들면서 쓰는 프로젝트라, 사용해 보며 구조와 방향이 계속 바뀝니다.
> 현재 확정된 설계는 [DESIGN.md](DESIGN.md)를 참고하세요.

## 무엇을 하나

하루 한 번 돌아가는 파이프라인:

```
02:00  크롤링     인디스워크 · 슈퍼루키에서 신규 공고 수집 (Playwright)
05:00  분석       공고 JD × 내 이력서/경험 → Gemini Flash로 fit 점수 산출
09:00  알림       fit 상위 3개를 Discord DM으로 전송
```

- DM의 [이력서 작성하기] 버튼을 누르면 LangGraph 에이전트가 지원 질문별 답변 초안을 생성
- 전체 공고와 점수는 웹 대시보드(FastAPI)에서 열람
- 기업 정보는 DART OpenAPI로 보강

## 구조

Docker Compose 위에 5개 서비스로 구성:

| 서비스 | 역할 |
|---|---|
| `postgres` | 공고 · 점수 · 사용자 데이터 |
| `scheduler` | APScheduler 기반 일일 파이프라인 트리거 |
| `crawler` | Playwright + BS4 크롤러 (실행 후 종료) |
| `web-api` | FastAPI + Jinja2 대시보드, 프로필 관리 |
| `discord-bot` | discord.py 알림 및 버튼 인터랙션 |

**스택**: Python 3.12 · FastAPI · discord.py · Playwright · LangChain/LangGraph + Gemini Flash · PostgreSQL 16 + SQLAlchemy · Docker Compose · AWS EC2 (프리티어)

## 실행

```bash
cp .env.example .env   # DISCORD_BOT_TOKEN, GEMINI_API_KEY, DART_API_KEY 입력
docker compose up -d                         # postgres, web-api, bot, scheduler
docker compose --profile crawl run crawler   # 크롤러 수동 실행
```

## 진행 상황

- [x] 프로젝트 기반 (config · logger · ORM 모델)
- [x] 시스템 설계 v2 확정 ([DESIGN.md](DESIGN.md))
- [ ] 크롤러 — 구현 중
- [ ] 분석 파이프라인 (Gemini fit 스코어링) — 구현 중
- [ ] Discord 봇 알림 — 구현 중
- [ ] 웹 대시보드
- [ ] 이력서 초안 생성 (LangGraph)
- [ ] EC2 배포 + GitHub Actions CI/CD

## 히스토리

| 시기 | 내용 |
|---|---|
| 2026-02 | v0.1 시작 — 기본 구조 셋업 |
| 2026-06 | v2 재설계 — 다중 사용자 지원, Discord 중심, Docker Compose 구조로 전환 |
