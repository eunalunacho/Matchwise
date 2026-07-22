# MatchWise v3 — System Design

**Date**: 2026-07-13
**Status**: Draft
**Author**: eunalunacho
**이전 버전**: v2 (2026-06-27, Approved — 본 문서의 Layer 1로 흡수) · v1 (2025-02, AWS 멀티에이전트 구상 — [docs/archive/2025-02-design-v1-aws.md](docs/archive/2025-02-design-v1-aws.md))

---

## 1. 배경과 설계 원칙

v1(AWS Lambda/ECS + DynamoDB + Pinecone + Slack, 8-에이전트 LangGraph)은 기능 과다로 구현에 이르지 못했다. v2는 범위를 "수집 → 분석 → 알림 → 초안" 단일 파이프라인으로 축소해 실제 구현을 진행했다(Task 8까지, 테스트 51개 통과).

v3는 **v1의 기능 명세를 v2의 인프라 위에 되살린다.** v1과 v2의 충돌 지점은 기능이 아니라 인프라였다 — v1이 구상한 지원 상태 추적(Application Manager), 리마인더, RAG 프로필은 2026-07-12 확정한 요구사항과 사실상 동일하다. 따라서 v1의 인프라 스택은 폐기하고 기능 개념만 흡수한다.

**설계 원칙**

1. **레이어별 적정 기술** — 반복 배치는 일반 코드로, 에이전트는 추론·글쓰기가 실제로 필요한 곳에만. (v1 실패 원인 = 전부 에이전트)
2. **인프라 불변** — 새 저장소·SaaS를 추가하지 않는다. 벡터 검색은 기존 Postgres에 pgvector 확장으로 해결.
3. **LLM 이원화** — 대량·저부가가치 호출은 Gemini Flash(무료 티어), 저빈도·고품질 호출은 Claude Pro 구독(OAuth 프로필 인증).
4. **포트폴리오 가치** — Layer 3(에이전트 레이어)는 AI Agent 직무 공고의 요구사항(Agent 설계, Tool/Function 설계, Prompt 엔지니어링, RAG 파이프라인, 성능 평가)에 1:1 대응하도록 설계한다.

---

## 2. 요구사항 (2026-07-12 확정)

| # | 요구 | 담당 레이어 | 현재 상태 |
|---|---|---|---|
| ① | 쓸 만한 공고 자동 수집 | Layer 1 | ✅ 구현됨 (인디스워크·슈퍼루키) |
| ② | 나에게 맞는 순서로 추천 | Layer 1 | 🟡 상위 3개 DM만, 전체 정렬 리스트는 대시보드 필요 |
| ③ | "쓰겠다" 하면 내 기록 기반 초안 작성 | Layer 3 (Draft Agent) | 🟡 설계만 (LangGraph 미구현) |
| ④ | "제출했다" 하면 일정 저장·리마인드 | Layer 2 | ❌ 없음 |
| ⑤ | 쓰겠다 하고 안 쓰면 재촉 | Layer 2 | ❌ 없음 |
| ⑥ | 위키처럼 이력을 쓰면 전용 기억저장소 구축 | Layer 3 (Memory Agent) | ❌ 텍스트 blob 2개뿐 |
| ⑦ | 외부 서비스(GitHub 등) 업데이트 추적 → 기억저장소 갱신 | Layer 3 (Memory Agent) | ❌ 없음 |
| ⑧ | 형태 잡히면 친구들도 쓰는 서비스로 | 전체 | 🟡 스키마는 멀티유저 전제, 온보딩·가드레일 없음 |

---

## 3. 아키텍처

```
AWS EC2 t2.micro — Docker Compose
│
├─ Layer 1. 파이프라인  (v2 승계 — 일반 코드, 에이전트 아님)
│    scheduler (APScheduler)
│      02:00 → crawler   (Playwright+BS4: 인디스워크, 슈퍼루키)
│      05:00 → analysis  (Gemini Flash: fit_score / DART: company_score)
│      09:00 → bot 알림  (discord.py: 유저별 상위 3개 DM)
│    요구 ①② 담당
│
├─ Layer 2. 지원 관리  (v1 Application Manager 개념을 새 인프라로)
│    applications 테이블 (상태머신) + Discord 버튼 [쓰겠다][제출했다]
│    scheduler 잡: 재촉(D-3/D-1), 면접 리마인드 — LLM 불필요
│    요구 ④⑤ 담당
│
├─ Layer 3. 에이전트  (LangGraph — 포트폴리오 코어)
│    memory/  Memory Agent: 위키·GitHub ingest → experiences(pgvector)
│    draft/   Draft Agent:  RAG 검색 → 질문별 초안 → 일관성 검토
│    LLM: Claude Pro (OAuth 프로필)
│    요구 ③⑥⑦ 담당
│
└─ postgres (+pgvector) · web-api (FastAPI 대시보드)

⑧ 멀티유저: Layer 1~3 안정화 후 (섹션 9 Phase G)
```

