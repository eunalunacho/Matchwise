"""잡코리아(jobkorea.co.kr) 신입·인턴 채용관 크롤러 — HTTP만 사용한다 (브라우저 렌더링 없음).

화면에서 직무·지역·고용형태·경력 버튼을 고르면 사이트가 폼 값을 AJAX 요청으로 보내 목록 HTML 조각을
받아 온다. 이 요청을 그대로 재현한다.

  1) GET /theme/entry-level-internship                    필터 버튼의 라벨 → 값(`data-value`)과 폼 기본값
  2) GET /Theme/TemplateFreeGnoList/entry-level-internship 조건 + 등록일순(최신부터)으로 거른 목록 조각 (20건/페이지)
  3) GET /Recruit/GI_Read/{번호}                           상세: JSON-LD(게시일·마감일·경력) + 조건 항목
  4) GET /Recruit/GI_Read_Comt_Ifrm?Gno={번호}             JD 본문 HTML (텍스트 또는 이미지)

검색 조건은 SEARCH_FILTERS 한 곳에서 바꾼다. 지역·직무는 서버 조건만 쓰고 크롤러가 다시 거르지 않는다:
여러 근무지·여러 직무 분류를 가진 공고가 서울·경기·AI·개발·데이터 중 하나만 해당해도 그대로 포함되며,
관련도는 분석 단계가 판단한다.

`apply_url`은 채우지 않는다. "홈페이지 지원" 주소는 로그인해야 보인다(버튼이 로그인 레이어를 연다).
"""
import json
import logging
import re
import time
from dataclasses import dataclass
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

BASE_URL = "https://www.jobkorea.co.kr"
THEME_URL = f"{BASE_URL}/theme/entry-level-internship"
LIST_URL = f"{BASE_URL}/Theme/TemplateFreeGnoList/entry-level-internship"
DETAIL_URL = f"{BASE_URL}/Recruit/GI_Read/{{gno}}"
BODY_URL = f"{BASE_URL}/Recruit/GI_Read_Comt_Ifrm"

KST = timezone(timedelta(hours=9))

# (테마 페이지의 버튼 영역, 목록 요청의 폼 필드, 고를 버튼 라벨). 검색 조건을 바꾸려면 여기만 고친다.
SEARCH_FILTERS = (
    ("bpart_no", "bpart_no", ("AI·개발·데이터",)),
    ("scd", "scd", ("서울", "경기")),
    ("job_type", "jtype", ("정규직", "인턴직")),
    ("career_type_code", "careerTypeCode", ("신입", "경력무관")),
)

SEARCH_MODE_DETAIL = "0"  # jobFilter: 상세검색
ORDER_BY_REGISTERED = "2"  # rOrderTab: 등록일순(최신부터). 기본값 1(추천순)은 날짜와 무관하다.

_HEADERS = {
    "User-Agent": "Matchwise/0.2 (personal job-search tool)",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
_AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Referer": THEME_URL}


@dataclass(frozen=True)
class _Card:
    """목록 조각의 공고 한 건."""

    gno: str
    title: str
    company: str
    deadline_label: str  # "~10/09(금)", "상시채용", "내일마감" 등 화면 표기
    apply_kind: str  # "즉시지원"(잡코리아 안에서 지원) | "홈페이지"(기업 사이트, 로그인해야 주소가 보임)

    @property
    def url(self) -> str:
        return DETAIL_URL.format(gno=self.gno)


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_deadline(value) -> datetime | None:
    """JSON-LD validThrough("2026-10-09T23:59")를 KST 시각으로. 없거나 형식이 다르면 None."""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=KST)


def _career_level(experience: str, employment: str) -> str:
    """JSON-LD의 경력 조건과 고용형태에서 career_level을 만든다."""
    levels = set()
    if "신입" in experience:
        levels.add("신입")
    if "무관" in experience:
        levels.update({"신입", "경력"})
    if "경력" in experience.replace("경력무관", ""):
        levels.add("경력")
    if "인턴" in employment:
        levels.add("인턴")
    return join_career_level(levels)


