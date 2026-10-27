"""JasoseolCrawler 테스트 — httpx.MockTransport로 실제 클라이언트 경로를 그대로 탄다.

응답 구조는 자소설 API에서 직접 받은 형태를 따른다 (목록은 2026-10-25, 나머지는 2026-09-24):
  GET  /api/v1/employment_companies?all=true → [...] 열린 공고 전체 (division·duty_group_ids는 list)
  GET  /api/v1/duty-groups                   → [{"id", "name", "group_id"}, ...]
  GET  /api/v1/employment_companies/{id}     → 상세 (employment_page_url, content HTML)
"""
from datetime import date, datetime, timedelta, timezone

import httpx

from crawler.jasoseol import JasoseolCrawler

KST = timezone(timedelta(hours=9))
TODAY = date(2026, 9, 24)  # 목요일. "어제"는 2026-09-23

# IT(94) > 웹개발(162) > 프론트엔드(175) / 경영·사무(91) > 기획·전략·경영(103) > 경영기획(109)
DUTY_GROUPS = [
    {"id": 94, "name": "IT·인터넷", "group_id": None},
    {"id": 162, "name": "웹개발", "group_id": 94},
    {"id": 175, "name": "프론트엔드개발", "group_id": 162},
    {"id": 166, "name": "네트워크·보안·운영", "group_id": 94},
    {"id": 91, "name": "경영·사무", "group_id": None},
    {"id": 103, "name": "기획·전략·경영", "group_id": 91},
    {"id": 109, "name": "경영기획", "group_id": 103},
]

IT_DUTY = 175
NON_IT_DUTY = 109


def _emp(emp_id, division, duty):
    """목록 API의 employment(포지션) 항목. division·duty_group_ids는 list다."""
    return {
        "id": emp_id,
        "field": f"포지션{emp_id}",
        "division": division if isinstance(division, list) else [division],
        "duty_group_ids": [duty],
        "end_time": None,
    }


def _list_item(posting_id, employments, start="2026-09-23", end="2026-10-07", name=None):
    return {
        "id": posting_id,
        "name": name or f"회사{posting_id}",
        "title": f"공고{posting_id}",
        "start_time": f"{start}T00:00:00.000+09:00",
        "end_time": f"{end}T23:59:00.000+09:00",
        "recruit_type": 0,
        "employment_page_url": f"https://careers.example.com/{posting_id}",
        "employments": employments,
    }


def _detail(posting_id, employments, content="<p>본문 텍스트</p>"):
    """상세 API 응답."""
    return {
        "id": posting_id,
        "name": f"회사{posting_id}",
        "title": f"공고{posting_id}",
        "employment_page_url": f"https://careers.example.com/{posting_id}?utm_source=jasoseol",
        "content": content,
        "employments": [
            {"id": e["id"], "field": e["field"], "division": e["division"], "duty_group_ids": e["duty_group_ids"]}
            for e in employments
        ],
    }


class FakeJasoseol:
    """MockTransport 핸들러. 받은 요청을 기록해 검증에 쓴다."""

    def __init__(self, items, details=None, failing_details=()):
        self.items = items
        self.details = details or {
            i["id"]: _detail(i["id"], i["employments"]) for i in items
        }
        self.failing_details = set(failing_details)
        self.list_params: list[dict] = []
        self.detail_requests: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/employment_companies":
            self.list_params.append(dict(request.url.params))
            return httpx.Response(200, json=self.items)
        if path == "/api/v1/duty-groups":
            return httpx.Response(200, json=DUTY_GROUPS)
        if path.startswith("/api/v1/employment_companies/"):
            posting_id = int(path.rsplit("/", 1)[1])
            self.detail_requests.append(posting_id)
            if posting_id in self.failing_details:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json=self.details[posting_id])
        return httpx.Response(404)


def _crawl(items, *, details=None, failing_details=(), known_urls=None, limit=None, lookback_days=None):
    fake = FakeJasoseol(items, details, failing_details)
    client = httpx.Client(transport=httpx.MockTransport(fake))
    options = {} if lookback_days is None else {"lookback_days": lookback_days}
    crawler = JasoseolCrawler(client=client, today=TODAY, detail_delay_sec=0, **options)
    jobs = crawler.fetch_jobs(limit=limit, known_urls=known_urls)
    return jobs, fake


# ── 목록 조회 · 조회 기간 ─────────────────────────────────────────────────

def test_lists_all_open_postings_in_a_single_request():
    """캘린더 API가 마감일 기준으로 바뀌어(2026-10-25 확인) 열린 공고 전체 목록을 1번 받아 시작일로 거른다."""
    _, fake = _crawl([])
    assert fake.list_params == [{"all": "true"}]


def test_lookback_does_not_add_requests():
    _, fake = _crawl([], lookback_days=5)
    assert len(fake.list_params) == 1