---

## 4. Layer 1 — 파이프라인 (v2 승계, 변경 없음)

v2 설계 그대로 유지. 상세 스키마(users, jobs, job_scores, application_questions, resume_drafts)와 일일 플로우는 v2 문서 내용과 동일하며 코드(`crawler/`, `analysis/`, `scheduler/`, `bot/`, `db/orm.py`)가 이미 구현하고 있다.

- **크롤링(02:00)**: 신규 공고만 URL 기준 중복 제거 후 `jobs` 저장
- **분석(05:00)**: (신규 공고 × 활성 유저) 조합마다 Gemini Flash로 `fit_score`(0-100), `fit_reasons` 산출. `company_score`는 DART 재무 데이터 우선, 없으면 LLM 폴백. 무료 티어 레이트리밋 대응(배치 분산)
- **알림(09:00)**: 유저별 상위 3개 Discord DM + 버튼

v3에서 Layer 1에 추가되는 것은 **알림 메시지의 버튼 구성뿐** (Layer 2의 `[쓰겠다]` `[제출했다]` 버튼 추가).

---

## 5. Layer 2 — 지원 관리

### 5-1. applications 테이블

```sql
CREATE TABLE applications (
    id            SERIAL PRIMARY KEY,
    job_id        INTEGER REFERENCES jobs(id),
    user_id       INTEGER REFERENCES users(id),
    status        VARCHAR(32) NOT NULL DEFAULT 'interested',
    -- interested | will_apply | submitted | rejected | passed
    will_apply_at TIMESTAMPTZ,
    submitted_at  TIMESTAMPTZ,
    interview_at  TIMESTAMPTZ,        -- 사용자가 직접 입력
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (job_id, user_id)
);
```

### 5-2. 상태 전이

```
interested ──[쓰겠다]──▶ will_apply ──[제출했다]──▶ submitted ──▶ rejected | passed
                              │
                              └─ (미제출 + 마감 임박) → 재촉 대상
```

- `[쓰겠다]` 클릭 → `status='will_apply'`, `will_apply_at` 기록 → Draft Agent 트리거 대상 + 재촉 감시 대상
- `[제출했다]` 클릭 → `status='submitted'`, `submitted_at` 기록 → 재촉 해제, 리마인드 대상으로 전환

### 5-3. scheduler 잡 (기존 02/05/09시 패턴에 추가)

| 잡 | 조건 | 동작 |
|---|---|---|
| 재촉 (10:00) | `status='will_apply' AND submitted_at IS NULL AND deadline` D-3, D-1 | "쓰기로 한 공고, 아직 제출 안 하셨어요" DM |
| 리마인드 (10:00) | `status='submitted' AND interview_at` D-1 | "내일 면접" DM |

Discord 버튼 3초 응답 제한은 v2와 동일하게 즉시 ACK 후 비동기 처리.

---

## 6. Layer 3 — 에이전트 (포트폴리오 코어)

> Layer 3는 `memory/`, `draft/` 패키지로 구현하되 **Matchwise 본체와 인터페이스로 분리**한다. 특히 Memory Agent는 "개인 기억저장소"라는 독립 프로젝트로 추출 가능한 구조를 유지한다 (별도 레포 분리는 안정화 후 판단).

### 6-1. experiences 테이블 (pgvector)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE experiences (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    title      VARCHAR(256),
    tags       TEXT[],
    period     VARCHAR(64),
    content    TEXT,
    embedding  VECTOR(768),
    source     VARCHAR(64),   -- 'wiki' | 'github' | 'manual'
    source_ref VARCHAR(512),  -- 원본 경로/URL (재동기화 키)
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

기존 `users.resume_text` / `experience_text` blob은 초기 마이그레이션 후 deprecated.

### 6-2. Memory Agent — 요구 ⑥⑦

