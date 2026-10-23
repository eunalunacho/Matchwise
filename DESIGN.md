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
| ① | 쓸 만한 공고 자동 수집 | Layer 1 | ✅ 인디스워크·자소설·잡코리아 수집 + 추출·OCR (docs/superpowers/specs 참고). 청킹(`job_chunks`)은 미착수 |
| ② | 나에게 맞는 순서로 추천 | Layer 1 | 🟡 사용자별 매칭 채널에 아직 안 보낸 공고 상위 3개 카드 + 나머지 스레드 + `/dashboard` 전체 점수순 목록(구 점수 표시·재채점 요청). 신규공고 페이지(B3)는 미구현 |
| ③ | "쓰겠다" 하면 내 기록 기반 초안 작성 | Layer 3 (Draft Agent) | 🟡 설계만 (LangGraph 미구현) |
| ④ | "제출했다" 하면 일정 저장·리마인드 | Layer 2 | ✅ 웹 [제출 완료]·DM [제출함] 버튼, 마감 D-7/D-2 DM 리마인드(C2) |
| ⑤ | 쓰겠다 하고 안 쓰면 재촉 | Layer 2 | ✅ 3일 현황 요약 DM(C1)·마감일 없는 공고는 등록 7일째 리마인드 |
| ⑥ | 위키처럼 이력을 쓰면 전용 기억저장소 구축 | Layer 3 (Memory Agent) | ❌ 텍스트 blob 2개뿐 |
| ⑦ | 외부 서비스(GitHub 등) 업데이트 추적 → 기억저장소 갱신 | Layer 3 (Memory Agent) | ❌ 없음 |
| ⑧ | 형태 잡히면 친구들도 쓰는 서비스로 | 전체 | 🟡 구글 로그인 + Discord 연결 완료. 유저당 분석 상한 없음(전체 공유 상한만) |

---

## 3. 아키텍처

