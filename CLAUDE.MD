# MatchWise — Claude Code 프로젝트 지침

> 설계의 단일 출처는 [DESIGN.md](DESIGN.md) (v3, 3-레이어 구조). 이 파일은 코딩 작업 지침만 담는다.
> 옛 v1 설계 문서(AWS 멀티에이전트 구상)는 `docs/archive/2025-02-design-v1-aws.md`로 아카이브됨 — 참고용, 현행 설계 아님.

## 프로젝트 한 줄 요약

채용 공고를 매일 수집·분석해 Discord로 알려주고, 지원 상태를 추적·리마인드하며, 개인 기억저장소(RAG) 기반으로 지원서 초안을 생성하는 개인용 구직 자동화 시스템.

## 아키텍처 요약 (상세는 DESIGN.md)

- **Layer 1 파이프라인**: `scheduler`(APScheduler) → `crawler`(02시) → `analysis`(05시, Gemini) → `bot` 알림(09시). 일반 코드 — 에이전트 아님.
- **Layer 2 지원 관리**: `applications` 상태머신(interested→will_apply→submitted→…) + Discord 버튼 + 재촉/리마인드 잡. LLM 불필요.
- **Layer 3 에이전트**: `memory/`(위키·GitHub ingest → pgvector RAG), `draft/`(LangGraph 초안 생성, Claude Pro OAuth). 포트폴리오 코어.

**LLM 사용 규칙**: 배치·대량 호출은 Gemini Flash만 사용. Claude(Pro OAuth)는 Draft Agent(저빈도·고품질)에만 사용 — 배치 작업에 Claude를 쓰면 구독 한도가 소진되므로 금지.

## 디렉토리

```
crawler/    사이트별 크롤러 (클래스 기반: InthisworkCrawler, SuperookieCrawler)
analysis/   scorer, gemini_client, dart_client
scheduler/  APScheduler 잡 정의 (jobs.py) + 엔트리 (main.py)
bot/        discord.py — notifications, interactions(버튼 핸들러)
db/         SQLAlchemy ORM (orm.py), init_db
shared/     config, logger, db 세션
memory/     (Phase D~) Memory Agent — 독립 추출 가능한 구조 유지
draft/      (Phase E~) Draft Agent — LangGraph StateGraph
scripts/    1회성 스크립트 (import_wiki.py, eval_draft.py 등)
tests/      pytest
```

## 코딩 규칙

- Type hints 필수, docstring은 Google Style
- 파일 `snake_case.py` / 클래스 `PascalCase` / 함수·변수 `snake_case` / 상수 `UPPER_SNAKE_CASE`
- 에러 처리는 명시적 try-except, 환경변수는 `.env` (`shared/config.py` 경유)
- 기능 추가 시 pytest 테스트 동반. 기존 테스트 깨뜨리지 않기 (`pytest` 전체 통과 유지)
- Discord 인터랙션은 3초 내 ACK 후 비동기 처리

## 커밋

- **사용자가 명시적으로 요청하기 전까지 git commit 금지**
- 메시지 형식: `feat|fix|docs|refactor|test: 한 줄 요약` (한국어 가능)

## 실행

```bash
docker compose up -d                         # postgres, web-api, bot, scheduler
docker compose --profile crawl run crawler   # 크롤러 수동 실행
pytest                                       # 테스트
```
