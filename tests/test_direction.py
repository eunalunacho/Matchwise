"""직무 방향 판정(judge_direction) — 이력 없이 JD만 보고 방향 목록의 줄 번호를 고르게 한다. LLM은 mock."""
import json
from unittest.mock import MagicMock, patch

import pytest

from analysis.direction import _DIRECTION_PROMPT, direction_options, judge_direction

PREFS = {
    "match": ["LLM·에이전트를 만들어 서비스·업무에 적용", "Python 기반 데이터 엔지니어"],
    "partial": ["고객사 대상 아키텍처 설계·기술 자문 (Solutions Architect)"],
    "mismatch": ["고객 응대(CS)·QA·테스트·운영 지원이 중심인 직무"],
}
JD = """담당업무
- 고객 문의(CS) 접수 및 응대
- MSSQL 데이터 조회 및 관리 등 DB 작업
자격요건
- 학력무관"""


def _chain(answer) -> MagicMock:
    response = MagicMock()
    response.content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    chain = MagicMock()
    chain.invoke.return_value = response
    return chain


def _judge(answer, jd=JD, prefs=PREFS):
    chain = _chain(answer)
    with patch("analysis.direction.build_direction_chain", return_value=chain):
        return judge_direction(jd, prefs), chain


# ── 줄 번호 ─────────────────────────────────────────────────────────────

def test_options_are_numbered_by_group_in_order():
    options = direction_options(PREFS)
    assert [(o["id"], o["verdict"]) for o in options] == [
        ("M1", "match"), ("M2", "match"), ("P1", "partial"), ("X1", "mismatch"),
    ]
    assert options[3]["text"] == "고객 응대(CS)·QA·테스트·운영 지원이 중심인 직무"


def test_options_accept_single_string_and_skip_missing_groups():
    assert [o["id"] for o in direction_options({"match": "FDE"})] == ["M1"]


@pytest.mark.parametrize("prefs", [None, {}, {"match": []}, "문자열"])
def test_no_options_means_no_judgement(prefs):
    with patch("analysis.direction.build_direction_chain") as build:
        assert judge_direction(JD, prefs) is None
    build.assert_not_called()  # 목록이 없으면 Gemini를 부르지 않는다


# ── 판정 결과 ───────────────────────────────────────────────────────────

def test_line_id_decides_direction_and_keeps_line_text_and_evidence():
    result, _ = _judge({"position": "인턴", "line": "X1", "evidence": "고객 문의(CS) 접수 및 응대", "reason": "CS 중심"})
    assert result == {
        "direction": "mismatch", "line": "X1", "line_text": "고객 응대(CS)·QA·테스트·운영 지원이 중심인 직무",
        "evidence": "고객 문의(CS) 접수 및 응대", "position": "인턴", "reason": "CS 중심",
    }


@pytest.mark.parametrize("line, direction", [("M2", "match"), ("p1", "partial"), (" X1 ", "mismatch")])
def test_line_id_is_normalized(line, direction):
    result, _ = _judge({"line": line, "evidence": "MSSQL 데이터 조회 및 관리 등 DB 작업"})
    assert result["direction"] == direction


def test_none_line_is_unclassified():
    result, _ = _judge({"line": "NONE", "evidence": "", "reason": "목록에 없는 업무"})
    assert result["direction"] == "unclassified"
    assert result["line"] == "NONE"
    assert result["reason"] == "목록에 없는 업무"


@pytest.mark.parametrize("line", ["M9", "match", "", None, 3])
def test_line_outside_options_is_unclassified(line):
    result, _ = _judge({"line": line, "evidence": "고객 문의(CS) 접수 및 응대"})
    assert result["direction"] == "unclassified"
    assert result["line"] == "NONE"


def test_evidence_not_in_jd_is_unclassified():
    """근거 문장을 지어냈으면 고른 줄을 믿지 않는다."""
    result, _ = _judge({"line": "M1", "evidence": "LLM 기반 상담 에이전트 개발"})
    assert result["direction"] == "unclassified"
    assert result["line"] == "NONE"
    assert "근거" in result["reason"]


def test_evidence_match_ignores_spacing_and_bullets():
    result, _ = _judge({"line": "X1", "evidence": "• 고객 문의(CS)  접수 및\n응대"})
    assert result["direction"] == "mismatch"


def test_empty_evidence_is_unclassified_unless_none():
    result, _ = _judge({"line": "M1", "evidence": ""})
    assert result["direction"] == "unclassified"


def test_unparseable_answer_raises_so_nothing_is_saved():
    with pytest.raises(ValueError):
        _judge("죄송합니다")


# ── 프롬프트 ────────────────────────────────────────────────────────────

def test_prompt_sees_only_jd_and_options_not_profile():
    assert set(_DIRECTION_PROMPT.input_variables) == {"jd_text", "options_text"}


def test_prompt_offers_none_and_forbids_forced_choice():
    text = _DIRECTION_PROMPT.format(jd_text="JD", options_text="M1 x")
    assert "NONE" in text
    assert "억지로" in text
    assert "담당업무" in text and "직함" in text


def test_prompt_treats_lines_as_categories_so_none_is_not_overused():
    """11/11 실측: 이유는 'AI 없는 일반 SW 개발'(X10)이라 써 놓고 NONE을 고른 경우가 5건 — 줄은 범주로 본다."""
    text = _DIRECTION_PROMPT.format(jd_text="JD", options_text="M1 x")
    assert "범주" in text
    assert "reason" in text and "같은 줄" in text


def test_options_text_sent_to_llm_lists_ids_by_group():
    _, chain = _judge({"line": "NONE", "evidence": ""})
    sent = chain.invoke.call_args.args[0]
    assert sent["jd_text"] == JD
    assert "M1 LLM·에이전트를 만들어 서비스·업무에 적용" in sent["options_text"]
    assert "X1 고객 응대(CS)·QA·테스트·운영 지원이 중심인 직무" in sent["options_text"]
    assert "NONE" in sent["options_text"]


def test_direction_chain_uses_zero_temperature():
    """11/11 실측: 같은 공고(Moloco QA)가 실행마다 X8·M1로 갈렸다 — 분류는 무작위성 없이."""
    from analysis.direction import build_direction_chain
    with patch("analysis.direction.get_llm") as get_llm:
        build_direction_chain()
    get_llm.assert_called_once_with(temperature=0)
