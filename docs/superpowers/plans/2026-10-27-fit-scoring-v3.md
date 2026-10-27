# 적합도 채점 v3 (판정은 LLM, 계산·등급은 코드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공고 적합도를 사용자 평가 기준(`preferences.json`)대로 채점해, 지원 불가·판단 불가·추천·권유·참고·비추천 등급을 정확히 매긴다.

**Architecture:** Gemini 호출은 공고×유저당 1회(single-turn). 프롬프트에 JD + 지원자 사실(`profile.json` → `users.basic_info`·`experiences`) + 지원 조건·선호(`preferences.json` → `users.preferences`)를 모두 넣고, LLM은 관문·항목별 ✓/△/✗·직무 방향만 판정한다. 점수 공식·가중치·관문 처리·마감·판단 불가·등급·중복 판정은 코드가 한다.

**Tech Stack:** Python 3.12, SQLAlchemy, LangChain(Gemini), pytest

**Spec:** 이 문서의 "설계" 절. 기준값의 원본은 `preferences.json`(gitignore, 개인 데이터)이고, 사람용 설명·보정 사례는 위키 `E:\00_llm_wiki\wiki\취준\평가기준.md`.

## 설계

**입력 분리**
| 출처 | DB | 프롬프트 블록 | 내용 |
|---|---|---|---|
| `profile.json` | `users.basic_info`, `experiences` | `[지원자 사실]` (`build_profile_text`) | 학력·졸업예정·자격·어학·기술·경험 |
| `preferences.json` | `users.preferences` (신규 JSON) | `[지원 조건·선호]` (`build_preferences_text`) | `constraints`, `direction` |
| `preferences.json` | `users.preferences` | 프롬프트에 안 넣음 | `weights`, `grade` — 코드만 사용 |

**LLM 출력 (판정만)**
```json
{"position": "직무명",
 "info_sufficient": true,
 "gate": {"degree": "pass|fail|unknown", "graduation": "...", "career": "...", "language": "...", "cert": "...", "fail_reason": "원문"},
 "required":  [{"item": "공고 문구", "verdict": "O|H|X|N", "evidence": "근거 경험"}],
 "preferred": [{"item": "...", "verdict": "...", "kind": "normal|department|vague", "evidence": "..."}],
 "tasks":     [{"item": "...", "verdict": "...", "evidence": "..."}],
 "direction": {"verdict": "match|partial|mismatch", "reason": "..."},
 "fit_reasons": "50자 한 줄"}
```
`verdict`: O=✓(1), H=△(0.5), X=✗(0), N=판단 불가(분모 제외). 기호 대신 영문자를 쓰는 건 파싱 안정성 때문.

**코드 계산**
- 카테고리 충족률 = Σ(값×가중치) / Σ(가중치), N 제외. 우대 가중치: normal 1, `department` = `weights.department_preferred`(0.25), `vague` = `weights.vague_preferred`(0.5). 필수·업무는 가중치 1.
- 점수 = 필수 충족률×50 + 우대×20 + 업무×30. 판정 가능한 항목이 없는 카테고리는 빼고 남은 배점으로 100점 환산.
- `status`: gate에 fail → `"ineligible"`, `info_sufficient`가 false거나 필수·업무 판정 가능 항목이 둘 다 0개 → `"insufficient"`, 그 외 `"scored"`. `ineligible`·`insufficient`는 `fit_score = None`.
- 등급(읽을 때 계산): 마감 경과 → `closed`, `ineligible` → `ineligible`, `insufficient` → `insufficient`, 점수≥`grade.recommend_min`(80)·match → `recommend`, `suggest_min`(70)~79·match → `suggest`, ≥70·partial → `reference`, 나머지 `not_recommended`. 고용형태는 등급에 쓰지 않는다(`employment_type_affects_grade: false`).
- 알림 대상 = `recommend`, `suggest`.

