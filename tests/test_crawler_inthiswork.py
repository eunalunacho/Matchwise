"""InthisworkCrawler 테스트 — WordPress REST API 기반. httpx.MockTransport로 실제 클라이언트 경로를 탄다.

응답 구조는 2026-09-24에 inthiswork.com/wp-json/wp/v2 에서 직접 받은 형태를 따른다:
  GET /categories?per_page=100  → [{"id", "name"}, ...]
  GET /tags?per_page=100        → [{"id", "name"}, ...]
  GET /posts?...                → [{"id","date","link","title":{"rendered"},"content":{"rendered"},
                                    "categories":[id],"tags":[id]}]   헤더 X-WP-TotalPages
"""
import math
from datetime import date

import httpx
import pytest

from crawler.inthiswork import InthisworkCrawler, _split_title

TODAY = date(2026, 9, 24)  # "어제"는 2026-09-23

CAT_NEW, CAT_JUNIOR, CAT_EDU = 191700167, 191700168, 191700345
TAG_IT, TAG_STARTUP = 191700187, 191700265

CATEGORIES = [
    {"id": CAT_NEW, "name": "신입/인턴"},
    {"id": CAT_JUNIOR, "name": "주니어경력"},
    {"id": CAT_EDU, "name": "교육/대외활동"},
]
TAGS = [
    {"id": TAG_IT, "name": "IT개발"},
    {"id": TAG_STARTUP, "name": "스타트업"},
]

APPLY_LINK = '<a href="https://careers.example.com/apply/1">지원하러 가기</a>'


def _post(post_id, *, title="회사｜포지션", content=None, categories=(CAT_NEW,), tags=(TAG_IT,)):
    if content is None:
        content = f"<p>본문 텍스트</p>{APPLY_LINK}"
    return {
        "id": post_id,
        "date": "2026-09-23T18:22:30",
        "date_gmt": "2026-09-23T09:22:30",
        "link": f"https://inthiswork.com/archives/{post_id}",
        "title": {"rendered": title},
        "content": {"rendered": content},
        "categories": list(categories),
        "tags": list(tags),
    }


