"""JobkoreaCrawler 테스트 — httpx.MockTransport로 실제 클라이언트 경로를 그대로 탄다.

응답 구조는 2026-09-24~26에 잡코리아에서 직접 받은 형태를 줄여서 따른다:
  GET /theme/entry-level-internship              테마 페이지 (필터 버튼 + 폼)
  GET /Theme/TemplateFreeGnoList/entry-level-internship?<폼 값>   목록 HTML 조각 (20건/페이지)
  GET /Recruit/GI_Read/{번호}                    상세 (JSON-LD + 조건 항목)
  GET /Recruit/GI_Read_Comt_Ifrm?Gno={번호}      JD 본문 HTML (텍스트 또는 이미지)
"""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from crawler.jobkorea import JobkoreaCrawler

KST = timezone(timedelta(hours=9))
TODAY = date(2026, 9, 26)  # 기본 조회 기간(2일)은 9/24~9/25

THEME_HTML = """
<html><body>
<form id="recruitSearchForm">
  <input type="radio" name="rlistTab" value="0"><input type="radio" name="rlistTab" value="24">
  <input type="text" name="rSearchText">
  <input type="hidden" name="bpart_no" value="">
  <input type="hidden" name="scd" value="">
  <input type="hidden" name="jtype" value="">
  <input type="hidden" name="careerTypeCode" value="">
  <input type="hidden" name="jobFilter" value="1">
  <input type="hidden" name="FreePageNo" value="1">
  <input type="hidden" name="themeNo" value="169">
</form>
<div area-type="bpart_no"><button data-value="0">전체</button><button data-value="10031">AI·개발·데이터</button></div>
<div area-type="scd"><button data-value="I000">서울</button><button data-value="B000">경기</button><button data-value="K000">인천</button></div>
<div area-type="job_type"><button data-value="1">정규직</button><button data-value="2">계약직</button><button data-value="3">인턴직</button></div>
<div area-type="career_type_code"><button data-value="1/3">신입</button><button data-value="2">경력</button><button data-value="4">경력무관</button></div>
</body></html>
"""


def _card(gno, title="[회사] 백엔드 개발자", company="㈜회사"):
    return f"""
<ul class="clear listItem"><li class="dmpItem"><div class="listCell">
  <p class="rLogo"><a class="dev-recruit-link" href="/Recruit/GI_Read/{gno}?Oem_Code=C1&amp;rPageCode=TL&amp;sc=416">
    <img alt="{company}" src="//imgs.jobkorea.co.kr/logo.gif"></a></p>
  <div class="listFoot">
    <p class="rCor"><a class="corNm" href="/Recruit/Co_Read/C/59652?Oem_Code=C1">{company}</a></p>
    <p class="rTit"><a href="/Recruit/GI_Read/{gno}?Oem_Code=C1&amp;rPageCode=TL&amp;sc=416">{title}</a></p>
    <p class="rInfo"><button class="rinfo-button button-homepage"><span>홈페이지</span></button>
      <span class="rPeriod"><span class="tahoma">~10/09</span>(금)</span><span class="rPart">웹기획 외</span></p>
  </div></div></li></ul>"""


def _detail(gno, *, title="[회사] 백엔드 개발자", company="㈜회사", posted="2026-09-25",
            valid_through="2026-10-09T23:59", experience="신입·경력", employment="정규직",
            with_ld=True):
    ld = {
        "@context": "https://schema.org", "@type": "JobPosting", "title": title,
        "datePosted": posted, "validThrough": valid_through, "employmentType": "FULL_TIME",
        "experienceRequirements": experience, "educationRequirements": "학력무관",
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "streetAddress": "경기 성남시 분당구 판교역로 1"}},
        "identifier": {"@type": "PropertyValue", "name": "JobKorea", "value": str(gno)},
        "url": f"https://www.jobkorea.co.kr/Recruit/GI_Read/{gno}",
    }
    if posted is None:
        del ld["datePosted"]
    if valid_through is None:
        del ld["validThrough"]
    ld_tag = f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>' if with_ld else ""
    return f"""<html><head>{ld_tag}</head><body>
<div data-sentry-component="RecruitmentItem"><span class="min-w-[80px]">고용형태</span><div><div><span>{employment}</span></div></div></div>
<div data-sentry-component="RecruitmentItem"><span class="min-w-[80px]">급여</span><div>회사 내규에 따름</div></div>
<div data-sentry-component="RecruitmentItem"><span class="min-w-[80px]">근무지주소</span><div>경기 성남시 분당구 판교역로 1 <button>지도보기</button></div></div>
</body></html>"""


