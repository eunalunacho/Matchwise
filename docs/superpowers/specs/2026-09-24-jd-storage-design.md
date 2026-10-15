# JD 저장 설계 — raw → jobs → job_chunks

**Date**: 2026-09-24
**Status**: 1·2차 저장(raw_postings, extract_job+OCR) 구현 완료, 수집 로그 파일·재시도 기간·진행 웹훅 완료(13-6), 3차(job_chunks) 미착수 — 13절에 구현 현황 (2026-10-14 코드 기준 갱신)
**관련**: [DESIGN.md](../../../DESIGN.md) 4절(Layer 1), 6-1~6-3(RAG), 11-6(원본 지원 페이지 정의)

---

## 1. 배경과 목표

자소설닷컴 크롤러를 추가하기 전에, 크롤링한 JD를 어떻게 저장할지 먼저 정한다.

목표는 세 가지다.

1. **RAG 매칭에 쓰기 좋은 형태**: JD 요구사항 하나하나에 내 경험 조각(`experiences`)을 붙일 수 있어야 한다. (DESIGN.md 6-3 `extract_requirements → match_experiences`)
2. **데이터 파이프라인 설계로 설명 가능한 수준의 체계**: 원본/정제/서빙 계층 분리, 멱등 적재, 실행 이력, 재처리 가능성.
3. **오버엔지니어링 금지**: 새 인프라 없음(설계 원칙 2번). 모두 기존 Postgres 테이블로 해결한다.

### 현재 구조의 문제 (2026-09-24 코드 기준)

| # | 문제 | 위치 |
|---|---|---|
| 1 | JD가 `jobs.jd_text` 통짜 텍스트 하나뿐. 원본 HTML/이미지/API 응답이 남지 않음 | `db/orm.py`, 크롤러 |
| 2 | 마감 지난 공고를 `cleanup_job`이 삭제해 이력이 쌓이지 않음 | `scheduler/jobs.py` |
| 3 | `summarize_jd`가 항목 수·글자 수를 잘라냄(업무 3~4개, 자격 3~5개). 카드용으로는 적합, RAG 원료로는 손실 | `analysis/scorer.py` |
| 4 | 상세 페이지 타임아웃 시 `jd_text=""`로 저장되고, 이후 `known_urls`에 걸려 영영 재수집되지 않음 | 크롤러 + `crawl_job` |
| 5 | 한 공고에 포지션이 여러 개인 사이트(자소설)를 담을 자리가 없음 | `db/orm.py` |
| 6 | 마이그레이션 도구가 없음(`create_all`만 사용). 기존 `jobs` 컬럼 추가에 별도 절차 필요 | `db/init_db.py` |

### 비목표

S3/parquet, dbt, Airflow, 별도 웹하우스, 이벤트 큐, 스키마 레지스트리, 사이트 간 중복 공고 병합, 원본의 버전 이력 관리. 필요해지는 시점에 추가한다.

---

## 2. 아키텍처

```
[크롤러]  ← 사이트를 아는 유일한 층
 인디스워크   슈퍼루키   자소설(+외부 사이트, OCR)   잡코리아(미래)
      │          │              │                      │
      └──────────┴──────┬───────┴──────────────────────┘
                        ▼
              공통 계약 RawJob  (4절)
                        │
      ══════════ 여기부터는 사이트를 모름 ══════════
                        │
                        ▼
 ① raw_postings   원본 창고: 받은 그대로, 수정·삭제 없음, (source, url) 기준 insert-if-absent
                        │  변환: (jd_text가 비고 이미지가 있으면) OCR → 추출 (LLM 1회)
                        │        추출은 jd_text + hints를 입력으로 받음
                        ▼
 ② jobs           정리된 공고: 기존 표 유지. 화면·알림이 읽음. 마감 후 정리 대상
                        │  항목별로 쪼개기
                        ▼
 ③ job_chunks     검색용 조각: 포지션 × (업무/자격/우대/스택) 1행씩
                        │
                        ▼
              experiences(내 경험 조각)와 매칭 — Phase D에서 임베딩 추가

 옆에서 기록: ingest_runs (실행별 수집/신규/실패 건수)
```

**단계 정의**: ① raw_postings = **1차 적재**(크롤러가 넘긴 원본 그대로), ② jobs = **2차 저장**(이미지 JD는 OCR로 텍스트화 + 추출), ③ job_chunks = **3차 저장**(청킹). 공고 추천(`analyze_job`, `notify_job`)은 3차까지 끝난 공고를 대상으로 돈다.

**계층 규칙**: 아래층은 위층에서 언제든 다시 만들 수 있다. 프롬프트를 개선하면 ①에서 ②③을 재생성하면 되고, 사이트를 다시 크롤링하지 않는다.

---

## 3. 사이트별 차이와 흡수 지점

| | 인디스워크 | 슈퍼루키 | 자소설 |
|---|---|---|---|
| 목록 수집 | HTTP, WordPress REST API (`/wp-json/wp/v2/posts`) | Playwright, 클릭 페이지네이션 | HTTP JSON API (`POST /employment/calendar_list.json`) |
| JD 위치 | 목록 응답의 `content`(HTML). 텍스트 또는 이미지 | 상세 `.job-detail` 텍스트 | 자체 이미지(OCR) 또는 외부 채용사이트 |
| 마감일 | 없음 | 카드에서 파싱 | API `end_time` |
| 포지션 수 | 1 | 1 | 여러 개 |
| 구조화 필드 | 카테고리(채용구분), 태그(직무·산업·기업유형·마감방식) | 없음 | 채용구분, 직무코드 |

사이트 의존이 남는 곳과 처리:

1. **사전 필터**(직무코드, 채용구분, 최근 2일 이내 시작)는 크롤러 내부 일이다. 저장 대상이 아니다.
2. **여러 포지션·채용구분**: 사이트가 구조화 값을 주면 크롤러가 사이트와 무관한 키로 정규화해 `hints`로 넘긴다(4절). 원본은 `payload`에 그대로 남는다. 구조화 값이 없는 사이트는 `hints`가 비고, 추출 단계가 텍스트에서 뽑는다. 사이트를 아는 코드는 크롤러에만 있다.
3. **사이트 간 중복**은 이번 범위 밖이다. `apply_url`을 확보해 두어 나중에 정규화 URL로 합칠 수 있게만 한다.

---

## 4. 공통 계약 RawJob (확정)

### 4-1. 현재 코드에서 각 필드의 의미 (사실)

| 필드 | 인디스워크 | 슈퍼루키 | 비고 |
|---|---|---|---|
| `source` | 클래스 상수 `"inthiswork"` | `"superookie"` | 수집 사이트 식별자 |
| `url` | 인디스워크 상세 글 URL (목록 제목 링크의 href) | `superookie.com` + 카드 href | **수집 사이트 안의 공고 페이지**. 기업 지원 페이지가 아님. `jobs.url`은 unique이며 중복 제거 키, Discord "자세히 보기"·웹 "공고 원문"의 링크 대상 |
| `title` | 목록 제목 전체 (`회사｜포지션` 형태 그대로) | `p.job-title` 텍스트 | 인디스워크는 포지션을 분리하지만 로그에만 쓰고 버림 |
| `company` | 제목에서 `｜` 앞부분 | `h5` 텍스트 | |
| `jd_text` | `.post-content` 텍스트(줄바꿈 구분) | `.job-detail` 텍스트 | 타임아웃 시 `""` |
| `deadline` | 없음(`None`) | 카드의 `MM/DD HH:MM 마감`에서 날짜만 추출, 연도는 현재 UTC 연도, tz는 UTC | 시각은 버려짐. `상시채용`이면 `None` |
| `extra` | 미사용 | 미사용 | 정의만 있고 어디서도 읽거나 쓰지 않음 |

### 4-2. 확정 계약 (`crawler/base.py`의 `RawJob`)

