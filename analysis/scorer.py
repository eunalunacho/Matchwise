"""공고 적합도 + 회사 점수 계산.

fit_score  : LLM 판정(JD × 지원자 사실 × 지원 조건·선호) → 코드가 점수·상태 계산 (v3)
company_score: 당분간 비활성화 — 수집→fit 계산→추천 흐름을 먼저 완성한다.
  계산 함수(score_company_from_dart/llm)는 재활성화 대비 그대로 남겨뒀다.
  현재 어디서도 호출하지 않는다 (scheduler/jobs.py::analyze_job()은 score_fit()만 사용).
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from analysis.gemini_client import get_llm
from analysis.preferences import career_of, weights_of

logger = logging.getLogger(__name__)

# ── 프롬프트 ────────────────────────────────────────────────────────────

# fit 채점 v3(2026-10-27 결정, 위키 취준/평가기준.md). LLM은 관문·항목별 판정·직무 방향만 내고,
# 점수·상태는 _compute_fit_score()가 계산한다.
#   점수 = 필수 충족률×50 + 우대 충족률×20 + 업무 겹침률×30
#   판정 가능한 항목이 없는 카테고리는 빼고 남은 배점으로 100점 환산
CATEGORY_POINTS: dict[str, int] = {"required": 50, "preferred": 20, "tasks": 30}
# 관문 — 하나라도 fail이면 지원 불가(ineligible), 점수 없음. 근무지·마감은 코드가 따로 본다.
#   degree 학위 / graduation 졸업(예정) 시한 / career 필수 경력 / language 주 사용 언어 / cert 필수 어학·자격
GATE_KEYS = ("degree", "graduation", "career", "language", "cert")

# 판정 문자 → 값. N(판단 불가)은 분모에서 뺀다.
_VERDICT_VALUES: dict[str, float] = {"O": 1.0, "H": 0.5, "X": 0.0}
# LLM이 영문자 대신 기호·단어로 답할 때의 별칭. 모르는 값은 N
_VERDICT_ALIASES: dict[str, str] = {
    "O": "O", "✓": "O", "✔": "O", "○": "O", "PASS": "O",
    "H": "H", "△": "H", "PARTIAL": "H",
    "X": "X", "✗": "X", "✘": "X", "×": "X", "FAIL": "X",
    "N": "N",
}
_DIRECTIONS = ("match", "partial", "mismatch")
_PREFERRED_KINDS = ("normal", "department", "vague")
# 필수의 경력 연차 항목(kind "career")은 판정 대신 "실무 개월 ÷ 요구 개월"(최대 1)로 점수를 준다(2026-10-27 결정)
_REQUIRED_KINDS = ("normal", "career")

# 채점 규칙(_FIT_PROMPT·배점) 버전. 규칙을 바꾸면 1 올린다 — 이전 버전으로 매긴 점수는 구 점수가 되고,
# 사용자가 요청할 때만 다시 채점한다(2026-10-15 결정).
#   1: 항목별 배점 첫 버전(2026-10-14)
#   2: 짧은 직무명(position)·한 줄 fit_reasons(2026-10-15)
#   3: 항목 판정 + 코드 계산·등급, 경력 비율 점수, 맞는 점·아쉬운 점 분리(2026-10-27)
SCORER_VERSION = 3


def is_stale_score(score, user_profile_version: int) -> bool:
    """점수가 지금 프로필·채점 규칙과 다른 버전으로 매겨졌는지(= 구 점수)."""
    return (score.scorer_version != SCORER_VERSION
            or score.profile_version != user_profile_version)

_FIT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """당신은 신입 채용 공고와 지원자의 적합도를 판정하는 채용 담당자입니다.
정해진 절차에 따라 지원자 사실에 적힌 내용만 근거로 항목마다 판정합니다.
점수·총점은 계산하지 않고 판정만 합니다.
유효한 JSON 하나만 출력하고, 마크다운 코드블록이나 다른 설명은 붙이지 않습니다."""),
    ("human", """
