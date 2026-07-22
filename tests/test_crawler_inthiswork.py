"""Unit tests for InthisworkCrawler using mocked Playwright."""
from unittest.mock import MagicMock, patch
from crawler.inthiswork import InthisworkCrawler, _split_title

# Minimal HTML matching the real inthiswork.com/it list page structure
LIST_HTML = """
<html><body>
<div class="dpt-entry" data-id="361725">
  <div class="dpt-entry-wrapper">
    <div class="sub-entry">
      <h3 class="dpt-title">
        <a class="dpt-title-link" href="https://inthiswork.com/archives/361725">SOOP｜AI 모델 개발</a>
      </h3>
    </div>
  </div>
</div>
<div class="dpt-entry" data-id="361719">
  <div class="dpt-entry-wrapper">
    <div class="sub-entry">
      <h3 class="dpt-title">
        <a class="dpt-title-link" href="https://inthiswork.com/archives/361719">딜로이트 안진｜AI Analytics 개발</a>
      </h3>
    </div>
  </div>
</div>
<div class="dpt-entry" data-id="99999">
  <div class="dpt-entry-wrapper">
    <div class="sub-entry">
      <!-- missing link tag - should be skipped -->
    </div>
  </div>
</div>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<div class="post-content">
[팀소개]
SOOP은 다양한 데이터를 보유하고 있습니다.

[담당업무]
– AI 모델 인퍼런스 파이프라인 개발

[자격요건]
– AI 모델 학습 경력 3년 이상
</div>
</body></html>
"""


# ── unit tests for _split_title ────────────────────────────────────────────

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


# ── integration tests with mocked Playwright ──────────────────────────────

def _make_mock_page(list_html: str, detail_html: str):
    """Return a mock Playwright page that returns list HTML once, then detail HTML."""
    page = MagicMock()
    call_count = {"n": 0}

    def content_side_effect():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return list_html
        return detail_html

    page.content.side_effect = content_side_effect
    return page


def test_fetch_jobs_parses_entries():
    """Two valid entries and one without a link tag → 2 jobs returned."""
    crawler = InthisworkCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.inthiswork.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs()

    assert len(jobs) == 2
    assert jobs[0].source == "inthiswork"
    assert jobs[0].url == "https://inthiswork.com/archives/361725"
    assert jobs[0].title == "SOOP｜AI 모델 개발"
    assert jobs[0].company == "SOOP"
    assert jobs[1].company == "딜로이트 안진"


def test_fetch_jobs_extracts_jd_text():
    """JD text from .post-content is stored in jd_text."""
    crawler = InthisworkCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.inthiswork.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs()

    assert "AI 모델 인퍼런스 파이프라인 개발" in jobs[0].jd_text
    assert "자격요건" in jobs[0].jd_text


def test_fetch_jobs_limit():
    """limit=1 returns only first entry."""
    crawler = InthisworkCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.inthiswork.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs(limit=1)

    assert len(jobs) == 1
    assert jobs[0].url == "https://inthiswork.com/archives/361725"


def test_fetch_jobs_deadline_is_none():
    """Inthiswork doesn't expose deadlines; field stays None."""
    crawler = InthisworkCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.inthiswork.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs(limit=1)

    assert jobs[0].deadline is None
