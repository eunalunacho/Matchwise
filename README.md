# MatchWise

채용 공고를 매일 자동 수집·분석해 Discord로 알려주고, 지원 상태를 추적·리마인드하며, 개인 기억저장소(RAG) 기반으로 지원서 초안까지 생성하는 **개인용 구직 자동화 시스템**.

> ⚠️ 개인 사용 목적으로 만들면서 쓰는 프로젝트라, 사용해 보며 구조와 방향이 계속 바뀝니다.
> 현재 확정된 설계는 [DESIGN.md](DESIGN.md) (v3)를 참고하세요.

## 무엇을 하나

**3-레이어 구조** ([DESIGN.md](DESIGN.md) 상세):

```
Layer 1  파이프라인   02시 크롤링 → 05시 fit 분석(Gemini) → 09시 Discord 알림
Layer 2  지원 관리    [쓰겠다][제출했다] 버튼 → 상태 추적 → 마감 재촉·면접 리마인드
Layer 3  에이전트     개인 위키·GitHub → 기억저장소(pgvector) → RAG 기반 초안 생성(LangGraph + Claude)
```

- DM의 [이력서 작성하기] 버튼 → Draft Agent가 내 경험을 검색(RAG)해 지원 질문별 답변 초안 생성
- [쓰겠다] 눌러놓고 제출 안 하면 마감 D-3, D-1에 재촉 DM
- 전체 공고와 점수는 웹 대시보드(FastAPI)에서 열람
- 기업 정보는 DART OpenAPI로 보강

## 구조

Docker Compose 위에 5개 서비스로 구성:

| 서비스 | 역할 |
|---|---|
| `postgres` | 공고 · 점수 · 지원 상태 · 경험 임베딩(pgvector) |
| `scheduler` | APScheduler 기반 일일 파이프라인 + 리마인드 잡 |
| `crawler` | Playwright + BS4 크롤러 (실행 후 종료) |
| `web-api` | FastAPI + Jinja2 대시보드, 프로필 관리 |
| `discord-bot` | discord.py 알림 및 버튼 인터랙션 |

**스택**: Python 3.12 · FastAPI · discord.py · Playwright · LangGraph · Gemini Flash(배치 분석) + Claude(초안 생성) · PostgreSQL 16 + pgvector · Docker Compose · AWS EC2 (프리티어)

## 실행

```bash
cp .env.example .env   # DISCORD_BOT_TOKEN, GEMINI_API_KEY, DART_API_KEY 입력
docker compose up -d                         # postgres, web-api, bot, scheduler
docker compose --profile crawl run crawler   # 크롤러 수동 실행
pytest                                       # 테스트
```

## 진행 상황

- [x] 프로젝트 기반 (config · logger · ORM 모델)
- [x] 크롤러 (인디스워크 · 슈퍼루키)
- [x] 분석 파이프라인 (Gemini fit 스코어링 + DART company_score)
- [x] Discord 봇 알림
- [x] 시스템 설계 v3 확정 — 3-레이어 통합 ([DESIGN.md](DESIGN.md))
- [ ] Discord 버튼 인터랙션 · 웹 대시보드 (Phase A)
- [ ] 지원 상태 추적 + 재촉·리마인드 (Phase B~C)
- [ ] 기억저장소 — pgvector + 위키 임포트 (Phase D)
- [ ] Draft Agent — LangGraph + RAG + Claude (Phase E)
- [ ] GitHub 동기화 (Phase F)
- [ ] EC2 배포 + CI/CD · 멀티유저 (Phase G)

## 히스토리

| 시기 | 내용 |
|---|---|
| 2026-02 | v1 — AWS 멀티에이전트 구상 (미구현, [아카이브](docs/archive/2025-02-design-v1-aws.md)) |
| 2026-06 | v2 — 단일 파이프라인으로 축소, Docker Compose 구조로 구현 착수 |
| 2026-07 | v3 — v1 기능 명세 × v2 인프라 통합. 지원 관리 + 기억저장소(RAG) + LLM 이원화(Gemini 배치/Claude 초안) |
