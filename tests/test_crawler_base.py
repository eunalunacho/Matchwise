"""RawJob 계약 테스트 — 기존 필수 필드는 그대로, 신규 선택 필드는 기본값을 가진다."""
import pytest

from crawler.base import RawJob, extract_image_urls, join_career_level


def test_rawjob_required_fields_only_still_works():
    job = RawJob(
        source="inthiswork",
        url="https://inthiswork.com/archives/1",
        title="SOOP｜AI 모델 개발",
        company="SOOP",
        jd_text="본문",
    )
    assert job.deadline is None


def test_rawjob_optional_fields_have_defaults():
    job = RawJob(source="s", url="u", title="t", company="c", jd_text="")
    assert job.apply_url is None
    assert job.payload == {}
    assert job.hints == {}
    assert job.image_urls == []


def test_rawjob_default_dicts_are_not_shared_between_instances():
    a = RawJob(source="s", url="u1", title="t", company="c", jd_text="")
    b = RawJob(source="s", url="u2", title="t", company="c", jd_text="")
    a.payload["k"] = "v"
    a.hints["k"] = "v"
    a.image_urls.append("https://cdn.example.com/a.png")
    assert b.payload == {}
    assert b.hints == {}
    assert b.image_urls == []


# ── career_level 표기: 구성 요소를 신입 → 인턴 → 경력 순서로 "/"로 잇는다 ──────

def test_join_career_level_orders_components_canonically():
    assert join_career_level(["경력", "신입"]) == "신입/경력"
    assert join_career_level(["경력", "인턴", "신입"]) == "신입/인턴/경력"


def test_join_career_level_removes_duplicates():
    assert join_career_level(["신입", "신입", "인턴"]) == "신입/인턴"


def test_join_career_level_keeps_other_levels_after_the_main_three():
    assert join_career_level(["계약직"]) == "계약직"
    assert join_career_level(["교육", "신입"]) == "신입/교육"


def test_join_career_level_rejects_unknown_component():
    with pytest.raises(ValueError):
        join_career_level(["신입", "시니어"])


# ── extract_image_urls: JD 본문 HTML에서 이미지 주소만 순서대로 ───────────────

def test_extract_image_urls_returns_src_in_document_order():
    html = '<p>a</p><img src="https://cdn.example.com/1.png"><div><img src="https://cdn.example.com/2.png"></div>'
    assert extract_image_urls(html) == [
        "https://cdn.example.com/1.png",
        "https://cdn.example.com/2.png",
    ]


def test_extract_image_urls_removes_duplicates_and_skips_img_without_src():
    html = '<img src="https://cdn.example.com/1.png"><img alt="x"><img src="https://cdn.example.com/1.png">'
    assert extract_image_urls(html) == ["https://cdn.example.com/1.png"]


def test_extract_image_urls_of_empty_or_missing_html_is_empty():
    assert extract_image_urls("") == []
    assert extract_image_urls(None) == []
    assert extract_image_urls("<p>텍스트만</p>") == []


def test_extract_image_urls_adds_https_to_protocol_relative_src():
    html = '<img src="//file2.jobkorea.co.kr/Net/Mng/DownImage/CorpEditor?file_No=1890922">'
    assert extract_image_urls(html) == ["https://file2.jobkorea.co.kr/Net/Mng/DownImage/CorpEditor?file_No=1890922"]
