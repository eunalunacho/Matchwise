import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from crawler.base import BaseCrawler, RawJob

logger = logging.getLogger(__name__)

LIST_URL = "https://inthiswork.com/it"
_TITLE_SEP = "｜"  # U+FF5C FULLWIDTH VERTICAL LINE


def _split_title(raw: str) -> tuple[str, str]:
    """Return (company, position). Falls back to (raw, '') if no separator."""
    if _TITLE_SEP in raw:
        company, _, position = raw.partition(_TITLE_SEP)
        return company.strip(), position.strip()
    return raw.strip(), ""


class InthisworkCrawler(BaseCrawler):
    source = "inthiswork"

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
        page.wait_for_selector(".dpt-entry", timeout=self._timeout)
        html = page.content()

        soup = BeautifulSoup(html, "html.parser")
        entries = soup.select(".dpt-entry")
        if limit:
            entries = entries[:limit]

        jobs: list[RawJob] = []
        for entry in entries:
            link_tag = entry.select_one("h3.dpt-title a.dpt-title-link")
            if not link_tag:
                continue
            raw_title = link_tag.get_text(strip=True)
            url = link_tag.get("href", "").strip()
            if not url:
                continue

            company, position = _split_title(raw_title)
            title = raw_title
            jd_text = self._fetch_jd(page, url)

            jobs.append(RawJob(
                source=self.source,
                url=url,
                title=title,
                company=company,
                jd_text=jd_text,
            ))
            logger.info("crawled %s | %s", company, position)

        return jobs

    def _fetch_jd(self, page, url: str) -> str:
        try:
            page.goto(url, timeout=self._timeout)
            page.wait_for_selector(".post-content", timeout=self._timeout)
            html = page.content()
        except PWTimeout:
            logger.warning("timeout fetching %s", url)
            return ""

        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one(".post-content")
        return content.get_text(separator="\n", strip=True) if content else ""
