# MatchWise — Claude Code 프로젝트 지침

> 설계의 단일 출처는 [DESIGN.md](DESIGN.md) (v3, 3-레이어 구조). 이 파일은 코딩 작업 지침만 담는다.
> 옛 v1 설계 문서(AWS 멀티에이전트 구상)는 `docs/archive/2025-02-design-v1-aws.md`로 아카이브됨 — 참고용, 현행 설계 아님.

## 프로젝트 한 줄 요약

채용 공고를 매일 수집·분석해 Discord로 알려주고, 지원 상태를 추적·리마인드하며, 개인 기억저장소(RAG) 기반으로 지원서 초안을 생성하는 개인용 구직 자동화 시스템.

## 아키텍처 요약 (상세는 DESIGN.md)

- **Layer 1 파이프라인**: `scheduler`(APScheduler) → `run_daily_pipeline`(08시 한 번에 이어서 실행: `run_crawl_pipeline`(`crawl_job`→`extract_job`, OCR 포함) → `analyze_job`(Gemini) → `notify_job`(bot 알림) → `assistant_job`(비서 DM)). 일반 코드 — 에이전트 아님.
- **Layer 2 지원 관리**: `tracker/` — `applications`(현재 상태: saved·will_apply·submitted·assignment·interview·passed·rejected·dismissed) + `application_events`(변경 기록). 상태는 `change_status`로만 바꾼다. Discord 버튼 + 비서 DM(마감 리마인드·3일 현황, `bot/assistant.py`). LLM 불필요.
- **Layer 3 에이전트**: `memory/`(위키·GitHub ingest → pgvector RAG), `draft/`(LangGraph 초안 생성, Claude Pro OAuth). 포트폴리오 코어.

**LLM 사용 규칙**: 배치·대량 호출은 Gemini Flash만 사용. Claude(Pro OAuth)는 Draft Agent(저빈도·고품질)에만 사용 — 배치 작업에 Claude를 쓰면 구독 한도가 소진되므로 금지.

## 디렉토리

```
crawler/    사이트별 크롤러 (BaseCrawler → RawJob. InthisworkCrawler, JasoseolCrawler, JobkoreaCrawler — HTTP만, 브라우저 없음)
analysis/   scorer, gemini_client, dart_client
scheduler/  APScheduler 잡 정의 (jobs.py — 수집 사이트 on/off는 _enabled_crawlers() 한 곳) + 엔트리 (main.py)
bot/        discord.py — notifications(매칭 채널), assistant(비서 DM), interactions(버튼 핸들러), commands(슬래시 명령어 /매칭채널)
tracker/    지원 상태 변경(applications.py::change_status) + 목록 조회(lists.py — 웹·DM 공용)
db/         SQLAlchemy ORM (orm.py), init_db
web/        FastAPI + Jinja2 — routers(auth, dashboard, draft, experiences, profile), templates
shared/     config, logger, db 세션
memory/     (Phase D~, 미생성) Memory Agent — 독립 추출 가능한 구조 유지
draft/      (Phase E~, 미생성) Draft Agent — LangGraph StateGraph
scripts/    1회성 스크립트 (add_user.py, migrate_users_columns.py, import_profile.py — profile.json → basic_info·experiences,
            migrate_tracking.py — 2026-10-15 지원 관리 구조 반영, backfill_deadlines.py — 본문에서 마감일 채우기)
tests/      pytest
docs/superpowers/specs/  기능별 상세 설계 (JD 저장 설계 등)
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
.venv/Scripts/python.exe -m pytest           # 테스트 (Windows Git Bash. `uv run`은 경로 문제로 실패할 수 있음)
# PowerShell에서는 앞에 .\ 필요:  .\.venv\Scripts\python.exe -m pytest
```

- 로그: 진입점마다 `shared/logger.py::setup_logging("<프로그램>")` → 콘솔 + `logs/<프로그램>.log`(자정마다 날짜 파일로 넘기고 최근 7일 보관). 모듈에서는 `logging.getLogger(__name__)`만 쓴다.
- `docker compose --profile crawl run --rm crawler` — 수집→추출(`run_crawl_pipeline`)을 수동으로 1회 실행 (`crawler/main.py`). 평소엔 scheduler가 08시 `run_daily_pipeline` 첫 단계로 같은 함수를 실행한다.
- 스키마 변경은 `create_all`(db/init_db.py)만으로는 기존 테이블에 컬럼이 추가되지 않는다 — 이미 만들어진 DB에 컬럼을 추가했다면 그 DB에 `ALTER TABLE`을 직접 1회 실행한다(로컬 `matchwise_dev.db`는 2026-10-15 `scripts/migrate_tracking.py` 적용 기준 최신 스키마).
- 시각 칸은 `db/orm.py::UTCDateTime`(UTC 저장·UTC 반환)을 쓴다. 날짜 계산(D-day 등)은 `tracker/lists.py::kst_date`로 한국 날짜로 바꿔서 한다.