| 구분 | 필드 | 의미 |
|---|---|---|
| 필수 | `source`, `title`, `company` | 기존 유지 |
| 필수 | `url` | **수집 사이트 안의 공고 페이지**, 추적 파라미터를 제거한 정규 URL. 중복 제거 키 |
| 필수 | `jd_text` | 크롤러가 추출한 JD 텍스트. 이미지 JD면 빈 문자열일 수 있다 |
| 선택 | `deadline` | 기존 유지. timezone-aware |
| 선택 | `apply_url` | **신규.** 기업 자체 채용사이트의 공고/지원 페이지 |
| 선택 | `payload` | **`extra` 대체.** 원본 응답/HTML 조각. 해석 없이 그대로 보존 |
| 선택 | `hints` | **신규.** 사이트 값을 사이트와 무관한 키로 정규화한 것. 현재 키는 하나: `positions: [{name, career_level}]` |
| 선택 | `image_urls` | **신규.** JD 본문에 들어 있는 이미지 주소(문서 순서). 2차 단계가 OCR 대상으로 쓴다. 이미지는 `payload`의 본문 HTML 안에도 있지만 HTML을 사이트별로 파싱해야 하므로, 크롤러가 `extract_image_urls()`로 미리 뽑아 넘긴다 |

`payload`와 `hints`의 관계: `payload`는 원본 전체(저장용), `hints`는 그중 하류 단계가 쓸 몇 개를 크롤러가 정규화한 것(사용용)이다. 하류 단계는 사이트별 payload 구조를 몰라도 `hints`만 읽으면 된다.

`hints` 규칙:

- `career_level`은 사이트 코드가 아니라 표시 값이다. 구성 요소(`신입`, `인턴`, `경력`, `계약직`, `교육`)를 `/`로 이은 문자열이며, 자소설은 코드 5·6을 `신입/경력`·`신입/인턴`으로, 인디스워크는 `신입/인턴`(카테고리 `신입/인턴`)과 `신입/인턴/경력`(카테고리 `주니어경력`이 함께 붙은 경우)으로 채운다. 표기 순서는 `crawler/base.py`의 `join_career_level()`이 강제한다: 구성 요소를 **신입 → 인턴 → 경력 → 계약직 → 교육** 순서로 잇고, 중복은 제거하며, 목록에 없는 값은 `ValueError`. 자소설·인디스워크 공통.
- `positions`에는 크롤러 사전 필터를 통과한 포지션만 넣는다. 탈락한 포지션은 `payload`에만 남는다.
- 구조화 값이 없는 사이트(인디스워크, 슈퍼루키)는 `hints`가 비어 있고, 추출 단계가 텍스트에서 판정한다.
- `hints`는 사이트가 준 값이라 틀릴 수 있다. JD 텍스트와 충돌하면 텍스트를 우선하고 근거를 `eligibility.evidence`에 남긴다(8절).

덜어낸 것:

- `external_id`: `url`이 정규화되어 안정적이면 불필요. 자소설도 `/recruit?ec={id}`를 `url`로 쓰면 유일하다.
- `fetched_at`: 크롤러가 아니라 적재 단계가 찍는다.
- 최상위 `position`/`employment_type`/직무코드: 포지션별 값이라 `hints.positions` 안에 둔다.

### 4-3. 자소설 크롤러의 매핑

| RawJob | 자소설 값 |
|---|---|
| `source` | `"jasoseol"` |
| `url` | `https://jasoseol.com/recruit?ec={id}` (번들에서 확인, 브라우저 미확인) |
| `apply_url` | 상세 API의 `employment_page_url` (추적 파라미터 포함 원문 그대로) |
| `title` / `company` | 목록 API의 `title` / `name` |
| `jd_text` | 상세 API `content`(HTML)에서 뽑은 텍스트. 이미지 JD면 `""` |
| `deadline` | `end_time` |
| `payload` | `{"calendar": 목록 항목, "detail": 상세 응답}` |
| `hints` | 사전 필터를 통과한 포지션의 `field`와 채용구분 코드→표시 값 |
| `image_urls` | 상세 API `content`(HTML)의 `<img src>` |

크롤러 사전 필터(저장 대상 아님):

1. 시작일이 조회 구간(기본 최근 2일, KST) 안. 목록 API는 하루 창과 겹치는 공고를 모두 주므로 `start_time`으로 다시 거른다. 창은 하루 단위로 나눠 조회한다(13-2).
2. **한 포지션**이 채용구분(신입 1, 인턴 3, 신입/경력 5, 신입/인턴 6)과 IT 직무(94 및 하위)를 동시에 만족. 채용구분 5·6의 의미는 번들 매핑으로만 확인했다.

---

## 5. 테이블 정의

### 5-1. raw_postings (신규)

```sql
CREATE TABLE raw_postings (
    id          SERIAL PRIMARY KEY,
    run_id      INTEGER REFERENCES ingest_runs(id),
    source      VARCHAR(64) NOT NULL,
    url         TEXT NOT NULL,
    apply_url   TEXT,
    title       VARCHAR(512),
    company     VARCHAR(256),
    deadline    TIMESTAMPTZ,
    jd_text     TEXT,            -- 크롤러가 추출한 텍스트. 이미지 JD면 빈 문자열 (OCR 결과는 여기 쓰지 않는다)
    payload     JSON,            -- 원본 응답/HTML 조각
    hints       JSON,            -- 크롤러가 정규화한 힌트 (4-2)
    fetched_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, url)
);
```

insert-if-absent. 수정·삭제하지 않는다. 마감 후에도 보존한다. 크기 실측(2026-09-10~23, 인디스워크 IT·신입/인턴 필터 통과분 137건): 본문(`content`) 평균 6.6KB, 중앙값 4.2KB, 최대 67KB. 하루 평균 9.8건이므로 인디스워크 본문만 연 약 24MB. 다른 소스와 `payload` 여분을 합쳐도 연 수십 MB 수준이다.

### 5-2. jobs (기존 표에 컬럼 추가)

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `apply_url` | Text, nullable | 기업 지원 페이지 |
| `raw_posting_id` | FK, nullable | 출처 원본. 기존 행은 NULL(백필 안 함) |
| `eligibility` | JSON, nullable | `{"new_grad_ok": true/false/null, "min_years": int/null, "evidence": "원문 문장"}` |
| `extract_status` | String(16), default `'pending'` | `pending` / `needs_ocr`(JD 텍스트 없음, 이미지 있음) / `ok` / `failed` / `empty`(텍스트도 이미지도 없음 — 재수집 대상) |

`jd_text`, `jd_tasks` 등 기존 컬럼은 그대로 둔다. 알림 카드가 계속 사용한다.

### 5-3. job_chunks (신규)

```sql
CREATE TABLE job_chunks (
    id         SERIAL PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id),
    position   VARCHAR(256),     -- 포지션명. 단일 포지션 공고는 NULL
    kind       VARCHAR(16) NOT NULL,  -- task | qualification | preferred | stack
    ord        SMALLINT NOT NULL DEFAULT 0,
    text       TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (job_id, position, kind, ord)
);
```

임베딩 컬럼은 이번에 만들지 않는다. Phase D에서 pgvector 확장과 함께 `ALTER TABLE`로 추가한다(DESIGN.md 6-1이 `experiences`에 쓰는 방식과 동일). 운영 Postgres 이미지가 pgvector 없는 `postgres:16-alpine`이라는 점도 이유다.

### 5-4. ingest_runs (신규) — 2026-10-14 미채택, 로그 파일로 대체(13-6)

```sql
CREATE TABLE ingest_runs (
    id            SERIAL PRIMARY KEY,
    source        VARCHAR(64) NOT NULL,
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    status        VARCHAR(16),   -- running | ok | failed
    fetched_count INTEGER DEFAULT 0,
    new_count     INTEGER DEFAULT 0,
    failed_count  INTEGER DEFAULT 0,
    error         TEXT
);
```

---

## 6. 적재 흐름

1. `crawl_job`: 크롤러별로 `ingest_runs` 행을 열고 `fetch_jobs()` 호출.
2. 각 `RawJob`을 `raw_postings`에 insert-if-absent. 이미 있으면 건너뜀(멱등).
3. `jobs`에 신규 행 생성. 상태 결정:
   - `jd_text`가 충분히 길면 `pending`
   - 비었는데 `payload`에 이미지가 있으면 `needs_ocr`
   - 텍스트도 이미지도 없으면 `empty`