[채용 공고 JD]
{jd_text}

[지원자 사실]
{profile_text}

[지원 조건·선호]
{preferences_text}

## 판정 절차

1. 직무 선택 (position)
   공고에 모집 직무가 여러 개면 [지원 조건·선호]의 직무 방향과 가장 잘 맞는 직무 하나를 고르고, 아래 모든 판정은 그 직무 기준으로 한다.
   position은 알림 카드 제목에 "[회사] 직무명"으로 쓰이는 짧은 직무명(30자 이내)이다.
   공고에 적힌 직무명·업무 표현을 바탕으로 쓰고, 지원자 희망직무 이름을 쓰지 않는다.
   번호, 문장 어미, 회사명, "채용"·"모집"·"신입" 같은 말은 뺀다.
   예: "1. IT 서비스 시스템 운영 및 개발 업무를 담당해요." → "IT 서비스 운영·개발"
       "[데이블] Jr. ML Engineer 머신러닝 엔지니어 채용 (신입)" → "Jr. ML Engineer"

2. 정보 충분성 (info_sufficient)
   담당업무와 자격요건이 둘 다 없거나, 본문이 비어 있거나 이미지·별도 파일이라 내용을 읽을 수 없으면 false.
   false여도 아래 판정은 읽을 수 있는 만큼만 하고, 없는 내용을 지어내지 않는다.

3. 지원 자격 관문 (gate) — 각 항목을 "pass" / "fail" / "unknown" 중 하나로 판정
   - degree: 학위 조건 (예: 석사 이상 필수)
   - graduation: 공고가 허용하는 졸업(예정) 시한·졸업자 한정 조건
   - career: 필수 경력 연차 — [지원 조건·선호]의 허용 경력(max_required_years)을 넘을 때만 "fail". 그 이하면 "pass"
   - language: 메인·필수 프로그래밍 언어 (코딩테스트 언어 포함)
   - cert: 필수 어학 점수·필수 자격증
   [지원 조건·선호]에 적힌 지원 조건(졸업 시한, 허용 경력, 학위, 사용 언어 등)을 공고 조건과 대조해 판정한다.
   그 블록이 비어 있으면 공고 조건과 [지원자 사실]만 대조한다.
   "fail"은 공고가 명시한 조건을 지원자가 확실히 충족하지 못할 때만 쓴다. 공고에 조건이 없거나 판단할 수 없으면 "unknown".
   fail이 있으면 fail_reason에 그 조건의 공고 원문을 적는다(없으면 빈 문자열).
   근무지·마감일은 판정하지 않는다.

4. 항목 분해 — 공고 문구를 항목 하나씩 나눠 required / preferred / tasks에 넣는다
   | JD 형식 | required | preferred | tasks |
   | 일반 | 자격요건 | 우대사항 | 담당업무 |
   | "이런 분을 기다려요" 형식 | 기다리는 분 | 더 좋아요 | 담당 업무 |
   | NCS 직무기술서 | 필요기술 | 필요지식 | 직무수행내용 ("기타"는 제외) |
   item에는 공고 문구를 그대로(길면 핵심만 줄여) 쓴다. 공고에 없는 칸은 빈 배열로 둔다.
   관문(학위·졸업·어학 점수)만 적은 문장은 gate에서 판정하고 required에 넣지 않는다.
   필수 경력 연차 문장(예: "경력 1년 이상", "실무 경력 18개월 이상")은 required에 넣고 kind를 "career",
   years에 요구 연차를 숫자로 적는다(개월이면 12로 나눈다. 예: 18개월 → 1.5). 그 밖의 required 항목은 kind "normal".
   경력 항목의 점수는 코드가 지원자 경력 기간으로 계산하므로 verdict는 아무 값이어도 된다.

