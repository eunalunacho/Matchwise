"""스코어러 유닛 테스트 — LLM/DART 모두 mock 처리."""
from unittest.mock import MagicMock, patch

from analysis.scorer import (
    score_company_from_dart,
    score_company_from_llm,
    summarize_jd,
)


# ── LLM mock (score_fit 테스트는 tests/test_fit_scoring.py) ───────────────

def _mock_fit_chain(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    chain = MagicMock()
    chain.invoke.return_value = resp
    return chain


# ── score_company_from_dart (규칙 기반) ─────────────────────────────────

def test_dart_score_profitable_stable():
    financials = {
        "op_profit": 5_000_000_000,   # 50억 흑자
        "debt_ratio": 80.0,           # 안정
        "employees": 200,
        "revenue": 50_000_000_000,    # 500억
    }
    score, summary = score_company_from_dart(financials)
    assert score > 60
    assert "흑자" in summary


def test_dart_score_loss_high_debt():
    financials = {
        "op_profit": -1_000_000_000,  # 적자
        "debt_ratio": 350.0,          # 위험
        "employees": 5,
        "revenue": None,
    }
    score, summary = score_company_from_dart(financials)
    assert score < 50
    assert "영업손실" in summary


def test_dart_score_missing_fields():
    """일부 필드 없어도 계산 가능."""
    score, summary = score_company_from_dart({})
    assert 0 <= score <= 100


def test_dart_score_clamped_to_range():
    financials = {
        "op_profit": 999_999_999_999,
        "debt_ratio": 1.0,
        "employees": 10000,
        "revenue": 100_000_000_000_000,
    }
    score, _ = score_company_from_dart(financials)
    assert 0 <= score <= 100


# ── score_company_from_llm (LLM fallback) ───────────────────────────────

def test_llm_company_score_valid_range():
    chain = MagicMock()
    chain.invoke.return_value = MagicMock(
        content='{"company_score": 70, "company_summary": "성장 중인 스타트업"}'
    )
    with patch("analysis.scorer.build_company_chain", return_value=chain):
        score, summary = score_company_from_llm("Python 백엔드 스타트업 시리즈 B")
    assert 0 <= score <= 100
    assert "[추정]" in summary


def test_llm_company_score_malformed_fallback():
    chain = MagicMock()
    chain.invoke.return_value = MagicMock(content="알 수 없음")
    with patch("analysis.scorer.build_company_chain", return_value=chain):
        score, summary = score_company_from_llm("JD 텍스트")
    assert 0 <= score <= 100  # 기본값 50


# ── summarize_jd (LLM mock) ─────────────────────────────────────────────

_EMPTY_ELIGIBILITY = {"new_grad_ok": None, "min_years": None, "evidence": ""}


def test_summarize_jd_parses_full_response():
    chain = _mock_fit_chain(
        '{"tasks": ["RAG 구축", "Agent 설계"], "stack": ["Python", "LangGraph"],'
        ' "employment_type": "정규직", "qualifications": ["경력 0~3년", "Python 능숙"],'
        ' "preferred": ["LangGraph 경험"],'
        ' "eligibility": {"new_grad_ok": true, "min_years": null, "evidence": "신입 지원 가능 명시"},'
        ' "locations": ["서울 강남구", "경기 성남시 분당구 판교역로"]}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("긴 JD 텍스트")

    assert result["tasks"] == ["RAG 구축", "Agent 설계"]
    assert result["stack"] == ["Python", "LangGraph"]
    assert result["employment_type"] == "정규직"
    assert result["qualifications"] == ["경력 0~3년", "Python 능숙"]
    assert result["preferred"] == ["LangGraph 경험"]
    assert result["eligibility"] == {
        "new_grad_ok": True, "min_years": None, "evidence": "신입 지원 가능 명시",
    }
    assert result["locations"] == ["서울 강남구", "경기 성남시 분당구 판교역로"]


def test_summarize_jd_caps_tasks_at_four():
    chain = _mock_fit_chain(
        '{"tasks": ["a", "b", "c", "d", "e", "f"], "stack": [],'
        ' "employment_type": "", "qualifications": [], "preferred": [], "eligibility": {}}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("JD")

    assert len(result["tasks"]) == 4


def test_summarize_jd_malformed_returns_empty_structure():
    chain = _mock_fit_chain("이건 JSON이 아님")
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("JD")

    assert result == {
        "tasks": [], "stack": [], "employment_type": "",
        "qualifications": [], "preferred": [], "eligibility": _EMPTY_ELIGIBILITY,
        "locations": [],
    }


def test_summarize_jd_missing_eligibility_key_defaults_to_empty():
    """eligibility 키 자체가 없는 응답(구버전 프롬프트 캐시 등)도 안전하게 처리한다."""
    chain = _mock_fit_chain(
        '{"tasks": [], "stack": [], "employment_type": "", "qualifications": [], "preferred": []}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("JD")

    assert result["eligibility"] == _EMPTY_ELIGIBILITY
    assert result["locations"] == []  # 근무지 필드가 없던 구버전 응답도 빈 목록


def test_summarize_jd_eligibility_with_wrong_types_falls_back():
    """new_grad_ok/min_years가 기대 타입이 아니면(문자열 등) None으로 안전 처리한다."""
    chain = _mock_fit_chain(
        '{"tasks": [], "stack": [], "employment_type": "", "qualifications": [], "preferred": [],'
        ' "eligibility": {"new_grad_ok": "아마도", "min_years": "3년", "evidence": "불명확"}}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("JD")

    assert result["eligibility"] == {"new_grad_ok": None, "min_years": None, "evidence": "불명확"}


def test_summarize_jd_without_hints_still_works():
    """hints를 안 주면(기본값) 힌트 없이 요청한다 — 기존 호출부 하위호환."""
    chain = _mock_fit_chain(
        '{"tasks": [], "stack": [], "employment_type": "", "qualifications": [], "preferred": [], "eligibility": {}}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("JD")  # hints 인자 생략

    assert result["eligibility"] == _EMPTY_ELIGIBILITY


def test_summarize_jd_passes_career_level_hints_into_prompt():
    """hints.positions의 career_level을 프롬프트 입력(hints_text)으로 넘긴다."""
    chain = _mock_fit_chain(
        '{"tasks": [], "stack": [], "employment_type": "", "qualifications": [], "preferred": [], "eligibility": {}}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        summarize_jd("JD", hints={"positions": [{"career_level": "신입"}, {"career_level": "신입/인턴"}]})

    invoked_with = chain.invoke.call_args[0][0]
    assert invoked_with["hints_text"] == "신입, 신입/인턴"


def test_summarize_jd_empty_hints_produces_placeholder_text():
    chain = _mock_fit_chain(
        '{"tasks": [], "stack": [], "employment_type": "", "qualifications": [], "preferred": [], "eligibility": {}}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        summarize_jd("JD", hints=None)

    invoked_with = chain.invoke.call_args[0][0]
    assert invoked_with["hints_text"] == "(없음)"


# ── Gemini 3 응답 형식: content가 블록 목록 (2026-10-14 실제 실행에서 발견) ──────────

def test_summarize_jd_joins_multiple_text_blocks_and_skips_non_text():
    chain = _mock_fit_chain([
        {"type": "thinking", "thinking": "{\"tasks\": [\"가짜\"]}"},
        {"type": "text", "text": '{"tasks": ["RAG 구축"], "stack": [],'},
        {"type": "text", "text": ' "employment_type": "", "qualifications": [], "preferred": []}'},
    ])
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("JD")
    assert result["tasks"] == ["RAG 구축"]