def test_only_postings_started_within_lookback_are_kept():
    items = [
        _list_item(1, [_emp(11, 1, IT_DUTY)], start="2026-09-23"),  # 어제 시작
        _list_item(2, [_emp(21, 1, IT_DUTY)], start="2026-09-22"),  # 그제 시작 (아직 열려 있는 이전 공고)
        _list_item(4, [_emp(41, 1, IT_DUTY)], start="2026-09-24"),  # 오늘 시작
        _list_item(5, [_emp(51, 1, IT_DUTY)], start="2026-09-26"),  # 시작 예정 (목록에 미리 올라온 것)
    ]
    jobs, _ = _crawl(items)
    assert [j.url.rsplit("=", 1)[1] for j in jobs] == ["1", "4"]


def test_monday_keeps_postings_started_over_the_weekend():
    items = [
        _list_item(1, [_emp(11, 1, IT_DUTY)], start="2026-09-25"),  # 금
        _list_item(2, [_emp(21, 1, IT_DUTY)], start="2026-09-26"),  # 토
        _list_item(3, [_emp(31, 1, IT_DUTY)], start="2026-09-27"),  # 일
        _list_item(4, [_emp(41, 1, IT_DUTY)], start="2026-09-28"),  # 월 (오늘)
    ]
    fake = FakeJasoseol(items, None, ())
    crawler = JasoseolCrawler(client=httpx.Client(transport=httpx.MockTransport(fake)), today=date(2026, 9, 28),
                              detail_delay_sec=0)
    jobs = crawler.fetch_jobs()
    assert [j.url.rsplit("=", 1)[1] for j in jobs] == ["2", "3", "4"]


# ── 채용구분 · 직무 필터 ───────────────────────────────────────────────────

def test_division_filter_keeps_new_grad_intern_and_new_or_experienced():
    items = [
        _list_item(1, [_emp(11, 1, IT_DUTY)]),  # 신입
        _list_item(2, [_emp(21, 2, IT_DUTY)]),  # 경력
        _list_item(3, [_emp(31, 3, IT_DUTY)]),  # 인턴
        _list_item(4, [_emp(41, 4, IT_DUTY)]),  # 계약직
        _list_item(5, [_emp(51, 5, IT_DUTY)]),  # 신입/경력
    ]
    jobs, _ = _crawl(items)
    assert [j.url.rsplit("=", 1)[1] for j in jobs] == ["1", "3", "5"]


def test_duty_filter_accepts_any_descendant_of_it_group():
    items = [
        _list_item(1, [_emp(11, 1, 175)]),  # 프론트엔드 (IT의 손자)
        _list_item(2, [_emp(21, 1, 166)]),  # 네트워크·보안·운영 (IT의 자식)
        _list_item(3, [_emp(31, 1, NON_IT_DUTY)]),  # 경영기획
    ]
    jobs, _ = _crawl(items)
    assert [j.url.rsplit("=", 1)[1] for j in jobs] == ["1", "2"]


def test_a_single_position_must_satisfy_both_division_and_duty():
    # 경력 IT 포지션 + 신입 비-IT 포지션: 두 조건을 한 포지션이 동시에 만족하지 않으므로 제외
    items = [_list_item(1, [_emp(11, 2, IT_DUTY), _emp(12, 1, NON_IT_DUTY)])]
    jobs, _ = _crawl(items)
    assert jobs == []


# ── 중복 · 제한 ────────────────────────────────────────────────────────────

def test_known_urls_are_skipped_without_detail_request():
    items = [
        _list_item(1, [_emp(11, 1, IT_DUTY)]),
        _list_item(2, [_emp(21, 1, IT_DUTY)]),
    ]
    jobs, fake = _crawl(items, known_urls={"https://jasoseol.com/recruit?ec=1"})
    assert [j.url for j in jobs] == ["https://jasoseol.com/recruit?ec=2"]
    assert fake.detail_requests == [2]


def test_limit_caps_number_of_jobs():
    items = [_list_item(i, [_emp(i * 10, 1, IT_DUTY)]) for i in (1, 2, 3)]
    jobs, _ = _crawl(items, limit=2)
    assert len(jobs) == 2


# ── RawJob 매핑 ────────────────────────────────────────────────────────────

def test_maps_listing_and_detail_to_rawjob():
    items = [_list_item(105263, [_emp(424682, 1, IT_DUTY)], name="NHN Cloud", end="2026-10-07")]
    jobs, _ = _crawl(items)
    job = jobs[0]

    assert job.source == "jasoseol"
    assert job.url == "https://jasoseol.com/recruit?ec=105263"
    assert job.apply_url == "https://careers.example.com/105263?utm_source=jasoseol"
    assert job.title == "공고105263"
    assert job.company == "NHN Cloud"
    assert job.deadline == datetime(2026, 10, 7, 23, 59, tzinfo=KST)
    assert job.jd_text == "본문 텍스트"


def test_missing_end_time_gives_no_deadline():
    item = _list_item(1, [_emp(11, 1, IT_DUTY)])
    item["end_time"] = None
    jobs, _ = _crawl([item])
    assert jobs[0].deadline is None


