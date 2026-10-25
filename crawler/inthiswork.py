"""인디스워크(inthiswork.com) 채용공고 크롤러 — WordPress REST API 기반.

Playwright로 페이지를 긁는 대신 사이트의 공개 REST API(/wp-json/wp/v2)를 호출한다.
글 목록 응답에 본문(content)이 이미 들어 있어서 상세 페이지 요청이 필요 없다.

하루 1회 실행 기준 호출은 3번이다 (분류 2회 + 글 목록 1회, 결과가 100건을 넘으면 페이지당 1회 추가).
  1) GET /categories  카테고리 이름 → ID  ("신입/인턴")
  2) GET /tags        태그 이름 → ID      ("IT개발")
  3) GET /posts       조회 기간 + 카테고리 + 태그를 서버에서 필터링한 글 목록 (본문 포함)

대상: 조회 기간(기본 최근 2일, 사이트 로컬 시각=KST)에 게시됐고, 카테고리 "신입/인턴"이며 태그 "IT개발"인 글.
카테고리 "신입/인턴"은 경력 전용(주니어경력만 붙은 글)과 교육/대외활동·콘텐츠 글을 제외한다.

요청 사이에 1.5초씩 쉬고, 429·5xx·연결 오류는 2번까지 다시 시도한다(2026-10-25 Railway에서 429 — _get 참고).
"""
import html
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from crawler.base import (
    DEFAULT_LOOKBACK_DAYS,
    BaseCrawler,
    RawJob,
    extract_image_urls,
    join_career_level,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://inthiswork.com"
API_URL = f"{BASE_URL}/wp-json/wp/v2"

KST = timezone(timedelta(hours=9))

CATEGORY_NEW = "신입/인턴"
CATEGORY_JUNIOR = "주니어경력"  # 붙어 있으면 경력 지원도 가능한 글
TAG_IT = "IT개발"

APPLY_BUTTON_TEXT = "지원하러 가기"  # 본문 안의 기업 지원 페이지 링크 문구
_TITLE_SEP = "｜"  # U+FF5C FULLWIDTH VERTICAL LINE

# 목록 응답에 필요한 필드만 받는다 (link/embedded 등 불필요한 필드 제외)
_POST_FIELDS = "id,date,date_gmt,link,title,content,categories,tags"

_HEADERS = {
    "User-Agent": "Matchwise/0.2 (personal job-search tool)",
    "Accept": "application/json",
}

REQUEST_DELAY_SEC = 1.5  # 요청 사이 간격 (첫 요청 앞에는 쉬지 않음)
RETRY_WAITS_SEC = (30.0, 60.0)  # Retry-After가 없을 때 n번째 재시도 전 대기. 길이 = 재시도 횟수
MAX_RETRY_AFTER_SEC = 120.0  # 서버가 준 Retry-After가 길어도 이만큼만 기다린다
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _split_title(raw: str) -> tuple[str, str]:
    """Return (company, position). Falls back to (raw, '') if no separator."""
    if _TITLE_SEP in raw:
        company, _, position = raw.partition(_TITLE_SEP)
        return company.strip(), position.strip()
    return raw.strip(), ""


def _parse_content(content_html: str) -> tuple[str, str | None]:
    """본문 HTML → (JD 텍스트, 기업 지원 페이지 URL).

    "지원하러 가기" 링크는 JD 내용이 아니라서 텍스트에서 빼고 href만 꺼낸다.
    이미지로만 된 JD는 텍스트가 빈 문자열이 된다.
    """
    soup = BeautifulSoup(content_html or "", "html.parser")
    apply_url = None
    for link in soup.find_all("a"):
        if link.get_text(strip=True) == APPLY_BUTTON_TEXT:
            apply_url = apply_url or link.get("href")
            link.decompose()
    return soup.get_text(separator="\n", strip=True), apply_url


class InthisworkCrawler(BaseCrawler):
    source = "inthiswork"

    def __init__(
        self,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        client: httpx.Client | None = None,
        today: date | None = None,
        page_size: int = 100,
        timeout_sec: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """
        Args:
            lookback_days: 오늘(KST)로부터 며칠 전까지 게시된 글을 볼지. 2면 그제·어제, 1이면 어제만.
                넓혀도 글 목록 호출 횟수는 그대로다 (결과가 page_size를 넘을 때만 페이지 추가).
            client: 테스트용 주입. 없으면 자체 생성하고 종료 시 닫는다.
            today: 테스트용 기준일(KST). 없으면 현재 KST 날짜.
            page_size: 한 번에 받을 글 수. WordPress 상한은 100.
            sleep: 요청 간격·재시도 대기에 쓰는 함수. 테스트는 기록만 하는 함수를 넣는다.
        """
        self._lookback_days = lookback_days
        self._client = client
        self._today = today
        self._page_size = page_size
        self._timeout = timeout_sec
        self._sleep = sleep
        self._requested = False

    def fetch_jobs(
        self, limit: int | None = None, known_urls: set[str] | None = None
    ) -> list[RawJob]:
        self.seen_count = 0
        self.detail_failed_count = 0
        self._requested = False
        own_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout, headers=_HEADERS)
        try:
            return self._crawl(client, limit, known_urls or set())
        finally:
            if own_client:
                client.close()

    def _crawl(
        self, client: httpx.Client, limit: int | None, known_urls: set[str]
    ) -> list[RawJob]:
        today = self._today or datetime.now(KST).date()
        first_day = today - timedelta(days=self._lookback_days)

        category_names = self._fetch_taxonomy(client, "categories")
        tag_names = self._fetch_taxonomy(client, "tags")
        category_id = self._id_of(category_names, CATEGORY_NEW, "카테고리")
        tag_id = self._id_of(tag_names, TAG_IT, "태그")

        jobs: list[RawJob] = []
        page = 1
        while True:
            response = self._get(
                client,
                f"{API_URL}/posts",
                params={
                    "per_page": self._page_size,
                    "page": page,
                    "after": f"{first_day.isoformat()}T00:00:00",
                    "before": f"{today.isoformat()}T00:00:00",
                    "categories": category_id,
                    "tags": tag_id,
                    "orderby": "date",
                    "order": "asc",
                    "_fields": _POST_FIELDS,
                },
            )

            for post in response.json():
                self.seen_count += 1
                if post["link"] in known_urls:
                    continue
                job = self._build_job(post, category_names, tag_names)
                jobs.append(job)
                logger.info("crawled %s | %s", job.company, job.title)
                if limit and len(jobs) >= limit:
                    return jobs

            if page >= int(response.headers.get("X-WP-TotalPages", "1")):
                return jobs
            page += 1

    def _fetch_taxonomy(self, client: httpx.Client, kind: str) -> dict[int, str]:
        """카테고리/태그 전체를 {id: 이름}으로. 인디스워크는 각각 11개/53개(2026-09 기준)."""
        response = self._get(client, f"{API_URL}/{kind}", params={"per_page": 100, "_fields": "id,name"})
        return {item["id"]: item["name"] for item in response.json()}

    def _get(self, client: httpx.Client, url: str, params: dict) -> httpx.Response:
        """GET 1건. 앞 요청과 REQUEST_DELAY_SEC 간격을 두고, 429·5xx·연결 오류는 2번까지 다시 시도한다.

        재시도 전에는 서버의 Retry-After(초, 최대 MAX_RETRY_AFTER_SEC)만큼, 없으면 RETRY_WAITS_SEC 순서대로 쉰다.
        재시도를 다 써도 실패하면 마지막 에러를 그대로 올린다 — 사이트 1곳 실패로 요약된다(_crawl_one_site).
        """
        if self._requested:
            self._sleep(REQUEST_DELAY_SEC)
        self._requested = True

        for attempt in range(len(RETRY_WAITS_SEC) + 1):
            last_try = attempt == len(RETRY_WAITS_SEC)
            try:
                response = client.get(url, params=params)
            except httpx.TransportError as exc:
                if last_try:
                    raise
                wait = RETRY_WAITS_SEC[attempt]
                logger.warning("inthiswork 연결 오류 %s — %.0f초 뒤 재시도 (%s)", url, wait, exc)
                self._sleep(wait)
                continue

            if response.status_code not in _RETRYABLE_STATUS or last_try:
                response.raise_for_status()
                return response

            wait = self._retry_wait(response, attempt)
            logger.warning(
                "inthiswork %s %s — %.0f초 뒤 재시도 (Retry-After=%s, 본문=%r)",
                response.status_code, url, wait, response.headers.get("Retry-After"), response.text[:200],
            )
            self._sleep(wait)
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_wait(response: httpx.Response, attempt: int) -> float:
        """Retry-After가 초 단위 숫자면 그 값(상한 적용), 아니면 RETRY_WAITS_SEC[attempt]."""
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.strip().isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER_SEC)
        return RETRY_WAITS_SEC[attempt]

    @staticmethod
    def _id_of(names: dict[int, str], wanted: str, label: str) -> int:
        for taxonomy_id, name in names.items():
            if name == wanted:
                return taxonomy_id
        raise RuntimeError(
            f"인디스워크 {label} '{wanted}'를 찾을 수 없음 — 분류 이름이 바뀌었을 수 있다"
        )

    def _build_job(
        self, post: dict, category_names: dict[int, str], tag_names: dict[int, str]
    ) -> RawJob:
        raw_title = html.unescape(post["title"]["rendered"])
        company, position = _split_title(raw_title)
        jd_text, apply_url = _parse_content(post["content"]["rendered"])

        categories = [category_names[i] for i in post["categories"] if i in category_names]
        tags = [tag_names[i] for i in post["tags"] if i in tag_names]

        levels = ["신입", "인턴"]  # 카테고리 "신입/인턴"으로 필터링했으므로 항상 해당
        if CATEGORY_JUNIOR in categories:
            levels.append("경력")
        career_level = join_career_level(levels)

        return RawJob(
            source=self.source,
            url=post["link"],
            title=raw_title,
            company=company,
            jd_text=jd_text,
            apply_url=apply_url,
            payload={"post": post, "category_names": categories, "tag_names": tags},
            hints={"positions": [{"name": position or raw_title, "career_level": career_level}]},
            image_urls=extract_image_urls(post["content"]["rendered"]),
        )
