"""score_fit — 항목 판정(LLM) → 점수·상태(코드) 계산 테스트. LLM은 mock."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from analysis.scorer import (
    CATEGORY_POINTS,
    GATE_KEYS,
    SCORER_VERSION,
    _FIT_PROMPT,
    is_stale_score,
    score_fit,
)


def _chain(content) -> MagicMock:
    response = MagicMock()
    response.content = content
    chain = MagicMock()
    chain.invoke.return_value = response
    return chain


def _items(*verdicts: str, kinds: tuple[str, ...] | None = None) -> list[dict]:
    """판정 문자 목록 → LLM 답의 항목 목록. kinds를 주면 우대 항목 kind를 붙인다."""
    items = []
    for i, verdict in enumerate(verdicts):
        item = {"item": f"항목{i}", "verdict": verdict, "evidence": "근거" if verdict == "O" else ""}
        if kinds is not None:
            item["kind"] = kinds[i]
        items.append(item)
    return items


def _answer(**overrides) -> dict:
    data = {
        "position": "AI 서비스 개발",
        "info_sufficient": True,
        "gate": {"degree": "pass", "graduation": "pass", "career": "pass", "language": "pass",
                 "cert": "unknown", "fail_reason": ""},
        "required": _items("O", "O", "H", "X"),
        "preferred": _items("O", "X", kinds=("normal", "normal")),
        "tasks": _items("O", "O", "O", "H"),
        "direction": {"verdict": "match", "reason": "LLM 서비스 개발"},
        "fit_reasons": "LLM 에이전트 경험 일치, Java는 부족",
    }
    data.update(overrides)
    return data


def _score(data, profile_text="프로필", **kwargs) -> dict:
    content = data if isinstance(data, (str, list)) else json.dumps(data, ensure_ascii=False)
    with patch("analysis.scorer.build_fit_chain", return_value=_chain(content)):
        return score_fit(jd_text="JD", profile_text=profile_text, **kwargs)


# ── 버전·프롬프트 ───────────────────────────────────────────────────────

def test_scorer_version_is_4():
    assert SCORER_VERSION == 4
    assert not is_stale_score(SimpleNamespace(scorer_version=4, profile_version=1), 1)
    assert is_stale_score(SimpleNamespace(scorer_version=3, profile_version=1), 1)


def test_prompt_takes_jd_profile_and_preferences():
    assert set(_FIT_PROMPT.input_variables) == {"jd_text", "profile_text", "preferences_text"}


def test_prompt_has_item_rules_and_no_total_score():
    text = "\n".join(m.prompt.template for m in _FIT_PROMPT.messages)
    for word in ("info_sufficient", "required", "preferred", "tasks", "department", "vague",
                 "evidence", "이런 분을 기다려요", "NCS", "태도", "[지원 조건·선호]", "담당업무"):
        assert word in text
    for key in GATE_KEYS:
        assert key in text
    assert "총점은 계산하지 않" in text


def test_prompt_marks_gate_sentences_as_n():
    """학력·전공·신입/경력 무관·입사 시점 같은 관문 문장은 required·preferred 어디에 있든 N (2026-10-30 결정)."""
    text = "\n".join(m.prompt.template for m in _FIT_PROMPT.messages)
    assert "관문 문장" in text
    for word in ("대학교 졸업 이상", "관련 전공", "신입·경력", "입사 가능"):
        assert word in text


def test_prompt_renders_with_empty_preferences():
    rendered = _FIT_PROMPT.format(jd_text="JD", profile_text="P", preferences_text="")
    assert "JD" in rendered


def test_sends_jd_profile_and_preferences_text_to_llm():
    chain = _chain(json.dumps(_answer()))
    with patch("analysis.scorer.build_fit_chain", return_value=chain):
        score_fit(jd_text="JD 본문", profile_text="[기본 정보]\n병역: 비대상", preferences_text="[지원 조건]\n학위: 학사")
    assert chain.invoke.call_args.args[0] == {
        "jd_text": "JD 본문",
        "profile_text": "[기본 정보]\n병역: 비대상",
        "preferences_text": "[지원 조건]\n학위: 학사",
    }


def test_preferences_text_defaults_to_empty():
    chain = _chain(json.dumps(_answer()))
    with patch("analysis.scorer.build_fit_chain", return_value=chain):
        score_fit("JD", "P")
    assert chain.invoke.call_args.args[0]["preferences_text"] == ""


# ── 점수 계산 ───────────────────────────────────────────────────────────

def test_category_points():
    assert CATEGORY_POINTS == {"required": 50, "preferred": 20, "tasks": 30}
    assert GATE_KEYS == ("degree", "graduation", "career", "language", "cert")


def test_score_is_weighted_sum_of_category_rates():
    result = _score(_answer())
    assert result["fit_score"] == round(0.625 * 50 + 0.5 * 20 + 0.875 * 30) == 68
    detail = result["fit_detail"]
    assert detail["status"] == "scored"
    assert detail["rates"] == {"required": 0.625, "preferred": 0.5, "tasks": 0.875}
    assert result["fit_reasons"] == "LLM 에이전트 경험 일치, Java는 부족"


def test_department_preferred_weighs_quarter():
    data = _answer(preferred=_items("O", "X", kinds=("normal", "department")))
    assert _score(data)["fit_detail"]["rates"]["preferred"] == pytest.approx(0.8)


def test_vague_preferred_weighs_half():
    data = _answer(preferred=_items("X", "O", kinds=("normal", "vague")))
    assert _score(data)["fit_detail"]["rates"]["preferred"] == pytest.approx(0.5 / 1.5)


def test_weights_come_from_argument():
    data = _answer(preferred=_items("O", "X", kinds=("normal", "department")))
    result = _score(data, weights={"department_preferred": 1.0, "vague_preferred": 0.5})
    assert result["fit_detail"]["rates"]["preferred"] == pytest.approx(0.5)


def test_n_verdicts_are_excluded_from_denominator():
    data = _answer(required=_items("O", "N", "N", "X"))
    assert _score(data)["fit_detail"]["rates"]["required"] == pytest.approx(0.5)


def test_all_department_preferred_does_not_divide_by_zero():
    data = _answer(preferred=_items("O", "X", kinds=("department", "department")))
    result = _score(data)
    assert result["fit_detail"]["rates"]["preferred"] == pytest.approx(0.5)
    assert result["fit_score"] is not None


def test_empty_category_rescales_remaining_points():
    data = _answer(preferred=[])
    result = _score(data)
    assert result["fit_detail"]["rates"]["preferred"] is None
    assert result["fit_score"] == round((0.625 * 50 + 0.875 * 30) / 80 * 100)


def test_all_attitude_required_rescales_with_preferred_and_tasks():
    """필수가 전부 태도형(N)이면 필수 카테고리를 빼고 우대·업무 50점을 100점으로 환산한다."""
    data = _answer(required=_items("N", "N"))
    result = _score(data)
    assert result["fit_detail"]["rates"]["required"] is None
    assert result["fit_detail"]["status"] == "scored"
    assert result["fit_score"] == round((0.5 * 20 + 0.875 * 30) / 50 * 100)


def test_full_marks_is_100():
    data = _answer(required=_items("O"), preferred=_items("O", kinds=("normal",)), tasks=_items("O"))
    assert _score(data)["fit_score"] == 100


# ── 상태 ────────────────────────────────────────────────────────────────

def test_gate_fail_marks_ineligible_with_no_score():
    gate = {"degree": "pass", "graduation": "fail", "career": "pass", "language": "pass", "cert": "pass",
            "fail_reason": "26.8월 졸업예정자까지"}
    result = _score(_answer(gate=gate))
    assert result["fit_score"] is None
    assert result["fit_detail"]["status"] == "ineligible"
    assert result["fit_detail"]["gate"]["fail_reason"] == "26.8월 졸업예정자까지"
    assert result["fit_detail"]["gate"]["graduation"] == "fail"


def test_unknown_gate_is_scored():
    gate = {key: "unknown" for key in GATE_KEYS}
    result = _score(_answer(gate=gate))
    assert result["fit_detail"]["status"] == "scored"
    assert result["fit_detail"]["gate"]["fail_reason"] == ""


def test_gate_values_are_normalized():
    gate = {"degree": "PASS", "graduation": " Fail ", "career": "모름", "fail_reason": None}
    detail = _score(_answer(gate=gate))["fit_detail"]
    assert detail["gate"] == {"degree": "pass", "graduation": "fail", "career": "unknown", "language": "unknown",
                              "cert": "unknown", "fail_reason": ""}


@pytest.mark.parametrize("overrides", [
    {"info_sufficient": False},
    {"required": [], "tasks": []},
    {"required": _items("N"), "tasks": _items("N", "N")},
])
def test_insufficient_when_flag_false_or_no_required_and_tasks(overrides):
    result = _score(_answer(**overrides))
    assert result["fit_score"] is None
    assert result["fit_detail"]["status"] == "insufficient"


def test_gate_fail_wins_over_insufficient():
    gate = {"degree": "fail", "fail_reason": "석사 이상"}
    result = _score(_answer(gate=gate, info_sufficient=False))
    assert result["fit_detail"]["status"] == "ineligible"


# ── 정규화 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("O", "O"), ("✓", "O"), ("o", "O"), ("pass", "O"),
    ("H", "H"), ("△", "H"), ("partial", "H"),
    ("X", "X"), ("✗", "X"), ("x", "X"),
    ("N", "N"), ("모름", "N"), ("", "N"), (None, "N"), (1, "N"),
])
def test_verdict_aliases_are_normalized(raw, expected):
    data = _answer(required=[{"item": "Python", "verdict": raw, "evidence": "e"}])
    assert _score(data)["fit_detail"]["required"][0]["verdict"] == expected


def test_unknown_kind_becomes_normal():
    data = _answer(preferred=[{"item": "a", "verdict": "O", "kind": "weird", "evidence": "e"},
                              {"item": "b", "verdict": "X"}])
    preferred = _score(data)["fit_detail"]["preferred"]
    assert [p["kind"] for p in preferred] == ["normal", "normal"]


@pytest.mark.parametrize("raw, expected", [
    ({"verdict": "match", "reason": "r"}, "match"),
    ({"verdict": "Partial"}, "partial"),
    ("mismatch", "mismatch"),
    ({"verdict": "글쎄"}, ""),
    (None, ""),
])
def test_direction_is_normalized(raw, expected):
    detail = _score(_answer(direction=raw))["fit_detail"]
    assert detail["direction"] == expected


def test_direction_reason_is_kept():
    detail = _score(_answer())["fit_detail"]
    assert detail["direction_reason"] == "LLM 서비스 개발"


def test_fit_detail_keeps_position_and_items():
    detail = _score(_answer())["fit_detail"]
    assert detail["position"] == "AI 서비스 개발"
    assert detail["required"][0] == {"item": "항목0", "verdict": "O", "evidence": "근거", "kind": "normal"}
    assert detail["preferred"][0]["kind"] == "normal"
    assert len(detail["tasks"]) == 4


# ── 파싱 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "분석할 수 없습니다.",
    json.dumps({"fit_reasons": "판정 없음"}),
    json.dumps(_answer(required="많음")),
])
def test_unparseable_answer_raises_so_nothing_is_saved(content):
    """해석 못 한 답을 저장하면 '이미 채점함'이 돼 다시 채점되지 않는다 — 예외로 알려 저장을 막는다."""
    with pytest.raises(ValueError):
        _score(content)


def test_reads_first_complete_json_when_model_appends_trailing_garbage():
    """2026-10-14 실제 응답: 올바른 JSON 뒤에 꼬리를 한 번 더 붙였다."""
    text = json.dumps(_answer(), ensure_ascii=False)
    tail = ', "required": [{"item": "x", "verdict": "X"}]}'
    assert _score(text + tail)["fit_score"] == 68
    assert _score("결과입니다:\n" + text + "\n끝")["fit_score"] == 68


def test_reads_gemini3_content_blocks():
    blocks = [{"type": "text", "text": json.dumps(_answer(), ensure_ascii=False), "extras": {"signature": "x"}}]
    assert _score(blocks)["fit_score"] == 68


# ── 경력 비율 점수 (2026-10-27 결정) ────────────────────────────────────

_CAREER = {"months": 6.0, "max_years": 2.0}


def _career_item(years, verdict="X") -> dict:
    return {"item": f"경력 {years}년 이상", "verdict": verdict, "kind": "career", "years": years, "evidence": ""}


@pytest.mark.parametrize("years, rate", [(0.5, 1.0), (1, 0.5), (1.5, 1 / 3), (2, 0.25)])
def test_career_item_scores_months_over_required_months(years, rate):
    """6개월 실무로 1년 요구면 0.5, 2년 요구면 0.25 — 판정(verdict)과 무관하게 비율로."""
    result = _score(_answer(required=[_career_item(years)]), career=_CAREER)
    assert result["fit_detail"]["rates"]["required"] == pytest.approx(rate)
    assert result["fit_detail"]["status"] == "scored"


def test_career_item_mixes_with_other_required_items():
    required = [_career_item(1)] + _items("O")
    result = _score(_answer(required=required), career=_CAREER)
    assert result["fit_detail"]["rates"]["required"] == pytest.approx((0.5 + 1) / 2)


def test_required_years_over_max_is_ineligible():
    result = _score(_answer(required=[_career_item(3)]), career=_CAREER)
    assert result["fit_score"] is None
    assert result["fit_detail"]["status"] == "ineligible"
    assert result["fit_detail"]["gate"]["career"] == "fail"
    assert result["fit_detail"]["gate"]["fail_reason"] == "경력 3년 이상"


def test_career_item_uses_verdict_when_career_months_unknown():
    """선호에 경력 개월이 없으면 비율 계산 없이 LLM 판정 그대로."""
    result = _score(_answer(required=[_career_item(1, verdict="H")]))
    assert result["fit_detail"]["rates"]["required"] == pytest.approx(0.5)
    result = _score(_answer(required=[_career_item(1, verdict="X")]))
    assert result["fit_detail"]["rates"]["required"] == 0


def test_career_years_string_is_parsed_and_bad_years_fall_back_to_verdict():
    result = _score(_answer(required=[_career_item("1.5")]), career=_CAREER)
    assert result["fit_detail"]["rates"]["required"] == pytest.approx(1 / 3)
    result = _score(_answer(required=[_career_item("많이", verdict="O")]), career=_CAREER)
    assert result["fit_detail"]["rates"]["required"] == 1.0


def test_prompt_asks_for_career_kind_and_split_reasons():
    text = "\n".join(m.prompt.template for m in _FIT_PROMPT.messages)
    for word in ('"career"', "years", "good_points", "weak_points"):
        assert word in text


# ── 맞는 점·아쉬운 점 ───────────────────────────────────────────────────

def test_good_and_weak_points_are_returned_separately():
    data = _answer(good_points="LLM 에이전트 경험 일치", weak_points="Java 경험 없음")
    del data["fit_reasons"]
    result = _score(data)
    assert result["fit_reasons"] == "LLM 에이전트 경험 일치"
    assert result["fit_detail"]["weak_points"] == "Java 경험 없음"


def test_legacy_fit_reasons_still_used_when_good_points_missing():
    result = _score(_answer())
    assert result["fit_reasons"] == "LLM 에이전트 경험 일치, Java는 부족"
    assert result["fit_detail"]["weak_points"] == ""
