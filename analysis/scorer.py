"""공고 적합도 + 회사 점수 계산.

fit_score  : LLM (JD × 지원자 프로필 — users.basic_info + experiences)
company_score: 당분간 비활성화 — 수집→fit 계산→추천 흐름을 먼저 완성한다.
  계산 함수(score_company_from_dart/llm)는 재활성화 대비 그대로 남겨뒀다.
  현재 어디서도 호출하지 않는다 (scheduler/jobs.py::analyze_job()은 score_fit()만 사용).
"""
import json
import logging
import re

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from analysis.gemini_client import get_llm

logger = logging.getLogger(__name__)

# ── 프롬프트 ────────────────────────────────────────────────────────────

# fit 항목별 배점(2026-10-14 결정). LLM은 항목 점수만 매기고 총점은 _compute_fit_score()가 계산한다.
#   direction      B1 직무 방향 — 담당 업무가 희망직무와 같은 일인가
#   required_stack B3 필수 기술 스택
#   experience     B4 업무 경험 근거
#   preferred      B5 우대사항 (공고에 없으면 null → 나머지를 100점으로 환산)
#   evidence       C1~C3 근거 강도 — 경험 종류·최근성·성과 수치
#   domain         D1 도메인
# 점수 구간: 80~ 지원 강력 추천 / 60~79 지원 고려 / 40~59 약함 / ~39 비추천·관문 탈락
FIT_SCORE_MAX = {
    "direction": 30,
    "required_stack": 15,
    "experience": 20,
    "preferred": 20,
    "evidence": 10,
    "domain": 5,
}
# A1~A4 관문(학력·경력·필수 어학/자격·전공) 중 하나라도 fail이면 총점 상한
FIT_GATE_FAIL_CAP = 30
_GATE_KEYS = ("education", "career", "language_cert", "major")