TEXT_BODY = "<html><body><style>.a{color:red}</style><h3>담당업무</h3><p>API 개발</p><p>데이터 파이프라인</p></body></html>"
IMAGE_BODY = '<html><body><img src="//file2.jobkorea.co.kr/Net/Mng/DownImage/CorpEditor?file_No=1890922"></body></html>'


class FakeJobkorea:
    """MockTransport 핸들러. pages는 페이지별 카드 gno 목록, postings는 gno→(detail html, body html)."""

    def __init__(self, pages, postings=None, theme=THEME_HTML, failing_details=(), failing_bodies=()):
        self.pages = pages  # list[list[int]]
        self.postings = postings or {}
        self.theme = theme
        self.failing_details = set(failing_details)
        self.failing_bodies = set(failing_bodies)
        self.requests: list[tuple[str, dict]] = []

    def paths(self):
        return [path for path, _ in self.requests]

    def list_params(self):
        return [p for path, p in self.requests if "TemplateFreeGnoList" in path]

    def body_requests(self):
        return [int(params["Gno"]) for path, params in self.requests if path == "/Recruit/GI_Read_Comt_Ifrm"]

    def detail_requests(self):
        return [int(path.rsplit("/", 1)[1]) for path, _ in self.requests if "/Recruit/GI_Read/" in path]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        self.requests.append((path, params))
        if path == "/theme/entry-level-internship":
            return httpx.Response(200, text=self.theme)
        if path == "/Theme/TemplateFreeGnoList/entry-level-internship":
            page = int(params["FreePageNo"])
            gnos = self.pages[page - 1] if page <= len(self.pages) else []
            return httpx.Response(200, text="".join(_card(g, **self.postings.get(g, {}).get("card", {})) for g in gnos))
        if path.startswith("/Recruit/GI_Read/"):
            gno = int(path.rsplit("/", 1)[1])
            if gno in self.failing_details:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=self.postings.get(gno, {}).get("detail") or _detail(gno))
        if path == "/Recruit/GI_Read_Comt_Ifrm":
            gno = int(params["Gno"])
            if gno in self.failing_bodies:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=self.postings.get(gno, {}).get("body") or TEXT_BODY)
        return httpx.Response(404)


def _crawl(pages, *, postings=None, known_urls=None, limit=None, lookback_days=None, page_size=20,
           max_consecutive_old=None, **fake_kwargs):
    fake = FakeJobkorea(pages, postings, **fake_kwargs)
    client = httpx.Client(transport=httpx.MockTransport(fake))
    options = {} if lookback_days is None else {"lookback_days": lookback_days}
    if max_consecutive_old is not None:
        options["max_consecutive_old"] = max_consecutive_old
    crawler = JobkoreaCrawler(client=client, today=TODAY, request_delay_sec=0, page_size=page_size, **options)
    jobs = crawler.fetch_jobs(limit=limit, known_urls=known_urls)
    return jobs, fake


def _url(gno):
    return f"https://www.jobkorea.co.kr/Recruit/GI_Read/{gno}"


# ── 필터: 화면 버튼의 라벨로 값을 찾아 서버에 보낸다 ─────────────────────────

def test_filter_values_are_resolved_from_button_labels():
    _, fake = _crawl([[1]])
    params = fake.list_params()[0]
    assert params["bpart_no"] == "10031"           # AI·개발·데이터
    assert params["scd"] == "I000,B000"            # 서울, 경기
    assert params["jtype"] == "1,3"                # 정규직, 인턴직
    assert params["careerTypeCode"] == "1/3,4"     # 신입, 경력무관