5. 항목 판정 (verdict) — 영문자 한 글자로
   - "O": [지원자 사실]에 직접 근거가 있음
   - "H": 비슷한 경험은 있지만 수준·범위가 다름 (실습·대회 수준, 직접 한 일이 아닌 추론 등)
   - "X": 근거 없음
   - "N": 판단 불가 — 경험으로 확인할 수 없는 문장 (예: "인근 거주자")
   의지·관심·태도·협업·성실·꼼꼼함·주도성 같은 태도형 문장은 required·preferred 어디에 있든 "N"으로 한다.
   [지원 조건·선호]에 "항목 판정 규칙"이 있으면 그 규칙을 따른다.
   근거가 된 경험을 evidence에 적지 못하면 "O"를 주지 않는다. "X"·"N"이면 evidence는 빈 문자열.

6. 우대 항목 종류 (preferred의 kind)
   - "department": 학과명 우대 (예: "AI 관련 학과 전공자", "관련학과 우대")
   - "vague": 막연한 우대 (예: "관련 자격증", "유관 경력")
   - "normal": 그 밖의 우대
   법정 가점(보훈·장애 등)은 항목에서 뺀다.

7. 직무 방향 (direction)
   직함이 아니라 담당업무 문장을 [지원 조건·선호]의 직무 방향 목록(맞음/부분/안 맞음)과 대조해
   "match" / "partial" / "mismatch" 중 하나로 정하고, reason에 이유를 한 줄로 적는다.
   직무 방향 목록이 없으면 verdict는 빈 문자열.

## 근거 규칙
- 지원자 사실에 적힌 내용만 근거로 쓴다. 적혀 있지 않은 경험·기술을 있다고 가정하지 않는다.
- 공고에 적힌 내용만 항목으로 본다. 요건이 적혀 있지 않다고 해서 "충족"으로 보지 않는다.
- 비슷해 보이는 기술을 같은 것으로 보지 않는다 (예: Python 경험은 Java 경험이 아니다).
- 총점은 계산하지 않는다.

## 출력
- good_points: 알림 카드 "맞는 점" 칸에 그대로 보이는 한 줄. 한국어, 50자 이내. 공고와 맞는 경험·기술.
- weak_points: 알림 카드 "아쉬운 점" 칸에 그대로 보이는 한 줄. 한국어, 50자 이내. 부족하거나 안 맞는 점. 없으면 빈 문자열.
  둘 다 "지원자는" 같은 주어와 점수는 쓰지 않는다.
  예: good_points "LLM 에이전트·GCP 경험이 우대사항과 일치", weak_points "Java 실무 경험 없음"

아래 JSON 형식으로만 답하세요:
{{"position": "<직무>", "info_sufficient": <true|false>, "gate": {{"degree": "pass|fail|unknown", "graduation": "pass|fail|unknown", "career": "pass|fail|unknown", "language": "pass|fail|unknown", "cert": "pass|fail|unknown", "fail_reason": "<원문 또는 빈 문자열>"}}, "required": [{{"item": "<공고 문구>", "verdict": "O|H|X|N", "kind": "normal|career", "years": <경력 항목만 숫자>, "evidence": "<근거 경험>"}}], "preferred": [{{"item": "<공고 문구>", "verdict": "O|H|X|N", "kind": "normal|department|vague", "evidence": "<근거 경험>"}}], "tasks": [{{"item": "<공고 문구>", "verdict": "O|H|X|N", "evidence": "<근거 경험>"}}], "direction": {{"verdict": "match|partial|mismatch", "reason": "<이유>"}}, "good_points": "<맞는 점>", "weak_points": "<아쉬운 점>"}}
"""),
])

_SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a job posting summarizer. Respond ONLY with valid JSON, no markdown fences."),
    ("human", """
다음 채용 공고 JD에서 아래 정보를 추출하세요. 원문에 있는 내용만 사용하세요.

JD:
{jd_text}

