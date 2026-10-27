"""사용자 선호(users.preferences) → LLM에 넣을 텍스트 + 코드가 쓰는 가중치·등급 기준.

preferences 모양: constraints(지원 조건), item_rules(항목 판정 규칙 문장), direction(직무 방향 목록),
  weights·grade(코드 전용 — 프롬프트에 넣지 않는다). `_`로 시작하는 키는 주석이라 무시한다.
"""
DEFAULT_WEIGHTS: dict[str, float] = {"department_preferred": 0.25, "vague_preferred": 0.5}
DEFAULT_GRADE: dict = {"recommend_min": 80, "suggest_min": 70}
# 경력: months = 지원자 실무 개월 수(0이면 비율 계산 안 함), max_years = 지원 가능한 최대 요구 연차(None이면 LLM 판정만)
DEFAULT_CAREER: dict = {"months": 0.0, "max_years": None}

# direction 키 → 표시 이름
_DIRECTION_LABELS = {"match": "맞음", "partial": "부분", "mismatch": "안 맞음"}


def as_list(value) -> list:
    """문자열 하나는 한 항목짜리 목록으로, 목록은 그대로, 없으면 빈 목록. (문자열을 글자 단위로 도는 실수 방지)"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _flatten(value) -> str:
    """값 하나를 한 줄 문자열로 만든다. 목록은 ", "로, dict는 "키 값"으로 잇고 `_` 키는 뺀다."""
    if isinstance(value, dict):
        return " · ".join(
            f"{k} {_flatten(v)}" for k, v in value.items() if not str(k).startswith("_") and v not in (None, "", [], {})
        )
    if isinstance(value, list):
        return ", ".join(_flatten(v) for v in value)
    return str(value)


def _lines(section) -> list[str]:
    """dict 섹션을 "키: 값" 줄 목록으로. `_` 키와 빈 값은 건너뛴다."""
    if not isinstance(section, dict):
        return []
    return [
        f"{k}: {_flatten(v)}"
        for k, v in section.items()
        if not str(k).startswith("_") and v not in (None, "", [], {})
    ]


def build_preferences_text(preferences: dict | None) -> str:
    """채점 프롬프트에 넣을 지원 조건·선호 텍스트. 넣을 게 없으면 빈 문자열.

    constraints, item_rules, direction(match/partial/mismatch)만 펼친다.
    weights·grade와 `_`로 시작하는 키는 넣지 않는다(코드 전용·주석).

    Args:
        preferences: users.preferences (없으면 None).

    Returns:
        "[지원 조건]", "[항목 판정 규칙]", "[직무 방향]" 블록이 빈 줄로 이어진 텍스트.
    """
    prefs = preferences or {}
    sections: list[str] = []

    cons = _lines(prefs.get("constraints"))
    if cons:
        sections.append("[지원 조건]\n" + "\n".join(cons))

    rules = _lines(prefs.get("item_rules"))
    if rules:
        sections.append("[항목 판정 규칙]\n" + "\n".join(rules))

    direction = prefs.get("direction")
    if isinstance(direction, dict):
        dir_lines = []
        for key, label in _DIRECTION_LABELS.items():
            items = as_list(direction.get(key))
            if items:
                dir_lines.append(f"{label}:\n" + "\n".join(f"- {i}" for i in items))
        if dir_lines:
            sections.append("[직무 방향 — 맞음/부분/안 맞음]\n" + "\n".join(dir_lines))

    return "\n\n".join(sections)


def weights_of(preferences: dict | None) -> dict[str, float]:
    """우대 항목 가중치. 없거나 일부만 있으면 기본값으로 채운다."""
    given = (preferences or {}).get("weights")
    merged = dict(DEFAULT_WEIGHTS)
    if isinstance(given, dict):
        merged.update({k: v for k, v in given.items() if k in DEFAULT_WEIGHTS and v is not None})
    return merged


def grade_config(preferences: dict | None) -> dict:
    """등급 기준(recommend_min·suggest_min). 없거나 일부만 있으면 기본값으로 채운다."""
    given = (preferences or {}).get("grade")
    merged = dict(DEFAULT_GRADE)
    if isinstance(given, dict):
        merged.update({k: v for k, v in given.items() if k in DEFAULT_GRADE and v is not None})
    return merged


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def career_of(preferences: dict | None) -> dict:
    """경력 비율 점수용 설정. constraints.career_months·max_required_years에서 읽는다.

    Returns:
        {"months": float, "max_years": float | None} — 없으면 DEFAULT_CAREER.
    """
    constraints = (preferences or {}).get("constraints") or {}
    months = _number(constraints.get("career_months"))
    return {
        "months": months if months and months > 0 else DEFAULT_CAREER["months"],
        "max_years": _number(constraints.get("max_required_years")),
    }