def test_list_request_uses_search_mode_newest_first_and_form_defaults():
    _, fake = _crawl([[1]])
    params = fake.list_params()[0]
    assert params["jobFilter"] == "0"      # 상세검색 모드
    assert params["rOrderTab"] == "2"      # 등록일순(최신부터)
    assert params["rlistTab"] == "0"       # 전체
    assert params["FreePageNo"] == "1"
    assert params["themeNo"] == "169"      # 폼의 기본값을 그대로 사용


def test_missing_filter_label_fails_loudly():
    theme = THEME_HTML.replace("AI·개발·데이터", "AI·개발")
    with pytest.raises(RuntimeError, match="AI·개발·데이터"):
        _crawl([[1]], theme=theme)


# ── 조회 기간 · 페이지 · 중복 ──────────────────────────────────────────────

def test_typical_run_costs_theme_list_and_two_requests_per_new_posting():
    _, fake = _crawl([[1, 2]])
    paths = fake.paths()
    assert paths[0] == "/theme/entry-level-internship"
    assert sum("TemplateFreeGnoList" in p for p in paths) == 1  # 카드가 page_size보다 적으면 다음 페이지를 부르지 않는다
    assert sorted(fake.detail_requests()) == [1, 2]
    assert paths.count("/Recruit/GI_Read_Comt_Ifrm") == 2


def test_known_urls_are_skipped_without_detail_requests():
    jobs, fake = _crawl([[1, 2]], known_urls={_url(1)})
    assert [j.url for j in jobs] == [_url(2)]
    assert fake.detail_requests() == [2]


def test_stops_paging_when_every_card_on_a_page_is_already_known():
    _, fake = _crawl([[1, 2], [3, 4]], known_urls={_url(1), _url(2)}, page_size=2)
    assert [p["FreePageNo"] for p in fake.list_params()] == ["1"]


def test_follows_next_page_when_page_is_full():
    jobs, fake = _crawl([[1, 2], [3]], page_size=2)
    assert [j.url for j in jobs] == [_url(1), _url(2), _url(3)]
    assert [p["FreePageNo"] for p in fake.list_params()] == ["1", "2"]


def test_old_posting_is_skipped_without_fetching_its_body_and_paging_continues():
    # 등록일순은 재등록 순서라서, 게시일이 오래된 공고가 맨 위에 올라올 수 있다
    postings = {
        1: {"detail": _detail(1, posted="2026-09-17")},   # 재등록된 오래된 공고
        2: {"detail": _detail(2, posted="2026-09-25")},
        3: {"detail": _detail(3, posted="2026-09-24")},   # 그제 (기본 조회 기간 안)
    }
    jobs, fake = _crawl([[1, 2, 3]], postings=postings)
    assert [j.url for j in jobs] == [_url(2), _url(3)]
    assert fake.body_requests() == [2, 3]


def test_stops_paging_when_a_page_has_no_posting_within_window():
    postings = {
        1: {"detail": _detail(1, posted="2026-09-25")},
        2: {"detail": _detail(2, posted="2026-09-25")},
        3: {"detail": _detail(3, posted="2026-09-20")},
        4: {"detail": _detail(4, posted="2026-09-19")},
    }
    jobs, fake = _crawl([[1, 2], [3, 4], [5, 6]], postings=postings, page_size=2)
    assert [j.url for j in jobs] == [_url(1), _url(2)]
    assert [p["FreePageNo"] for p in fake.list_params()] == ["1", "2"]   # 3페이지는 부르지 않는다


def test_keeps_paging_while_each_page_has_a_recent_posting():
    postings = {
        1: {"detail": _detail(1, posted="2026-09-25")},
        2: {"detail": _detail(2, posted="2026-09-10")},   # 오래된 공고가 섞여 있어도
        3: {"detail": _detail(3, posted="2026-09-24")},   # 다음 페이지에 최근 공고가 있다
    }
    jobs, fake = _crawl([[1, 2], [3]], postings=postings, page_size=2)
    assert [j.url for j in jobs] == [_url(1), _url(3)]
    assert [p["FreePageNo"] for p in fake.list_params()] == ["1", "2"]