참고용 힌트 — 채용 사이트가 분류한 채용구분(신입/경력/인턴 등). 틀릴 수 있으니 JD 원문과
다르면 원문을 우선하고, 그 불일치를 eligibility.evidence에 적으세요:
{hints_text}

- tasks: 실제 담당하게 될 업무 3~4개 (각 40자 이내, 한국어)
- stack: 명시된 기술 스택 목록 (없으면 빈 배열)
- employment_type: 고용형태 — "정규직", "계약직", "인턴 (6개월)" 같은 형식, 기간 명시 시 포함 (명시 없으면 빈 문자열)
- qualifications: 자격요건 3~5개 (각 50자 이내, 없으면 빈 배열)
- preferred: 우대사항 최대 5개 (각 50자 이내, 없으면 빈 배열)
- eligibility: 신입(학사 갓 졸업/졸업예정) 지원 가능 여부
  - new_grad_ok: JD가 신입 지원을 명시적으로 허용하면(신입, 신입/경력, 경력무관 포함) true,
    명시적으로 배제(예: "경력 3년 이상 필수")하면 false, 판단 근거가 없으면 null
  - min_years: 필수로 요구하는 최소 경력 연차(정수). 없으면 null
  - evidence: 판단 근거가 된 원문 문장 그대로(한국어). 근거 없으면 빈 문자열
- locations: 근무지 목록. 원문에 적힌 주소·지역명을 시/도부터 그대로 (예: "서울 강남구", "경기 성남시 분당구 판교역로").
  근무지가 여러 곳이면 모두, 명시가 없으면 빈 배열
- deadline: 서류 접수 마감 일시. "YYYY-MM-DD HH:MM"(24시간제, 한국 시간), 시간이 없으면 "YYYY-MM-DD".
  접수 기간이 "10월 12일 ~ 10월 28일 23:59"처럼 적혀 있으면 끝 날짜를 쓴다. 연도가 없으면 공고 맥락의 연도.
  "상시채용", "채용 시 마감"처럼 날짜가 없으면 null. 서류 마감이 아닌 일정(발표일·면접일)은 쓰지 않는다

아래 JSON 형식으로만 답하세요:
{{"tasks": ["<업무>"], "stack": ["<기술>"], "employment_type": "<고용형태>", "qualifications": ["<자격요건>"], "preferred": ["<우대사항>"], "eligibility": {{"new_grad_ok": <true|false|null>, "min_years": <정수|null>, "evidence": "<원문 문장>"}}, "locations": ["<근무지>"], "deadline": "<YYYY-MM-DD HH:MM 또는 null>"}}
"""),
])

_COMPANY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a workplace quality analyst. Respond ONLY with valid JSON, no markdown fences."),
    ("human", """
다음 채용 공고 JD에서 회사의 매력도를 분석하세요.

JD:
{jd_text}

아래 항목을 JD에서 읽히는 신호로 평가하세요:
- 기술 스택 현대성 (최신 기술 사용 여부)
- 복지/워라밸 언급 수준
- 회사 성장 단계 (스타트업/시리즈/대기업)
- 직무 자율성/성장 기회 언급