4. `analyze_job` 1단계(현재 JD 요약 단계를 확장). OCR은 크롤러가 아니라 이 변환 단계에서 한다(02시 크롤링에 느린 호출을 끼우지 않고, raw는 받은 그대로 유지하기 위해).
   - `needs_ocr`: 이미지를 OCR해 **`jobs.jd_text`**에 쓴다(raw는 건드리지 않는다). 이후 `pending`과 동일하게 진행.
   - `pending`/`failed`: 추출 LLM 1회 호출(Gemini Flash, 기존 6초 간격 유지). 입력은 `jd_text` + `hints`.
     - 출력: `positions[]`, 각 포지션의 `tasks`/`stack`/`qualifications`/`preferred`, `employment_type`, `eligibility`.
     - `hints.positions`가 있으면 그 포지션에 대해서만 청크를 만든다(IT·채용구분 사전 필터를 통과한 것). 없으면 텍스트에서 포지션을 나눈다.
     - 한 트랜잭션에서 해당 job의 `job_chunks`를 삭제 후 재삽입(멱등), `jd_*` 카드 컬럼과 `eligibility` 갱신, `extract_status='ok'`.
     - 실패 시 `failed`. 다음 실행에서 재시도.
5. 실행 종료 시 `ingest_runs`에 건수·상태 기록.

**함께 고칠 것(문제 4번)**: `known_urls` 조회에서 `extract_status='empty'`인 URL은 제외해 다음 실행에서 재수집한다.

---

## 7. 청킹 규칙

- 단위: 포지션 × kind × 항목 1개 = 1행. 항목은 원문 표현을 유지한다(요약·재작성 금지).
- kind: `task`(담당 업무), `qualification`(자격요건), `preferred`(우대사항), `stack`(기술 스택).
- 카드용 항목 수 제한(3~5개)은 저장 시 적용하지 않고 표시 시에만 자른다.
- 포지션이 하나뿐이면 `position=NULL`.

## 8. 신입/경력 필터

- 크롤러 사전 필터: 한 포지션이 채용구분(신입/인턴/신입·경력)과 IT 직무를 동시에 만족하는 공고만 수집.
- 추출 단계에서 JD 텍스트를 보고 `eligibility.new_grad_ok`를 채우고 근거 문장(`evidence`)을 남긴다.
- `hints.positions[].career_level`은 초기값으로 참고하되, JD 텍스트와 충돌하면 텍스트를 우선하고 충돌 사실을 `evidence`에 적는다(사이트 값이 틀릴 수 있다).
- 후보 목록·알림에서 `new_grad_ok == false`는 제외, `null`(불명)은 표시를 달아 노출한다.

## 9. cleanup 변경

- `jobs`와 그 종속 데이터(`job_scores`, `application_questions`, `resume_drafts`, `job_chunks`)는 현재처럼 삭제한다. 지원 이력(`applications`)이 있는 공고는 보존한다.
- `raw_postings`는 삭제하지 않는다.

## 10. 마이그레이션

새 테이블은 `create_all`이 처리한다. `jobs`의 신규 컬럼은 `scripts/migrate_jd_storage.py`가 컬럼 존재 여부를 확인한 뒤 `ALTER TABLE ... ADD COLUMN`을 실행한다(SQLite/Postgres 공용). Alembic은 도입하지 않는다.

## 11. 테스트

- 각 크롤러 출력이 RawJob 필수 필드를 만족하는지(계약 테스트).
- `raw_postings` 재적재 시 중복 행이 생기지 않는지.
- `cleanup_job` 후 `raw_postings`가 남는지.
- 추출 재실행 시 `job_chunks`가 중복 없이 교체되는지.
- 추출 JSON 파싱 실패 시 `failed`로 남고 다음 실행에서 재시도되는지.
- 빈 JD가 `empty`로 표시되고 재수집 대상에 포함되는지.
- 텍스트 없이 이미지만 있는 JD가 `needs_ocr`로 분류되고, OCR 결과가 `jobs.jd_text`에만 쓰이며 `raw_postings`는 변하지 않는지.
- `hints.positions`가 있을 때 그 포지션에 대해서만 청크가 생기고, `hints`가 비면 텍스트에서 포지션을 나누는지.
- `hints`의 채용구분이 JD 텍스트와 충돌할 때 텍스트가 우선되고 `evidence`에 기록되는지.

---

## 12. 미결 사항

결정됨(2026-09-24):

- RawJob 계약: 4절대로 확정(`apply_url`, `payload`, `hints` 추가, `external_id` 제외).
- IT 외 포지션의 청크: 만들지 않는다. `hints.positions`에 있는 포지션만 대상(탈락 포지션은 `payload`에만 남음).
- `career_level` 표기: 신입 → 인턴 → 경력 순서로 `/`로 잇는 규칙을 두 크롤러에 공통 적용(`join_career_level`).
- 신규 공고만 수집: 조회 기간(`DEFAULT_LOOKBACK_DAYS = 2`, 그제·어제) + `known_urls` 중복 제거. 두 크롤러 공통.
- 소스 간 중복: raw는 소스별로 모두 보존한다(소스마다 담은 정보가 다르다). 합치기는 jobs 층의 문제이며 지금은 하지 않는다. 자소설 크롤러를 붙인 뒤 회사명 기준으로 겹치는 쌍을 며칠간 세어 보고, 많으면 `apply_url` 정규화 매칭으로 `jobs`에 `duplicate_of`를 추가한다.

남은 것:

1. **링크 노출**: "자세히 보기"/"공고 원문"에 `apply_url`을 우선할지, 수집 사이트 `url`을 유지할지, 둘 다 보여줄지. (추천: `apply_url` 우선, 없으면 `url`)
2. **OCR 방식**: 어떤 모델·비용·이미지 URL 소멸 시 대응(OCR 후 텍스트만 남김). 외부 채용사이트(PDF, 로그인, SPA) 텍스트 추출은 별도 설계.
3. ~~**추출 실패 재시도 상한**~~ → 첫 실패 포함 2일로 결정 (2026-10-14, 13-6).
4. **Alembic 도입 여부**: 현재는 미도입.
5. **자소설 채용구분 5·6의 실제 의미**: 번들 매핑으로만 확인.

---

## 13. 구현 현황

| 항목 | 상태 |
|---|---|
| `RawJob` 확장 (`apply_url`, `payload`, `hints`) | 완료 — `crawler/base.py`, `tests/test_crawler_base.py` |
| `JasoseolCrawler` | 완료 — `crawler/jasoseol.py`, `tests/test_crawler_jasoseol.py`. 실제 API 스모크 테스트 통과. 하루 창 단위 조회(13-2) |
| `JobkoreaCrawler` | 완료 — HTTP만 사용. 기본 조회 기간(2일)에서 호출 17회·16초. 스케줄러에는 미등록 |
| `InthisworkCrawler` WordPress API 전환 | 완료 — `crawler/inthiswork.py`, `tests/test_crawler_inthiswork.py`. Playwright 제거, 실제 사이트 스모크 통과(호출 3회, 어제 10건) |
| `crawl_job`에 등록 | **완료** — 인디스워크·자소설·잡코리아 3곳 모두 `_enabled_crawlers()`에 등록 (OCR 채택 후) |
| `raw_postings` 테이블 + 저장 로직, `jobs.extract_status` | **완료** (2026-09-30) |
| 2차 저장(추출) — `extract_job()`, `eligibility` | **완료** (2026-09-30, 13-4/13-5). pending/failed/needs_ocr 전부 처리 |
| `ingest_runs` 테이블 | **대체** — DB 표 대신 로그 파일로 (2026-10-14, 13-6) |
| `job_chunks` 테이블 | 미착수 |
| OCR 변환 단계 — Google Cloud Document AI | **완료** (2026-09-30, 13-5). `OCR_PROVIDER=claude_code`로 Claude Code 헤드리스 전환 가능 |

자소설·잡코리아 등록을 한동안 보류했던 이유: 2026-09-24 실제 호출에서 대상 2건(서강대학교, 한국앤컴퍼니)의 JD가 모두 이미지였다. OCR 없이 등록하면 `needs_ocr` 행이 미처리로 쌓이기 때문이었다. OCR(13-5)이 들어간 뒤 등록했다.

### 13-4. 2026-09-30 진행 — 1차 적재 + 2차 저장(추출)