_FIT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """당신은 신입 채용 공고와 지원자 프로필의 적합도를 평가하는 채용 담당자입니다.
정해진 절차와 배점에 따라 프로필에 적힌 사실만 근거로 채점합니다.
총점은 계산하지 않고 항목별 점수만 매깁니다.
유효한 JSON 하나만 출력하고, 마크다운 코드블록이나 다른 설명은 붙이지 않습니다."""),
    ("human", """
채용 공고 JD:
{jd_text}

지원자 프로필:
{profile_text}

## 채점 절차

1. 직무 선택 (position)
   공고에 모집 직무가 여러 개면 지원자 희망직무와 가장 잘 맞는 직무 하나를 고르고, 아래 모든 판단은 그 직무 기준으로 한다.
   position은 알림 카드 제목에 "[회사] 직무명"으로 쓰이는 짧은 직무명(30자 이내)이다.
   공고에 적힌 직무명·업무 표현을 바탕으로 쓰고, 지원자 희망직무 이름을 쓰지 않는다.
   번호, 문장 어미, 회사명, "채용"·"모집"·"신입" 같은 말은 뺀다.
   예: "1. IT 서비스 시스템 운영 및 개발 업무를 담당해요." → "IT 서비스 운영·개발"
       "[데이블] Jr. ML Engineer 머신러닝 엔지니어 채용 (신입)" → "Jr. ML Engineer"

2. 지원 자격 관문 (gate) — 각 항목을 "pass" / "fail" / "unknown" 중 하나로 판정
   - education: 학력·졸업(예정) 시기 조건
   - career: 경력 요건 (신입·경력무관이면 pass, 필수 경력 연차가 있으면 fail)
   - language_cert: 필수 어학 점수·필수 자격증
   - major: 전공 제한 (복수전공 포함해 판단)
   "fail"은 공고가 명시한 조건을 프로필이 확실히 충족하지 못할 때만 쓴다.
   공고에 해당 조건이 없거나 판단할 수 없으면 "unknown".
   fail이 있으면 fail_reason에 그 조건의 공고 원문을 적는다(없으면 빈 문자열).

3. 항목별 점수 (scores) — 정수, 범위를 넘기지 않는다
   - direction (0-30): 담당 업무가 희망직무와 얼마나 같은 일인가.
     같은 일 25-30 / 일부 겹침 10-24 / 다른 직군(영업·법무·QA·ERP 운영 등) 0-9
   - required_stack (0-15): 필수 기술·도구 중 프로필에 사용 근거가 있는 비율.
     필수 기술이 명시되지 않았으면 담당 업무 수행에 필요한 기술 기준. 그것도 판단할 수 없으면 7 이하
   - experience (0-20): 담당 업무와 같거나 비슷한 일을 실제로 해본 근거
   - preferred (0-20 또는 null): 우대사항 중 프로필로 충족하는 비율. 공고에 우대사항이 없으면 null.
     "유사업무 경험", "관련 자격증"처럼 구체적이지 않은 우대사항은 충족해도 절반만 인정
   - evidence (0-10): 위 근거들의 강도.
     인턴 실무 > 본인 주도 프로젝트 > 팀 프로젝트 > 교육 실습 순으로 강하고,
     최근 경험일수록, 성과가 수치로 있을수록 높게
   - domain (0-5): 회사·서비스 분야와 지원자 경험 분야가 겹치는 정도

4. 근거 규칙
   - 프로필에 적힌 내용만 근거로 쓴다. 적혀 있지 않은 경험·기술을 있다고 가정하지 않는다.
   - 공고에 적힌 내용만 요건으로 본다. 요건·우대사항이 적혀 있지 않다고 해서 "모두 충족"으로 보지 않는다.
   - 공고 정보가 적을수록 보수적으로 채점한다. 담당 업무가 한두 줄로 구체적이지 않으면
     direction은 20 이하, experience는 12 이하로 준다.
   - 비슷해 보이는 기술을 같은 것으로 보지 않는다 (예: Python 경험은 Java 경험이 아니다).
   - 교육 실습으로만 다룬 기술은 실무·프로젝트 근거보다 낮게 본다.

## 출력
- matched: 프로필이 충족하는 요건·우대사항 (공고 표현 그대로, 최대 6개)
- missing: 프로필이 충족하지 못하는 필수 요건·우대사항 (최대 6개)
- fit_reasons: 알림 카드에 그대로 보이는 한 줄. 한국어 한 문장, 50자 이내, "[맞는 점], [부족한 점]" 형식.
  "지원자는" 같은 주어와 총점은 쓰지 않는다. 부족한 점이 없으면 맞는 점만 쓴다.
  예: "LLM 에이전트·GCP 경험이 우대사항과 일치, Java는 부족"

아래 JSON 형식으로만 답하세요:
{{"position": "<직무>", "gate": {{"education": "pass|fail|unknown", "career": "pass|fail|unknown", "language_cert": "pass|fail|unknown", "major": "pass|fail|unknown", "fail_reason": "<원문 또는 빈 문자열>"}}, "scores": {{"direction": <0-30>, "required_stack": <0-15>, "experience": <0-20>, "preferred": <0-20 또는 null>, "evidence": <0-10>, "domain": <0-5>}}, "matched": ["<요건>"], "missing": ["<요건>"], "fit_reasons": "<요약>"}}
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

아래 JSON 형식으로만 답하세요:
{{"tasks": ["<업무>"], "stack": ["<기술>"], "employment_type": "<고용형태>", "qualifications": ["<자격요건>"], "preferred": ["<우대사항>"], "eligibility": {{"new_grad_ok": <true|false|null>, "min_years": <정수|null>, "evidence": "<원문 문장>"}}, "locations": ["<근무지>"]}}
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


# ── fit 점수 (LLM) ──────────────────────────────────────────────────────

def _str_items(value, cap: int = 6) -> list[str]:
    return [str(v) for v in value][:cap] if isinstance(value, list) else []


def _compute_fit_score(data: dict) -> tuple[int, dict]:
    """LLM 답(항목 점수·관문) → (총점, fit_detail).

    항목 점수를 배점 범위로 맞추고 합산한다. preferred가 null이면 나머지 만점(80) 기준으로 100점 환산,
    관문에 fail이 하나라도 있으면 FIT_GATE_FAIL_CAP으로 상한을 둔다.

    Raises:
        ValueError: scores가 없거나, preferred 외 항목이 빠졌거나 숫자가 아닐 때.
    """
    raw_scores = data.get("scores")
    if not isinstance(raw_scores, dict):
        raise ValueError("scores 없음")

    scores: dict[str, int | None] = {}
    for key, max_point in FIT_SCORE_MAX.items():
        value = raw_scores.get(key)
        if value is None and key == "preferred":
            scores[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"scores.{key} 값이 숫자가 아님: {value!r}")
        scores[key] = _clamp(value, 0, max_point)

    earned = sum(v for v in scores.values() if v is not None)
    possible = sum(FIT_SCORE_MAX[k] for k, v in scores.items() if v is not None)
    total = round(earned * 100 / possible)

    raw_gate = data.get("gate") if isinstance(data.get("gate"), dict) else {}
    gate = {key: str(raw_gate.get(key) or "unknown") for key in _GATE_KEYS}
    gate["fail_reason"] = str(raw_gate.get("fail_reason") or "")
    if any(gate[key] == "fail" for key in _GATE_KEYS):
        total = min(total, FIT_GATE_FAIL_CAP)

    detail = {
        "position": str(data.get("position") or ""),
        "gate": gate,
        "scores": scores,
        "matched": _str_items(data.get("matched")),
        "missing": _str_items(data.get("missing")),
    }
    return total, detail


def score_fit(jd_text: str, profile_text: str) -> dict:
    """JD × 지원자 프로필 → fit_score(코드가 계산한 총점) + fit_reasons + fit_detail.

    profile_text는 analysis/profile.py::build_profile_text()가 users.basic_info + experiences로 만든다.

    Raises:
        ValueError: 답을 해석하지 못했을 때. 0점으로 저장하면 '이미 채점함'이 돼 다시 채점되지 않으므로
            호출자(analyze_job)는 저장하지 않고 다음 실행에 다시 시도한다.
    """
    response = build_fit_chain().invoke({
        "jd_text": jd_text,
        "profile_text": profile_text or "",
    })
    data = _parse_json(_response_text(response))
    total, detail = _compute_fit_score(data)
    return {
        "fit_score": total,
        "fit_reasons": str(data.get("fit_reasons") or ""),
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


def summarize_jd(jd_text: str, hints: dict | None = None) -> dict:
    """JD 원문(+선택적 채용구분 힌트) → 카드용 요약 + 신입 지원 가능 여부(eligibility) + 근무지(locations).

    hints는 크롤러가 채운 {"positions": [{"career_level": str, ...}]} 형태(자소설/잡코리아 등).
    JD 원문이 우선 근거이며, 힌트와 충돌하면 원문을 따르고 그 사실을 eligibility.evidence에 남기도록
    프롬프트에 지시한다. 알림 카드(제목·고용형태·'하는 일')와 상세 분석에 사용한다.
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