**10/27 오후 추가 결정 (preferences.json에 반영됨 — 각 Task 구현 시 함께 처리)**
- **근무지 관문**: `constraints.work_locations`(서울·과천·판교·분당)를 `scheduler/jobs.py::_TARGET_REGION_KEYWORDS` 하드코딩 대신 쓴다(Task 6). 유저 선호에 값이 없으면 지금 상수로 폴백. 근무지 미기재는 통과(현행 동작 유지)
- **태도형 항목 = N**: Task 3 프롬프트에 `item_rules.attitude_items` 문장을 넣어 학습의지·관심·협업·꼼꼼함 같은 문장은 verdict `N`으로 받는다. Task 4 테스트에 "필수가 전부 N → 필수 카테고리 빼고 우대·업무로 100점 환산" 케이스 추가
- **방향 목록 변경**: 순수 인프라 운영(MSP·IaaS)은 mismatch, AI 활용 사무직은 partial. 목록은 preferences에서 읽으므로 코드 변경 없음

**10/27 저녁 추가 결정 (구현 완료)**
- **경력 비율 점수**: 필수 경력 2년 이하(`constraints.max_required_years`)는 지원 가능. 경력 문장은 required에 `kind: "career"`, `years`로 넣고 코드가 `min(1, career_months ÷ (years×12))`로 점수(1년 0.5, 2년 0.25). 초과는 관문 fail. 사전 필터도 `min_years ≤ max_required_years`면 통과
- **판단 불가**: 담당업무·자격요건이 둘 다 없을 때만. 사전 필터도 둘 다 비었을 때만 제외
- **맞는 점·아쉬운 점 분리**: LLM이 `good_points`·`weak_points`를 내고 `fit_reasons` = 맞는 점, `fit_detail.weak_points` = 아쉬운 점. 카드는 "✨ 맞는 점"·"⚠️ 아쉬운 점" 두 칸 (이전 점수는 "✨ 왜 나랑 맞나" 한 줄)

## Global Constraints

- 배치 LLM은 Gemini만 (CLAUDE.md LLM 사용 규칙). 공고×유저당 호출 1회 유지
- `SCORER_VERSION = 3`. 이전 버전 점수는 구 점수로 표시되고 요청 시에만 재채점 (2026-10-15 결정 유지)
- `preferences.json`·`profile.json`은 gitignore 개인 데이터. 테스트는 픽스처 dict만 쓴다
- 프롬프트에 이름·링크를 넣지 않는다 (`_SCORING_EXCLUDED_INFO` 유지)
- 기존 테스트 전체 통과 유지. 커밋은 사용자 요청 시에만 (CLAUDE.md)

## Review Focus

1. `users.preferences`가 비어 있는 유저 → 선호 블록 없이 채점되고, direction 판정이 비면 등급은 방향 조건을 건너뛰고 점수로만 매긴다(v2와 비슷한 동작). Task 2·5 테스트
2. LLM이 verdict를 `"✓"`, `"o"`, `"pass"`처럼 다르게 쓰는 경우 → 정규화, 모르는 값은 N. Task 4 테스트
3. `deadline`이 None(채용 시 마감) → `closed` 아님. Task 5 테스트
4. 우대 항목이 전부 `department` → 분모가 0이 되지 않고 정상 계산. Task 4 테스트
5. v2로 매긴 기존 행(`fit_detail`에 `status`·`direction` 없음) → 대시보드·알림이 깨지지 않고 점수 기준으로만 등급. Task 5 테스트

---

### Task 1: `users.preferences` 컬럼과 적재

**Files:**
- Modify: `db/orm.py` (User에 `preferences = Column(JSON)`)
- Modify: `scripts/migrate_users_columns.py` (`_NEW_COLUMNS`에 `"preferences": "JSON"`)
- Modify: `scripts/import_profile.py`
- Test: `tests/test_import_profile.py`, `tests/test_migrate_users_columns.py`

**Interfaces:**
- Produces: `User.preferences: dict | None`; `import_preferences(session, user_id: int, data: dict) -> None` (덮어쓰고 `mark_profile_changed`, 커밋까지); CLI `python -m scripts.import_profile [profile.json] [--preferences preferences.json] [--user-id N]` — `--preferences`를 주면 둘 다 적재

