from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawJob:
    source: str
    url: str
    title: str
    company: str
    jd_text: str
    deadline: datetime | None = None
    extra: dict = field(default_factory=dict)


class BaseCrawler:
    """Sync interface for site crawlers. Each subclass implements fetch_jobs."""

    source: str = ""

    def fetch_jobs(self, limit: int | None = None) -> list[RawJob]:
        raise NotImplementedError