**1차 적재** (`raw_postings` 테이블, `db/orm.py`): `crawl_job`이 `RawJob`을 해석 없이 그대로 보존한다. `(source, url)` insert-if-absent(멱등). `jobs.extract_status`(`pending`/`needs_ocr`/`empty`)를 `jd_text`·`image_urls` 유무로 자동 분류해 함께 저장한다(공백만 있는 텍스트는 "없음"으로 취급).

**2차 저장** (`extract_job()`, `scheduler/jobs.py`): `extract_status`가 `pending`/`failed`인 공고만 대상으로 `summarize_jd()`를 호출해 `jd_tasks`/`jd_stack`/`jd_employment_type`/`jd_qualifications`/`jd_preferred`와 신규 필드 `jobs.eligibility`(`{new_grad_ok, min_years, evidence}`)를 채우고 `extract_status`를 `ok`/`failed`로 전환한다. `raw_postings.hints`(크롤러가 채운 채용구분)를 조회해 프롬프트 입력으로 넘기고, JD 원문이 힌트와 다르면 원문을 우선하며 그 불일치를 `evidence`에 남기도록 지시한다(8절 규칙 구현). 실패는 `failed`로 남아 다음 실행에서 자동 재시도된다(2026-10-14부터 수집한 날 + 다음 날까지만 — 13-6).

**결정 사항** (사용자 확정, 2026-09-30):
- **실행 방식(A2)**: `analyze_job`과 별도로 `extract_job()`을 신설하되, 고정 시간(예: 03시) cron이 아니라 **`run_crawl_pipeline()`이 `crawl_job()` → `extract_job()`을 함수 호출로 체인**한다. 스케줄러는 `run_crawl_pipeline`만 02시에 트리거하고, 내부는 순차 실행이다 — n8n 같은 워크플로우 체인을 목표로 한 첫걸음. 이후 `analyze_job`/`notify_job`도 같은 방식으로 체인에 편입할 수 있다(이번엔 미포함, 기존 cron 유지).
- **OCR(B)**: 이 시점엔 보류였다. 같은 날 13-5에서 Document AI 채택으로 바뀌었다 — 지금은 `extract_job()`이 `needs_ocr`도 처리한다.
- **스코프(C1)**: "이 단계가 JD 내용을 다 채우는 단계"라는 목표에 맞춰 `eligibility`까지 이번에 포함했다(애초 검토했던 축소안 C2는 채택 안 함).

**함께 고친 것**: `analyze_job()`의 fit_score 루프가 `extract_status`가 `needs_ocr`/`empty`인 공고(= `jd_text` 없음)를 스코어링 대상에서 제외하도록 했다. 이전엔 빈 JD에도 Gemini를 호출해 의미 없는 점수를 만드는 문제가 있었다(13-2에서 지적한 문제).

**검증**: `analyze_job`의 `extract_status` 필터와 `extract_job`의 대상 필터(`pending`/`failed`만) 둘 다 변형 검사(mutation test) — 필터를 지우면 관련 테스트가 실제로 깨지는 것을 확인했다.

### 13-1. 인디스워크 API 호출 예산 (2026-09-24 실측)

- 하루 1회 실행 = **호출 3번**: 카테고리 1 + 태그 1 + 글 목록 1. 결과가 100건을 넘을 때만 페이지당 1회 추가.
- 상세 페이지 요청은 없다. 본문이 목록 응답에 들어 있다(기존 Playwright 방식은 공고마다 상세 요청).
- 필터 통과분은 최근 14일 137건, 하루 평균 9.8건·최대 17건이라 하루 1회면 충분하다. 하루 응답은 약 81KB.
- 서버 필터 없이 하루 전체를 받으면 평균 64건·최대 99건으로 100건 상한에 근접하므로 서버 필터를 쓴다.
- 조회 기간은 기본 2일(`DEFAULT_LOOKBACK_DAYS`, 그제·어제)이다. 인디스워크는 넓혀도 호출 횟수가 그대로다(`lookback_days`). 이미 저장된 URL은 `known_urls`로 건너뛰므로 중복 저장되지 않고, 하루 실행이 실패해도 다음 실행에서 복구된다.
- 자소설은 기간 폭에 비례해 호출이 늘어난다: 하루 창 1회 × 일수 + 직무 트리 1회 + 신규 공고당 상세 1회. 기본 2일 기준 최초 실행 12회(상세 9건), 이후에는 이미 본 공고의 상세 호출이 빠진다.

### 13-2. 알려진 주의점

- **게시 시각은 API의 `date`(KST)를 쓴다.** 상세 페이지의 `article:published_time` 메타는 UTC 시각에 `+09:00`을 잘못 붙여 9시간 어긋난다.
- ~~`payload`·`hints`·`apply_url`·`image_urls`는 DB에 저장되지 않는다~~ → 1차 적재(`raw_postings`, 2026-09-30)로 해결. 다만 그 이전에 쌓인 `jobs` 행은 `raw_postings` 짝이 없어 이미지 주소·힌트가 없다. `apply_url`은 `raw_postings`에만 있고 화면·알림은 아직 `jobs.url`을 쓴다(12절 남은 것 1번).
- **이미지 JD가 많다.** 인디스워크 어제 10건 중 6건, 자소설 2건 중 2건이 `jd_text`가 빈 문자열이다. 지금은 `needs_ocr`로 분류돼 `extract_job`이 OCR한다(13-5). OCR이 끝나기 전까지는 `analyze_job`이 스코어링에서 제외한다.
- `company`는 제목의 `｜` 앞부분이라 `｜`가 없는 제목(예: 공공기관 공고)은 제목 전체가 들어간다.
- `deadline`은 인디스워크에서 채울 수 없다. `deadline=None`이면 `cleanup_job`이 지우지 않는다.
- 인디스워크 `robots.txt`는 일반 크롤러에는 `/wp-admin/`만 막고, ClaudeBot·GPTBot 등 AI 관련 크롤러는 전면 차단한다. 크롤러는 별도 UA(`Matchwise/0.2 (personal job-search tool)`)로 하루 3회만 호출한다. 이용약관은 확인하지 못했다.
- **자소설 목록 API는 기간 폭에 따라 결과가 달라진다(2026-09-24 관찰, 원인 미확인).** 시작일이 9/23인 공고가 하루 창에서는 12건, 2일 창(9/22~23)에서는 3건, 한 달 창(9/1~10/1)에서는 4건만 나왔다. 응답은 항상 같은 값이라 무작위는 아니고, 창 경계를 1초·1시간 옮겨도 같았다. 반면 하루 창을 9/10~9/23 14일간 각각 조회해 넓은 창(9/1~9/24)과 대조하니 모든 날짜에서 공고가 양방향으로 완전히 일치했다(차이 0건). 그래서 하루 창 단위로 조회하고 id로 합친다. 다만 하루 창이 항상 완전하다는 보장은 없으므로, 수집 건수가 갑자기 줄면 이 API의 변화를 의심해야 한다.
- 인디스워크 이미지 주소(`src`)에는 Jetpack 리사이즈 파라미터(`?resize=1220%2C14777&ssl=1`)가 붙어 있다. 원본 주소는 `<img>`의 `data-orig-file` 속성에 있으며 `payload`(본문 HTML)에 그대로 남는다. 2일치 21건 중 이미지 JD 10건은 모두 `image_urls`가 채워졌고 자소설 9건도 모두 채워졌다.

### 13-3. 할 일 (진행 순서)