- [ ] **Step 1: 실패 테스트** — `test_import_preferences_overwrites_and_bumps_version`: 유저에 `{"direction": {...}}`를 적재하면 `user.preferences == data`, `profile_version`이 1 증가. `test_import_preferences_rejects_missing_sections`: `constraints`·`direction` 중 하나라도 없으면 `ValueError`. `test_migration_adds_preferences_column`: 기존 SQLite users 테이블에 `preferences` 컬럼이 추가되고 두 번 실행해도 안전
- [ ] **Step 2: 실패 확인** — `.venv/Scripts/python.exe -m pytest tests/test_import_profile.py tests/test_migrate_users_columns.py -v` → FAIL
- [ ] **Step 3: 구현** — `_comment`처럼 `_`로 시작하는 최상위 키는 저장하지 않는다
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: users.preferences 컬럼과 preferences.json 적재`

### Task 2: 선호 텍스트 블록

**Files:**
- Create: `analysis/preferences.py`
- Test: `tests/test_preferences.py`

**Interfaces:**
- Produces: `build_preferences_text(preferences: dict | None) -> str` — 비었으면 `""`. `constraints`의 각 값과 `direction.match/partial/mismatch` 목록만 펼친다. `weights`·`grade`·`_`로 시작하는 키는 넣지 않는다. `weights_of(preferences: dict | None) -> dict[str, float]` — 없으면 `{"department_preferred": 0.25, "vague_preferred": 0.5}`. `grade_config(preferences: dict | None) -> dict` — 없으면 `{"recommend_min": 80, "suggest_min": 70}`

- [ ] **Step 1: 실패 테스트** — `test_renders_constraints_and_direction_lists` (출력에 `"Python"`, `"크롤링·데이터 수집만 하는 직무"`가 있음), `test_excludes_weights_and_grade` (`"recommend_min"`이 없음), `test_empty_preferences_returns_empty_string`, `test_defaults_when_weights_missing`
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** (`tests/test_preferences.py`)
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: 채점용 선호 텍스트 블록`

### Task 3: v3 프롬프트

**Files:**
- Modify: `analysis/scorer.py` (`_FIT_PROMPT`, `build_fit_chain`, `SCORER_VERSION = 3`과 버전 주석 `3: 항목 판정 + 코드 계산·등급(2026-10-27)`)
- Test: `tests/test_fit_scoring.py`

**Interfaces:**
- Produces: `_FIT_PROMPT` 입력 변수 `jd_text`, `profile_text`, `preferences_text`. 출력은 "설계"의 JSON 스키마

프롬프트에 반드시 들어갈 규칙 (평가기준.md 3절 그대로):
- 항목 분해 대응표 (자격요건→required, 우대→preferred, 담당업무→tasks / "이런 분을 기다려요"형 / NCS 필요기술·필요지식·직무수행내용, "기타" 제외)
- 판정 기준 O/H/X/N과 "근거 경험을 evidence에 적지 못하면 O를 주지 않는다"
- preferred `kind`: 학과명 우대 → `department`, "관련 자격증"·"유관 경력"처럼 막연 → `vague`, 법정 가점(보훈·장애)은 항목에서 제외
- gate는 `[지원 조건·선호]` 블록의 규칙으로 판정 (졸업 시한, 허용 경력, 학위, 사용 언어)
- direction은 직함이 아니라 담당업무 문장으로 `[지원 조건·선호]`의 목록과 대조
- 담당업무·자격요건이 없거나 본문이 비면 `info_sufficient: false`
- 총점을 계산하지 않는다