class JobkoreaCrawler(BaseCrawler):
    source = "jobkorea"

    def __init__(
        self,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        client: httpx.Client | None = None,
        today: date | None = None,
        request_delay_sec: float = 0.5,
        timeout_sec: float = 30.0,
        page_size: int = 20,
        max_pages: int = 50,
        max_consecutive_old: int = 8,
    ):
        """
        Args:
            lookback_days: 오늘(KST)로부터 며칠 전까지 등록된 공고를 볼지. 2면 그제·어제.
            client: 테스트용 주입. 없으면 자체 생성하고 종료 시 닫는다.
            today: 테스트용 기준일(KST). 없으면 현재 KST 날짜.
            request_delay_sec: 요청 사이 대기 (서버 부담 완화).
            page_size: 목록 한 페이지의 공고 수. 이보다 적게 오면 마지막 페이지로 본다.
            max_pages: 안전장치 — 사이트 이상 동작 시 무한 루프 방지.
            max_consecutive_old: 조회 기간보다 오래된 공고가 이만큼 연속으로 나오면 중단한다. 등록일순에서
                최근 공고는 위쪽에 몰려 있고 그 뒤로 오래된 공고가 길게 이어져서, 끝까지 상세를 열 필요가 없다.
                재등록된 오래된 공고가 위에 섞여도 놓치지 않도록 연속 기준이며 최근 공고가 나오면 초기화된다.
        """
        self._lookback_days = lookback_days
        self._client = client
        self._today = today
        self._delay = request_delay_sec
        self._timeout = timeout_sec
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_consecutive_old = max_consecutive_old

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
        """등록일순 목록을 넘기며 신규 공고를 모은다.

        등록일순은 재등록(갱신) 순서라서 게시일이 오래된 공고가 위쪽에 섞여 나온다. 그래서 오래된
        공고는 본문을 받지 않고 건너뛴다.
        중단 조건: ① 페이지의 공고가 전부 이미 아는 URL ② 오래된 공고가 max_consecutive_old건 연속
        ③ 페이지에 조회 기간 안의 공고가 하나도 없음 ④ 마지막 페이지.
        """
        today = self._today or datetime.now(KST).date()
        first_day = today - timedelta(days=self._lookback_days)
        base_params = self._load_search_params(client)

        jobs: list[RawJob] = []
        old_streak = 0
        for page in range(1, self._max_pages + 1):
            cards = self._fetch_cards(client, base_params, page)
            self.seen_count += len(cards)
            new_cards = [card for card in cards if card.url not in known_urls]
            if not new_cards:
                break

            recent_on_page = 0
            for card in new_cards:
                detail = self._fetch_detail(client, card)
                if detail is None:
                    self.detail_failed_count += 1
                    continue
                json_ld, conditions = detail

                posted = _parse_date(json_ld.get("datePosted"))
                if posted is not None and posted < first_day:  # 조회 기간보다 오래된 공고(재등록된 것 포함)
                    old_streak += 1
                    if old_streak >= self._max_consecutive_old:
                        return jobs
                    continue
                old_streak = 0
                recent_on_page += 1

                job = self._build_job(client, card, json_ld, conditions)
                if job is None:
                    self.detail_failed_count += 1
                    continue
                jobs.append(job)
                logger.info("crawled %s | %s", job.company, job.title)
                if limit and len(jobs) >= limit:
                    return jobs

            if recent_on_page == 0 or len(cards) < self._page_size:
                break
        return jobs

    # ── 목록 ──────────────────────────────────────────────────────────────

    def _load_search_params(self, client: httpx.Client) -> dict[str, str]:
        """테마 페이지에서 폼 기본값과 SEARCH_FILTERS의 라벨 → 값을 읽어 목록 요청 파라미터의 뼈대를 만든다."""
        soup = BeautifulSoup(self._get(client, THEME_URL).text, "html.parser")
        form = soup.select_one("#recruitSearchForm")
        if form is None:
            raise RuntimeError("잡코리아 검색 폼(#recruitSearchForm)을 찾을 수 없음 — 페이지 구조가 바뀌었을 수 있다")

        params = {
            field["name"]: field.get("value") or ""
            for field in form.select("input[name]")
            if field.get("type") != "radio"
        }
        for area, field_name, labels in SEARCH_FILTERS:
            buttons = {
                button.get_text(strip=True): button.get("data-value")
                for button in soup.select(f"[area-type='{area}'] button")
                if button.get("data-value") is not None
            }
            values = []
            for label in labels:
                if label not in buttons:
                    raise RuntimeError(f"잡코리아 필터 버튼 '{label}'을 찾을 수 없음 — 버튼 이름이 바뀌었을 수 있다")
                values.append(buttons[label])
            params[field_name] = ",".join(values)

        params.update(
            {"rlistTab": "0", "jobFilter": SEARCH_MODE_DETAIL, "rOrderTab": ORDER_BY_REGISTERED, "MainPageNo": "1"}
        )
        return params

    def _fetch_cards(self, client: httpx.Client, base_params: dict[str, str], page: int) -> list[_Card]:
        response = self._get(client, LIST_URL, params={**base_params, "FreePageNo": str(page)}, headers=_AJAX_HEADERS)
        cards = []
        for item in BeautifulSoup(response.text, "html.parser").select("li.dmpItem"):
            link = item.select_one("a.dev-recruit-link")
            match = re.search(r"GI_Read/(\d+)", link.get("href", "")) if link else None
            if not match:
                continue
            title, company = item.select_one(".rTit"), item.select_one(".corNm")
            period = item.select_one(".rPeriod")
            cards.append(
                _Card(
                    gno=match.group(1),
                    title=title.get_text(strip=True) if title else "",
                    company=company.get_text(strip=True) if company else "",
                    deadline_label=period.get_text(strip=True) if period else "",
                    apply_kind="홈페이지" if item.select_one(".button-homepage") else "즉시지원",
                )
            )
        return cards

    # ── 상세 · 본문 ───────────────────────────────────────────────────────

    def _fetch_detail(self, client: httpx.Client, card: _Card) -> tuple[dict, dict[str, str]] | None:
        """상세 페이지에서 (JSON-LD, 조건 항목)을 읽는다. 실패하거나 JSON-LD가 없으면 None."""
        try:
            soup = BeautifulSoup(self._get(client, card.url).text, "html.parser")
        except (httpx.HTTPError, ValueError):
            logger.warning("잡코리아 상세 조회 실패 gno=%s", card.gno, exc_info=True)
            return None

        json_ld = None
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or "")
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                json_ld = data
                break
        if json_ld is None:
            logger.warning("잡코리아 상세에 JobPosting JSON-LD가 없음 gno=%s — 건너뜀", card.gno)
            return None

        conditions: dict[str, str] = {}
        for item in soup.select('[data-sentry-component="RecruitmentItem"]'):
            parts = [child for child in item.children if getattr(child, "name", None)]
            if parts:
                conditions[parts[0].get_text(strip=True)] = " ".join(p.get_text(" ", strip=True) for p in parts[1:])
        return json_ld, conditions

    def _build_job(
        self, client: httpx.Client, card: _Card, json_ld: dict, conditions: dict[str, str]
    ) -> RawJob | None:
        try:
            body_html = self._get(client, BODY_URL, params={"Gno": card.gno}).text
        except (httpx.HTTPError, ValueError):
            logger.warning("잡코리아 본문 조회 실패 gno=%s", card.gno, exc_info=True)
            return None

        body = BeautifulSoup(body_html, "html.parser")
        for tag in body(["style", "script"]):
            tag.decompose()

        career_level = _career_level(
            str(json_ld.get("experienceRequirements") or ""), conditions.get("고용형태", "")
        )
        return RawJob(
            source=self.source,
            url=card.url,
            title=card.title,
            company=card.company,
            jd_text=body.get_text(separator="\n", strip=True),
            deadline=_parse_deadline(json_ld.get("validThrough")),
            apply_url=None,
            payload={
                "card": {
                    "gno": card.gno,
                    "title": card.title,
                    "company": card.company,
                    "deadline_label": card.deadline_label,
                    "apply_kind": card.apply_kind,
                },
                "json_ld": json_ld,
                "conditions": conditions,
                "body_html": body_html,
            },
            hints={"positions": [{"name": card.title, "career_level": career_level}]},
            image_urls=extract_image_urls(body_html),
        )

    def _get(self, client: httpx.Client, url: str, **kwargs) -> httpx.Response:
        if self._delay:
            time.sleep(self._delay)
        response = client.get(url, **kwargs)
        response.raise_for_status()
        return response