**역할**: 개인 이력·경험의 단일 저장소를 구축·유지한다. 소스는 로컬 LLM 위키(`E:\00_llm_wiki`, frontmatter 구조 이미 일관됨)와 GitHub.

**Tools** (LangGraph 노드에서 호출하는 함수 툴):

| Tool | 기능 |
|---|---|
| `ingest_wiki(path)` | 위키 md 파싱(frontmatter + 본문) → 청킹 → 임베딩 → `experiences` upsert |
| `sync_github(repos)` | 등록 repo의 신규 커밋/README 변경 폴링 → 변경분 요약 → `experiences` 반영 |
| `search_memory(query, k)` | pgvector 코사인 유사도 top-k 검색 |
| `dedupe_memory()` | source_ref 기준 중복·구버전 정리 |

- 초기 적재: `scripts/import_wiki.py` (1회성 배치)
- 이후 갱신: scheduler 주간 잡으로 `sync_github` 폴링 (webhook은 외부 노출 URL이 필요해 현 인프라엔 폴링이 적정)
- Notion 연동은 실사용 시작 전까지 보류 (과설계 방지)

### 6-3. Draft Agent — 요구 ③

**역할**: `[이력서 작성하기]` 버튼 클릭 시 지원 질문별 답변 초안 생성.

**LangGraph StateGraph** (v2의 5단계를 RAG 기반으로 고도화):

```
extract_requirements   JD에서 핵심 요구사항 추출          (Gemini — 저비용 전처리)
        │
match_experiences      search_memory()로 요구사항별
        │              관련 경험 top-k 검색                (LLM 호출 없음, pgvector)
        │
draft_answers          질문별 초안 생성                    (Claude Pro — 품질 핵심)
        │              컨텍스트 = JD 요구사항 + 검색된 경험만
        │              (이력서 전체 주입 대비 품질↑ 토큰↓)
check_consistency      전체 일관성·톤 검토                  (Claude Pro)
        │
save_drafts            resume_drafts 저장 → Discord로 웹 링크 DM
```

### 6-4. Claude Pro OAuth 인증 설계

- EC2에서 `ant auth login --no-browser`(또는 Claude Code `/login`)로 1회 인증 → 프로필이 `~/.config/anthropic/`에 저장
- Draft Agent의 Claude 호출은 `anthropic.Anthropic()` 제로-인자 클라이언트 — API 키 없이 OAuth 프로필을 자동 인식 (공식 지원 경로)
- **적합성 근거**: Draft Agent는 유저 버튼 클릭 트리거라 저빈도 → Pro 5시간 롤링 한도 내에서 충분. 배치성 fit_score는 절대 Claude로 돌리지 않는다(한도 소진 방지)
- **장애 대응**: refresh token 만료(슬라이딩 아님) 시 호출 실패 → Discord 관리자 DM으로 재로그인 알림, Draft 요청은 실패 안내 후 재시도 유도

### 6-5. 평가 (포트폴리오 요구사항 대응)

| 대상 | 방법 |
|---|---|
| RAG 검색 품질 | 실제 자소서 문항 → 기대 경험 매칭 여부로 recall@k 측정 (수동 라벨 ~20건) |
| 초안 품질 | 내가 실제 제출한 자소서 vs 생성 초안 비교 평가셋. LLM-as-judge + 본인 평가 병행 |
| 회귀 방지 | 프롬프트 변경 시 평가셋 재실행하는 스크립트 (`scripts/eval_draft.py`) |

### 6-6. AI Agent 직무 공고 매핑

| 공고 요구 | 본 설계에서 대응 |
|---|---|
| LLM·LangChain/LangGraph 활용 Agent 설계·프로토타입 | Memory/Draft Agent StateGraph 설계·구현 |
| Tool/Function 설계 | `ingest_wiki` `sync_github` `search_memory` 등 명시적 툴 계약 |
| Prompt 엔지니어링 | 단계별 프롬프트(요구사항 추출/초안/검토) 설계·버전 관리 |
| RAG 파이프라인 구축 | 위키 md → 청킹·임베딩 → pgvector → 검색 → 주입 전 과정 직접 구축 |
| Agent 테스트·성능 평가·개선 | 6-5 평가 체계 |
| 문서화 | 본 설계 문서 + 레포 문서 |

---

## 7. LLM 호출 설계 (전체 요약)