- [x] RawJob 계약 확정(`apply_url`, `payload`, `hints`, `image_urls`), 자소설·인디스워크 크롤러
- [x] 슈퍼루키: 유의미한 공고가 없어 수집 대상에서 제외하고 크롤러·테스트 삭제 (2026-09-24, 14절 조사 내용은 재도입 시 참고용으로 보존)
- [x] 잡코리아 구조 조사 (15절)
- [x] 잡코리아 크롤러 — `crawler/jobkorea.py`, `tests/test_crawler_jobkorea.py`. 실제 사이트 스모크 통과 (15절)
- [x] **1차 적재: `raw_postings` 저장 계층 + `jobs.extract_status` 자동 분류** (2026-09-30, 13-4)
- [x] **2차 저장(pending/failed): `extract_job()` + `eligibility`** (2026-09-30, 13-4)
- [x] OCR 방식 결정(Document AI) → `needs_ocr` 처리를 `extract_job()`에 추가 (2026-09-30, 13-5)
- [x] 잡코리아·자소설을 `_enabled_crawlers()`에 등록
- [ ] 3차 저장: 청킹 → `job_chunks` (7절)
- [x] 실행 이력 (2026-10-14, 13-6) — 5-4의 `ingest_runs` 표 대신 로그 파일(`logs/<프로그램>.log`, 최근 7일 보관)에 사이트별 목록 본 수·신규·빈 공고·상세 실패·에러를 남긴다. `raw_postings.run_id`도 만들지 않음
- [~] ~~`known_urls`에서 `empty` URL 제외 → 재수집~~ → 하지 않기로 함 (2026-10-14, 13-6). 지금 크롤러는 상세 조회 실패 시 공고를 저장하지 않아 다음 날 자연히 다시 받는다 — `empty`는 사이트가 본문·이미지 없이 준 공고(주로 외부 링크)라 재수집해도 그대로다. 수집 로그·웹훅의 "빈 공고 n"으로 빈도를 본 뒤 많으면 "외부 링크 따라가 본문 가져오기"를 별도 설계
- [x] 기존 로컬 DB(`matchwise_dev.db`) 스키마 보강 — 2026-10-14 1회 ALTER로 `jobs`(extract_status, jd_* 5개, eligibility)·`users`(google_sub, email, basic_info) 컬럼 추가, 누락 테이블은 `create_all`로 생성. 예전 DB가 이것 하나뿐이라 재사용 스크립트는 만들지 않음 (Docker postgres는 아직 생성 전이라 처음부터 새 스키마로 생성됨)
- [x] 채점 대상 조정 (2026-10-14): `analyze_job`이 `extract_status='ok'`만 채점(이전엔 pending·failed도 채점). 추출 때 근무지 `jobs.work_locations`를 함께 뽑고, 채점 전 `_is_scoring_target()`으로 신입 불가·근무지(서울/과천/판교/분당 밖) 공고 제외 — 정보가 없으면(null/미기재) 채점. 8절의 "알림에서 제외" 규칙을 채점 단계에서 적용한 것
- [ ] `analyze_job`/`notify_job`도 워크플로우 체인에 편입할지 결정 (지금은 기존 cron 유지)

---

## 14. 슈퍼루키·잡코리아 착수 전 점검 (2026-09-24)

> **슈퍼루키는 2026-09-24에 수집 대상에서 제외하고 `crawler/superookie.py`와 테스트를 삭제했다.** 아래 슈퍼루키 조사 내용(HTTP만으로 수집 가능, 서버 필터, 구조화 JD 필드 등)은 재도입할 때 참고용으로만 남긴다. 사이트를 켜고 끄는 지점은 `scheduler/jobs.py`의 `_enabled_crawlers()` 한 곳이다. 잡코리아는 15절.

### 14-1. 사이트별 상태

| | 슈퍼루키 | 잡코리아 |
|---|---|---|
| 접근 | **User-Agent 기반 차단.** 자체 UA(`Matchwise/0.2`)와 Playwright 기본 headless UA(`HeadlessChrome/145`)는 Cloudflare가 403("Sorry, you have been blocked"). 일반 Chrome UA는 일반 HTTP·headless·headed 모두 200. 기존 크롤러가 오늘 실패한 것은 Playwright 기본 UA 때문 | 메인 페이지 HTTP 200, 차단 문구 없음. 공고 목록·상세 접근은 미확인 |
| robots.txt | **전부 허용**(`User-agent: *` / `Disallow:` 비어 있음) | `*`에는 로그인·내정보·기업관리 등만 금지, 공고 조회 경로는 미금지. ClaudeBot·GPTBot 등 AI 학습 크롤러와 Scrapy 등 스크래퍼는 전면 차단 |
| 이용약관 | 미확인 | 미확인 |
| 목록 | 화면은 JS로 렌더링하지만 **브라우저 없이 HTTP만으로 수집 가능**(2026-09-24 검증). ① 목록 페이지 HTML을 받아 인라인 스크립트의 토큰(`data['access_token'] = '...'`)과 필터 `<select>`의 옵션 ID를 읽고 ② `GET /api/jobs/search`를 `X-Requested-With: XMLHttpRequest`·`Referer`와 함께 호출한다(토큰 없으면 400). `page_length=200`까지 허용(전체 1,079건이 6페이지). 값은 문서에 기록하지 않음 | 미조사 |
| 서버 필터 | `job_level[]=<신입 ID>&job_level[]=<인턴 ID>&duty_group[]=<IT·인터넷 대분류 ID>`가 동작한다. 전체를 받아 클라이언트에서 거른 결과와 67건 전부 일치(차이 0건). 현재 크롤러 URL의 `positions[]`는 아무것도 거르지 않는다(값과 무관하게 total 1,079) | 미조사 |
| 목록 응답 필드 | `job_title`, `company_name`, `start_at`(KST), `published_at`(KST, 게시일), `end_at`(고정 마감일이 있는 공고만), `is_until_recruit`(채용시마감), `apply_link`(기업 채용사이트), `apply_type`, `job_level`(신입/인턴/계약직/파트타임/주니어경력), `duty_group_lv1/2/3_id`, 그리고 구조화된 JD `about_job`(담당업무)·`job_requirement`(자격요건)·`preference`(우대)·`steps`·`additional_info` 등. 필터 통과 67건 중 구조화 JD는 36건, `apply_link`는 65건, `end_at`은 21건, `is_until_recruit`은 36건. 결과는 `published_at` 순으로 정렬돼 있지 않고, 10건은 `published_at`이 없다 | 미조사 |
| 상세 | JSON-LD `JobPosting`에 **`datePosted`가 있음**(예: 2026-09-10). 화면 문구 "09월 10일 19:00 시작", "09월 10일 ~ 채용시마감". `.job-detail` 블록이 5개(본문, 추천 채용, 지원 버튼 등)라 기존 크롤러(`select_one`)는 첫 블록만 읽는데 그 블록에도 "링크 공유/이메일 지원" 같은 UI 문구가 섞여 있음. 텍스트 JD와 이미지 JD가 모두 있을 수 있음(확인한 1건은 텍스트) | 미조사 |
| 지원 링크 | "지원하기" 버튼이 **기업 채용사이트 주소**(`href`)를 가짐. 별도로 로그인이 필요한 슈퍼루키 자체 지원 링크 2개(`/login?next=...`)가 있음 | 미조사 |
| 기존 코드 | 계약과 어긋남: `deadline`이 날짜만·UTC·연도는 올해로 가정, `apply_url`/`payload`/`hints`/`image_urls` 없음, Playwright 기본 UA | 크롤러 없음 |

### 14-2. 이전 단계에서 정해지지 않은 것