아래 JSON 형식으로만 답하세요:
{{"company_score": <0-100 정수>, "company_summary": "<한국어 1-2줄 요약>"}}
"""),
])


# ── 체인 팩토리 ─────────────────────────────────────────────────────────

def build_fit_chain() -> Runnable:
    return _FIT_PROMPT | get_llm()


def build_company_chain() -> Runnable:
    return _COMPANY_PROMPT | get_llm()


def build_summary_chain() -> Runnable:
    return _SUMMARY_PROMPT | get_llm()


# ── JSON 파싱 헬퍼 ──────────────────────────────────────────────────────

def _response_text(response) -> str:
    """LLM 응답(AIMessage)의 본문 글자만 꺼낸다.

    gemini-3.x는 content를 문자열이 아니라 블록 목록으로 준다 —
    [{"type": "text", "text": "...", "extras": {"signature": ...}}]. text 블록만 이어 붙이고
    thinking 같은 다른 블록은 버린다(그 안의 중괄호가 JSON 파싱을 오염시키지 않게).
    """
    content = response.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def _parse_json(text: str) -> dict:
    """텍스트에서 처음으로 완성되는 JSON 객체 하나를 읽는다. 없으면 {}.

    첫 '{'부터 마지막 '}'까지를 통째로 읽으면, 모델이 올바른 JSON 뒤에 꼬리를 덧붙였을 때
    (2026-10-14 실제 응답: `{...}, "scores": {...}}`) 전체가 깨진다. raw_decode는 객체 하나가
    끝나는 지점에서 멈추므로 뒤에 붙은 내용을 무시한다.
    """
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _clamp(val, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(val)))


# ── fit 점수 (LLM 판정 → 코드 계산) ─────────────────────────────────────

def _normalize_verdict(value) -> str:
    """판정 값 → "O"/"H"/"X"/"N". 기호·단어 별칭을 받고, 모르는 값은 N(판단 불가)."""
    if not isinstance(value, str):
        return "N"
    return _VERDICT_ALIASES.get(value.strip().upper(), "N")


# 관문 값 별칭 — LLM이 pass/fail 대신 한국어·기호로 답하는 경우
_GATE_ALIASES = {
    "pass": "pass", "통과": "pass", "충족": "pass", "가능": "pass", "✓": "pass", "o": "pass",
    "fail": "fail", "불가": "fail", "탈락": "fail", "미충족": "fail", "✗": "fail", "x": "fail",
}


def _normalize_gate_value(value) -> str:
    """관문 값 → "pass"/"fail"/"unknown"."""
    text = "".join(str(value or "").split()).lower()
    return _GATE_ALIASES.get(text, "unknown")


def _normalize_direction(value) -> tuple[str, str]:
    """direction({"verdict", "reason"} 또는 문자열) → (match|partial|mismatch|"", 이유)."""
    if isinstance(value, dict):
        verdict, reason = value.get("verdict"), str(value.get("reason") or "")
    else:
        verdict, reason = value, ""
    text = str(verdict or "").strip().lower()
    return (text if text in _DIRECTIONS else ""), reason


def _parse_items(value, key: str, kinds: tuple[str, ...] | None = None) -> list[dict]:
    """항목 목록을 {"item", "verdict", "evidence"(, "kind", "years")} 모양으로 맞춘다. dict가 아닌 원소는 버린다.

    kinds를 주면 kind를 그 중 하나로 맞춘다(모르는 값은 "normal"). 경력 항목은 years(요구 연차)를 숫자로 읽는다.

    Raises:
        ValueError: 값이 목록이 아닐 때(없으면 빈 목록).
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} 값이 목록이 아님: {value!r}")
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        item = {
            "item": str(raw.get("item") or ""),
            "verdict": _normalize_verdict(raw.get("verdict")),
            "evidence": str(raw.get("evidence") or ""),
        }
        if kinds:
            kind = str(raw.get("kind") or "").strip().lower()
            item["kind"] = kind if kind in kinds else "normal"
            if item["kind"] == "career":
                item["years"] = _positive_number(raw.get("years"))
        items.append(item)
    return items


