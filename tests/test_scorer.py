"""스코어러 유닛 테스트 — LLM/DART 모두 mock 처리."""
from unittest.mock import MagicMock, patch

from analysis.scorer import (
    score_company_from_dart,
    score_company_from_llm,
    score_fit,
    score_job_for_user,
    summarize_jd,
)


# ── score_fit (LLM mock) ────────────────────────────────────────────────

def _mock_fit_chain(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    chain = MagicMock()
    chain.invoke.return_value = resp
    return chain


def test_score_fit_returns_valid_range():
    chain = _mock_fit_chain('{"fit_score": 82, "fit_reasons": "Python 경험 일치"}')
    with patch("analysis.scorer.build_fit_chain", return_value=chain):
        result = score_fit(
            jd_text="Python 백엔드 개발자. AWS 우대.",
            resume_text="Python 3년, FastAPI, AWS Lambda 경험.",
            experience_text="스타트업 결제 시스템 개발.",
        )
    assert 0 <= result["fit_score"] <= 100
    assert isinstance(result["fit_reasons"], str)
    assert len(result["fit_reasons"]) > 0


def test_score_fit_handles_malformed_json():
    chain = _mock_fit_chain("분석할 수 없습니다.")
    with patch("analysis.scorer.build_fit_chain", return_value=chain):
        result = score_fit(jd_text="", resume_text="", experience_text="")
    assert result["fit_score"] == 0


def test_score_fit_clamps_over_100():
    chain = _mock_fit_chain('{"fit_score": 150, "fit_reasons": "완벽"}')
    with patch("analysis.scorer.build_fit_chain", return_value=chain):
        result = score_fit("jd", "resume", "exp")
    assert result["fit_score"] == 100


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


# ── score_job_for_user (통합) ────────────────────────────────────────────

def test_score_job_uses_dart_when_available():
    """DART 데이터 있으면 dart 경로 사용."""
    fit_chain = _mock_fit_chain('{"fit_score": 75, "fit_reasons": "적합"}')
    mock_dart = MagicMock()
    mock_dart.get_company_data.return_value = {
        "op_profit": 1_000_000_000,
        "debt_ratio": 90.0,
        "employees": 150,
        "revenue": 20_000_000_000,
    }

    with patch("analysis.scorer.build_fit_chain", return_value=fit_chain):
        result = score_job_for_user(
            jd_text="JD",
            company="테스트 주식회사",
            resume_text="이력서",
            experience_text="경험",
            dart_client=mock_dart,
        )

    assert result["fit_score"] == 75
    assert 0 <= result["company_score"] <= 100
    assert "[추정]" not in result["company_summary"]  # DART 경로 (추정 표시 없음)


def test_score_job_falls_back_to_llm_when_dart_missing():
    """DART에서 회사 못 찾으면 LLM fallback."""
    fit_chain = _mock_fit_chain('{"fit_score": 60, "fit_reasons": "보통"}')
    company_chain = MagicMock()
    company_chain.invoke.return_value = MagicMock(
        content='{"company_score": 55, "company_summary": "중간 수준"}'
    )
    mock_dart = MagicMock()
    mock_dart.get_company_data.return_value = None  # 미등록

    with patch("analysis.scorer.build_fit_chain", return_value=fit_chain), \
         patch("analysis.scorer.build_company_chain", return_value=company_chain):
        result = score_job_for_user(
            jd_text="JD",
            company="미등록스타트업",
            resume_text="이력서",
            experience_text="경험",
            dart_client=mock_dart,
        )

    assert "[추정]" in result["company_summary"]  # LLM fallback 표시


def test_score_job_no_dart_client_uses_llm():
    """dart_client=None이면 바로 LLM fallback."""
    fit_chain = _mock_fit_chain('{"fit_score": 50, "fit_reasons": "보통"}')
    company_chain = MagicMock()
    company_chain.invoke.return_value = MagicMock(
        content='{"company_score": 60, "company_summary": "괜찮음"}'
    )

    with patch("analysis.scorer.build_fit_chain", return_value=fit_chain), \
         patch("analysis.scorer.build_company_chain", return_value=company_chain):
        result = score_job_for_user(
            jd_text="JD", company="회사", resume_text="", experience_text="",
            dart_client=None,
        )

    assert "[추정]" in result["company_summary"]


# ── summarize_jd (LLM mock) ─────────────────────────────────────────────

def test_summarize_jd_parses_full_response():
    chain = _mock_fit_chain(
        '{"tasks": ["RAG 구축", "Agent 설계"], "stack": ["Python", "LangGraph"],'
        ' "employment_type": "정규직", "qualifications": ["경력 0~3년", "Python 능숙"],'
        ' "preferred": ["LangGraph 경험"]}'
    )
    with patch("analysis.scorer.build_summary_chain", return_value=chain):
        result = summarize_jd("긴 JD 텍스트")

    assert result["tasks"] == ["RAG 구축", "Agent 설계"]
    assert result["stack"] == ["Python", "LangGraph"]
    assert result["employment_type"] == "정규직"
    assert result["qualifications"] == ["경력 0~3년", "Python 능숙"]
    assert result["preferred"] == ["LangGraph 경험"]


def test_summarize_jd_caps_tasks_at_four():
    chain = _mock_fit_chain(
        '{"tasks": ["a", "b", "c", "d", "e", "f"], "stack": [],'
        ' "employment_type": "", "qualifications": [], "preferred": []}'
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
        "qualifications": [], "preferred": [],
    }
