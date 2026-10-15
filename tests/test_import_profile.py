"""scripts/import_profile.py — profile.json → users.basic_info + experiences 테스트."""
from db.orm import Experience, User
from scripts.import_profile import import_profile

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
