"""score_fit — 항목별 점수(LLM) → 총점(코드) 계산 테스트. LLM은 mock."""
import json
from unittest.mock import MagicMock, patch

import pytest

from analysis.scorer import FIT_GATE_FAIL_CAP, FIT_SCORE_MAX, score_fit


def _chain(content) -> MagicMock:
    response = MagicMock()
    response.content = content
    chain = MagicMock()
    chain.invoke.return_value = response
    return chain


def _answer(**overrides) -> dict:
    data = {
        "position": "AI 서비스 개발",
        "gate": {"education": "pass", "career": "pass", "language_cert": "unknown", "major": "pass",
                 "fail_reason": ""},
        "scores": {"direction": 24, "required_stack": 6, "experience": 14, "preferred": 12,
                   "evidence": 8, "domain": 2},
        "matched": ["LLM·AI Agent 구축", "Public Cloud(GCP)"],
        "missing": ["Java"],
        "fit_reasons": "AI 서비스 개발 직무 기준. LLM 경험 일치, Java 근거 없음.",
    }
    data.update(overrides)
    return data


def _score(data, profile_text="프로필") -> dict:
    content = data if isinstance(data, (str, list)) else json.dumps(data, ensure_ascii=False)
    with patch("analysis.scorer.build_fit_chain", return_value=_chain(content)):
        return score_fit(jd_text="JD", profile_text=profile_text)


def test_fit_score_is_sum_of_item_scores_computed_in_code():
    result = _score(_answer())
    assert result["fit_score"] == 24 + 6 + 14 + 12 + 8 + 2
    assert result["fit_reasons"] == "AI 서비스 개발 직무 기준. LLM 경험 일치, Java 근거 없음."


def test_fit_detail_keeps_position_gate_items_and_match_lists():
    detail = _score(_answer())["fit_detail"]
    assert detail["position"] == "AI 서비스 개발"
    assert detail["gate"]["language_cert"] == "unknown"
    assert detail["scores"]["direction"] == 24
    assert detail["matched"] == ["LLM·AI Agent 구축", "Public Cloud(GCP)"]
    assert detail["missing"] == ["Java"]


def test_item_scores_are_clamped_to_their_max():
    """배점보다 큰 값(direction 40 → 30)과 음수(→ 0)는 범위 안으로 맞춘다."""
    scores = {"direction": 40, "required_stack": -3, "experience": 20, "preferred": 20, "evidence": 10, "domain": 5}
    result = _score(_answer(scores=scores))
    assert result["fit_detail"]["scores"]["direction"] == FIT_SCORE_MAX["direction"]
    assert result["fit_detail"]["scores"]["required_stack"] == 0
    assert result["fit_score"] == 30 + 0 + 20 + 20 + 10 + 5


def test_missing_preferred_section_rescales_remaining_80_points_to_100():
    """공고에 우대사항이 없으면 preferred=null — 나머지 80점 만점을 100점으로 환산한다."""
    scores = {"direction": 30, "required_stack": 15, "experience": 20, "preferred": None, "evidence": 10, "domain": 5}
    assert _score(_answer(scores=scores))["fit_score"] == 100
    half = {"direction": 15, "required_stack": 8, "experience": 10, "preferred": None, "evidence": 5, "domain": 2}
    assert _score(_answer(scores=half))["fit_score"] == round(40 * 100 / 80)


def test_gate_fail_caps_total_score():
    gate = {"education": "pass", "career": "fail", "language_cert": "pass", "major": "pass",
            "fail_reason": "경력 3년 이상 필수"}
    scores = {"direction": 30, "required_stack": 15, "experience": 20, "preferred": 20, "evidence": 10, "domain": 5}
    result = _score(_answer(gate=gate, scores=scores))
    assert result["fit_score"] == FIT_GATE_FAIL_CAP
    assert result["fit_detail"]["gate"]["fail_reason"] == "경력 3년 이상 필수"


def test_unknown_gate_does_not_cap():
    gate = {"education": "unknown", "career": "unknown", "language_cert": "unknown", "major": "unknown"}
    scores = {"direction": 30, "required_stack": 15, "experience": 20, "preferred": 20, "evidence": 10, "domain": 5}
    assert _score(_answer(gate=gate, scores=scores))["fit_score"] == 100


@pytest.mark.parametrize("content", [
    "분석할 수 없습니다.",
    json.dumps({"fit_reasons": "scores 없음"}),
    json.dumps(_answer(scores={"direction": "높음", "required_stack": 6, "experience": 14,
                               "preferred": 12, "evidence": 8, "domain": 2})),
    json.dumps(_answer(scores={"direction": 24})),
])
def test_unparseable_answer_raises_so_nothing_is_saved(content):
    """해석 못 한 답을 0점으로 저장하면 '이미 채점함'이 돼 다시 채점되지 않는다 — 예외로 알려 저장을 막는다."""
    with pytest.raises(ValueError):
        _score(content)


def test_reads_first_complete_json_when_model_appends_trailing_garbage():
    """2026-10-14 실제 응답: 올바른 JSON 뒤에 `, "scores": {...}}` 꼬리를 한 번 더 붙였다."""
    text = json.dumps(_answer(), ensure_ascii=False)
    tail = ', "scores": {"direction": 1, "required_stack": 1, "experience": 1, "preferred": 1, "evidence": 1, "domain": 1}}'
    assert _score(text + tail)["fit_score"] == 66
    assert _score("결과입니다:\n" + text + "\n끝")["fit_score"] == 66


def test_reads_gemini3_content_blocks():
    blocks = [{"type": "text", "text": json.dumps(_answer(), ensure_ascii=False), "extras": {"signature": "x"}}]
    assert _score(blocks)["fit_score"] == 66


def test_sends_jd_and_profile_text_to_llm():
    chain = _chain(json.dumps(_answer()))
    with patch("analysis.scorer.build_fit_chain", return_value=chain):
        score_fit(jd_text="JD 본문", profile_text="[기본 정보]\n병역: 비대상")
    assert chain.invoke.call_args.args[0] == {"jd_text": "JD 본문", "profile_text": "[기본 정보]\n병역: 비대상"}
