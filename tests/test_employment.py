"""고용형태 분류·순위 — 같은 등급 안에서 알림 순서를 정한다 (2026-11-11 결정)."""
import pytest

from analysis.employment import DEFAULT_ORDER, employment_category, employment_order, employment_rank


@pytest.mark.parametrize("text, category", [
    # 11/10~11/11 실제 수집값
    ("정규직", "정규직"),
    ("정규직 (2개월 수습기간)", "정규직"),
    ("정규직 (수습기간 3개월)", "정규직"),
    ("정규직 전환형 인턴 (3개월)", "채용전환형 인턴"),
    ("인턴 (정규직 전환형 인턴)", "채용전환형 인턴"),
    ("3개월 계약 후 정규직 전환", "채용전환형 인턴"),
    ("인턴십 5개월", "체험형 인턴 (6개월 미만)"),
    ("인턴 (6개월)", "체험형 인턴 (6개월 이상)"),
    ("인턴", "체험형 인턴 (6개월 이상)"),  # 기간 모름 — 짧다고 단정하지 않는다
    ("계약직 (1년)", "계약직"),
    ("무기계약직", "계약직"),
    ("단기계약직 (6개월)", "계약직"),
    ("계약직(대체인력)", "계약직"),
    ("", "미상"),
    (None, "미상"),
    ("정규직 B2사원", "정규직"),
])
def test_category(text, category):
    assert employment_category(text) == category


def test_default_order_follows_decision():
    assert DEFAULT_ORDER == ("정규직", "미상", "채용전환형 인턴", "체험형 인턴 (6개월 이상)",
                             "계약직", "체험형 인턴 (6개월 미만)")


def test_rank_follows_order():
    ranks = [employment_rank(t) for t in ("정규직", "", "정규직 전환형 인턴 (3개월)", "인턴 (6개월)",
                                          "계약직 (1년)", "인턴십 5개월")]
    assert ranks == sorted(ranks) and len(set(ranks)) == 6


def test_order_comes_from_preferences_and_unknown_names_are_ignored():
    order = employment_order({"employment_priority": ["계약직", "정규직", "없는 형태"]})
    assert order[:2] == ("계약직", "정규직")
    assert "없는 형태" not in order
    # 선호에 빠진 형태는 기본 순서대로 뒤에 붙는다
    assert set(order) == set(DEFAULT_ORDER)
    assert employment_rank("계약직 (1년)", order) < employment_rank("정규직", order)


@pytest.mark.parametrize("prefs", [None, {}, {"employment_priority": "정규직"}, {"employment_priority": []}])
def test_order_defaults(prefs):
    assert employment_order(prefs)[0] == "정규직"