- [ ] **Step 1: 실패 테스트** — 기존 `test_sends_jd_and_profile_text_to_llm`을 `test_sends_jd_profile_and_preferences_text_to_llm`으로 바꿔 체인 호출 인자에 `preferences_text`가 포함되는지 확인. `test_scorer_version_is_3`
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** (`tests/test_fit_scoring.py`)
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: 적합도 프롬프트 v3`

### Task 4: 항목 판정 → 점수·상태 계산

**Files:**
- Modify: `analysis/scorer.py` (`_compute_fit_score`, `score_fit`; `FIT_SCORE_MAX`·`FIT_GATE_FAIL_CAP`·`_GATE_KEYS` 교체)
- Test: `tests/test_fit_scoring.py` (v2 배점 테스트는 v3 기준으로 교체)

**Interfaces:**
- Consumes: `weights_of` (Task 2)
- Produces: `_compute_fit_score(data: dict, weights: dict[str, float]) -> tuple[int | None, dict]` — detail 키: `position`, `status`(`scored|ineligible|insufficient`), `gate`, `required`, `preferred`, `tasks`, `direction`(`match|partial|mismatch|""`), `rates`(카테고리별 충족률 또는 None). `score_fit(jd_text: str, profile_text: str, preferences_text: str = "", weights: dict | None = None) -> dict` — 반환 키는 기존과 같음(`fit_score`, `fit_reasons`, `fit_detail`)
- `CATEGORY_POINTS = {"required": 50, "preferred": 20, "tasks": 30}`, `GATE_KEYS = ("degree", "graduation", "career", "language", "cert")`

- [ ] **Step 1: 실패 테스트**
  - `test_score_is_weighted_sum_of_category_rates`: required [O,O,H,X] → 0.625, preferred [O,X] → 0.5, tasks [O,O,O,H] → 0.875 ⇒ `round(0.625*50 + 0.5*20 + 0.875*30)` = 68
  - `test_department_preferred_weighs_quarter`: preferred [O(normal), X(department)] → (1×1 + 0×0.25)/(1+0.25) = 0.8
  - `test_vague_preferred_weighs_half`
  - `test_n_verdicts_are_excluded_from_denominator`
  - `test_all_department_preferred_does_not_divide_by_zero`
  - `test_empty_category_rescales_remaining_points`: preferred 없음 → (required×50 + tasks×30)/80×100
  - `test_gate_fail_marks_ineligible_with_no_score`: `fit_score is None`, `status == "ineligible"`, `fail_reason` 보존
  - `test_insufficient_when_flag_false_or_no_required_and_tasks`
  - `test_verdict_aliases_are_normalized`: `"✓"`·`"o"`·`"pass"` → O, `"△"`·`"partial"` → H, `"✗"`·`"x"` → X, 그 외 → N
  - `test_unparseable_answer_raises_so_nothing_is_saved` (기존 유지)
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** (`tests/test_fit_scoring.py`)
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: 항목 판정 기반 점수·상태 계산`

### Task 5: 등급

**Files:**
- Create: `analysis/grading.py`
- Test: `tests/test_grading.py`

**Interfaces:**
- Consumes: `grade_config` (Task 2)
- Produces: `GRADES = ("closed", "ineligible", "insufficient", "recommend", "suggest", "reference", "not_recommended")`; `GRADE_LABELS`(한국어: 마감, 지원 불가, 판단 불가, 추천, 권유, 참고, 비추천); `NOTIFY_GRADES = ("recommend", "suggest")`; `grade_for(fit_score: int | None, fit_detail: dict | None, deadline: datetime | None, now: datetime, grade_cfg: dict) -> str`

- [ ] **Step 1: 실패 테스트** — 위키 보정 사례를 코드 쪽에서 고정
  - `test_closed_when_deadline_passed` / `test_no_deadline_is_not_closed`
  - `test_recommend_at_80_with_match` (네피리티 91·match)
  - `test_suggest_between_70_and_79_with_match`
  - `test_reference_when_partial_and_70_or_more` (KIAT 76·partial)
  - `test_high_score_with_mismatch_is_not_recommended` (한국평가데이터 92·mismatch)
  - `test_ineligible_and_insufficient_pass_through` (제논·KETI / 테이슨)
  - `test_v2_detail_without_direction_grades_by_score_only`: `fit_detail`에 `direction` 없음 → 80 이상 `recommend`, 70~79 `suggest`
  - `test_thresholds_come_from_grade_config`
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** (`tests/test_grading.py`)
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: 적합도 등급`

### Task 6: analyze_job 연결 — 선호 전달, 마감 공고 건너뛰기, 중복 공고 건너뛰기

**Files:**
- Create: `analysis/dedup.py`
- Modify: `scheduler/jobs.py` (`analyze_job`, `_is_scoring_target`)
- Test: `tests/test_dedup.py`, `tests/test_scheduler_jobs.py`

**Interfaces:**
- Consumes: `build_preferences_text`, `weights_of` (Task 2), `score_fit` (Task 4)
- Produces: `posting_key(company: str | None, title: str | None) -> str` — 제목 앞의 `"회사명｜"`·`"[회사명]"` 접두어 제거, 공백·특수문자 제거, 소문자화 후 `회사|제목`

- [ ] **Step 1: 실패 테스트**
  - `test_posting_key_matches_same_posting_from_two_sites`: (`"한국전자기술연구원"`, `"한국전자기술연구원｜2026년 정규직 공개채용"`)과 (`"한국전자기술연구원"`, `"2026년 정규직 공개채용"`)의 키가 같음
  - `test_analyze_passes_preferences_text_and_weights_to_score_fit`
  - `test_closed_jobs_are_not_scored` (deadline < now → `score_fit` 호출 안 함, Gemini 한도도 안 씀)
  - `test_duplicate_posting_is_scored_once_per_user` (같은 키 공고가 이미 그 유저 점수를 가지면 건너뜀)
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** (`tests/test_dedup.py tests/test_scheduler_jobs.py`)
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: 채점에 선호 반영, 마감·중복 공고 제외`

