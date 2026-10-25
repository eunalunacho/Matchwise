"""자소설닷컴(jasoseol.com) 채용공고 크롤러.

Playwright 없이 사이트가 내부에서 쓰는 JSON API를 직접 호출한다 (비공식 API — 스키마가 바뀔 수 있음).

  1) GET /api/v1/employment_companies?all=true  지금 열려 있는 공고 전체 (포지션별 채용구분·직무코드 포함)
  2) GET /api/v1/duty-groups                   직무 분류 트리 — IT·인터넷(94)과 그 하위 코드를 구한다
  3) GET /api/v1/employment_companies/{id}     상세 — 기업 지원 페이지 URL, JD 본문(HTML)

목록은 예전엔 캘린더 API(POST /employment/calendar_list.json)를 하루 창으로 나눠 썼다. 2026-10-25 확인 결과
그 API가 "그날 마감하는" 공고만 주도록 바뀌어 시작일 기준 수집이 0건이 됐고, 마감일 기준 60일치를 모아도
열린 공고 목록(1)의 일부(시작 10/19~23 기준 30건 vs 87건, 목록에 없는 건 0건)였다. 목록은 시작일 필터
파라미터를 무시하므로 받은 뒤 시작일로 직접 거른다.

대상: 시작일이 조회 기간(기본 최근 2일, KST) 안에 있고, 한 포지션이 채용구분(신입/인턴/신입·경력)과
IT 직무를 동시에 만족하는 공고. JD가 이미지면 jd_text는 빈 문자열이고 이미지는 payload에 남는다.
"""
import logging
import time
from collections import defaultdict
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

BASE_URL = "https://jasoseol.com"
LIST_URL = f"{BASE_URL}/api/v1/employment_companies"
DUTY_GROUPS_URL = f"{BASE_URL}/api/v1/duty-groups"
DETAIL_URL = f"{BASE_URL}/api/v1/employment_companies/{{posting_id}}"

KST = timezone(timedelta(hours=9))

IT_ROOT_DUTY_ID = 94  # 직무 분류 대분류 "IT·인터넷"

# 자소설 채용구분 코드 (프런트 번들의 매핑. 5·6은 실사용 확인 전)
CAREER_LEVELS = {1: "신입", 2: "경력", 3: "인턴", 4: "계약직", 5: "신입/경력", 6: "신입/인턴", 7: "교육"}
TARGET_DIVISIONS = {1, 3, 5, 6}

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": f"{BASE_URL}/recruit",
}


def job_url(posting_id: int) -> str:
    """자소설 공고 페이지 URL. 중복 제거 키로 쓴다."""
    return f"{BASE_URL}/recruit?ec={posting_id}"


def _divisions(employment: dict) -> set[int]:
    """division은 보통 list다(목록·상세 API). 예전 캘린더 API처럼 int로 와도 받는다."""
    raw = employment.get("division")
    if isinstance(raw, list):
        return set(raw)
    return {raw} if raw is not None else set()


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)


class JasoseolCrawler(BaseCrawler):
    source = "jasoseol"

    def __init__(
        self,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        client: httpx.Client | None = None,
        today: date | None = None,
        detail_delay_sec: float = 0.3,
        timeout_sec: float = 20.0,
    ):
        """
        Args:
            lookback_days: 오늘(KST)로부터 며칠 전까지 시작한 공고를 볼지. 2면 그제·어제, 1이면 어제만.
            client: 테스트용 주입. 없으면 자체 생성하고 종료 시 닫는다.
            today: 테스트용 기준일(KST). 없으면 현재 KST 날짜.
            detail_delay_sec: 상세 API 호출 사이 대기 (서버 부담 완화).
        """
        self._lookback_days = lookback_days
        self._client = client
        self._today = today
        self._detail_delay = detail_delay_sec
        self._timeout = timeout_sec

    def fetch_jobs(
        self, limit: int | None = None, known_urls: set[str] | None = None
    ) -> list[RawJob]:
        self.seen_count = 0
        self.detail_failed_count = 0
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

        it_duty_ids = self._load_it_duty_ids(client)
        items = self._get_json(client.get, LIST_URL, params={"all": "true"})

        jobs: list[RawJob] = []
        for item in items:
            started = datetime.fromisoformat(item["start_time"]).astimezone(KST).date()
            if not first_day <= started < today:
                continue  # 이전에 시작해 아직 열려 있거나, 시작 예정인 공고

            matched = [e for e in item.get("employments", []) if self._matches(e, it_duty_ids)]
            if not matched:
                continue

            self.seen_count += 1
            url = job_url(item["id"])
            if url in known_urls:
                continue  # 이미 DB에 있는 공고 — 상세 요청 생략

            job = self._build_job(client, item, matched, url)
            if job is None:
                self.detail_failed_count += 1
                continue
            jobs.append(job)
            logger.info("crawled %s | %s", job.company, job.title)

            if limit and len(jobs) >= limit:
                break

        return jobs

    @staticmethod
    def _matches(employment: dict, it_duty_ids: set[int]) -> bool:
        """한 포지션이 채용구분(신입/인턴/신입·경력)과 IT 직무를 동시에 만족하는지."""
        duty_ids = set(employment.get("duty_group_ids") or [])
        return bool(_divisions(employment) & TARGET_DIVISIONS) and bool(duty_ids & it_duty_ids)

    def _load_it_duty_ids(self, client: httpx.Client) -> set[int]:
        """직무 트리에서 IT·인터넷(94)과 모든 하위 코드를 구한다."""
        groups = self._get_json(client.get, DUTY_GROUPS_URL)
        children: dict[int | None, list[int]] = defaultdict(list)
        for group in groups:
            children[group["group_id"]].append(group["id"])

        ids = {IT_ROOT_DUTY_ID}
        stack = [IT_ROOT_DUTY_ID]
        while stack:
            for child in children[stack.pop()]:
                ids.add(child)
                stack.append(child)
        return ids

    def _build_job(
        self, client: httpx.Client, item: dict, matched: list[dict], url: str
    ) -> RawJob | None:
        """상세 API를 호출해 RawJob을 만든다. 실패하면 로그만 남기고 None."""
        if self._detail_delay:
            time.sleep(self._detail_delay)
        try:
            detail = self._get_json(client.get, DETAIL_URL.format(posting_id=item["id"]))
        except (httpx.HTTPError, ValueError):
            logger.warning("jasoseol 상세 조회 실패 id=%s", item["id"], exc_info=True)
            return None

        field_names = {e["id"]: e.get("field") for e in detail.get("employments", [])}
        positions = [
            {
                "name": field_names.get(e["id"]) or item["title"],
                "career_level": join_career_level(
                    part
                    for d in _divisions(e) if d in CAREER_LEVELS
                    for part in CAREER_LEVELS[d].split("/")
                ),
            }
            for e in matched
        ]

        end_time = item.get("end_time")
        return RawJob(
            source=self.source,
            url=url,
            title=item["title"],
            company=item["name"],
            jd_text=_html_to_text(detail.get("content")),
            deadline=datetime.fromisoformat(end_time) if end_time else None,
            apply_url=detail.get("employment_page_url"),
            payload={"listing": item, "detail": detail},
            hints={"positions": positions},
            image_urls=extract_image_urls(detail.get("content")),
        )

    @staticmethod
    def _get_json(request, url: str, **kwargs):
        response = request(url, **kwargs)
        response.raise_for_status()
        return response.json()