def test_stops_after_a_run_of_consecutive_old_postings_within_a_page():
    # 실제 패턴: 최근 공고 몇 개 뒤로 오래된 공고가 길게 이어진다. 끝까지 상세를 열 필요가 없다.
    postings = {1: {"detail": _detail(1, posted="2026-09-25")}}
    postings.update({g: {"detail": _detail(g, posted="2026-09-20")} for g in range(2, 13)})
    jobs, fake = _crawl([list(range(1, 13))], postings=postings, max_consecutive_old=3)
    assert [j.url for j in jobs] == [_url(1)]
    assert sorted(fake.detail_requests()) == [1, 2, 3, 4]   # 최근 1건 + 오래된 3건에서 중단


def test_a_recent_posting_resets_the_old_streak():
    old, recent = "2026-09-20", "2026-09-25"
    postings = {
        1: {"detail": _detail(1, posted=old)},
        2: {"detail": _detail(2, posted=old)},
        3: {"detail": _detail(3, posted=recent)},
        4: {"detail": _detail(4, posted=old)},
        5: {"detail": _detail(5, posted=old)},
        6: {"detail": _detail(6, posted=recent)},
    }
    jobs, _ = _crawl([[1, 2, 3, 4, 5, 6]], postings=postings, max_consecutive_old=3)
    assert [j.url for j in jobs] == [_url(3), _url(6)]


def test_old_streak_carries_over_to_the_next_page():
    postings = {g: {"detail": _detail(g, posted="2026-09-20")} for g in range(1, 7)}
    postings[1] = {"detail": _detail(1, posted="2026-09-25")}
    _, fake = _crawl([[1, 2, 3], [4, 5, 6]], postings=postings, page_size=3, max_consecutive_old=4)
    assert sorted(fake.detail_requests()) == [1, 2, 3, 4, 5]   # 오래된 4건(2~5)에서 중단, 6은 열지 않는다


def test_lookback_of_one_day_is_yesterday_only():
    postings = {
        1: {"detail": _detail(1, posted="2026-09-25")},
        2: {"detail": _detail(2, posted="2026-09-24")},
    }
    jobs, _ = _crawl([[1, 2]], postings=postings, lookback_days=1)
    assert [j.url for j in jobs] == [_url(1)]


def test_posting_without_posted_date_is_kept():
    jobs, _ = _crawl([[1]], postings={1: {"detail": _detail(1, posted=None)}})
    assert [j.url for j in jobs] == [_url(1)]


def test_limit_caps_number_of_jobs():
    jobs, _ = _crawl([[1, 2, 3]], limit=2)
    assert len(jobs) == 2


# ── RawJob 매핑 ────────────────────────────────────────────────────────────

def test_maps_posting_to_rawjob():
    postings = {7: {"card": {"title": "[링커리어] 백엔드개발 인턴", "company": "㈜링커리어"},
                    "detail": _detail(7, valid_through="2026-10-09T23:59")}}
    jobs, _ = _crawl([[7]], postings=postings)
    job = jobs[0]

    assert job.source == "jobkorea"
    assert job.url == "https://www.jobkorea.co.kr/Recruit/GI_Read/7"   # 추적 파라미터 제거한 정규 URL
    assert job.title == "[링커리어] 백엔드개발 인턴"
    assert job.company == "㈜링커리어"
    assert job.deadline == datetime(2026, 10, 9, 23, 59, tzinfo=KST)
    assert job.jd_text == "담당업무\nAPI 개발\n데이터 파이프라인"     # <style> 내용은 제외
    assert job.apply_url is None   # 지원 주소는 로그인해야 보이므로 채우지 않는다