### Task 7: 알림·목록을 등급 기준으로

**Files:**
- Modify: `bot/notifications.py` (`_unsent_scores`: `MIN_FIT_SCORE` 필터 대신 `grade_for(...) in NOTIFY_GRADES`), `tracker/lists.py` (`MISSED_MIN_FIT_SCORE` 대신 같은 기준), 카드 문구에 등급 라벨 추가
- Modify: 대시보드 템플릿(`web/templates/`)의 점수 칸 옆에 `GRADE_LABELS` 표시
- Test: `tests/test_notifications.py`, `tests/test_tracker.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `grade_for`, `NOTIFY_GRADES`, `GRADE_LABELS` (Task 5), `grade_config` (Task 2)

등급은 `fit_score`만으로 SQL에서 거를 수 없으므로 `fit_score IS NOT NULL OR` 조건으로 후보를 가져온 뒤 파이썬에서 `grade_for`로 거른다. 정렬은 기존(점수 10점 단위 → 마감 가까운 순) 유지.

- [ ] **Step 1: 실패 테스트** — `test_mismatch_high_score_is_not_notified`, `test_suggest_grade_is_notified`, `test_ineligible_is_not_notified`, `test_card_shows_grade_label`, `test_dashboard_shows_grade_label`
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** (전체 `pytest`)
- [ ] **Step 5: 커밋 (사용자 요청 시)** — `feat: 알림·목록 등급 기준`

### Task 8: 배포 (코드 변경 없음, 사용자 실행)

- [ ] (a) **배포 전에** 운영 DB에 `python scripts/migrate_users_columns.py` 실행 (preferences 컬럼 추가) — 구 코드는 새 nullable 컬럼을 무시하지만, 새 코드가 아직 없는 컬럼을 SELECT하면 모든 곳에서 500이 나기 때문
- [ ] (b) Railway에 새 코드 배포 — 컬럼이 이미 있으므로 구/신 코드 모두 안전하게 뜬다
- [ ] (c) 배포 직후, **다음 평일 08:00 KST 실행 전에** `profile.json`·`preferences.json`을 base64로 넘겨 `python -m scripts.import_profile /tmp/profile.json --preferences /tmp/preferences.json` 실행. 기대 출력: `유저 1: basic_info 6개 항목, 경험 14건 적재` + 선호 적재 메시지 — preferences가 비어 있는 채로 08:00 파이프라인이 돌면 선호 없이 채점·알림이 나가기 때문
- [ ] (d) 웹에서 재채점 요청 → 다음 실행에서 v3로 재채점됐는지 위키 보정 사례 중 DB에 있는 공고(디케이테크인 2건, 한국평가데이터, 비상교육)로 확인 — 적재가 반영돼 점수가 실제로 v3로 바뀌었는지 검증하는 단계이기 때문

## Self-Review 결과

- 사용자 요구 5가지 대응: ① 관문→지원 불가 (Task 4·5), ② 학과 우대 ¼ (Task 4), ③ 70~79 권유 (Task 5), ④ 고용형태 미반영 (Task 5 `employment_type_affects_grade`), ⑤ 직무 방향 (Task 3·5)
- 추가로 드러난 문제: 정보 없는 공고 고득점 (Task 4 insufficient), 같은 공고 중복 (Task 6), 마감 공고 채점 낭비 (Task 6)
- 범위 밖: 직무 방향 목록을 웹에서 편집하는 UI, 등급 기준 변경 시 Gemini 없이 재계산하는 스크립트 (등급은 읽을 때 계산하므로 당장 불필요)