| # | 항목 | 왜 필요한가 | 제안 |
|---|---|---|---|
| 1 | **User-Agent 정책** | 슈퍼루키는 자체 UA와 headless 기본 UA를 차단하고 일반 브라우저 UA만 통과한다. 인디스워크·자소설은 자체 UA로 동작 | 기본은 자체 UA로 하되, 일반 브라우저 UA를 요구하는 사이트에서만 일반 UA를 쓴다. robots.txt가 전부 허용인 슈퍼루키는 이에 해당. **UA를 바꾸는 것 이상의 회피(스텔스 플러그인, IP 회전, 챌린지 해결)는 하지 않고, 그래도 막히면 그 사이트를 제외한다** |
| 2 | 슈퍼루키의 신규 판별 | **해결됨.** 목록 API 응답에 `published_at`이 있다. 정렬이 게시일순이 아니라서 조기 중단은 못 하지만, 필터 통과분이 67건이라 한 번(`page_length=200`)에 전부 받아 `published_at`으로 최근 2일만 고른다 | 인디스워크·자소설과 같은 조회 기간 + `known_urls` 규칙 |
| 3 | **`apply_url` 정의 보완** | 슈퍼루키에는 기업 사이트 링크와 로그인이 필요한 자체 지원 링크가 함께 있다(잡코리아는 미조사) | 기업 채용사이트 주소가 있으면 그것, 없으면 `None`. 사이트 안에서만 지원되는 링크는 `apply_url`로 쓰지 않는다 |
| 4 | 슈퍼루키 본문 범위 | 구조화 JD 필드가 있는 공고(36/67)는 API 값이 본문이고, 없는 공고(31/67)는 상세 페이지 `.job-detail` 첫 블록(UI 문구 섞임)에 텍스트가 있다 | 구조화 필드가 있으면 `about_job`/`job_requirement`/`preference`를 라벨을 붙여 `jd_text`로 만들고, 없으면 상세 페이지를 HTTP로 받아 JD 블록만 사용. 어느 블록이 JD인지와 JD 이미지 여부는 크롤러 작성 시 확인 |
| 5 | 슈퍼루키 `job_level`이 `계약직`인 공고 | `신입 및 경력직`처럼 신입 지원이 가능한 공고도 계약직으로 분류돼 있다(예: 삼정KPMG). `job_level`은 공고당 값이 하나라 신입/경력 동시 채용을 표현하지 못한다 | 사전 필터는 `신입`·`인턴`만 통과시키고, 계약직 포함 여부는 결정 필요 |
| 6 | 슈퍼루키 `apply_link`의 세션 값 | 일부 주소에 `;jsessionid=...`가 붙어 있다(예: KPMG) | `payload`에는 원문 그대로, `apply_url`에서는 세션 값 제거 여부 결정 |

이미 정해진 것: RawJob 계약(`image_urls` 포함), `hints` 규칙, `career_level` 표기, 조회 기간(2일)과 `known_urls`, 소스별 원본 보존, 1차/2차/3차 순서, 사이트 간 중복은 관측 후 결정.

---

## 15. 잡코리아 크롤러 계획 (2026-09-24 조사)

대상: `https://www.jobkorea.co.kr/theme/entry-level-internship`(신입·인턴 채용관). 화면에서 직무 AI·개발·데이터, 상세검색 서울·경기, 정규직·인턴직, 신입·경력무관을 고르는 검색을 자동화한다.

### 15-1. 조사 결과 (2026-09-24, 모두 자체 UA `Matchwise/0.2`로 HTTP 200)

| 항목 | 결과 |
|---|---|
| 접근 | 테마 페이지·목록 조각·상세·본문 모두 **브라우저 렌더링 없이 HTTP로 열림**, 차단 없음. `robots.txt`는 `*`에 로그인·내정보·기업관리·`/Recruit/JobList/SpaceClick` 등만 금지하고 아래 경로는 금지하지 않음. AI 학습 크롤러(ClaudeBot 등)와 Scrapy 등은 전면 차단. **이용약관은 확인하지 못함** |
| 필터가 주소에 안 보이는 이유 | 필터 값은 폼(`#recruitSearchForm`)에 담겼다가 jQuery가 AJAX GET으로 `/Theme/TemplateFreeGnoList/entry-level-internship?<폼 직렬화>`를 호출해 **HTML 목록 조각**(20건/페이지)을 받는 구조 |
| 필터 필드 | `bpart_no`=직무(AI·개발·데이터 `10031`), `scd`=지역(서울 `I000`, 경기 `B000`), `jtype`=고용형태(정규직 `1`, 계약직 `2`, 인턴직 `3`), `careerTypeCode`=경력(신입 `1/3`, 경력무관 `4`). 여러 개는 쉼표로 잇는다(예: `jtype=1,3`, `careerTypeCode=1/3,4`). `jobFilter=0`은 상세검색 모드, `FreePageNo`=페이지, `themeNo=169`. 값은 테마 페이지 버튼의 `data-value`에서 라벨로 찾을 수 있다 |
| 배너 목록 | `TemplateMainGnoList`는 배너(광고) 공고 목록이고 이 필터에서는 빈 응답 |
| 정렬 | `rOrderTab`: `1` 추천순(기본), `2` 등록일순, `3` 최신업데이트순, `4` 마감일순, `99` 랜덤순. **등록일순(`2`)은 최신 등록부터**(1페이지 첫 공고 9/24, 나머지 9/23). 기본 추천순은 날짜와 무관 |
| 목록 규모 | 위 조건 적용 시 **602건, 31페이지**. 필터 없는 기본 1페이지는 호텔·주방 공고 위주였고 IT 공고는 0건 → 서버 필터는 실제로 걸린다 |
| 목록 카드 정보 | 공고번호(`GI_Read/{번호}`), 기업명, 제목, 마감(`~10/09(금)`/`상시채용`/`내일마감`), 직무 1개 + "외", 지원 방식(`즉시지원` 10건 / `홈페이지` 10건). **지역·고용형태·경력은 카드에 없다.** 등록일은 "오늘시작" 표시뿐 |
| 상세 (`/Recruit/GI_Read/{번호}`) | Next.js 서버 렌더링. JSON-LD `JobPosting`(`datePosted`, `validThrough`, `employmentType`, 근무지 주소), 텍스트로 된 조건 항목(고용형태, 급여, 근무시간, 근무지주소, 경력, 학력, 우대조건, 접수기간 시작일·마감일, 사원수, 기업구분, 업종), 데이터 조각에 직무 키워드와 분류 코드 |
| JD 본문 | 상세 안의 별도 주소 `/Recruit/GI_Read_Comt_Ifrm?Gno={번호}`가 HTTP로 열린다. **텍스트 JD**(예: 5,644자 + 이미지 4개)와 **이미지 JD**(텍스트 0자 + 이미지 1개)가 모두 있다. 이미지 `src`가 `//file2.jobkorea.co.kr/...`처럼 `https:`가 없다 |

### 15-2. 결과가 일관성 없어 보이는 이유 (필터 적용 1페이지 20건을 상세까지 열어 확인)

- **고용형태 20/20, 경력 20/20 충족.** 지역은 18/20: 2건은 첫 근무지가 충북·대전(여러 근무지 중 서울·경기가 있어 걸린 것으로 보임).
- **직무는 "하나라도 해당하면 포함"이다.** 이마트, 셀트리온제약, 국가철도공단 같은 종합 신입 공채는 직무 분류가 15~23개나 붙어 있어 AI·개발·데이터에도 걸린다. 제목으로 눈대중 분류하면 개발·데이터·IT 운영이 분명한 공고 13건, 여러 직군을 뽑는 종합 채용 4건, 경계선(데이터 라벨링 인턴, 드론 제작 인턴 등) 3건이었다. (제 판단이라 참고용이며, IT 분류 코드 기준 검증은 코드 의미를 확정하지 못해 하지 않았다.)
- 같은 회사의 유사 공고가 연달아 나온다(같은 회사 언어별 인턴 공고 2건 등).
- 카드에 조건 정보가 없어서 눌러보기 전에는 판단이 안 된다. 기본 정렬은 추천순이라 날짜순도 아니다.

### 15-3. 자소설이 채우던 항목의 수집 가능 여부

| 항목 | 가능 여부 | 출처 |
|---|---|---|
| `url` | 가능 | `https://www.jobkorea.co.kr/Recruit/GI_Read/{번호}`. 추적 파라미터(`Oem_Code`, `rPageCode`, `sc`)를 뺀 주소가 열리는지는 구현 때 확인 |
| `title`, `company` | 가능 | 목록 카드 |
| `deadline` | 가능 | JSON-LD `validThrough`(예: `2026-10-09T23:59`). `상시채용`은 `None` |
| 게시일(신규 판별) | 가능 | JSON-LD `datePosted` + 등록일순 정렬 |
| `jd_text` | 가능 | 본문 주소의 텍스트 + 상세의 조건 항목(고용형태, 근무지, 경력, 학력, 우대조건 등) |
| `image_urls` | 가능(정규화 필요) | 본문 주소의 `<img>`. `//`로 시작하는 주소에 `https:`를 붙여야 함 → `extract_image_urls()` 보완 필요 |
| `payload` | 가능 | 목록 카드 정보, JSON-LD, 조건 항목, 직무 키워드, 본문 HTML |
| `hints.career_level` | 가능 | 상세의 경력(`신입`, `신입·경력`)과 고용형태(`인턴`) |
| `hints.positions` | 부분 가능 | 공고당 제목 하나. 직무 키워드는 여러 개라 포지션 이름으로는 부적합 |
| 채용구분·직무·지역 서버 필터 | 가능 | 15-1의 폼 필드 |
| **`apply_url`** | **불가 → `None`** | "홈페이지 지원"/"즉시지원" 버튼을 브라우저에서 눌러 보면 로그인 레이어(`/Login/M_Login_Layer3`)가 뜬다. 외부 지원 주소는 로그인해야 보인다(2026-09-26 확인). 로그인은 하지 않는다 |

