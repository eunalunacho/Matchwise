"""지원자 프로필(users.basic_info + experiences) → LLM에 넣을 텍스트.

basic_info: 한 사람에 하나씩 정해진 칸(인적사항·학력·어학·자격증·컴퓨터활용능력 등).
  키 이름이 곧 항목 이름이고 값은 문자열·dict·목록 무엇이든 될 수 있어, 모양에 맞춰 한 줄로 펼친다.
experiences: 개수가 늘어나는 서술형 항목(경력·프로젝트·수상·학내외활동). kind별로 묶어 보여준다.
"""
from collections.abc import Iterable

from db.orm import Experience

# experiences.kind → 표시 이름. 이 순서대로 출력하고, 여기 없는 kind는 맨 뒤에 kind 그대로 붙인다.
KIND_LABELS = {
    "career": "경력사항",
    "project": "프로젝트",
    "award": "수상경력",
    "campus": "교내활동",
    "club": "동아리활동",
    "team_project": "팀프로젝트·공모전",
    "education": "교육이수",
    "volunteer": "봉사활동",
    "story": "자소서용 에피소드",
}


def _flatten(value) -> str:
    """basic_info 값 하나를 한 줄 문자열로 만든다. dict는 "키 값 · 키 값", 목록은 " / "로 잇는다."""
    if isinstance(value, dict):
        return " · ".join(f"{k} {_flatten(v)}" for k, v in value.items() if v not in (None, "", [], {}))
    if isinstance(value, list):
        return " / ".join(_flatten(v) for v in value)
    return str(value)


def build_profile_text(
    basic_info: dict | None,
    experiences: Iterable[Experience],
    exclude_keys: Iterable[str] = (),
) -> str:
    """채점(score_fit)에 넣을 지원자 프로필 텍스트. 저장된 게 하나도 없으면 빈 문자열.

    Args:
        basic_info: users.basic_info (없으면 None).
        experiences: 그 유저의 Experience 행들.
        exclude_keys: 넣지 않을 basic_info 키 (예: 채점에는 이름·링크 제외).

    Returns:
        "[기본 정보]" 블록 다음에 kind별 "[경력사항]" 등의 블록이 이어지는 텍스트.
    """
    sections: list[str] = []

    excluded = set(exclude_keys)
    info_lines = [
        f"{key}: {_flatten(value)}"
        for key, value in (basic_info or {}).items()
        if key not in excluded and value not in (None, "", [], {})
    ]
    if info_lines:
        sections.append("[기본 정보]\n" + "\n".join(info_lines))

    grouped: dict[str, list[Experience]] = {}
    for exp in experiences:
        grouped.setdefault(exp.kind, []).append(exp)
    order = [k for k in KIND_LABELS if k in grouped] + [k for k in grouped if k not in KIND_LABELS]
    for kind in order:
        items = []
        for exp in grouped[kind]:
            head = f"■ {exp.title or '(제목 없음)'}" + (f" | {exp.period}" if exp.period else "")
            items.append(f"{head}\n{exp.content}")
        sections.append(f"[{KIND_LABELS.get(kind, kind)}]\n" + "\n".join(items))

    return "\n\n".join(sections)


def mark_profile_changed(user) -> None:
    """프로필(basic_info·experiences)을 고친 뒤 호출한다 — profile_version을 1 올린다.

    이전 버전으로 매긴 점수는 구 점수가 되고(analysis/scorer.py::is_stale_score), 사용자가 요청할 때만
    다시 채점한다. 새로 들어오는 공고는 analyze_job이 바뀐 프로필로 채점한다. 커밋은 호출자가 한다.
    """
    user.profile_version = (user.profile_version or 1) + 1