| 호출 지점 | 빈도·볼륨 | 요구 품질 | LLM | 인증 |
|---|---|---|---|---|
| fit_score 배치 (05:00) | 高 (유저×신규공고, 몰림) | 낮음 (채점) | Gemini Flash | API 키 (무료 티어) |
| JD 요구사항 추출 (Draft 전처리) | 低 | 중간 | Gemini Flash | API 키 |
| 초안 생성·일관성 검토 | 低 (버튼 클릭 시) | **높음** | Claude (Pro) | OAuth 프로필 |
| GitHub 변경 요약 (주간) | 低 | 낮음 | Gemini Flash | API 키 |

---

## 8. 기술 스택 (v2 대비 변경분만)

| 역할 | v2 | v3 |
|---|---|---|
| 벡터 검색 | (없음) | **pgvector** (기존 Postgres 확장) |
| 초안 생성 LLM | Gemini Flash | **Claude Pro (OAuth)** |
| Agent 프레임워크 | LangGraph (계획만) | LangGraph (Memory + Draft 2개 그래프) |
| 나머지 | Python 3.12 · FastAPI · discord.py · Playwright · PostgreSQL 16 · APScheduler · Docker Compose · EC2 | 동일 |

---

## 9. 로드맵

| Phase | 내용 | 요구 | 의존성 |
|---|---|---|---|
| **A** | (진행 중) Layer 1 완성 — 봇 버튼 인터랙션, web-api 대시보드 | ①② | — |
| **B** | `applications` 테이블 + `[쓰겠다]`/`[제출했다]` 버튼 | ④⑤ 토대 | A |
| **C** | 재촉·리마인드 scheduler 잡 | ④⑤ | B |
| **D** | pgvector + `experiences` + 위키 임포트 (`scripts/import_wiki.py`) | ⑥ | A |
| **E** | Draft Agent (LangGraph + RAG + Claude Pro OAuth) + 평가셋 | ③ | D |
| **F** | GitHub 동기화 (Memory Agent `sync_github`) | ⑦ | D |
| **G** | 멀티유저 — `/register` 슬래시 커맨드 온보딩, 유저당 일일 분석 상한 | ⑧ | A~F 안정화 |

B/C와 D/E/F는 상호 독립 — 병렬 진행 가능.

---

## 10. 제약 및 리스크

| 제약 | 대응 |
|---|---|
| t2.micro 1GB RAM | 스왑 2GB, 크롤러 02시 단독 실행 (v2 동일). pgvector는 경량 확장이나 **설치 후 메모리 사용 실측 필요** |
| Gemini 무료 티어 15 req/min | 배치 분산 (v2 동일). 멀티유저 확장 시 유저당 상한 필수 |
| Claude Pro 5시간 롤링 한도 | Draft Agent만 사용 (저빈도). 배치 작업 사용 금지 |
| OAuth refresh token 만료 | 실패 감지 → 관리자 Discord DM → `--no-browser` 재로그인 |
| 위키 파싱 안정성 | frontmatter(`updated/tags/status/sources`)가 이미 일관되나, 임포트 스크립트에 스키마 검증 + 실패 파일 리포트 포함 |
| Discord 버튼 3초 제한 | 즉시 ACK 후 비동기 (v2 동일) |

---

## 11. 미결 사항

1. **GitHub 동기화 대상**: 전체 소유 repo vs 지정 repo 목록 (Phase F 착수 전 결정)
2. **pgvector 실측**: t2.micro에서 확장 설치 + 임베딩 768차원 × 예상 수백 행 규모 검증 (Phase D 첫 작업)
3. **Memory Agent 독립 레포 분리 시점**: Phase D~F 완료 후 판단
4. **임베딩 모델**: Gemini `text-embedding-004`(768d, 무료) 기본. 품질 이슈 시 재검토

---

## 12. 문서 이력

| 버전 | 날짜 | 내용 | 위치 |
|---|---|---|---|
| v1 | 2025-02 | AWS 멀티에이전트 구상 (미구현) | `docs/archive/2025-02-design-v1-aws.md` |
| v2 | 2026-06-27 | 단일 파이프라인 축소, 구현 착수 | 본 문서 Layer 1로 흡수 |
| v3 | 2026-07-13 | 3-레이어 통합 — v1 기능 명세 × v2 인프라 + 요구 8종 + LLM 이원화 | 본 문서 |
