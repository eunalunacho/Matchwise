"""같은 공고가 여러 사이트에 올라온 경우를 묶기 위한 키."""
import re

_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
_SEPARATORS = ("｜", "|")


def _squash(text: str) -> str:
    """공백·특수문자를 모두 지우고 소문자로 만든다."""
    return _PUNCT_RE.sub("", text).lower()


def _same_company(name: str, head: str) -> bool:
    """접두어가 회사명과 같거나 한쪽이 다른 쪽을 포함하면 True ("(주)카카오" vs "카카오")."""
    head = _squash(head)
    return bool(name and head and (name in head or head in name))


def _strip_company_prefix(company: str, title: str) -> str:
    """제목 앞의 "회사명｜"·"회사명|"·"[회사명]" 접두어를 뗀다. 회사명이 접두어에 들어 있을 때만."""
    name = _squash(company)
    stripped = title.strip()
    if stripped.startswith("["):
        end = stripped.find("]")
        if end > 0 and _same_company(name, stripped[1:end]):
            return stripped[end + 1:]
    for sep in _SEPARATORS:
        head, found, rest = stripped.partition(sep)
        if found and _same_company(name, head):
            return rest
    return stripped


def posting_key(company: str | None, title: str | None) -> str:
    """같은 공고인지 판단하는 키. 접두어·공백·특수문자 차이를 무시한다.

    Args:
        company: 회사명.
        title: 공고 제목 (앞에 "회사명｜"·"[회사명]"이 붙어 있을 수 있다).

    Returns:
        "회사|제목" 형태의 정규화된 문자열. 회사나 제목이 (정규화 후) 비면 ""
        — 빈 키는 서로 같은 공고로 볼 근거가 없으므로 중복 판단에 쓰지 않는다.
    """
    company = company or ""
    title = _strip_company_prefix(company, title or "")
    squashed_company, squashed_title = _squash(company), _squash(title)
    if not squashed_company or not squashed_title:
        return ""
    return f"{squashed_company}|{squashed_title}"