### 15-4. 구조 (구현 완료, HTTP만·Playwright 없음)

1. 테마 페이지를 받아 폼 기본값(`themeNo` 등)과 필터 버튼의 라벨→`data-value`를 읽는다. 검색 조건은 `crawler/jobkorea.py`의 `SEARCH_FILTERS` 한 곳에서 바꾼다(직무 AI·개발·데이터, 지역 서울·경기, 고용형태 정규직·인턴직, 경력 신입·경력무관). 라벨이 없으면 실패한다.
2. 등록일순(`rOrderTab=2`)으로 목록 조각을 넘긴다.
3. `known_urls`에 없는 카드만 상세를 받아 게시일(JSON-LD `datePosted`)을 확인하고, 조회 기간 안이면 본문을 받아 `RawJob`을 만든다.
4. 중단 조건: ① 페이지의 공고가 전부 이미 아는 URL ② 오래된 공고가 연속 8건 ③ 페이지에 조회 기간 안의 공고가 하나도 없음 ④ 마지막 페이지(20건 미만).
5. `crawl_job`(`_enabled_crawlers()`)에는 등록하지 않았다. 1차 적재가 구현되기 전에는 이미지 JD가 빈 JD로 저장되기 때문이다.

**등록일순은 재등록(갱신) 순서다.** 실제 확인 결과 게시일이 9/17인 공고가 등록일순 맨 위에 있었고, 그 뒤로 9/25, 9/25, 9/24, 이어서 9/23 공고가 27건 연속으로 나왔다. 처음 구현했던 "오래된 공고를 처음 만나면 중단" 규칙은 이 첫 공고에서 끊겨 0건을 반환했다. 그래서 오래된 공고는 건너뛰고 연속 기준으로 중단한다.

### 15-5. 실측 (2026-09-26, 자체 UA)

| 조회 기간 | 호출 | 소요 | 수집 | 비고 |
|---|---|---|---|---|
| 2일(기본, 9/24~25) | 17회 | 16초 | 3건 | 게시일 9/24 1건, 9/25 2건 |
| 5일 | 100회 | 85초 | 40건 | 게시일 9/21 1건, 9/22 9건, 9/23 27건, 9/24 1건, 9/25 2건 |

- **하루 신규량은 매우 들쭉날쭉하다**(1~27건). 앞서 "하루 20건 안팎"이라고 쓴 추정은 틀렸다(9/23 하루 27건은 예외적이었고, 9/24~25는 합계 3건).
- 5일 40건 중 이미지 JD(텍스트 없음)가 7건(17.5%), 텍스트 JD에 이미지도 함께 있는 경우가 14건. 채용구분은 `신입` 19, `신입/경력` 16, `신입/인턴` 4, `신입/인턴/경력` 1.
- 지원 방식: 표본 3건 모두 "즉시지원". `apply_url`은 모두 `None`.
- 본문 HTML(`payload.body_html`) 크기는 평균 약 250KB, 최대 약 640KB로 예상보다 크다(텍스트 JD에도 인라인 스타일과 표가 많음). 1차 적재의 저장 크기 추정(연 수십 MB)은 잡코리아 때문에 커질 수 있어 실측이 필요하다.

### 15-6. 결정된 것과 남은 것

결정됨(2026-09-26):

- **이용약관**: 사용자가 확인 후 진행하기로 함(저는 약관을 읽지 못했다).
- **지역**: 서버 조건(서울·경기)만 쓰고 근무지 주소로 다시 거르지 않는다. 서울 또는 경기가 포함된 공고는 모두 포함된다(경기 전체이므로 판교 포함).
- **직무**: 서버 조건 "AI·개발·데이터"만 쓰고, 관련도는 분석 단계에 맡긴다.
- **홈페이지 지원 주소**: 로그인해야 보이므로 `apply_url`은 `None`.

남은 것:

1. `payload.body_html`을 통째로 보존할지, 이미지 JD의 본문만 보존할지(크기 문제, 위 실측).
2. 조건 항목(`payload.conditions`: 고용형태, 급여, 근무지 등)을 2차 단계(추출)에 입력으로 넘길지. `jd_text`에 섞으면 이미지 JD가 "빈 JD"로 감지되지 않으므로 `payload`에만 둔다.
3. 잡코리아 응답 구조 의존성: 조건 항목이 `data-sentry-component="RecruitmentItem"`, 필터가 `area-type` 속성에 기대고 있어 사이트 개편 시 깨질 수 있다(필터 라벨이 없으면 실패하도록 해 두었다).

### 13-5. 2026-09-30 진행 — OCR(B) 채택: Google Cloud Document AI

**후보 비교** (동일 이미지, HS효성그룹 채용 포스터로 실측):

| | Tesseract(로컬, 한국어팩) | Google Cloud Document AI |
|---|---|---|
| 결과 | 7,398자, 음절마다 띄어쓰기 오류, 표 구조 인식 안 됨, URL·연락처 다수 깨짐 | 2,901자, 정확한 띄어쓰기, **표 구조까지 인식**(법인별 모집부문·직무·전공·필수/우대사항), URL·이메일·연락처 정확 |
| 비용 | 0원(로컬 실행) | 무료 티어 없음(신규 GCP 고객 $300/90일 크레딧은 Document AI 전용이 아니라 GCP 공통 프로모션). 1,000페이지당 $1.50, 실측 기준 월 100~350건 처리해도 월 200~700원 수준 |
| 설정 부담 | winget 설치 + 언어팩 다운로드만 | GCP 프로젝트+결제계정, 프로세서 생성, 인증 설정 |

**결정**: Document AI 채택. 품질 차이가 압도적(특히 표 형태 다직군 공고에서)이고, 예상 비용이 무시할 수준이라 결제 계정 부담을 상쇄.

**인증**: JSON 서비스 계정 키(정적, 무만료) 대신 **ADC + 서비스 계정 위장(impersonation)** 채택 — `gcloud auth application-default login --impersonate-service-account=matchwise-crawling@<project>.iam.gserviceaccount.com`. 짧은 수명 토큰이라 로컬에 정적 키 파일이 안 생긴다. `google-cloud-documentai` 클라이언트는 인자 없이 생성하면(`DocumentProcessorServiceClient()`) 이 자격증명을 자동으로 찾는다.

**서비스 계정 분리 정책**: 지금은 Document AI를 호출하는 지점이 `extract_job()` 하나뿐이라 계정 1개(`matchwise-crawling`)로 운영. 서로 다른 신뢰 경계(예: 실시간/온라인 호출 경로)가 실제로 생기는 시점에 분리하기로 함 — 미리 쪼개지 않음.

**설정 스키마** (`shared/config.py`): `GCPSettings`(접두사 `GCP_`)로 통합 — `project_id`(프로젝트 공통값, 서비스 접두사 없음이 맞지만 사용자 결정으로 `GCP_` 안에 포함), `project_location`(기본 `us`), `doc_processor_id`. 별도 `DocumentAISettings`는 만들지 않음(초기에 분리했다가 사용자 요청으로 재통합).

**구현** (`scheduler/jobs.py`):
- `_ocr_image(image_url)`: 이미지 다운로드(httpx) → Document AI 프로세서 호출 → `document.text` 반환.
- `extract_job()`: `needs_ocr` 공고는 `raw_postings.image_urls[0]`(첫 이미지만, 여러 장인 경우의 선택 로직은 미결)을 OCR해 `jobs.jd_text`를 채운 뒤 **같은 실행에서 바로** `pending`과 동일한 추출 경로를 탄다.
  - OCR 자체가 실패(이미지 없음 포함)하면 상태를 `needs_ocr`로 유지 — 다음 실행에 재시도(Gemini 미호출이라 sleep도 안 함).
  - OCR은 성공했는데 그 뒤 LLM 추출이 실패하면 `failed`로 전환 — `jd_text`는 이미 저장돼 있어 다음 실행이 OCR을 다시 안 하고 추출만 재시도한다.
