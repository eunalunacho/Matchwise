"""등급(grade_for) 테스트. 위키 보정 사례를 코드 쪽에서 고정한다."""
from datetime import datetime, timedelta, timezone

from analysis.grading import GRADE_LABELS, GRADES, NOTIFY_GRADES, grade_for
from analysis.preferences import grade_config

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CFG = grade_config(None)


def _detail(direction="match", status="scored"):
    return {"status": status, "direction": direction}


def test_constants():
    assert GRADES == ("closed", "ineligible", "insufficient", "recommend", "suggest", "reference", "not_recommended")
    assert NOTIFY_GRADES == ("recommend", "suggest")
    assert [GRADE_LABELS[g] for g in GRADES] == ["마감", "지원 불가", "판단 불가", "추천", "권유", "참고", "비추천"]


def test_closed_when_deadline_passed():
    assert grade_for(91, _detail(), NOW - timedelta(minutes=1), NOW, CFG) == "closed"


def test_closed_takes_priority_over_ineligible():
    assert grade_for(None, _detail(status="ineligible"), NOW - timedelta(days=1), NOW, CFG) == "closed"


def test_no_deadline_is_not_closed():
    assert grade_for(91, _detail(), None, NOW, CFG) == "recommend"


def test_future_deadline_is_not_closed():
    assert grade_for(91, _detail(), NOW + timedelta(days=1), NOW, CFG) == "recommend"


def test_naive_deadline_treated_as_utc():
    assert grade_for(91, _detail(), datetime(2026, 10, 27, 11, 0), NOW, CFG) == "closed"
    assert grade_for(91, _detail(), datetime(2026, 10, 27, 13, 0), NOW, CFG) == "recommend"


def test_recommend_at_80_with_match():
    assert grade_for(91, _detail(), None, NOW, CFG) == "recommend"
    assert grade_for(80, _detail(), None, NOW, CFG) == "recommend"


def test_suggest_between_70_and_79_with_match():
    assert grade_for(79, _detail(), None, NOW, CFG) == "suggest"
    assert grade_for(70, _detail(), None, NOW, CFG) == "suggest"


def test_match_below_70_not_recommended():
    assert grade_for(56, _detail(), None, NOW, CFG) == "not_recommended"


def test_reference_when_partial_and_70_or_more():
    assert grade_for(76, _detail("partial"), None, NOW, CFG) == "reference"
    assert grade_for(94, _detail("partial"), None, NOW, CFG) == "reference"
    assert grade_for(69, _detail("partial"), None, NOW, CFG) == "not_recommended"


def test_high_score_with_mismatch_is_not_recommended():
    assert grade_for(92, _detail("mismatch"), None, NOW, CFG) == "not_recommended"


def test_ineligible_and_insufficient_pass_through():
    assert grade_for(None, _detail("", "ineligible"), None, NOW, CFG) == "ineligible"
    assert grade_for(None, _detail("", "insufficient"), None, NOW, CFG) == "insufficient"


def test_score_none_without_status_is_insufficient():
    assert grade_for(None, _detail("match", "scored"), None, NOW, CFG) == "insufficient"
    assert grade_for(None, {}, None, NOW, CFG) == "insufficient"
    assert grade_for(None, None, None, NOW, CFG) == "insufficient"


def test_v2_detail_without_direction_grades_by_score_only():
    assert grade_for(85, {"required": {}}, None, NOW, CFG) == "recommend"
    assert grade_for(75, {"required": {}}, None, NOW, CFG) == "suggest"
    assert grade_for(60, {}, None, NOW, CFG) == "not_recommended"
    assert grade_for(85, None, None, NOW, CFG) == "recommend"
    assert grade_for(85, _detail(""), None, NOW, CFG) == "recommend"


def test_thresholds_come_from_grade_config():
    cfg = grade_config({"grade": {"recommend_min": 90, "suggest_min": 60}})
    assert grade_for(85, _detail(), None, NOW, cfg) == "suggest"
    assert grade_for(90, _detail(), None, NOW, cfg) == "recommend"
    assert grade_for(65, _detail("partial"), None, NOW, cfg) == "reference"
    assert grade_for(55, _detail(), None, NOW, cfg) == "not_recommended"