def _positive_number(value) -> float | None:
    """양수면 float, 아니면 None ("1.5" → 1.5)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _apply_career_credit(items: list[dict], career: dict) -> None:
    """경력 항목에 credit = min(1, 실무 개월 ÷ 요구 개월)을 붙인다. 실무 개월이나 요구 연차를 모르면 판정 그대로."""
    months = float(career.get("months") or 0)
    for item in items:
        if item.get("kind") == "career" and item.get("years") and months > 0:
            item["credit"] = min(1.0, months / (item["years"] * 12))


def _category_rate(items: list[dict], weights: dict[str, float]) -> float | None:
    """카테고리 충족률 = Σ(값×가중치) / Σ(가중치). N은 빼고, 판정 가능한 항목이 없으면 None.

    경력 항목처럼 credit이 붙은 항목은 판정 대신 credit을 값으로 쓴다.
    """
    kind_weight = {
        "normal": 1.0,
        "department": float(weights["department_preferred"]),
        "vague": float(weights["vague_preferred"]),
    }
    earned = total = 0.0
    for item in items:
        value = item["credit"] if "credit" in item else _VERDICT_VALUES.get(item["verdict"])
        if value is None:
            continue
        weight = kind_weight.get(item.get("kind", "normal"), 1.0)
        earned += value * weight
        total += weight
    return earned / total if total > 0 else None


def _compute_fit_score(
    data: dict, weights: dict[str, float], career: dict | None = None,
) -> tuple[int | None, dict]:
    """LLM 판정(관문·항목·방향) → (점수, fit_detail).

    상태: 관문에 fail → "ineligible", info_sufficient가 false이거나 필수·업무에 판정 가능한 항목이
    하나도 없으면 → "insufficient", 그 외 "scored". ineligible·insufficient는 점수 None.
    점수 = Σ(카테고리 충족률 × CATEGORY_POINTS) — 충족률이 None인 카테고리는 빼고 남은 배점으로 100점 환산.

    Args:
        data: LLM 답 JSON.
        weights: 우대 kind별 가중치 (analysis/preferences.py::weights_of).
        career: 경력 설정 {"months", "max_years"} (analysis/preferences.py::career_of). None이면 기본값.
            경력 항목은 months ÷ (요구 연차×12)로 점수를 주고, 요구 연차가 max_years를 넘으면 관문 fail.

    Returns:
        (0~100 정수 또는 None, fit_detail dict).

    Raises:
        ValueError: 판정(gate·required·preferred·tasks)이 하나도 없거나 항목 값이 목록이 아닐 때.
    """
    if not any(key in data for key in ("gate", *CATEGORY_POINTS)):
        raise ValueError("판정 없음")

    categories = {
        "required": _parse_items(data.get("required"), "required", kinds=_REQUIRED_KINDS),
        "preferred": _parse_items(data.get("preferred"), "preferred", kinds=_PREFERRED_KINDS),
        "tasks": _parse_items(data.get("tasks"), "tasks"),
    }
    career = career if career is not None else career_of(None)
    _apply_career_credit(categories["required"], career)
    rates = {key: _category_rate(items, weights) for key, items in categories.items()}

    raw_gate = data.get("gate") if isinstance(data.get("gate"), dict) else {}
    gate = {key: _normalize_gate_value(raw_gate.get(key)) for key in GATE_KEYS}
    gate["fail_reason"] = str(raw_gate.get("fail_reason") or "")
    # 허용 경력을 넘는 요구 연차는 LLM 판정과 무관하게 지원 불가
    max_years = career.get("max_years")
    over = [i for i in categories["required"]
            if i.get("kind") == "career" and i.get("years") and max_years and i["years"] > max_years]
    if over:
        gate["career"] = "fail"
        gate["fail_reason"] = gate["fail_reason"] or over[0]["item"]

    info_sufficient = data.get("info_sufficient")
    if isinstance(info_sufficient, str):
        info_sufficient = info_sufficient.strip().lower() != "false"

    if any(gate[key] == "fail" for key in GATE_KEYS):
        status = "ineligible"
    elif info_sufficient is False or (rates["required"] is None and rates["tasks"] is None):
        status = "insufficient"
    else:
        status = "scored"

    total: int | None = None
    if status == "scored":
        possible = sum(CATEGORY_POINTS[k] for k, rate in rates.items() if rate is not None)
        earned = sum(rate * CATEGORY_POINTS[k] for k, rate in rates.items() if rate is not None)
        total = round(earned * 100 / possible) if possible else None

    direction, direction_reason = _normalize_direction(data.get("direction"))
    detail = {
        "position": str(data.get("position") or ""),
        "status": status,
        "gate": gate,
        **categories,
        "direction": direction,
        "direction_reason": direction_reason,
        "rates": rates,
        "weak_points": str(data.get("weak_points") or ""),
    }
    return total, detail


def score_fit(
    jd_text: str,
    profile_text: str,
    preferences_text: str = "",
    weights: dict[str, float] | None = None,
    career: dict | None = None,
) -> dict:
    """JD × 지원자 사실 × 지원 조건·선호 → fit_score(코드 계산) + fit_reasons + fit_detail.

    profile_text는 analysis/profile.py::build_profile_text(), preferences_text는
    analysis/preferences.py::build_preferences_text()가 만든다. 선호가 없으면 빈 문자열이어도 된다.

    Args:
        jd_text: 공고 본문.
        profile_text: [지원자 사실] 블록 텍스트.
        preferences_text: [지원 조건·선호] 블록 텍스트.
        weights: 우대 kind별 가중치. None이면 기본값(weights_of(None)).
        career: 경력 비율 점수 설정. None이면 기본값(career_of(None) — 비율 계산 없이 판정 그대로).

    Returns:
        {"fit_score": int | None, "fit_reasons": str, "fit_detail": dict}. 지원 불가·판단 불가면 fit_score None.

    Raises:
        ValueError: 답을 해석하지 못했을 때. 저장하면 '이미 채점함'이 돼 다시 채점되지 않으므로
            호출자(analyze_job)는 저장하지 않고 다음 실행에 다시 시도한다.
    """
    response = build_fit_chain().invoke({
        "jd_text": jd_text,
        "profile_text": profile_text or "",
        "preferences_text": preferences_text or "",
    })
    data = _parse_json(_response_text(response))
    total, detail = _compute_fit_score(
        data, weights if weights is not None else weights_of(None), career,
    )
    return {
        "fit_score": total,
        # 맞는 점. 아쉬운 점은 fit_detail["weak_points"]
        "fit_reasons": str(data.get("good_points") or data.get("fit_reasons") or ""),
        "fit_detail": detail,
    }


# ── JD 요약 (공고당 1회) ────────────────────────────────────────────────

def _format_career_level_hints(hints: dict | None) -> str:
    """크롤러가 채운 hints.positions[].career_level을 프롬프트용 문자열로."""
    if not hints:
        return "(없음)"
    levels = [p.get("career_level") for p in hints.get("positions", []) if p.get("career_level")]
    return ", ".join(levels) if levels else "(없음)"


def _parse_eligibility(data: dict) -> dict:
    """eligibility 필드를 안전하게 파싱한다. 키가 없거나 타입이 안 맞으면 해당 값만 None/빈 문자열."""
    raw = data.get("eligibility")
    raw = raw if isinstance(raw, dict) else {}
    new_grad_ok = raw.get("new_grad_ok")
    min_years = raw.get("min_years")
    return {
        "new_grad_ok": new_grad_ok if isinstance(new_grad_ok, bool) else None,
        # bool은 int의 서브클래스라 isinstance(True, int)가 True이므로 별도로 먼저 배제
        "min_years": min_years if isinstance(min_years, int) and not isinstance(min_years, bool) else None,
        "evidence": str(raw.get("evidence") or ""),
    }


_KST = timezone(timedelta(hours=9))


def _parse_deadline(value) -> datetime | None:
    """요약의 deadline 문자열(한국 시간) → UTC datetime. 시간이 없으면 그날 23:59. 형식이 다르면 None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt, has_time in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%d", False)):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if not has_time:
            parsed = parsed.replace(hour=23, minute=59)
        return parsed.replace(tzinfo=_KST).astimezone(timezone.utc)
    return None


