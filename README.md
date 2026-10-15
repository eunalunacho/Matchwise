# MatchWise

채용 공고를 매일 자동 수집·분석해 Discord로 알려주고, 지원 상태를 추적·리마인드하며, 개인 기억저장소(RAG) 기반으로 지원서 초안까지 생성하는 **개인용 구직 자동화 시스템**.

> ⚠️ 개인 사용 목적으로 만들면서 쓰는 프로젝트라, 사용해 보며 구조와 방향이 계속 바뀝니다.
> 현재 확정된 설계는 [DESIGN.md](DESIGN.md) (v3)를 참고하세요.

## 무엇을 하나

**3-레이어 구조** ([DESIGN.md](DESIGN.md) 상세):

```
Layer 1  파이프라인   02시 수집→추출(OCR 포함) → 05시 fit 분석(Gemini) → 09시 Discord 알림
Layer 2  지원 관리    [목록에 등록][패스] 버튼 → 상태 추적 → 채널 현황 공유·마감 리마인드
Layer 3  에이전트     개인 위키·GitHub → 기억저장소(pgvector) → RAG 기반 초안 생성(LangGraph + Claude)
```

- DM 추천 카드의 [목록에 등록] → 지원 예정으로 기록, 자소서 작성·제출 표시는 웹에서 (예정)
- 등록해 놓고 제출 안 하면 마감 D-7, D-2에 채널에서 태그 리마인드 (예정)
- 전체 공고와 점수는 웹 대시보드(FastAPI)에서 열람, 구글 로그인 + Discord 계정 연결
- 기업 정보 DART 보강(company_score)은 코드만 남기고 당분간 비활성화

## 구조

Docker Compose 위에 5개 서비스로 구성:

| 서비스 | 역할 |
|---|---|
| `postgres` | 공고 · 점수 · 지원 상태 · 경험 임베딩(pgvector) |
| `scheduler` | APScheduler 기반 일일 파이프라인 (크롤링도 여기서 `run_crawl_pipeline`으로 실행) |
| `crawler` | 수동 실행용 프로필 — 스케줄러를 기다리지 않고 수집→추출을 1회 실행 후 종료 (`crawler.main`) |
| `web-api` | FastAPI + Jinja2 대시보드, 프로필 관리 |
| `discord-bot` | discord.py 알림 및 버튼 인터랙션 |

**스택**: Python 3.12 · FastAPI · discord.py · httpx(크롤링, 브라우저 없음) · Google Document AI(OCR) · LangGraph · Gemini Flash(배치 분석) + Claude(초안 생성) · PostgreSQL 16 + pgvector · Docker Compose · AWS EC2 (프리티어)

## 실행

```bash
cp .env.example .env   # DISCORD_BOT_TOKEN, GEMINI_API_KEY, DART_API_KEY 입력
docker compose up -d                         # postgres, web-api, bot, scheduler
docker compose --profile crawl run --rm crawler   # 수집→추출 수동 1회 실행
pytest                                       # 테스트
```

## 진행 상황

- [x] 프로젝트 기반 (config · logger · ORM 모델)
- [x] 분석 파이프라인 (Gemini fit 스코어링, 하루 호출 상한 400건 공유). DART company_score는 비활성화
- [x] Discord DM 추천 카드 — 3버튼(자세히 보기/목록에 등록/패스) (Phase A, B2)
- [x] 웹 대시보드 · 공고 상세 · 지원 질문 등록 · 초안 조회 (Phase A)
- [x] `applications` 지원 상태 테이블 (Phase B1)
- [x] 경험 조각 저장 페이지 `/experiences` (Phase B4)
- [x] 구글 로그인 + Discord 계정 연결 (Phase G 일부)
- [x] 시스템 설계 v3 확정 — 3-레이어 통합 ([DESIGN.md](DESIGN.md))
- [x] JD 수집 계약(RawJob) 확정 + 인디스워크(WordPress API)·자소설·잡코리아 크롤러, 3곳 모두 수집 등록 ([설계](docs/superpowers/specs/2026-09-24-jd-storage-design.md)). 슈퍼루키는 유의미한 공고가 없어 삭제
- [x] **1차 적재 — `raw_postings` 저장 계층** + `jobs.extract_status` 자동 분류(pending/needs_ocr/empty)
- [x] **2차 저장 — 추출 `extract_job()`** (pending/failed/needs_ocr → jd_tasks 등 + `eligibility`). `run_crawl_pipeline`으로 crawl→extract 체인 실행(고정 시간 아닌 함수 호출 체인)
- [x] **OCR** — `needs_ocr` 공고를 이미지→텍스트 변환 후 같은 실행에서 추출까지 진행. 기본 Google Cloud Document AI, `OCR_PROVIDER=claude_code`로 Claude Code 헤드리스 전환 가능. 인증은 로컬 ADC — 운영 배포 시 인증 방식 재검토 필요
- [x] 로그 파일(`logs/`, 최근 7일 보관) + 추출 실패 재시도 2일 제한 + 수집 진행 Discord 웹훅
- [ ] 3차 저장 — 청킹 → `job_chunks` → 공고 추천 연결
- [ ] 웹 메인·신규공고·작성중 공고 페이지 (Phase B3)
- [ ] 작성 페이지 `/jobs/{id}/apply` + LLM 초안 생성 + 제출 완료 표시 (Phase B5)
- [ ] 채널 3일 현황 공유 · 마감 D-7/D-2 리마인드 (Phase C1, C2)
- [ ] 기억저장소 — pgvector + 위키 임포트 (Phase D)
- [ ] Draft Agent — LangGraph + RAG + Claude (Phase E)
- [ ] GitHub 동기화 (Phase F)
- [ ] 유저당 일일 분석 상한 · EC2 배포 + CI/CD (Phase G 나머지)

## 히스토리

| 시기 | 내용 |
|---|---|
| 2026-02 | v1 — AWS 멀티에이전트 구상 (미구현, [아카이브](docs/archive/2025-02-design-v1-aws.md)) |
| 2026-06 | v2 — 단일 파이프라인으로 축소, Docker Compose 구조로 구현 착수 |
| 2026-07 | v3 — v1 기능 명세 × v2 인프라 통합. 지원 관리 + 기억저장소(RAG) + LLM 이원화(Gemini 배치/Claude 초안) |
