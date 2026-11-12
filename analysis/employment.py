"""고용형태 분류·순위 — 같은 등급 안에서 알림 순서를 정한다 (2026-11-11 결정).

고용형태로 공고를 빼지 않는다. 등급(적합도·방향)은 그대로 두고, 같은 등급 안에서 이 순서로 보여 준다.
공고의 고용형태 문구(jobs.jd_employment_type, 자유 텍스트)를 아래 범주 중 하나로 나눈다.
"""
import re

from analysis.preferences import as_list

# 사용자 결정 순서: 정규직 > 채용전환형 인턴 > 체험형 인턴 6개월 이상 > 계약직 > 체험형 인턴 6개월 미만.
# 고용형태가 안 적힌 공고(미상)는 정규직이 많아(잡코리아 정규 공고) 정규직 바로 뒤에 둔다.
DEFAULT_ORDER: tuple[str, ...] = (
    "정규직", "미상", "채용전환형 인턴", "체험형 인턴 (6개월 이상)", "계약직", "체험형 인턴 (6개월 미만)",
)
_MONTHS = re.compile(r"(\d+)\s*개월")
_CONVERSION = ("전환", "연계")


def employment_category(text: str | None) -> str:
    """고용형태 문구 → DEFAULT_ORDER의 범주 하나.

    "정규직 전환형 인턴", "3개월 계약 후 정규직 전환"처럼 전환이 붙으면 채용전환형 인턴이다.
    체험형 인턴은 기간(N개월)으로 6개월 이상/미만을 나누고, 기간이 없으면 6개월 이상으로 본다(짧다고 단정하지 않음).
    """
    value = str(text or "").strip()
    if not value:
        return "미상"
    converts = any(word in value for word in _CONVERSION)
    if "인턴" in value:
        if converts:
            return "채용전환형 인턴"
        months = [int(m) for m in _MONTHS.findall(value)]
        return "체험형 인턴 (6개월 미만)" if months and max(months) < 6 else "체험형 인턴 (6개월 이상)"
    if "계약" in value:
        return "채용전환형 인턴" if converts and "정규" in value else "계약직"
    if "정규" in value:
        return "정규직"
    return "미상"


def employment_order(preferences: dict | None) -> tuple[str, ...]:
    """선호의 employment_priority(범주 이름 목록)로 순서를 만든다. 모르는 이름은 버리고, 빠진 범주는 기본 순서대로 뒤에 붙인다."""
    given = [name for name in as_list((preferences or {}).get("employment_priority")) if name in DEFAULT_ORDER]
    ordered = list(dict.fromkeys(given))
    return tuple(ordered + [name for name in DEFAULT_ORDER if name not in ordered])


def employment_rank(text: str | None, order: tuple[str, ...] = DEFAULT_ORDER) -> int:
    """고용형태 문구의 순위(작을수록 먼저)."""
    category = employment_category(text)
    return order.index(category) if category in order else len(order)
