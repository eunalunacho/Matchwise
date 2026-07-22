"""DART OpenAPI 클라이언트.

회사명으로 corp_code를 검색하고, 재무 데이터를 가져온다.
corpCode.xml은 매일 1회 로컬 캐시에 저장해 API 호출을 최소화한다.
"""
import io
import json
import logging
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DART_BASE = "https://opendart.fss.or.kr/api"
_CACHE_TTL_HOURS = 24


class DartClient:
    def __init__(self, api_key: str, cache_dir: str = ".dart_cache"):
        self._key = api_key
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._corp_codes: dict[str, str] | None = None  # {name: corp_code}

    # ── corp code 캐시 ──────────────────────────────────────────────────

    def _corp_cache_path(self) -> Path:
        return self._cache_dir / "corp_codes.json"

    def _load_corp_codes(self) -> dict[str, str]:
        cache = self._corp_cache_path()
        if cache.exists():
            mtime = datetime.fromtimestamp(cache.stat().st_mtime, tz=timezone.utc)
            if datetime.now(timezone.utc) - mtime < timedelta(hours=_CACHE_TTL_HOURS):
                return json.loads(cache.read_text(encoding="utf-8"))

        logger.info("DART corp code 다운로드 중...")
        resp = httpx.get(
            f"{_DART_BASE}/corpCode.xml",
            params={"crtfc_key": self._key},
            timeout=60,
        )
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            with z.open("CORPCODE.xml") as f:
                tree = ET.parse(f)

        codes: dict[str, str] = {}
        for item in tree.getroot().findall("list"):
            name = item.findtext("corp_name", "").strip()
            code = item.findtext("corp_code", "").strip()
            if name and code:
                codes[name] = code

        cache.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
        logger.info("DART corp code %d개 캐시 완료", len(codes))
        return codes

    def _get_corp_codes(self) -> dict[str, str]:
        if self._corp_codes is None:
            self._corp_codes = self._load_corp_codes()
        return self._corp_codes

    # ── 회사 검색 ───────────────────────────────────────────────────────

    def find_corp_code(self, company_name: str) -> str | None:
        """회사명으로 corp_code 반환. 없으면 None."""
        codes = self._get_corp_codes()

        # 1. 완전 일치
        if company_name in codes:
            return codes[company_name]

        # 2. 부분 일치 (회사명이 포함되거나 포함하는 경우)
        name_clean = company_name.replace("(주)", "").replace("주식회사", "").strip()
        matches = [
            (k, v) for k, v in codes.items()
            if name_clean in k or k in company_name
        ]
        if not matches:
            return None

        # 길이 차이 가장 작은 것 선택
        return min(matches, key=lambda x: abs(len(x[0]) - len(company_name)))[1]

    # ── 재무 데이터 ─────────────────────────────────────────────────────

    def _get_recent_year(self) -> str:
        """가장 최근에 공시된 연도 추정 (전년도 기준)."""
        return str(datetime.now(timezone.utc).year - 1)

    def get_financials(self, corp_code: str) -> dict | None:
        """단일 회사 재무제표에서 핵심 지표 반환.

        Returns:
            {revenue, op_profit, assets, liabilities, debt_ratio,
             employees, avg_salary_10k} or None
        """
        year = self._get_recent_year()
        try:
            resp = httpx.get(
                f"{_DART_BASE}/fnlttSinglAcnt.json",
                params={
                    "crtfc_key": self._key,
                    "corp_code": corp_code,
                    "bsns_year": year,
                    "reprt_code": "11011",  # 사업보고서
                    "fs_div": "OFS",        # 별도재무제표
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("DART 재무 조회 실패 corp_code=%s: %s", corp_code, e)
            return None

        if data.get("status") != "000":
            return None

        items = {row["account_id"]: row for row in data.get("list", [])}

        def _amount(account_id: str) -> int | None:
            row = items.get(account_id)
            if not row:
                return None
            try:
                return int(row["thstrm_amount"].replace(",", ""))
            except (ValueError, KeyError):
                return None

        revenue = _amount("ifrs-full_Revenue") or _amount("dart_TotalOperatingIncomeExpenses")
        op_profit = _amount("dart_OperatingIncomeLoss")
        assets = _amount("ifrs-full_Assets")
        liabilities = _amount("ifrs-full_Liabilities")

        debt_ratio: float | None = None
        equity = (assets - liabilities) if assets and liabilities else None
        if equity and equity > 0:
            debt_ratio = round(liabilities / equity * 100, 1)

        employees = self._get_employees(corp_code, year)

        return {
            "revenue": revenue,
            "op_profit": op_profit,
            "assets": assets,
            "liabilities": liabilities,
            "debt_ratio": debt_ratio,
            "employees": employees,
        }

    def _get_employees(self, corp_code: str, year: str) -> int | None:
        try:
            resp = httpx.get(
                f"{_DART_BASE}/empSttus.json",
                params={
                    "crtfc_key": self._key,
                    "corp_code": corp_code,
                    "bsns_year": year,
                    "reprt_code": "11011",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        if data.get("status") != "000":
            return None

        total = 0
        for row in data.get("list", []):
            try:
                total += int(row.get("fyer_salary_totamt", "0").replace(",", ""))
            except ValueError:
                pass
        # 실제로는 직원 수 필드: "to_mthavg_salary" 아님, "sm" 필드
        rows = data.get("list", [])
        if rows:
            try:
                return sum(int(r.get("sm", "0").replace(",", "")) for r in rows)
            except ValueError:
                pass
        return None

    # ── 최상위 인터페이스 ────────────────────────────────────────────────

    def get_company_data(self, company_name: str) -> dict | None:
        """회사명 → 재무 데이터. 못 찾거나 DART 미등록이면 None."""
        corp_code = self.find_corp_code(company_name)
        if not corp_code:
            logger.info("DART에서 '%s' 미발견", company_name)
            return None
        return self.get_financials(corp_code)
