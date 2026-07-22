import logging
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from crawler.base import BaseCrawler, RawJob

logger = logging.getLogger(__name__)

BASE_URL = "https://www.superookie.com"
LIST_URL = f"{BASE_URL}/jobs?positions[]=0&positions[]=1"

_DEADLINE_RE = re.compile(r"(\d{2}/\d{2})\s+\d{2}:\d{2}\s*마감")


def _parse_deadline(text: str) -> datetime | None:
    """"07/13 23:59 마감" 형식에서 datetime 추출. 파싱 실패 시 None."""
    m = _DEADLINE_RE.search(text)
    if not m:
        return None
    month_day = m.group(1)  # "07/13"
    year = datetime.now(timezone.utc).year
    try:
        return datetime.strptime(f"{year}/{month_day}", "%Y/%m/%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


class SuperookieCrawler(BaseCrawler):
    source = "superookie"

    def __init__(self, timeout_ms: int = 30_000, headless: bool = True):
        self._timeout = timeout_ms
        self._headless = headless

    def fetch_jobs(self, limit: int | None = None) -> list[RawJob]:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self._headless)
            page = browser.new_page()
            try:
                return self._crawl(page, limit)
            finally:
                browser.close()

    def _crawl(self, page, limit: int | None) -> list[RawJob]:
        page.goto(LIST_URL, timeout=self._timeout)
        page.wait_for_selector(".main-item-job", timeout=self._timeout)
        html = page.content()

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".main-item-job")
        if limit:
            cards = cards[:limit]

        jobs: list[RawJob] = []
        for card in cards:
            link = card.select_one("a.job-detail-link")
            if not link:
                continue
            href = link.get("href", "").strip()
            if not href:
                continue
            url = BASE_URL + href if href.startswith("/") else href

            company_el = card.select_one("h5")
            title_el = card.select_one("p.job-title")
            deadline_el = card.select_one("div.color-gray.mobile-text-12")

            company = company_el.get_text(strip=True) if company_el else ""
            title = title_el.get_text(strip=True) if title_el else ""
            deadline_text = deadline_el.get_text(strip=True) if deadline_el else ""
            deadline = _parse_deadline(deadline_text)

            jd_text = self._fetch_jd(page, url)
            jobs.append(RawJob(
                source=self.source,
                url=url,
                title=title,
                company=company,
                jd_text=jd_text,
                deadline=deadline,
            ))
            logger.info("crawled %s | %s", company, title)

        return jobs

    def _fetch_jd(self, page, url: str) -> str:
        try:
            page.goto(url, timeout=self._timeout)
            page.wait_for_selector(".job-detail", timeout=self._timeout)
            html = page.content()
        except PWTimeout:
            logger.warning("timeout fetching %s", url)
            return ""

        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one(".job-detail")
        return content.get_text(separator="\n", strip=True) if content else ""