def test_missing_valid_through_gives_no_deadline():
    jobs, _ = _crawl([[1]], postings={1: {"detail": _detail(1, valid_through=None)}})
    assert jobs[0].deadline is None


def test_unparseable_valid_through_gives_no_deadline():
    jobs, _ = _crawl([[1]], postings={1: {"detail": _detail(1, valid_through="상시채용")}})
    assert jobs[0].deadline is None


def test_unparseable_posted_date_is_treated_as_missing():
    jobs, _ = _crawl([[1]], postings={1: {"detail": _detail(1, posted="상시")}})
    assert [j.url for j in jobs] == [_url(1)]


def test_payload_keeps_card_json_ld_conditions_and_body():
    jobs, _ = _crawl([[1]])
    payload = jobs[0].payload

    assert payload["card"]["deadline_label"] == "~10/09(금)"
    assert payload["card"]["apply_kind"] == "홈페이지"
    assert payload["json_ld"]["datePosted"] == "2026-09-25"
    assert payload["conditions"]["고용형태"] == "정규직"
    assert payload["conditions"]["근무지주소"].startswith("경기 성남시 분당구 판교역로 1")
    assert "API 개발" in payload["body_html"]


# ── hints: 경력(JSON-LD)과 고용형태에서 career_level을 만든다 ──────────────

@pytest.mark.parametrize(
    "experience, employment, expected",
    [
        ("신입·경력", "정규직", "신입/경력"),
        ("신입", "정규직", "신입"),
        ("신입", "인턴", "신입/인턴"),
        ("경력무관", "정규직", "신입/경력"),
        ("신입·경력 (직무별 상이/상세요강 참고)", "인턴", "신입/인턴/경력"),
    ],
)
def test_career_level_from_experience_and_employment(experience, employment, expected):
    detail = _detail(1, experience=experience, employment=employment)
    jobs, _ = _crawl([[1]], postings={1: {"detail": detail}})
    assert jobs[0].hints == {"positions": [{"name": "[회사] 백엔드 개발자", "career_level": expected}]}


# ── JD 본문: 텍스트 / 이미지 ───────────────────────────────────────────────

def test_image_only_body_gives_empty_text_and_https_image_urls():
    jobs, _ = _crawl([[1]], postings={1: {"body": IMAGE_BODY}})
    assert jobs[0].jd_text == ""
    assert jobs[0].image_urls == ["https://file2.jobkorea.co.kr/Net/Mng/DownImage/CorpEditor?file_No=1890922"]


def test_text_body_without_images_has_no_image_urls():
    jobs, _ = _crawl([[1]])
    assert jobs[0].image_urls == []


# ── 오류 처리 ──────────────────────────────────────────────────────────────

def test_detail_failure_skips_only_that_posting():
    jobs, _ = _crawl([[1, 2]], failing_details={1})
    assert [j.url for j in jobs] == [_url(2)]


def test_body_failure_skips_only_that_posting():
    jobs, _ = _crawl([[1, 2]], failing_bodies={1})
    assert [j.url for j in jobs] == [_url(2)]


def test_detail_without_json_ld_is_skipped():
    jobs, _ = _crawl([[1, 2]], postings={1: {"detail": _detail(1, with_ld=False)}})
    assert [j.url for j in jobs] == [_url(2)]


# ── 수집 요약용 카운터 (로그·웹훅) ─────────────────────────────────────────

def test_counts_seen_cards_and_detail_or_body_failures():
    """목록 카드는 이미 아는 것도 '본 수'에 들어가고, 상세·본문 조회 실패는 따로 센다."""
    fake = FakeJobkorea([[1, 2, 3, 4]], None, failing_details={2}, failing_bodies={3})
    crawler = JobkoreaCrawler(
        client=httpx.Client(transport=httpx.MockTransport(fake)), today=TODAY, request_delay_sec=0,
    )
    jobs = crawler.fetch_jobs(known_urls={_url(1)})

    assert [j.url for j in jobs] == [_url(4)]
    assert crawler.seen_count == 4
    assert crawler.detail_failed_count == 2