```
AWS EC2 t2.micro — Docker Compose
│
├─ Layer 1. 파이프라인  (v2 승계 — 일반 코드, 에이전트 아님)
│    scheduler (APScheduler)
│      08:00 → run_daily_pipeline: run_crawl_pipeline (crawl_job → extract_job 순차 함수 호출, 워크플로우 체인 —
│                                  고정 시간에 따로 도는 게 아니라 크롤링이 끝나면 바로 추출로 이어짐)
│                                  crawl_job:   인디스워크 WordPress API · 자소설 API · 잡코리아 HTTP
│                                               (on/off는 _enabled_crawlers() 한 곳)
│                                  extract_job: pending·failed·needs_ocr를 추출(jd_tasks 등 +
│                                               eligibility). needs_ocr는 OCR(기본 Google Cloud
│                                               Document AI, OCR_PROVIDER로 Claude Code 전환 가능)로
│                                               이미지→텍스트 변환 후 곧바로 같은 실행에서 추출까지
│                                               진행(raw_postings는 안 건드림). OCR 자체가 실패하면
│                                               상태를 needs_ocr로 유지해 다음 실행에 재시도.
│                                               empty만 계속 보류(텍스트·이미지 둘 다 없음)
│        이어서 → analysis  (Gemini Flash: fit_score. 추출 완료(ok) + 신입 가능/모름 + 서울·과천·판교·분당/미기재만.
│                         extract_job과 하루 Gemini 호출 상한 400건 공유
│                         / DART company_score는 비활성화)
│        이어서 → bot 알림  (discord.py: 사용자별 매칭 채널에 안 보낸 추천 상위 3개 + 스레드)
│        이어서 → 비서 DM   (마감 리마인드 매일 · 현황 요약 3일마다)
│    요구 ①② 담당
│
├─ Layer 2. 지원 관리  (v1 Application Manager 개념을 새 인프라로)
│    tracker/  applications(현재 상태) + application_events(변경 기록), change_status 한 곳에서 변경
│              lists — 작성 중/미작성/마감 리마인드/놓친 추천/최근 변화 조회 (웹·DM 공용)
│    Discord 버튼 [자세히 보기][목록에 등록][패스] (5-2), 리마인드 DM [제출함]
│    bot/assistant.py: DM 3일 현황 요약(5-3), 마감 D-7/D-2 리마인드(5-4) — LLM 불필요
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

## 4. Layer 1 — 파이프라인 (v2 승계 + JD 저장 계층)

v2의 일일 플로우를 유지하되, 수집 단계는 [JD 저장 설계](docs/superpowers/specs/2026-09-24-jd-storage-design.md)대로 원본(`raw_postings`) → 정리(`jobs`) 계층으로 바뀌었다. 코드는 `crawler/`, `analysis/`, `scheduler/`, `bot/`, `db/orm.py`.

- **수집·추출(08:00 체인 1단계, `run_crawl_pipeline`)**: `crawl_job`이 신규 공고를 URL 기준 중복 제거 후 `raw_postings`(원본 그대로)와 `jobs`(`extract_status` 자동 분류)에 저장하고 사이트별 결과를 로그 파일(`logs/<프로그램>.log`, 최근 7일 보관)에 기록 → 이어서 `extract_job`이 JD 요약(`jd_*`)·`eligibility`·근무지(`work_locations`)를 채우고 이미지 JD는 OCR. 크롤러가 마감일을 못 준 공고(인디스워크)는 요약·OCR 본문에 적힌 서류 마감일로 `deadline`을 채운다(2026-10-15, 기존 공고는 `scripts/backfill_deadlines.py`). 실패(`failed`·`needs_ocr`)는 수집한 날 + 다음 날까지만 재시도. 진행 상황(시작·수집 완료·추출 완료·에러)은 Discord 비공개 채널 웹훅으로 전송
- **분석(체인 2단계)**: 채점 대상은 `extract_status='ok'`이고 아직 점수가 없는 (공고 × 활성 유저) 조합 중, 하는 일(`jd_tasks`)이 비어 있지 않고, 신입 불가(`eligibility.new_grad_ok=false`)가 아니고 근무지(`work_locations`)가 서울·과천·판교·분당 중 하나이거나 미기재인 공고(2026-10-14 결정 — 정보가 없으면 채점, 명시적으로 안 맞는 것만 제외). 조합마다 Gemini Flash가 직무 선택·관문(학력·경력·필수 어학/자격·전공)·항목 점수(직무 방향 30·필수 스택 15·업무 경험 20·우대 20·근거 강도 10·도메인 5)를 매기면 코드가 총점(`fit_score`, 우대사항 없으면 100점 환산, 관문 fail이면 30점 상한)을 계산해 `fit_reasons`·`fit_detail`(JSON)과 함께 저장(2026-10-14). 답을 해석 못 하면 저장하지 않고 다음 실행에 재시도. 근거는 `users.basic_info` + `experiences`를 `analysis/profile.py::build_profile_text()`로 펼친 프로필 텍스트(2026-10-14 전환, 둘 다 비어 있는 유저는 채점 건너뜀). `company_score`는 비활성화(DART/LLM 계산 함수만 보존). 하루 호출 상한 400건을 `extract_job`과 공유(`gemini_usage` 테이블)
- **채점 버전과 재채점(2026-10-15)**: 점수마다 채점에 쓴 `profile_version`(프로필을 고칠 때마다 +1)과 `scorer_version`(`analysis/scorer.py::SCORER_VERSION`, 채점 규칙을 바꾸면 +1)을 남긴다. 둘 중 하나라도 지금 값과 다르면 **구 점수**로 표시만 하고 자동으로 다시 채점하지 않는다. 사용자가 웹(`/dashboard`의 [구 점수 재채점 요청], 공고 상세의 [재채점 요청])에서 요청하면 `rescore_requested_at`이 찍히고, 다음 `analyze_job`이 신규 채점 뒤에 하루 한도 안에서 줄을 지우지 않고 값만 덮어쓴다. 새로 들어오는 공고는 바뀐 프로필로 채점된다
- **알림(체인 3단계, 이어서 비서 DM)**: 사용자마다 자기 매칭 채널(`users` 테이블의 `match_channel_id` 칸 — 사용자가 그 채널에서 슬래시 명령어 `/매칭채널`로 직접 정한다, `bot/commands.py`. 없으면 그 사용자는 건너뜀 — 공유 채널은 남의 점수가 보여 두지 않는다)에 **아직 안 보낸**(`job_scores.notified_at` 비어 있음) 점수만 보내고, 보낸 뒤 `notified_at`·`notified_as`(card/thread)를 기록한다. 재채점해도 다시 나가지 않는다(2026-10-15, 이전의 "오늘 채점된 것" 기준을 대체). 상위 3개는 카드(제목 `[회사] 직무명 (고용형태)`, 본문 하는 일 + 왜 나랑 맞나, 3버튼 5-2), 나머지는 스레드에 `회사 — 직무 · 점수` 한 줄 링크. fit_score 50점 미만·지원 상태(등록·패스 등)가 있는 공고 제외 (2026-10-15)
- **정리(01:00, `cleanup_job`)**: 마감 지난 `jobs`와 점수·질문·초안 삭제. 패스 외의 `applications`가 있는 공고와 `raw_postings`는 보존(패스만 있는 공고는 그 기록과 함께 삭제)
- **시각 저장 규칙(2026-10-15)**: 모든 시각 칸은 `db/orm.py::UTCDateTime` — UTC로 저장하고 UTC로 읽는다. SQLite가 시간대를 버려 KST 마감일과 UTC 기록 시각이 섞이던 문제를 고쳤다(기존 마감일은 `scripts/migrate_tracking.py`가 -9시간 보정). D-day·7일째 계산은 한국 날짜 기준(`tracker/lists.py::kst_date`)

---

## 5. Layer 2 — 지원 관리

> 2026-08-11 구체화(v3.1). 아래 5-2~5-8은 실사용 요구를 반영해 처음보다 구체적으로 확정한 내용이며, 일부는 판단이 필요해 **[결정]** 태그로 명시하고 11절 미결 사항에도 정리했다.

### 5-1. applications + application_events (구현됨, 2026-10-15 개편)

**2026-10-15 결정 — 2명이 쓰지만 2~100명이 붙어도 되는 구조**: 사용자 × 공고의 상태는 현재 상태(`applications`)와 변경 기록(`application_events`)으로 나눈다. 패스(관심 없음)도 사용자 행동이라 점수(`job_scores.dismissed`, 삭제됨)가 아니라 여기 `dismissed`로 둔다. 상태는 `tracker/applications.py::change_status`로만 바꾼다 — 현재 상태를 고치고 기록을 한 줄 추가하는 일을 한 번에 한다(Discord 버튼·웹 모두).

```sql
CREATE TABLE applications (              -- (공고, 사용자) 한 쌍에 한 줄, 상태가 바뀌면 고쳐 씀
    id            SERIAL PRIMARY KEY,
    job_id        INTEGER REFERENCES jobs(id),
    user_id       INTEGER REFERENCES users(id),
    status        VARCHAR(32) NOT NULL DEFAULT 'saved',
    -- saved(관심) | will_apply(지원 예정) | submitted(제출) | assignment(과제) | interview(면접)
    -- | passed | rejected | dismissed(패스)
    will_apply_at TIMESTAMPTZ,           -- 마감일 없는 공고의 "등록 7일째" 리마인드 기준
    submitted_at  TIMESTAMPTZ,
    interview_at  TIMESTAMPTZ,           -- 사용자가 직접 입력
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (job_id, user_id)
);  -- INDEX (user_id, status)

