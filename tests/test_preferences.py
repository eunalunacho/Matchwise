"""analysis/preferences.py — 선호 텍스트 블록과 가중치·등급 기본값."""
from analysis.preferences import build_preferences_text, career_of, grade_config, weights_of

PREFS = {
    "_comment": "x",
    "constraints": {
        "official_graduation": "2027-02",
        "languages_ok": ["Python"],
        "work_locations": ["서울", "판교"],
        "location_rule": "근무지가 work_locations 밖이면 지원 불가",
    },
    "item_rules": {"attitude_items": "의지·관심·태도 같은 문장은 판단 불가(N)"},
    "direction": {
        "match": ["LLM·에이전트를 만들어 서비스·업무에 적용"],
        "partial": ["크롤링·데이터 수집이 업무 일부인 직무"],
        "mismatch": ["크롤링·데이터 수집만 하는 직무"],
    },
    "weights": {"department_preferred": 0.25, "vague_preferred": 0.5},
    "grade": {"recommend_min": 80, "suggest_min": 70, "employment_type_affects_grade": False},
}


def test_renders_constraints_and_direction_lists():
    text = build_preferences_text(PREFS)
    assert "Python" in text
    assert "2027-02" in text
    assert "서울" in text and "판교" in text
    assert "크롤링·데이터 수집만 하는 직무" in text
    assert "LLM·에이전트를 만들어 서비스·업무에 적용" in text


def test_renders_attitude_item_rule():
    assert "의지·관심·태도 같은 문장은 판단 불가(N)" in build_preferences_text(PREFS)


def test_excludes_weights_grade_and_underscore_keys():
    text = build_preferences_text(PREFS)
    for banned in ("recommend_min", "suggest_min", "department_preferred", "vague_preferred", "_comment", "weights"):
        assert banned not in text
    nested = {"constraints": {"_note": "숨김", "a": "보임"}}
    out = build_preferences_text(nested)
    assert "숨김" not in out and "보임" in out


def test_empty_preferences_returns_empty_string():
    assert build_preferences_text(None) == ""
    assert build_preferences_text({}) == ""
    assert build_preferences_text({"weights": {"vague_preferred": 0.5}}) == ""


def test_defaults_when_weights_missing():
    assert weights_of(None) == {"department_preferred": 0.25, "vague_preferred": 0.5}
    assert weights_of({}) == {"department_preferred": 0.25, "vague_preferred": 0.5}
    assert grade_config(None) == {"recommend_min": 80, "suggest_min": 70}
    assert grade_config({}) == {"recommend_min": 80, "suggest_min": 70}


def test_partial_sections_fill_from_defaults():
    assert weights_of({"weights": {"vague_preferred": 0.6}}) == {"department_preferred": 0.25, "vague_preferred": 0.6}
    assert grade_config({"grade": {"recommend_min": 85}})["suggest_min"] == 70
    assert weights_of(PREFS)["department_preferred"] == 0.25
    assert grade_config(PREFS)["recommend_min"] == 80


def test_direction_bare_string_is_one_item_not_per_character():
    text = build_preferences_text({"direction": {"match": "LLM 에이전트 개발", "partial": ["크롤링"]}})
    assert "- L" + chr(10) not in text


def test_career_of_reads_months_and_max_years():
    prefs = {"constraints": {"career_months": 6, "max_required_years": 2}}
    assert career_of(prefs) == {"months": 6.0, "max_years": 2.0}


def test_career_of_defaults_when_missing():
    assert career_of(None) == {"months": 0.0, "max_years": None}
    assert career_of({"constraints": {"career_months": "모름"}}) == {"months": 0.0, "max_years": None}