def summarize_jd(jd_text: str, hints: dict | None = None) -> dict:
    """JD 원문(+선택적 채용구분 힌트) → 카드용 요약 + 신입 지원 가능 여부(eligibility) + 근무지(locations).

    hints는 크롤러가 채운 {"positions": [{"career_level": str, ...}]} 형태(자소설/잡코리아 등).
    JD 원문이 우선 근거이며, 힌트와 충돌하면 원문을 따르고 그 사실을 eligibility.evidence에 남기도록
    프롬프트에 지시한다. 알림 카드(제목·고용형태·'하는 일')와 상세 분석에 사용한다.
    deadline은 본문에 적힌 서류 마감(UTC datetime, 없으면 None) — 목록에 마감일이 없는 사이트(인디스워크)용.
    파싱 실패 시 빈 구조를 반환해 카드에서 해당 블록이 생략되게 한다.
    """
    response = build_summary_chain().invoke({
        "jd_text": jd_text,
        "hints_text": _format_career_level_hints(hints),
    })
    data = _parse_json(_response_text(response))

    def _str_list(key: str, cap: int) -> list[str]:
        val = data.get(key)
        return [str(v) for v in val][:cap] if isinstance(val, list) else []

    employment_type = data.get("employment_type")

    return {
        "tasks": _str_list("tasks", 4),
        "stack": _str_list("stack", 12),
        "employment_type": str(employment_type) if employment_type else "",
        "qualifications": _str_list("qualifications", 5),
        "preferred": _str_list("preferred", 5),
        "eligibility": _parse_eligibility(data),
        "locations": _str_list("locations", 10),
        "deadline": _parse_deadline(data.get("deadline")),
    }


