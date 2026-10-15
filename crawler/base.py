from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

# 신규 공고 조회 기간. 오늘(KST) 기준 며칠 전까지 게시·시작한 공고를 볼지.
# 2면 그제·어제. 이미 저장된 URL은 known_urls로 건너뛰므로 넓혀도 중복 저장되지 않고,
# 하루 실행이 실패해도 다음 실행에서 놓친 공고를 복구할 수 있다.
DEFAULT_LOOKBACK_DAYS = 2

# career_level 표기 순서. 구성 요소를 이 순서로 "/"로 잇는다 (예: 신입/인턴/경력).
CAREER_LEVEL_ORDER = ("신입", "인턴", "경력", "계약직", "교육")


def join_career_level(components: Iterable[str]) -> str:
    """채용구분 구성 요소를 정해진 순서로 "/"로 이어 career_level 문자열을 만든다.

    Raises:
        ValueError: CAREER_LEVEL_ORDER에 없는 구성 요소가 있을 때.
    """
    unique = set(components)
    unknown = unique - set(CAREER_LEVEL_ORDER)
    if unknown:
        raise ValueError(f"알 수 없는 채용구분: {sorted(unknown)}")
    return "/".join(level for level in CAREER_LEVEL_ORDER if level in unique)


def extract_image_urls(html: str | None) -> list[str]:
    """JD 본문 HTML에서 이미지 주소(`src`)를 문서 순서대로 뽑는다. 중복과 `src` 없는 태그는 제외.

    이미지 JD는 2차 단계(OCR)가 이 목록을 대상으로 삼는다.
    """
    urls: list[str] = []
    for image in BeautifulSoup(html or "", "html.parser").find_all("img"):
        src = image.get("src")
        if src and src.startswith("//"):
            src = "https:" + src  # 프로토콜 생략 주소(예: 잡코리아)
        if src and src not in urls:
            urls.append(src)
    return urls


@dataclass
class RawJob:
    """크롤러가 적재 단계로 넘기는 공통 계약. 사이트를 아는 코드는 크롤러 안에만 둔다.

    필수: source, url, title, company, jd_text
      url        수집 사이트 안의 공고 페이지 (추적 파라미터 없는 정규 URL, 중복 제거 키)
      jd_text    크롤러가 추출한 JD 텍스트. 이미지 JD면 빈 문자열일 수 있다.
    선택:
      deadline   마감일 (timezone-aware)
      apply_url  기업 자체 채용사이트의 공고/지원 페이지
      payload    원본 응답/HTML 조각. 해석 없이 그대로 보존한다.
      hints      사이트 값을 사이트와 무관한 키로 정규화한 것.
                 {"positions": [{"name": str, "career_level": str}]}
                 career_level은 join_career_level()로 만든다.
      image_urls JD 본문에 들어 있는 이미지 주소(문서 순서). 이미지 JD의 OCR 대상.
                 텍스트 JD라도 본문에 이미지가 있으면 채운다. extract_image_urls() 사용.
    """

    source: str
    url: str
    title: str
    company: str
    jd_text: str
    deadline: datetime | None = None
    apply_url: str | None = None
    payload: dict = field(default_factory=dict)
    hints: dict = field(default_factory=dict)
    image_urls: list[str] = field(default_factory=list)


class BaseCrawler:
    """Sync interface for site crawlers. Each subclass implements fetch_jobs."""

    source: str = ""
    # 수집 요약(로그·웹훅 메시지)용 카운터. fetch_jobs()가 호출마다 0부터 다시 센다.
    # seen_count: 목록에서 본 공고 수(이미 아는 공고 포함) — 0이면 사이트 변화를 의심한다.
    # detail_failed_count: 상세·본문 조회에 실패해 건너뛴 공고 수.
    seen_count: int = 0
    detail_failed_count: int = 0

    def fetch_jobs(
        self, limit: int | None = None, known_urls: set[str] | None = None
    ) -> list[RawJob]:
        """known_urls에 있는 URL은 상세 페이지 요청 없이 건너뛴다 (중복 스크래핑 방지)."""
        raise NotImplementedError
