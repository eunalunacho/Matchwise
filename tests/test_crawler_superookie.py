"""Unit tests for SuperookieCrawler using mocked Playwright."""
from datetime import timezone
from unittest.mock import MagicMock, patch
from crawler.superookie import SuperookieCrawler, _parse_deadline

# 실제 superookie.com 구조를 반영한 목 HTML
LIST_HTML = """
<html><body>
<div class="main-item-job">
  <a class="job-detail-link color-black" href="/jobs/6a3a280b49d7420d402543b1">
    <div class="team pointer">
      <div class="team-title allpadding-xxs">
        <div class="row mobile-nomargin">
          <div class="col-md-12">
            <h5 class="notopmargin nobottommargin leftmargin-xxs fleft">AlphaSights</h5>
          </div>
        </div>
        <p class="bottommargin-xxs leftmargin-xxs job-title">글로벌 로테이션 신입 채용</p>
        <div class="bottommargin-xxxs leftmargin-xxs mobile-noleftmargin color-gray mobile-text-12">
          07/13 23:59 마감
        </div>
      </div>
    </div>
  </a>
</div>
<div class="main-item-job">
  <a class="job-detail-link color-black" href="/jobs/6a3e49c461e506538835850c">
    <div class="team pointer">
      <div class="team-title allpadding-xxs">
        <div class="row mobile-nomargin">
          <div class="col-md-12">
            <h5 class="notopmargin nobottommargin leftmargin-xxs fleft">마이리얼트립</h5>
          </div>
        </div>
        <p class="bottommargin-xxs leftmargin-xxs job-title">사업개발 매니저</p>
        <div class="bottommargin-xxxs leftmargin-xxs mobile-noleftmargin color-gray mobile-text-12">
          상시채용
        </div>
      </div>
    </div>
  </a>
</div>
<div class="main-item-job">
  <!-- href 없는 카드: 스킵돼야 함 -->
  <a class="job-detail-link color-black">
    <p class="job-title">스킵될 공고</p>
  </a>
</div>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<div class="job-detail">
지원분야
모집부문
글로벌 로테이션 신입 채용
고용형태
신입
지원 자격
학력: 대졸
직무관련: 능통한 한국어 실력
접수기간
07/01 ~ 07/13
</div>
</body></html>
"""


# ── _parse_deadline 유닛 테스트 ────────────────────────────────────────────

def test_parse_deadline_with_date():
    result = _parse_deadline("07/13 23:59 마감")
    assert result is not None
    assert result.month == 7
    assert result.day == 13
    assert result.tzinfo == timezone.utc


def test_parse_deadline_always_open():
    assert _parse_deadline("상시채용") is None


def test_parse_deadline_on_hire():
    assert _parse_deadline("채용시마감") is None


def test_parse_deadline_empty():
    assert _parse_deadline("") is None


# ── 통합 테스트 (Playwright mocked) ──────────────────────────────────────

def _make_mock_page(list_html: str, detail_html: str):
    page = MagicMock()
    call_count = {"n": 0}

    def content_side_effect():
        call_count["n"] += 1
        return list_html if call_count["n"] == 1 else detail_html

    page.content.side_effect = content_side_effect
    return page


def test_fetch_jobs_parses_cards():
    """유효한 카드 2개 + href 없는 카드 1개 → 2개 반환."""
    crawler = SuperookieCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.superookie.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs()

    assert len(jobs) == 2
    assert jobs[0].source == "superookie"
    assert jobs[0].url == "https://www.superookie.com/jobs/6a3a280b49d7420d402543b1"
    assert jobs[0].company == "AlphaSights"
    assert jobs[0].title == "글로벌 로테이션 신입 채용"


def test_fetch_jobs_extracts_jd():
    """상세 페이지 .job-detail 텍스트가 jd_text에 저장된다."""
    crawler = SuperookieCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.superookie.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs()

    assert "직무관련" in jobs[0].jd_text
    assert "대졸" in jobs[0].jd_text


def test_fetch_jobs_parses_deadline():
    """07/13 마감 형식이 datetime으로 파싱된다."""
    crawler = SuperookieCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.superookie.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs()

    assert jobs[0].deadline is not None
    assert jobs[0].deadline.month == 7
    assert jobs[0].deadline.day == 13


def test_fetch_jobs_always_open_deadline_is_none():
    """상시채용은 deadline이 None."""
    crawler = SuperookieCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.superookie.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs()

    assert jobs[1].deadline is None


def test_fetch_jobs_limit():
    """limit=1이면 첫 번째 카드만 반환."""
    crawler = SuperookieCrawler()
    mock_page = _make_mock_page(LIST_HTML, DETAIL_HTML)

    with patch("crawler.superookie.sync_playwright") as mock_pw_ctx:
        mock_browser = MagicMock()
        mock_pw_ctx.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page

        jobs = crawler.fetch_jobs(limit=1)

    assert len(jobs) == 1
    assert jobs[0].company == "AlphaSights"
