"""analysis/profile.py — 채점·초안용 지원자 프로필 텍스트 테스트."""
from analysis.profile import KIND_LABELS, build_profile_text
from db.orm import Experience


def _exp(kind, title, content, period=None):
    return Experience(user_id=1, kind=kind, title=title, period=period, content=content)


def test_build_profile_text_renders_basic_info_values_of_any_shape():
    """basic_info는 문자열·dict·dict 목록이 섞여 있어도 키 이름과 값을 그대로 보여준다."""
    text = build_profile_text(
        {
            "병역": "비대상",
            "대학교": [{"학교": "건국대학교", "전공": "응용통계학과", "학점": "3.93 / 4.50"}],
            "고등학교": {"학교": "하남고등학교", "졸업구분": "졸업"},
            "컴퓨터활용능력": ["Python 고급", "SQL 중급"],
        },
        [],
    )
    assert "병역: 비대상" in text
    assert "대학교: 학교 건국대학교 · 전공 응용통계학과 · 학점 3.93 / 4.50" in text
    assert "고등학교: 학교 하남고등학교 · 졸업구분 졸업" in text
    assert "컴퓨터활용능력: Python 고급 / SQL 중급" in text


def test_build_profile_text_groups_experiences_by_kind_in_fixed_order():
    text = build_profile_text(
        None,
        [
            _exp("education", "SeSAC", "LangGraph 실습", "2025.10 ~ 2026.02"),
            _exp("career", "여기어때컴퍼니 인턴", "GCP 이관", "2026.03 ~ 2026.08"),
            _exp("project", "Altify", "LLaVA 서비스"),
        ],
    )
    career_at = text.index(f"[{KIND_LABELS['career']}]")
    project_at = text.index(f"[{KIND_LABELS['project']}]")
    education_at = text.index(f"[{KIND_LABELS['education']}]")
    assert career_at < project_at < education_at
    assert "■ 여기어때컴퍼니 인턴 | 2026.03 ~ 2026.08\nGCP 이관" in text
    assert "■ Altify\nLLaVA 서비스" in text


def test_build_profile_text_keeps_unknown_kind_at_the_end():
    text = build_profile_text(None, [_exp("misc", "기타 활동", "내용"), _exp("career", "회사", "일")])
    assert text.index("[misc]") > text.index(f"[{KIND_LABELS['career']}]")


def test_build_profile_text_leaves_out_excluded_basic_info_keys():
    """채점에는 적합도와 무관한 이름·링크를 넣지 않는다 — 초안 생성 등 다른 용도엔 그대로 쓸 수 있게 인자로 고른다."""
    info = {"이름": "조은아 (Euna Cho)", "병역": "비대상", "링크": {"GitHub": "https://github.com/x"}}
    text = build_profile_text(info, [], exclude_keys=("이름", "링크"))
    assert "병역: 비대상" in text
    assert "조은아" not in text and "github" not in text
    assert "조은아" in build_profile_text(info, [])


def test_build_profile_text_is_empty_when_nothing_is_stored():
    """비어 있으면 빈 문자열 — analyze_job이 이걸 보고 채점을 건너뛴다."""
    assert build_profile_text(None, []) == ""
    assert build_profile_text({}, []) == ""
