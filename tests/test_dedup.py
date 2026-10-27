from analysis.dedup import posting_key


def test_posting_key_matches_same_posting_from_two_sites():
    a = posting_key("한국전자기술연구원", "한국전자기술연구원｜2026년 정규직 공개채용")
    b = posting_key("한국전자기술연구원", "2026년 정규직 공개채용")
    assert a == b == "한국전자기술연구원|2026년정규직공개채용"


def test_posting_key_strips_bracket_and_ascii_pipe_prefix():
    base = posting_key("(주)카카오", "백엔드 개발자 (신입)")
    assert posting_key("(주)카카오", "[카카오] 백엔드 개발자 (신입)") == base
    assert posting_key("(주)카카오", "카카오 | 백엔드 개발자 (신입)") == base


def test_posting_key_differs_when_titles_differ():
    assert posting_key("A사", "A사｜백엔드 신입") != posting_key("A사", "A사｜프론트 신입")


def test_posting_key_keeps_unrelated_prefix_and_handles_none():
    assert posting_key("A사", "[긴급] 백엔드") == "a사|긴급백엔드"
    assert posting_key(None, None) == ""


def test_posting_key_is_empty_when_company_or_title_is_blank():
    assert posting_key("", "백엔드") == ""
    assert posting_key("A사", "") == ""
    assert posting_key("A사", "  !! ") == ""
    assert posting_key("A사", "A사｜") == ""