def test_payload_keeps_listing_item_and_detail_response_whole():
    items = [_list_item(1, [_emp(11, 1, IT_DUTY), _emp(12, 2, NON_IT_DUTY)])]
    jobs, _ = _crawl(items)
    payload = jobs[0].payload

    assert payload["listing"] == items[0]
    assert payload["detail"]["id"] == 1
    # 필터에서 탈락한 포지션도 원본에는 남는다
    assert len(payload["listing"]["employments"]) == 2


def test_hints_list_only_matched_positions_with_normalized_career_level():
    items = [
        _list_item(1, [
            _emp(11, 1, IT_DUTY),        # 매칭: 신입
            _emp(12, 5, 166),            # 매칭: 신입/경력
            _emp(13, 2, IT_DUTY),        # 탈락: 경력
            _emp(14, 1, NON_IT_DUTY),    # 탈락: 비-IT
        ])
    ]
    jobs, _ = _crawl(items)
    assert jobs[0].hints == {
        "positions": [
            {"name": "포지션11", "career_level": "신입"},
            {"name": "포지션12", "career_level": "신입/경력"},
        ]
    }


def test_career_level_joins_all_divisions_of_a_position_in_canonical_order():
    # 코드 번호순이면 신입(1)/경력(2)/인턴(3)이지만 표기는 신입/인턴/경력
    items = [
        _list_item(1, [_emp(11, [3, 1, 2], IT_DUTY)]),
        _list_item(2, [_emp(21, [6, 2], IT_DUTY)]),  # 신입/인턴 + 경력
        _list_item(3, [_emp(31, [1, 5], IT_DUTY)]),  # 신입 + 신입/경력
    ]
    jobs, _ = _crawl(items)
    assert [j.hints["positions"][0]["career_level"] for j in jobs] == [
        "신입/인턴/경력",
        "신입/인턴/경력",
        "신입/경력",
    ]


# ── JD 본문 ────────────────────────────────────────────────────────────────

def test_jd_text_strips_html_and_keeps_line_breaks():
    content = "<html><body><h3>담당업무</h3><p>API 개발</p><p>데이터 파이프라인</p></body></html>"
    items = [_list_item(1, [_emp(11, 1, IT_DUTY)])]
    details = {1: _detail(1, items[0]["employments"], content=content)}
    jobs, _ = _crawl(items, details=details)
    assert jobs[0].jd_text == "담당업무\nAPI 개발\n데이터 파이프라인"


def test_image_only_jd_gives_empty_text_and_keeps_image_in_payload():
    content = '<html><body><img src="https://cdn.example.com/jd.webp"></body></html>'
    items = [_list_item(1, [_emp(11, 1, IT_DUTY)])]
    details = {1: _detail(1, items[0]["employments"], content=content)}
    jobs, _ = _crawl(items, details=details)
    assert jobs[0].jd_text == ""
    assert "https://cdn.example.com/jd.webp" in jobs[0].payload["detail"]["content"]
    assert jobs[0].image_urls == ["https://cdn.example.com/jd.webp"]


def test_null_content_gives_empty_text():
    items = [_list_item(1, [_emp(11, 1, IT_DUTY)])]
    details = {1: _detail(1, items[0]["employments"], content=None)}
    jobs, _ = _crawl(items, details=details)
    assert jobs[0].jd_text == ""


# ── 오류 처리 ──────────────────────────────────────────────────────────────

def test_detail_failure_skips_only_that_posting():
    items = [
        _list_item(1, [_emp(11, 1, IT_DUTY)]),
        _list_item(2, [_emp(21, 1, IT_DUTY)]),
    ]
    jobs, _ = _crawl(items, failing_details={1})
    assert [j.url for j in jobs] == ["https://jasoseol.com/recruit?ec=2"]


# ── 수집 요약용 카운터 (로그·웹훅) ─────────────────────────────────────────

def test_counts_seen_and_detail_failed_postings():
    """'본 수'는 목록 API가 준 전체 수(기간·직무로 거르기 전), 상세 실패는 따로 센다.

    기간 안 공고가 없는 날(주말)에도 0이 아니어야 사이트 변화 경고가 잘못 뜨지 않는다 (2026-10-26).
    """
    items = [
        _list_item(1, [_emp(11, 1, IT_DUTY)]),
        _list_item(2, [_emp(21, 1, IT_DUTY)]),
        _list_item(3, [_emp(31, 1, IT_DUTY)]),
        _list_item(4, [_emp(41, 1, IT_DUTY)], start="2026-09-01"),  # 기간 밖
        _list_item(5, [_emp(51, 1, NON_IT_DUTY)]),                  # IT 아님
    ]
    fake = FakeJasoseol(items, None, {3})
    crawler = JasoseolCrawler(
        client=httpx.Client(transport=httpx.MockTransport(fake)), today=TODAY, detail_delay_sec=0,
    )
    jobs = crawler.fetch_jobs(known_urls={"https://jasoseol.com/recruit?ec=1"})

    assert [j.url for j in jobs] == ["https://jasoseol.com/recruit?ec=2"]
    assert crawler.seen_count == 5
    assert crawler.detail_failed_count == 1
