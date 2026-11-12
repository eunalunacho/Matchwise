"""직무 방향 판정 — 이 공고가 사용자가 하고 싶은 일인가 (1차 필터, 2026-11-11 결정).

적합도 채점(analysis/scorer.py::score_fit)과 따로 부른다. 지원자 이력을 넣지 않고 JD와 방향 목록만 보여 주므로
"경험과 맞으니 일치" 같은 판정이 나오지 않는다. LLM은 match/partial/mismatch를 직접 고르지 않고
목록의 줄 번호(M1·P1·X1…) 하나와 근거 문장만 낸다 — 방향은 줄 번호의 머리글자로 코드가 정한다.

맞는 줄이 없으면 NONE을 고르게 하고, 결과는 "unclassified"(분류 안 됨)로 따로 알린다.
보기 밖의 값이나 JD에 없는 근거 문장도 unclassified — 억지로 고른 줄을 믿지 않는다.
"""
import logging
import re

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from analysis.gemini_client import get_llm
from analysis.preferences import as_list
from analysis.scorer import _parse_json, _response_text

logger = logging.getLogger(__name__)

# 선호 direction 키 → (줄 번호 머리글자, 화면 이름)
_GROUPS = (("match", "M", "일치"), ("partial", "P", "부분"), ("mismatch", "X", "불일치"))
NONE_LINE = "NONE"
UNCLASSIFIED = "unclassified"

_DIRECTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """당신은 채용 공고의 담당업무를 읽고 정해진 직무 목록 중 어디에 해당하는지 분류하는 사람입니다.
지원자가 누구인지는 모르고, 알 필요도 없습니다. 공고의 업무가 어떤 종류의 일인지만 봅니다.
유효한 JSON 하나만 출력하고, 마크다운 코드블록이나 다른 설명은 붙이지 않습니다."""),
    ("human", """
[채용 공고 JD]
{jd_text}

[직무 목록]
{options_text}

## 분류 규칙
1. 직함이 아니라 담당업무 문장으로 분류한다. "AI 엔지니어"라는 직함이어도 업무가 고객 응대면 고객 응대로 본다.
2. 모집 직무가 여러 개면, 목록의 일치(M) 줄에 가장 가까운 직무 하나를 골라 그 직무만 분류한다. position에 그 직무명을 적는다.
3. 한 직무에 업무가 섞여 있으면 담당업무에서 비중이 가장 큰 업무로 분류한다. 단 줄 문장에 예외가 적혀 있으면 그 예외를 따른다.
4. 목록의 각 줄은 예시 하나가 아니라 범주다. 업무가 그 범주에 들어가면 그 줄을 고른다
   (예: C++ 의료기기 SW 개발은 "AI·데이터 업무가 없는 일반 SW 개발" 범주, 전용회선·네트워크 운용은 "순수 인프라·시스템 운영" 범주).
   reason에 쓴 판단이 어떤 줄의 범주와 같으면 line도 그 같은 줄이어야 한다.
5. NONE은 두 경우에만 고른다: 업무를 읽을 수 없을 때(담당업무가 없거나 "직무소개서 참조"처럼 본문에 없음),
   또는 업무가 어느 줄의 범주에도 들어가지 않을 때. 범주가 애매하게 겹친다는 이유로 억지로 가장 비슷한 줄을 고르지는 않는다.
6. evidence에는 그 줄을 고른 근거가 된 담당업무 문장을 공고에서 그대로 복사한다. 고쳐 쓰거나 지어내지 않는다. NONE이면 빈 문자열.

아래 JSON 형식으로만 답하세요:
{{"position": "<분류한 직무명>", "line": "<줄 번호 또는 NONE>", "evidence": "<공고 원문 문장>", "reason": "<한 줄 이유>"}}
"""),
])


def build_direction_chain() -> Runnable:
    return _DIRECTION_PROMPT | get_llm(temperature=0)


def direction_options(direction_prefs) -> list[dict]:
    """선호의 direction(match/partial/mismatch 목록) → 줄 번호가 붙은 보기 목록.

    Args:
        direction_prefs: users.preferences["direction"]. dict가 아니면 빈 목록.

    Returns:
        [{"id": "M1", "text": "...", "verdict": "match"}, ...] — 일치·부분·불일치 순, 그룹마다 1부터.
    """
    if not isinstance(direction_prefs, dict):
        return []
    options = []
    for verdict, prefix, _ in _GROUPS:
        for index, text in enumerate(as_list(direction_prefs.get(verdict)), start=1):
            options.append({"id": f"{prefix}{index}", "text": str(text), "verdict": verdict})
    return options


def _options_text(options: list[dict]) -> str:
    lines = []
    for verdict, prefix, label in _GROUPS:
        group = [o for o in options if o["verdict"] == verdict]
        if group:
            lines.append(f"{label}:")
            lines.extend(f"{o['id']} {o['text']}" for o in group)
    lines.append(f"해당 없음:\n{NONE_LINE} 위 어떤 줄에도 확실히 해당하지 않음")
    return "\n".join(lines)


_NOISE = re.compile(r"[\s·•\-–—*,.;:'\"()\[\]/]+")


def _evidence_in_jd(evidence: str, jd_text: str) -> bool:
    """근거 문장이 JD에 있는지 — 띄어쓰기·글머리표·문장부호 차이는 무시한다."""
    needle = _NOISE.sub("", evidence).lower()
    return bool(needle) and needle in _NOISE.sub("", jd_text).lower()


def judge_direction(jd_text: str, direction_prefs) -> dict | None:
    """JD만 보고 직무 방향을 판정한다. 방향 목록이 없으면 Gemini를 부르지 않고 None.

    Args:
        jd_text: 공고 본문.
        direction_prefs: users.preferences["direction"].

    Returns:
        {"direction": match|partial|mismatch|unclassified, "line": 줄 번호|"NONE", "line_text", "evidence",
         "position", "reason"} 또는 None(목록 없음).

    Raises:
        ValueError: 답을 해석하지 못했을 때 — 호출자는 저장하지 않고 다음 실행에 다시 시도한다.
    """
    options = direction_options(direction_prefs)
    if not options:
        return None
    response = build_direction_chain().invoke({"jd_text": jd_text, "options_text": _options_text(options)})
    data = _parse_json(_response_text(response))
    if not data:
        raise ValueError("방향 판정 답 없음")

    by_id = {o["id"]: o for o in options}
    line = str(data.get("line") or "").strip().upper() if isinstance(data.get("line"), str) else ""
    evidence = str(data.get("evidence") or "")
    reason = str(data.get("reason") or "")
    option = by_id.get(line)
    if option is not None and not _evidence_in_jd(evidence, jd_text):
        logger.info("[direction] 근거 문장이 JD에 없음 — %s 무시: %r", line, evidence[:80])
        reason = f"근거 문장이 공고에 없어 {line} 판정을 버림 — {reason}".rstrip(" —")
        option = None

    return {
        "direction": option["verdict"] if option else UNCLASSIFIED,
        "line": option["id"] if option else NONE_LINE,
        "line_text": option["text"] if option else "",
        "evidence": evidence if option else "",
        "position": str(data.get("position") or ""),
        "reason": reason,
    }
