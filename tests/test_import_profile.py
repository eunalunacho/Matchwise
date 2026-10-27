"""scripts/import_profile.py — profile.json → users.basic_info + experiences 테스트."""
import pytest
from db.orm import Experience, User
from scripts.import_profile import import_preferences, import_profile

_DATA = {
    "basic_info": {"이름": "조은아", "병역": "비대상"},
    "experiences": [
        {"kind": "career", "title": "여기어때컴퍼니", "period": "2026.03 ~ 2026.08", "content": "GCP 이관"},
        {"kind": "project", "title": "Altify", "content": "LLaVA 서비스", "tags": ["LLM"]},
    ],
}


def test_import_profile_sets_basic_info_and_inserts_experiences(session):
    user = User(username="u")
    session.add(user)
    session.commit()

    count = import_profile(session, user.id, _DATA)

    assert count == 2
    assert user.basic_info == {"이름": "조은아", "병역": "비대상"}
    rows = session.query(Experience).filter_by(user_id=user.id).order_by(Experience.id).all()
    assert [(r.kind, r.title, r.period) for r in rows] == [
        ("career", "여기어때컴퍼니", "2026.03 ~ 2026.08"),
        ("project", "Altify", None),
    ]
    assert rows[1].tags == ["LLM"]


def test_import_profile_replaces_previous_import_instead_of_duplicating(session):
    """profile.json을 고쳐서 다시 넣으면 그 유저의 경험은 파일 내용으로 통째로 바뀐다."""
    user = User(username="u")
    session.add(user)
    session.commit()

    import_profile(session, user.id, _DATA)
    import_profile(session, user.id, {"basic_info": {"이름": "조은아"}, "experiences": [_DATA["experiences"][0]]})

    assert session.query(Experience).filter_by(user_id=user.id).count() == 1
    assert user.basic_info == {"이름": "조은아"}


def test_import_profile_rejects_unknown_user(session):
    import pytest

    with pytest.raises(ValueError):
        import_profile(session, 999, _DATA)


def test_import_profile_bumps_profile_version_so_old_scores_become_stale(session):
    user = User(username="u")
    session.add(user)
    session.commit()
    before = user.profile_version

    import_profile(session, user.id, {"basic_info": {"병역": "비대상"}, "experiences": []})

    assert session.get(User, user.id).profile_version == before + 1


_PREFS = {
    "_comment": "개인 기준",
    "constraints": {"work_locations": ["서울"]},
    "direction": {"match": ["AI"]},
    "weights": {"department_preferred": 0.25},
}


def test_import_preferences_overwrites_and_bumps_version(session):
    user = User(username="u", preferences={"old": 1})
    session.add(user)
    session.commit()
    before = user.profile_version

    import_preferences(session, user.id, _PREFS)

    saved = session.get(User, user.id)
    assert saved.preferences == {k: v for k, v in _PREFS.items() if k != "_comment"}
    assert "_comment" not in saved.preferences
    assert saved.profile_version == before + 1


def test_import_preferences_rejects_missing_sections(session):
    import pytest

    user = User(username="u")
    session.add(user)
    session.commit()

    with pytest.raises(ValueError):
        import_preferences(session, user.id, {"direction": {}})
    with pytest.raises(ValueError):
        import_preferences(session, user.id, {"constraints": {}})
    with pytest.raises(ValueError):
        import_preferences(session, 999, _PREFS)


def test_cli_bad_preferences_file_changes_nothing(session, tmp_path, monkeypatch):
    """--preferences 파일이 잘못되면 profile도 적재하지 않는다."""
    import json
    import sys
    from unittest.mock import patch

    from scripts import import_profile as mod

    user = User(username="u")
    session.add(user)
    session.commit()
    uid = user.id
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_DATA, ensure_ascii=False), encoding="utf-8")
    prefs_path = tmp_path / "preferences.json"
    prefs_path.write_text(json.dumps({"constraints": {}}), encoding="utf-8")  # direction 없음
    monkeypatch.setattr(sys, "argv", ["import_profile", str(profile_path), "--preferences", str(prefs_path),
                                      "--user-id", str(uid)])

    with patch.object(mod, "init"), patch.object(mod, "get_session", return_value=session):
        with pytest.raises(ValueError, match="direction"):
            mod.main()

    assert session.query(Experience).filter_by(user_id=uid).count() == 0
    assert session.get(User, uid).basic_info in (None, {})