CREATE TABLE application_events (        -- 바뀔 때마다 한 줄 추가, 고치거나 지우지 않음
    id             SERIAL PRIMARY KEY,
    application_id INTEGER REFERENCES applications(id),
    user_id        INTEGER REFERENCES users(id),
    from_status    VARCHAR(32),          -- 처음 만들어질 때 NULL
    to_status      VARCHAR(32) NOT NULL,
    source         VARCHAR(16) NOT NULL, -- discord | web | system
    created_at     TIMESTAMPTZ
);  -- INDEX (user_id, created_at)
```

예: A가 10/15 애경을 [목록에 등록] → 10/20 웹에서 [제출 완료]하면 `applications`는 1줄(`submitted`), `application_events`는 2줄(`NULL→will_apply`, `will_apply→submitted`). 목록(작성 중·미작성·리마인드·놓친 추천)은 저장하지 않고 `tracker/lists.py`가 조건으로 만든다.

### 5-2. Discord 매칭 채널 — 일일 추천 카드 (버튼 3종으로 축소)

**2026-10-15 결정 — 뉴스레터와 비서를 위치로 나눈다**: 매일 매칭 공고(훑어보는 정보)는 **채널**, 지원 현황·마감 리마인드·초안 완성처럼 나에게 할 일을 알리는 '비서' 메시지는 **DM**, 수집 진행 로그는 웹훅 채널(`DISCORD_INGEST_WEBHOOK_URL`). 매칭 카드는 버튼이 있어 웹훅이 아니라 봇이 보낸다. 멀티유저가 되면 공유 채널에 개인 점수가 보이므로 유저별 비공개 채널로 바꾼다. 이 결정으로 아래 5-3·5-4의 "채널"은 DM으로 바뀐다.

기존에 만들었던 5버튼(상세분석/이력서작성하기/쓰겠다/제출했다/별로임) 구성을 **3버튼으로 되돌린다** — "실제 입력·내용 확인은 웹에서만" 원칙에 맞춰 카드는 순수 알림 역할만 한다.

| 버튼 | 종류 | 동작 |
|---|---|---|
| 자세히 보기 | 링크 버튼 (`style=link`, `url=job.url`) | 스크랩한 원본 공고로 바로 이동. Discord 인터랙션 자체가 안 뜸 — DB 왕복 없음 |
| 목록에 등록 | 일반 버튼 | `applications.status='will_apply'` 즉시 기록(가벼운 상태 마킹) + ephemeral 응답으로 웹 작성 페이지 링크(`/jobs/{id}/apply`) 안내 |
| 패스 | 일반 버튼 | `applications.status='dismissed'` — 이후 추천에서 완전 제외 (2026-10-15 점수 테이블에서 이동) |

**[결정]** "목록에 등록"은 상태만 즉시 DB에 쓰고(가벼운 북마크), 자소서 같은 실제 콘텐츠 작성·저장은 전부 웹에서만 일어난다 — "클릭 즉시 DB 반영 금지" 원칙은 콘텐츠에 대해서만 적용, 상태 마킹에는 적용하지 않는 걸로 해석했다.

### 5-3. Discord DM — 3일 주기 현황 공유

3일마다 **DM**으로 현황 요약을 보낸다 (2026-10-15 채널→DM). 구현: `bot/assistant.py` — 매일 09:10 잡이 돌고 한국 날짜 번호가 3의 배수인 날만 보낸다(`IntervalTrigger`는 스케줄러를 다시 켜면 주기가 밀려서 쓰지 않음). 보여줄 게 없으면 보내지 않는다. 작성 중·미작성은 상태가 아니라 `will_apply`를 초안 유무로 나눈 목록이라, 초안 기능(B5) 전까지는 작성 중이 비어 있을 뿐 둘 다 유지한다.

| 섹션 | 조건 | 최대 |
|---|---|---|
| 작성 중 | `status='will_apply'` AND 해당 job에 `resume_drafts` 1건 이상 존재 | 3건 |
| 미작성 | `status='will_apply'` AND `resume_drafts` 0건 | 3건 |
| 놓치고 있는 추천 | 카드로 보낸 지(`notified_as='card'`) 3일 이상 지남 AND `application` 레코드 없음(등록·패스 둘 다 안 누름) AND `fit_score >= 50` AND 마감 전 (2026-10-15 구체화) | 3건 |
| 최근 3일 변화 | `application_events` 중 3일 이내, 패스 제외 | 전부 |

DM 대상은 `users.discord_id`(없는 유저는 건너뜀).

### 5-4. Discord DM — 마감 리마인드 (D-7 / D-2)

| 조건 | 동작 |
|---|---|
| `status='will_apply' AND deadline` 한국 날짜로 D-7 또는 D-2 | DM으로 "마감 D-N — 아직 제출 전이에요" 카드 + [자세히 보기][제출함] |
| `status='will_apply' AND deadline IS NULL` 등록(`will_apply_at`) 후 7일째 | 같은 카드, "등록 7일째 (마감일 미확인)" |

**[결정]** 기존 설계(D-3/D-1, DM)를 **D-7/D-2**로 교체한다. 위치는 2026-08 "채널+태그"에서 2026-10-15 **DM**으로 다시 바꿨다(5-2 뉴스레터/비서 분리).

**[결정 2026-10-15] 제출 완료 표시는 두 곳**: 웹 공고 페이지의 [제출 완료] 버튼 + 리마인드 DM의 [제출함] 버튼. 둘 다 같은 상태 변경 함수로 `status='submitted'`를 기록한다.

**[결정 2026-10-15] 마감일 없는 공고**: 인디스워크는 목록에 마감일이 없다. 확인 결과 ① 본문(jd_text)에 "2026.10.12 ~ 2026.10.25"처럼 적힌 경우가 31건 중 6건 이상, ② 원본 채용 페이지(`apply_url`)는 사이트마다 달라 나인하이어처럼 마감일 필드가 있는 곳도 있고 recruiter.co.kr·노션처럼 HTTP로는 내용이 안 오는 곳도 있다. → 추출 단계에서 본문의 마감일을 함께 뽑고, 그래도 없으면 등록 후 7일째 한 번 리마인드한다. 원본 페이지 파싱은 자주 나오는 채용 사이트부터 나중에 검토.

### 5-5. 웹 — 메인 페이지

버튼/링크 4개:

1. 내가 "목록에 등록"한 공고 일정 캘린더
2. 신규공고 보기
3. 작성중인 공고 보기
4. 경력·자기소개서 업데이트

### 5-6. 웹 — 신규공고 페이지

- 대상: `fit_score >= 50` AND `jobs.crawled_at >= now() - 7일`
- 표시: 공고명, 마감일, 내 액션 상태(`will_apply` / `passed` / 없음)

### 5-7. 웹 — 작성중인 공고 페이지 + 작성 페이지

- 목록: `status='will_apply'` 인 것만, 마감일 순 정렬
- 클릭 → `/jobs/{id}/apply`: 상단에 원본 공고 링크(`job.url`), 하단에 `application_questions`별 입력창 — 각 질문에 [초안 생성] 누르면 LLM이 5-8의 경험 데이터를 근거로 초안 작성, 편집 후 저장하면 `resume_drafts`에 반영. 같은 페이지에 "제출완료로 표시" 체크박스(→ `status='submitted'`, `submitted_at` 기록)

**[결정]** "마감일 내림차순"이라고 하셨는데, 지금까지 일관되게 "마감 가까운 걸 먼저" 써왔던 것과 반대라 오름차순(가까운 순)으로 해석했다 — 아니면 알려주세요.

### 5-8. 웹 — 경력·자기소개서 업데이트 페이지 (경험 조각 저장, RAG 없음)

문서 통째로 쓰는 페이지가 아니라 **재사용 가능한 경험 조각**을 저장하는 페이지.

- 기본 정보(이름·학력 등): LLM 안 거치고 폼 입력 그대로 저장 — `users.basic_info JSON` 신규 컬럼
- 경험 조각(경력/프로젝트/자소서용 에피소드): 신규 `experiences` 테이블 — pgvector 없이 단순 저장, 초안 생성 시 유저의 경험 조각 전부를 LLM 컨텍스트에 넣는 방식(RAG 아님, 6-1과 다른 경량 버전)

```sql
CREATE TABLE experiences (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    kind       VARCHAR(32) NOT NULL,  -- 'career' | 'project' | 'story'
    title      VARCHAR(256),
    period     VARCHAR(64),
    content    TEXT NOT NULL,
    tags       TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**2026-10-14 항목 확정**: `basic_info`(한 사람에 하나씩 정해진 칸) = 이름·생년월일·병역·취업우대(보훈·장애)·고등학교·대학교·공인외국어시험·자격증·컴퓨터활용능력·링크. `experiences.kind`(개수가 늘어나는 서술형) = `career`(경력사항)·`project`(프로젝트)·`award`(수상)·`campus`(교내활동)·`club`(동아리)·`team_project`(팀프로젝트·공모전)·`education`(교육이수)·`volunteer`(봉사)·`story`(자소서 에피소드). 처음 데이터는 로컬 위키 + 사용자 최신 이력서로 만든 `profile.json`(gitignore)을 `python -m scripts.import_profile`로 적재 — 다시 실행하면 파일 내용으로 통째로 교체. 웹 기본 정보 폼은 이름·학력 두 칸만 바꾸고 나머지 항목은 보존한다.

**[결정]** 초안 생성 근거를 "resume_text/experience_text 블롭 그대로 LLM에" 방식으로 이미 합의했었는데, 이 페이지가 생기면서 근거 데이터가 그 블롭이 아니라 **이 `experiences` 조각들**로 바뀐다 — 방식(LLM 컨텍스트에 통째로 주입, pgvector 없음)은 동일하게 유지, 데이터 소스만 구조화된 걸로 교체.

Discord 버튼 3초 응답 제한은 v2와 동일하게 즉시 ACK 후 비동기 처리.

---

## 6. Layer 3 — 에이전트 (포트폴리오 코어)

> Layer 3는 `memory/`, `draft/` 패키지로 구현하되 **Matchwise 본체와 인터페이스로 분리**한다. 특히 Memory Agent는 "개인 기억저장소"라는 독립 프로젝트로 추출 가능한 구조를 유지한다 (별도 레포 분리는 안정화 후 판단).

### 6-1. experiences 테이블 (pgvector) — 5-8 경량 버전의 확장판

`experiences` 테이블은 5-8에서 **pgvector 없는 경량 버전**(`kind`/`title`/`period`/`content`/`tags`)으로 먼저 만든다. 이 절은 그 테이블에 검색·동기화가 실제로 필요해졌을 때(경험 조각이 많아져서 LLM 컨텍스트에 전부 넣기 부담스러워지는 시점) 아래 컬럼을 `ALTER TABLE`로 추가하는 확장판이다 — 별도 테이블이 아니다.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE experiences
    ADD COLUMN embedding  VECTOR(768),
    ADD COLUMN source     VARCHAR(64),   -- 'wiki' | 'github' | 'manual' (5-8 수기 입력분은 'manual')
    ADD COLUMN source_ref VARCHAR(512);  -- 원본 경로/URL (재동기화 키)
```

기존 `users.resume_text` / `experience_text` blob은 5-8 도입 후 deprecated.

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

### 6-7. 크롤러 온보딩 에이전트 (확장 아이디어, 우선순위 미정)

**배경**: 크롤링을 Claude Code(OAuth 구독) + Skills로 대체하는 안을 검토했으나, 이미 안정적으로 동작하는 인디스워크 크롤러(Phase A)를 에이전트로 교체하는 건 이력/설계 관점에서 이득이 적다고 판단했다 — 제어 로직이 Claude Code의 내장 에이전트 루프 안에 있어 "직접 설계한 에이전트"로 보기 어렵고, 설계 원칙 1번(반복 배치는 일반 코드로)과도 충돌한다. 대신 **일반 코드로 처리 못 하는 지점** — 신규 채용 사이트를 추가할 때마다 사람이 selector를 새로 짜야 하는 부분 — 을 에이전트화한다.

**역할**: 신규 채용 사이트 URL을 주면, 페이지 구조를 스스로 파악해 목록/상세 추출 스키마(선택자·추출 규칙)를 제안하고 검증한다.

**Tools**:

| Tool | 기능 |
|---|---|
| `fetch_page(url)` | 페이지 HTML/구조 로드 |
| `propose_extraction_schema(html)` | title/company/url/deadline 등 필드의 추출 규칙 제안 |
| `validate_extraction(schema, html)` | 제안된 스키마로 샘플 추출 후 정확도 확인 |

**인증**: Claude Pro/Max OAuth 프로필(`anthropic.Anthropic()` 제로-인자 클라이언트, 6-4와 동일) — 별도 API 과금 없이 구독 재사용. Claude Code CLI/Skills가 아니라 **직접 설계한 tool-calling 루프**로 구현해 Agent/Tool 설계 역량이 코드로 남도록 한다.

**적용 범위**: 매일 도는 기존 2개 사이트 크롤러(Phase A)는 그대로 유지 — 신규 사이트 추가 시에만 사용.

**의존성**: Layer 1 안정화 이후. 우선순위 낮음, 착수 시점 미정.

---

## 7. LLM 호출 설계 (전체 요약)

| 호출 지점 | 빈도·볼륨 | 요구 품질 | LLM | 인증 |
|---|---|---|---|---|
| JD 요약·eligibility 추출 (08:00 체인, `extract_job`) | 中 (신규 공고당 1회) | 중간 | Gemini Flash | API 키 (무료 티어) |
| 이미지 JD OCR (08:00 체인, `extract_job`) | 中 (이미지 공고당 1회) | 높음 (원문 그대로) | Google Document AI (기본) / Claude Code 헤드리스 sonnet (선택) | ADC / 구독 로그인 |
| fit_score 배치 (08:00 체인) | 高 (유저×신규공고, 몰림) | 낮음 (채점) | Gemini Flash | API 키 (무료 티어) |
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
| 크롤링 | Playwright | **httpx만** (사이트 API/HTML 직접 호출, 브라우저 없음) |
| OCR | (없음) | **Google Cloud Document AI** |
| 나머지 | Python 3.12 · FastAPI · discord.py · PostgreSQL 16 · APScheduler · Docker Compose · EC2 | 동일 |

---

## 9. 로드맵

| Phase | 내용 | 요구 | 상태 | 의존성 |
|---|---|---|---|---|
| **A** | Layer 1 완성 — 봇 버튼 인터랙션, web-api 대시보드 | ①② | ✅ 완료 (JD 저장 계층 1·2차까지. 3차 `job_chunks`는 스펙 문서에서 관리) | — |
| **B1** | `applications` 테이블 | ④⑤ 토대 | ✅ 구현됨 (2026-10-15 `application_events`·`change_status`·패스 이동) | A |
| **B2** | Discord DM 3버튼(자세히보기/목록등록/패스)으로 재정리 (5-2) | ④⑤ | ✅ 완료 — 단 "목록에 등록" 응답의 링크 `/jobs/{id}/apply`는 B5 전까지 404 | B1 |
| **B3** | 웹: 메인/신규공고/작성중공고 페이지 (5-5, 5-6, 5-7 목록부) | ②④ | ❌ 없음 | B1 |
| **B4** | 경험 조각 저장 페이지 (5-8, pgvector 없음) | ③ 토대 | ✅ 완료 (`/experiences`) | — (독립) |
| **B5** | 작성 페이지 + LLM 자동 초안 생성 (5-7 작성부) | ③ | ❌ 없음 (지금은 `/jobs/{id}` 질문 등록 + `/draft/{id}` 조회만, 초안을 만드는 코드 없음) | B3, B4 |
| **C1** | DM 3일 현황 공유 (5-3) | ④⑤ | ✅ 2026-10-15 (`bot/assistant.py`) | B1 |
| **C2** | 마감 리마인드 D-7/D-2, DM (5-4) | ④⑤ | ✅ 2026-10-15 — [제출함] 버튼, 마감일 없으면 등록 7일째 | B1 |
| **D** | pgvector로 `experiences` 확장 (6-1) | ⑥ | 보류 — B4 데이터 많아지면 착수 | B4 |
| **E** | Draft Agent를 LangGraph StateGraph로 고도화 (6-3) | ③ | 보류 — B5(직접 LLM 호출)로 우선 충분 | D |
| **F** | GitHub 동기화 (Memory Agent `sync_github`) | ⑦ | ❌ 없음 | D |
| **G** | 멀티유저 — ~~`/register` 슬래시 커맨드~~ **구글 로그인 + Discord 계정 연결**(2026-08-13 방향 전환), 유저당 일일 분석 상한 | ⑧ | ✅ 로그인/연결 완료, 일일 상한은 미착수 | B~F 안정화 |
| **H** | 크롤러 온보딩 에이전트 (6-7) | 확장 (①) | ❌ 없음, 우선순위 낮음 | A 안정화 |

B4는 다른 B*와 독립적으로 바로 시작 가능. C1/C2는 B1만 있으면 되고 B2~B5와 병렬 가능. D/E/F/G/H는 보류 또는 후순위.

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
2. **pgvector 실측**: t2.micro에서 확장 설치 + 임베딩 768차원 × 예상 수백 행 규모 검증 (Phase D 첫 작업, 보류 중)
3. **Memory Agent 독립 레포 분리 시점**: Phase D~F 완료 후 판단
4. **임베딩 모델**: Gemini `text-embedding-004`(768d, 무료) 기본. 품질 이슈 시 재검토
5. **(신규) 작성중인 공고 목록 정렬 방향**: 5-7에서 마감일 오름차순(가까운 순)으로 임시 결정 — 확인 필요
6. **(신규) "원본 지원 페이지" 정의**: 지금은 `job.url`(스크랩한 목록 링크)로 임시 결정. 실제 지원 폼 URL이 따로 있는 사이트가 나오면 별도 필드 필요할 수 있음
7. **(신규) `users.basic_info` 필드 범위**: "이름·학력 등"에서 "등"이 구체적으로 뭘 포함하는지 미정 (나이/연락처/포트폴리오 링크 등) — 웹 폼 설계 시 확정
8. **(신규) 초안 자동 생성 트리거**: 질문마다 개별 [초안 생성] 버튼인지, 페이지 진입 시 전체 질문 일괄 생성인지 미정
9. **(2026-10-14) 서버 배포·멀티유저 방식 보류**: 우선 내 PC에서만 돌린다. 친구들이 AI 툴을 주로 쓰므로 웹페이지를 계속 만들지, 다른 형태로 제공할지 기능 완성 후 재논의. 그때 Google API 인증도 함께 결정 — 후보: 로컬은 ADC 파일 마운트, EC2면 Workload Identity Federation(키 없음), GCP로 옮기면 VM에 서비스 계정 부착. 서비스 계정 키 파일은 비추천
10. **(2026-10-15, 이후 할 일) 사이트 간 중복 공고 제거**: 같은 공고가 여러 사이트에서 따로 수집돼 두 번 채점·추천된다(예: 애경 AK아이에스 2027 신입 공채 — 인디스워크 84점·잡코리아 82점). 지금 중복 기준은 사이트 안의 URL뿐이다. 후보: `apply_url`(기업 채용 페이지) 정규화 일치, 또는 회사명+제목 유사도. 알림 단계에서 같은 회사·같은 직무를 하나로 묶는 것부터 검토
11. **(2026-10-15, MVP 이후 할 일) 채점 전 사전 필터**: 사용자가 늘면 `신규 공고 수 × 사용자 수`만큼 Gemini를 부른다(하루 150건 × 5명 = 750회 > 하루 한도 400). 희망직무 키워드·직군 분류처럼 AI를 쓰지 않는 규칙으로 그 사용자 분야의 공고만 채점 대상으로 넘긴다
12. **(2026-10-15, 전체 구조를 잡은 뒤 할 일) 사용자별 크롤러 검색 조건**: 분야가 다른 사용자의 공고가 수집되려면 사이트별 검색 조건(직군·키워드)을 사용자 희망직무에서 모아 만들어야 한다. 지금 크롤러 조건은 한 벌이다

---

## 12. 문서 이력

| 버전 | 날짜 | 내용 | 위치 |
|---|---|---|---|
| v1 | 2025-02 | AWS 멀티에이전트 구상 (미구현) | `docs/archive/2025-02-design-v1-aws.md` |
| v2 | 2026-06-27 | 단일 파이프라인 축소, 구현 착수 | 본 문서 Layer 1로 흡수 |
| v3 | 2026-07-13 | 3-레이어 통합 — v1 기능 명세 × v2 인프라 + 요구 8종 + LLM 이원화 | 본 문서 |
| v3.1 | 2026-08-11 | Layer 2 구체화 — Discord 3버튼, 채널 현황/리마인드, 웹 5개 페이지, 경험 조각 저장(pgvector 없는 경량판) | 본 문서 5절 |
| v3.2 | 2026-10-14 | 코드 기준 현행화 — Layer 1에 JD 저장 계층(raw_postings·extract_job·OCR)·크롤러 3곳 반영, Playwright 제거, company_score 비활성화 명시, 로드맵 상태 갱신 | 본 문서 2·3·4·7·8·9절 |