# ── company 점수: DART 규칙 기반 ────────────────────────────────────────

def score_company_from_dart(financials: dict) -> tuple[int, str]:
    """DART 재무 데이터 → company_score (0-100) + 근거 요약."""
    score = 50  # 중립 기준

    op_profit = financials.get("op_profit")
    debt_ratio = financials.get("debt_ratio")
    revenue = financials.get("revenue")
    employees = financials.get("employees")

    reasons = []

    # 영업이익: 흑자 여부
    if op_profit is not None:
        if op_profit > 0:
            score += 15
            reasons.append("흑자 기업")
        else:
            score -= 20
            reasons.append("영업손실")

    # 부채비율: 낮을수록 안정
    if debt_ratio is not None:
        if debt_ratio < 100:
            score += 15
            reasons.append(f"부채비율 {debt_ratio:.0f}% (안정)")
        elif debt_ratio < 200:
            score += 5
            reasons.append(f"부채비율 {debt_ratio:.0f}% (보통)")
        else:
            score -= 15
            reasons.append(f"부채비율 {debt_ratio:.0f}% (높음)")

    # 규모: 직원 수
    if employees is not None:
        if employees >= 100:
            score += 10
            reasons.append(f"직원 {employees}명 (중견+)")
        elif employees >= 20:
            score += 5
            reasons.append(f"직원 {employees}명 (소규모)")
        else:
            reasons.append(f"직원 {employees}명 (극소규모)")

    # 1인당 매출 (생산성)
    if revenue and employees and employees > 0:
        per_person = revenue / employees
        if per_person >= 5_0000_0000:  # 5억+
            score += 10
            reasons.append("1인당 매출 높음")

    summary = " / ".join(reasons) if reasons else "DART 재무 기준 평가"
    return _clamp(score), summary


# ── company 점수: LLM fallback ──────────────────────────────────────────

def score_company_from_llm(jd_text: str) -> tuple[int, str]:
    """JD 텍스트만으로 company_score 추정 (DART 미등록 fallback)."""
    response = build_company_chain().invoke({"jd_text": jd_text})
    data = _parse_json(_response_text(response))
    score = _clamp(data.get("company_score", 50))
    summary = str(data.get("company_summary", "JD 기반 추정"))
    return score, f"[추정] {summary}"