class FakeInthiswork:
    """MockTransport 핸들러. 받은 요청을 기록해 검증에 쓴다.

    failures: {경로: [실패 응답, ...]} — 그 경로의 첫 요청들에 차례로 돌려줄 응답. 다 쓰면 정상 응답.
    """

    def __init__(self, posts, categories=CATEGORIES, tags=TAGS, status=200, failures=None):
        self.posts = posts
        self.categories = categories
        self.tags = tags
        self.status = status
        self.failures = {path: list(responses) for path, responses in (failures or {}).items()}
        self.requests: list[tuple[str, dict]] = []

    def paths(self):
        return [path for path, _ in self.requests]

    def post_params(self):
        return [params for path, params in self.requests if path.endswith("/posts")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        self.requests.append((path, params))
        if self.failures.get(path):
            failure = self.failures[path].pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        if self.status != 200:
            return httpx.Response(self.status, json={"code": "boom"})
        if path == "/wp-json/wp/v2/categories":
            return httpx.Response(200, json=self.categories)
        if path == "/wp-json/wp/v2/tags":
            return httpx.Response(200, json=self.tags)
        if path == "/wp-json/wp/v2/posts":
            per_page, page = int(params["per_page"]), int(params.get("page", "1"))
            chunk = self.posts[(page - 1) * per_page : page * per_page]
            total_pages = max(1, math.ceil(len(self.posts) / per_page))
            return httpx.Response(
                200,
                json=chunk,
                headers={"X-WP-Total": str(len(self.posts)), "X-WP-TotalPages": str(total_pages)},
            )
        return httpx.Response(404)


def _crawl(posts, *, known_urls=None, limit=None, lookback_days=None, page_size=100, sleeps=None, **fake_kwargs):
    """sleeps: 넘기면 크롤러가 쉰 시간(초)을 여기에 기록한다. 실제로는 쉬지 않는다."""
    fake = FakeInthiswork(posts, **fake_kwargs)
    client = httpx.Client(transport=httpx.MockTransport(fake))
    options = {} if lookback_days is None else {"lookback_days": lookback_days}
    record = sleeps if sleeps is not None else []
    crawler = InthisworkCrawler(
        client=client, today=TODAY, page_size=page_size, sleep=record.append, **options
    )
    jobs = crawler.fetch_jobs(limit=limit, known_urls=known_urls)
    return jobs, fake


# ── _split_title ───────────────────────────────────────────────────────────

def test_split_title_with_separator():
    company, position = _split_title("SOOP｜AI 모델 개발")
    assert company == "SOOP"
    assert position == "AI 모델 개발"


def test_split_title_no_separator():
    company, position = _split_title("52g ReBoot Camp – GS K-뉴딜 아카데미")
    assert company == "52g ReBoot Camp – GS K-뉴딜 아카데미"
    assert position == ""


def test_split_title_strips_whitespace():
    company, position = _split_title("  Company A ｜ Backend Engineer  ")
    assert company == "Company A"
    assert position == "Backend Engineer"


# ── 서버 쪽 필터 · 조회 기간 · 호출 횟수 ───────────────────────────────────

def test_filters_by_category_and_tag_ids_resolved_from_names():
    _, fake = _crawl([_post(1)])
    params = fake.post_params()[0]
    assert params["categories"] == str(CAT_NEW)
    assert params["tags"] == str(TAG_IT)


def test_default_window_covers_the_last_two_days_in_site_local_time():
    _, fake = _crawl([_post(1)])
    params = fake.post_params()[0]
    assert params["after"] == "2026-09-22T00:00:00"
    assert params["before"] == "2026-09-24T00:00:00"


def test_lookback_of_one_day_is_yesterday_only():
    _, fake = _crawl([_post(1)], lookback_days=1)
    params = fake.post_params()[0]
    assert params["after"] == "2026-09-23T00:00:00"
    assert params["before"] == "2026-09-24T00:00:00"


def test_lookback_days_widens_window_without_extra_requests():
    _, fake = _crawl([_post(1)], lookback_days=3)
    assert fake.post_params()[0]["after"] == "2026-09-21T00:00:00"
    assert len(fake.requests) == 3


def test_typical_daily_run_costs_three_requests():
    """분류(카테고리·태그) 2회 + 글 목록 1회. 상세 페이지 요청은 없다(본문이 목록 응답에 포함)."""
    _, fake = _crawl([_post(i) for i in range(1, 18)])  # 하루 최대치(17건) 수준
    assert fake.paths() == [
        "/wp-json/wp/v2/categories",
        "/wp-json/wp/v2/tags",
        "/wp-json/wp/v2/posts",
    ]


def test_follows_pagination_until_last_page():
    jobs, fake = _crawl([_post(1), _post(2), _post(3)], page_size=2)
    assert [j.url.rsplit("/", 1)[1] for j in jobs] == ["1", "2", "3"]
    assert [p["page"] for p in fake.post_params()] == ["1", "2"]


def test_missing_taxonomy_name_fails_loudly():
    with pytest.raises(RuntimeError, match="IT개발"):
        _crawl([_post(1)], tags=[{"id": TAG_STARTUP, "name": "스타트업"}])


def test_http_error_propagates_after_retries():
    """5xx가 계속되면 처음 1번 + 재시도 2번 후 에러를 올린다."""
    with pytest.raises(httpx.HTTPStatusError):
        _crawl([_post(1)], status=503)


# ── 요청 간격 · 재시도 (2026-10-25: Railway에서 429) ─────────────────────────

def test_waits_between_requests_but_not_before_the_first():
    sleeps = []
    _crawl([_post(1)], sleeps=sleeps)
    assert sleeps == [1.5, 1.5]  # categories → (1.5) → tags → (1.5) → posts


def test_retries_429_using_retry_after_header():
    sleeps = []
    jobs, fake = _crawl(
        [_post(1)],
        sleeps=sleeps,
        failures={"/wp-json/wp/v2/posts": [httpx.Response(429, headers={"Retry-After": "7"})]},
    )
    assert len(jobs) == 1
    assert fake.paths().count("/wp-json/wp/v2/posts") == 2
    assert sleeps == [1.5, 1.5, 7.0]


def test_retries_without_retry_after_wait_30_then_60_and_give_up():
    sleeps = []
    too_many = [httpx.Response(429) for _ in range(3)]
    with pytest.raises(httpx.HTTPStatusError):
        _crawl([_post(1)], sleeps=sleeps, failures={"/wp-json/wp/v2/posts": too_many})
    assert sleeps == [1.5, 1.5, 30.0, 60.0]


def test_retry_after_is_capped():
    sleeps = []
    _crawl(
        [_post(1)],
        sleeps=sleeps,
        failures={"/wp-json/wp/v2/categories": [httpx.Response(429, headers={"Retry-After": "3600"})]},
    )
    assert sleeps[0] == 120.0


def test_transport_error_is_retried():
    jobs, fake = _crawl(
        [_post(1)],
        failures={"/wp-json/wp/v2/tags": [httpx.ConnectError("reset")]},
    )
    assert len(jobs) == 1
    assert fake.paths().count("/wp-json/wp/v2/tags") == 2


def test_client_error_other_than_429_is_not_retried():
    fake_kwargs = {"failures": {"/wp-json/wp/v2/posts": [httpx.Response(403)]}}
    with pytest.raises(httpx.HTTPStatusError):
        _crawl([_post(1)], **fake_kwargs)


# ── 중복 · 제한 ────────────────────────────────────────────────────────────

def test_known_urls_are_skipped():
    jobs, _ = _crawl(
        [_post(1), _post(2)], known_urls={"https://inthiswork.com/archives/1"}
    )
    assert [j.url for j in jobs] == ["https://inthiswork.com/archives/2"]


def test_limit_caps_number_of_jobs():
    jobs, _ = _crawl([_post(1), _post(2), _post(3)], limit=2)
    assert len(jobs) == 2


# ── RawJob 매핑 ────────────────────────────────────────────────────────────

def test_maps_post_to_rawjob():
    post = _post(399936, title="베어로보틱스｜Firmware Engineering Intern")
    jobs, _ = _crawl([post])
    job = jobs[0]

    assert job.source == "inthiswork"
    assert job.url == "https://inthiswork.com/archives/399936"
    assert job.title == "베어로보틱스｜Firmware Engineering Intern"
    assert job.company == "베어로보틱스"
    assert job.jd_text == "본문 텍스트"
    assert job.apply_url == "https://careers.example.com/apply/1"
    assert job.deadline is None  # 인디스워크는 구조화된 마감일을 주지 않는다


def test_title_html_entities_are_unescaped():
    jobs, _ = _crawl([_post(1, title="인피니언 코리아｜Internship &#8211; Radar")])
    assert jobs[0].title == "인피니언 코리아｜Internship – Radar"


def test_payload_keeps_raw_post_and_resolved_names():
    post = _post(1, categories=(CAT_NEW, CAT_JUNIOR), tags=(TAG_IT, TAG_STARTUP))
    jobs, _ = _crawl([post])
    payload = jobs[0].payload

    assert payload["post"] == post
    assert payload["category_names"] == ["신입/인턴", "주니어경력"]
    assert payload["tag_names"] == ["IT개발", "스타트업"]


def test_unknown_taxonomy_ids_on_a_post_are_left_out_of_names():
    jobs, _ = _crawl([_post(1, tags=(TAG_IT, 999))])
    assert jobs[0].payload["tag_names"] == ["IT개발"]


# ── hints ──────────────────────────────────────────────────────────────────

def test_hints_new_grad_and_intern_category():
    jobs, _ = _crawl([_post(1, title="SOOP｜AI 모델 개발", categories=(CAT_NEW,))])
    assert jobs[0].hints == {
        "positions": [{"name": "AI 모델 개발", "career_level": "신입/인턴"}]
    }


def test_hints_junior_career_category_adds_experienced():
    jobs, _ = _crawl([_post(1, categories=(CAT_NEW, CAT_JUNIOR))])
    assert jobs[0].hints["positions"][0]["career_level"] == "신입/인턴/경력"


def test_hints_position_falls_back_to_title_when_no_separator():
    jobs, _ = _crawl([_post(1, title="(주)드림텍 2026년 4분기 상시채용")])
    assert jobs[0].hints["positions"][0]["name"] == "(주)드림텍 2026년 4분기 상시채용"


# ── JD 본문 ────────────────────────────────────────────────────────────────

def test_apply_button_text_is_removed_from_jd_text():
    jobs, _ = _crawl([_post(1, content=f"<p>담당업무</p><p>API 개발</p>{APPLY_LINK}")])
    assert jobs[0].jd_text == "담당업무\nAPI 개발"


def test_missing_apply_link_gives_no_apply_url():
    jobs, _ = _crawl([_post(1, content="<p>본문만 있음</p>")])
    assert jobs[0].apply_url is None
    assert jobs[0].jd_text == "본문만 있음"


def test_image_only_jd_gives_empty_text_and_keeps_image_in_payload():
    content = f'<p><img src="https://i0.wp.com/jd.jpg"></p>{APPLY_LINK}'
    jobs, _ = _crawl([_post(1, content=content)])
    assert jobs[0].jd_text == ""
    assert "https://i0.wp.com/jd.jpg" in jobs[0].payload["post"]["content"]["rendered"]
    assert jobs[0].image_urls == ["https://i0.wp.com/jd.jpg"]


def test_text_jd_without_images_has_no_image_urls():
    jobs, _ = _crawl([_post(1, content="<p>텍스트 JD</p>")])
    assert jobs[0].image_urls == []


def test_all_body_images_are_listed_in_order():
    content = '<img src="https://i0.wp.com/1.jpg"><p>중간 텍스트</p><img src="https://i0.wp.com/2.jpg">'
    jobs, _ = _crawl([_post(1, content=content)])
    assert jobs[0].image_urls == ["https://i0.wp.com/1.jpg", "https://i0.wp.com/2.jpg"]


# ── 수집 요약용 카운터 (로그·웹훅) ─────────────────────────────────────────

def test_seen_count_includes_known_posts():
    """목록에서 본 글 수는 이미 아는 글도 센다 — 사이트가 0건을 주는 이상을 신규 0건과 구분하기 위해."""
    fake = FakeInthiswork([_post(1), _post(2), _post(3)])
    crawler = InthisworkCrawler(
        client=httpx.Client(transport=httpx.MockTransport(fake)), today=TODAY, sleep=lambda _: None
    )
    jobs = crawler.fetch_jobs(known_urls={"https://inthiswork.com/archives/1"})

    assert len(jobs) == 2
    assert crawler.seen_count == 3
    assert crawler.detail_failed_count == 0  # 본문이 목록 응답에 있어 상세 요청 자체가 없음