- `pyproject.toml`에 `google-cloud-documentai` 정식 추가(`uv add`).
- 이후 추가: `_ocr_image()`가 `settings.ocr_provider`로 백엔드를 고른다 — `document_ai`(기본) 또는 `claude_code`(Claude Code 헤드리스 `-p`, 구독 로그인 재사용, 모델 `sonnet` 고정. haiku는 2026-09-30 실측에서 회사명을 지어내는 환각이 있어 쓰지 않음). `claude_code`는 배치 경로에서 Claude 구독을 쓰므로 CLAUDE.md의 "배치에 Claude 금지" 규칙과 충돌한다 — 기본값은 `document_ai`이고, 이 선택지를 계속 둘지는 미결.

**검증**: OCR 실패 시 상태를 `needs_ocr`로 유지하는 로직을 변형 검사로 확인 — 이 처리를 지우면(실패 즉시 `failed`로 전환) 관련 테스트가 실제로 깨짐.

**미결**:
1. 여러 이미지 중 첫 장만 OCR한다 — 진짜 JD가 두 번째 이미지 이후에 있는 공고는 놓칠 수 있음.
2. **운영 배포 시 인증 방식.** ADC+위장 로그인은 로컬 개발자 세션에 묶인 방식이라, EC2 등 무인 서버에 그대로 옮기기 어려움. 배포 시점(Phase G)에 Workload Identity Federation(AWS↔GCP 키리스 인증) 등으로 교체 검토 필요.
3. Document AI 무료 크레딧($300/90일) 소진 후 실제 과금 확인 필요.

### 13-6. 2026-10-14 진행 — 수집 기록·재시도 기간·진행 웹훅

**실행 이력 — DB 표 대신 로그 파일**: 처음엔 `ingest_runs` 표로 만들었다가 같은 날 로그 파일로 바꿨다(사용자 결정). `shared/logger.py::setup_logging("<프로그램>")`을 scheduler·crawler·bot 진입점이 호출해 콘솔과 `logs/<프로그램>.log`에 함께 남긴다. `TimedRotatingFileHandler`(자정 기준, `backupCount=7`)라 최근 7일치만 남고 오래된 날짜 파일은 자동 삭제된다. 실행 중에 바로 기록하므로 중간에 죽어도 그 시점까지 남는다. 프로그램마다 파일을 나눈 이유는 Windows에서 다른 프로세스가 연 파일의 이름 바꾸기가 실패할 수 있어서다. `crawl_job`은 사이트마다 `[crawl] jasoseol: ok · 목록 12 · 신규 3 · 빈 공고 1 · 상세 실패 0` 한 줄을 남기고, 같은 요약을 웹훅용으로 반환한다. 목록 본 수는 크롤러만 알 수 있어 `BaseCrawler.seen_count`/`detail_failed_count` 카운터를 두고 3개 크롤러가 올린다(인디스워크: API가 준 글 수, 자소설: 기간·채용구분·IT 조건 통과 공고 수, 잡코리아: 목록 카드 수). 신규 0건은 정상일 수 있어 목록 0건과 구분한다. 함께 고친 것: 기존 `setup_logging`은 설정에 없는 `settings.sentry`를 참조해 호출하면 에러가 나는 상태여서 Sentry 부분을 걷어냈다.

**재시도 기간 — 첫 실패 포함 2일**: `failed`·`needs_ocr`는 `jobs.crawled_at`이 어제 0시(KST) 이후인 공고만 다시 시도한다(`_retry_window_start`). 추출은 수집 직후 같은 실행에서 돌기 때문에 수집한 날 = 첫 시도일이라 실패 날짜를 따로 저장하지 않는다. 기간이 지난 `needs_ocr`는 `failed`로 바꾸고(OCR 포기), 기간이 지난 `failed`는 그대로 둔다 — 새 상태값은 만들지 않았다. `pending`은 대기(하루 Gemini 한도에 밀린 것 등)라 기간 제한이 없다. 한계: 한도에 며칠 밀렸다가 처음 실패한 공고는 재시도 없이 바로 기간 밖일 수 있다.

**진행 웹훅** (`bot/ingest_report.py`, `DISCORD_INGEST_WEBHOOK_URL`): `run_crawl_pipeline`이 비공개 채널로 ① 시작 ② 수집 완료(사이트별 목록·신규·빈 공고·상세 실패, 크롤러 에러·목록 0건은 ⚠️) ③ 추출 완료(성공·실패·OCR 실패, OCR 재시도 실패=기간 지나 포기, 한도로 남은 대기는 있을 때만)를 보낸다. `crawl_job`/`extract_job`은 예기치 않은 오류를 롤백 후 다시 올리고, 파이프라인이 ❌ 에러 메시지를 보낸 뒤 종료한다(스케줄러까지는 올리지 않음). 사이트 1곳의 크롤러 실패는 멈춤이 아니라 ⚠️ 표시. 주소가 비었거나 전송이 실패해도 수집은 계속된다.

### 13-7. 2026-10-14 첫 실제 실행에서 발견한 실패 3종 (수정 완료)

1. **추출 전부 실패 — `TypeError: expected string ... got 'list'`**: `gemini-3.5-flash-lite`는 `AIMessage.content`를 문자열이 아니라 블록 목록(`[{"type": "text", "text": ..., "extras": {"signature": ...}}]`)으로 준다. `analysis/scorer.py::_response_text()`가 `text` 블록만 이어 붙여 읽게 했다(thinking 등 다른 블록은 버림). fit 채점·회사 점수도 같은 경로라 함께 고쳐졌다.
2. **OCR 실패 — `Image width or height exceeds the 10k pixel limit`**: Document AI는 한 변 10,000px 초과 이미지를 거부한다. 인디스워크 캡처 공고는 세로 11,000~13,500px대가 있다. `_image_parts_for_document_ai()`가 제한을 넘으면 세로 9,000px 조각(200px 겹침)으로 잘라 PNG로 보내고, 조각별 결과를 줄바꿈으로 잇는다. Pillow 의존성 추가.
3. **OCR 실패 — `308 Permanent Redirect`**: `httpx`는 기본적으로 리다이렉트를 따라가지 않는다. 두 OCR 백엔드의 이미지 다운로드에 `follow_redirects=True`.

이 실행에서 failed/needs_ocr가 된 공고는 수집한 날이 오늘이라 다음 실행에서 자동 재시도된다(13-6 재시도 기간).

### 13-8. 2026-10-14 첫 채점에서 발견한 문제 — 본문이 이미지인데 텍스트 한 줄 때문에 OCR을 건너뜀 (수정 완료)

- **증상**: 본문이 "신우이엔지㈜"(6자)·"접수기한 2026.10.25"(24자)·"홈페이지 입사 지원하기"뿐인 공고가 fit 100점. LLM이 희망직무를 직무명으로 쓰고 "명시된 요건이 없으니 다 충족"으로 판단했다.
- **원인**: `_classify_extract_status`는 텍스트가 한 글자라도 있으면 `pending`이라 본문 이미지가 OCR되지 않았고, 요약의 하는 일이 비어도 `ok`가 됐다. ok 공고 중 하는 일이 빈 18건이 모두 이미지가 있었다.
- **수정**: ① `extract_job` — 텍스트 요약에 하는 일·자격요건이 모두 비고 이미지가 있으면 첫 이미지를 OCR해 `jd_text` 뒤에 붙이고 다시 요약(같은 실행에서 이미 OCR한 공고는 제외, 보완 OCR 실패 시 텍스트 요약 결과로 ok). 글자 수 기준은 389자인데 내용 없는 사례가 있어 쓰지 않았다. ② `_is_scoring_target` — 하는 일(`jd_tasks`)이 비면 채점 제외. ③ 채점 프롬프트 — position은 공고의 직무명, 공고에 없는 요건을 충족으로 보지 않기, 정보가 적은 공고는 direction ≤20·experience ≤12.
- 18건은 `pending`으로 되돌려 다시 추출, job_scores 106건은 지우고 전체 재채점.
