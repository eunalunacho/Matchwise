"""공고 적합도 + 회사 점수 계산.

fit_score  : LLM (JD × 이력서 × 경험)
company_score:
  1순위 - DART 재무 데이터 → 규칙 기반 계산
  2순위 - LLM이 JD 텍스트에서 신호 추출 후 추정
"""
import json
import logging
import re

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from analysis.gemini_client import get_llm

logger = logging.getLogger(__name__)

# ── 프롬프트 ────────────────────────────────────────────────────────────

_FIT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a job fit analyzer. Respond ONLY with valid JSON, no markdown fences."),
    ("human", """
채용 공고 JD:
{jd_text}

지원자 이력서:
{resume_text}

지원자 경험 (협업, 갈등 해결 등):
{experience_text}

아래 JSON 형식으로만 답하세요:
{{"fit_score": <0-100 정수>, "fit_reasons": "<한국어 2-3줄 요약>"}}
"""),
])

_SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are a job posting summarizer. Respond ONLY with valid JSON, no markdown fences."),
    ("human", """
다음 채용 공고 JD에서 아래 정보를 추출하세요. 원문에 있는 내용만 사용하세요.

JD:
{jd_text}

- tasks: 실제 담당하게 될 업무 3~4개 (각 40자 이내, 한국어)
- stack: 명시된 기술 스택 목록 (없으면 빈 배열)
- employment_type: 고용형태 — "정규직", "계약직", "인턴 (6개월)" 같은 형식, 기간 명시 시 포함 (명시 없으면 빈 문자열)
- qualifications: 자격요건 3~5개 (각 50자 이내, 없으면 빈 배열)
- preferred: 우대사항 최대 5개 (각 50자 이내, 없으면 빈 배열)

아래 JSON 형식으로만 답하세요:
{{"tasks": ["<업무>"], "stack": ["<기술>"], "employment_type": "<고용형태>", "qualifications": ["<자격요건>"], "preferred": ["<우대사항>"]}}
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

def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {}


def _clamp(val, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(val)))


# ── fit 점수 (LLM) ──────────────────────────────────────────────────────

def score_fit(
    jd_text: str,
    resume_text: str,
    experience_text: str,
) -> dict:
    """JD × 이력서 × 경험 → fit_score + fit_reasons."""
    response = build_fit_chain().invoke({
        "jd_text": jd_text,
        "resume_text": resume_text or "",
        "experience_text": experience_text or "",
    })
    data = _parse_json(response.content)
    return {
        "fit_score": _clamp(data.get("fit_score", 0)),
        "fit_reasons": str(data.get("fit_reasons", "분석 실패")),
    }


# ── JD 요약 (공고당 1회) ────────────────────────────────────────────────

def summarize_jd(jd_text: str) -> dict:
    """JD 원문 → {"tasks", "stack", "employment_type", "qualifications", "preferred"}.

    알림 카드(제목 고용형태, '하는 일')와 상세 분석('스택'/'자격요건'/'우대사항')에 사용한다.
    파싱 실패 시 빈 구조를 반환해 카드에서 해당 블록이 생략되게 한다.
    """
    response = build_summary_chain().invoke({"jd_text": jd_text})
    data = _parse_json(response.content)

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
    data = _parse_json(response.content)
    score = _clamp(data.get("company_score", 50))
    summary = str(data.get("company_summary", "JD 기반 추정"))
    return score, f"[추정] {summary}"


# ── 최상위 인터페이스 ────────────────────────────────────────────────────

def score_job_for_user(
    jd_text: str,
    company: str,
    resume_text: str,
    experience_text: str,
    dart_client=None,  # DartClient | None (테스트에서 주입 가능)
) -> dict:
    """공고 1개 × 유저 1명 → {fit_score, fit_reasons, company_score, company_score_source}.

    company_score_source: "dart" | "llm"
    """
    fit = score_fit(jd_text, resume_text, experience_text)

    company_score, company_summary = _get_company_score(
        company=company,
        jd_text=jd_text,
        dart_client=dart_client,
    )

    return {
        **fit,
        "company_score": company_score,
        "company_summary": company_summary,
    }


def _get_company_score(
    company: str,
    jd_text: str,
    dart_client=None,
) -> tuple[int, str]:
    """DART 우선, 실패 시 LLM fallback."""
    if dart_client and company:
        try:
            financials = dart_client.get_company_data(company)
            if financials:
                return score_company_from_dart(financials)
        except Exception as e:
            logger.warning("DART 조회 실패 (%s): %s", company, e)

    return score_company_from_llm(jd_text)
