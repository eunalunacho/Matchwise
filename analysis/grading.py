"""적합도 등급. 저장된 점수 행(fit_score, fit_detail, 마감일)에서 읽을 때 계산한다."""
from datetime import datetime, timezone

GRADES = ("closed", "ineligible", "insufficient", "recommend", "suggest", "reference", "not_recommended")
GRADE_LABELS = {
    "closed": "마감",
    "ineligible": "지원 불가",
    "insufficient": "판단 불가",
    "recommend": "추천",
    "suggest": "권유",
    "reference": "참고",
    "not_recommended": "비추천",
}
NOTIFY_GRADES = ("recommend", "suggest")


def _as_utc(dt: datetime) -> datetime:
    """naive datetime은 UTC로 간주한다 (DB 값은 보통 tz-aware UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def grade_for(
    fit_score: int | None,
    fit_detail: dict | None,
    deadline: datetime | None,
    now: datetime,
    grade_cfg: dict,
) -> str:
    """점수 행의 등급을 계산한다.

    순서: 마감 경과 -> 지원 불가 -> 판단 불가 -> 점수/방향 규칙.
    방향(direction)이 없거나 빈 값이면(v2 행 등) 점수만으로 매긴다.

    Args:
        fit_score: 적합도 점수. 지원 불가/판단 불가 행은 None.
        fit_detail: 채점 상세 (status, direction 사용). None이면 {}로 본다.
        deadline: 공고 마감일. None(채용 시 마감)이면 마감으로 보지 않는다.
        now: 기준 시각.
        grade_cfg: grade_config() 결과 (recommend_min, suggest_min).

    Returns:
        GRADES 중 하나.
    """
    detail = fit_detail or {}
    if deadline is not None and _as_utc(deadline) < _as_utc(now):
        return "closed"
    status = detail.get("status")
    if status == "ineligible":
        return "ineligible"
    if status == "insufficient" or fit_score is None:
        return "insufficient"

    recommend_min = grade_cfg["recommend_min"]
    suggest_min = grade_cfg["suggest_min"]
    direction = detail.get("direction") or ""
    if direction == "mismatch":
        return "not_recommended"
    if direction == "partial":
        return "reference" if fit_score >= suggest_min else "not_recommended"
    # match 또는 방향 판정 없음: 점수만으로
    if fit_score >= recommend_min:
        return "recommend"
    if fit_score >= suggest_min:
        return "suggest"
    return "not_recommended"
