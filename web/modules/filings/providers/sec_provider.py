from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from core.filings import SUPPORTED_US_FORMS, normalize_filing_item


SEC_HEADERS = {
    "User-Agent": "InvestmentAssistant waitingtangvr@gmail.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/plain, */*",
}

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_TICKER_MAP_TTL_HOURS = 24 * 7
SEC_MIN_REQUEST_INTERVAL_SECONDS = 0.8


def normalize_sec_entry(*, stock_id: str, ticker: str, company_name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    SAMPLE = str(raw.get("SAMPLE") or "").strip().upper()
    importance = "high" if SAMPLE in {"10-K", "10-Q", "8-K", "4", "6-K"} else "low"
    tags: List[str] = []
    if SAMPLE in {"10-K", "10-Q"}:
        tags.append("earnings")
    elif SAMPLE == "8-K":
        tags.append("event")
    elif SAMPLE == "4":
        tags.append("insider")
    return normalize_filing_item(
        {
            "source": "sec",
            "market": "US",
            "stock_id": stock_id,
            "ticker": ticker,
            "company_name": company_name,
            "filed_at": raw.get("filing_date") or raw.get("filed_at"),
            "doc_type": SAMPLE,
            "title": raw.get("title") or SAMPLE,
            "summary": raw.get("summary") or raw.get("title") or SAMPLE,
            "url": raw.get("url") or "",
            "period_of_report": raw.get("period_of_report") or "",
            "importance": importance,
            "tags": tags,
        }
    )


class SECFilingsProvider:
    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        timeout: float = 8.0,
        cache_dir: Optional[Path] = None,
        min_request_interval_seconds: float = SEC_MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._ticker_map_cache_path = (
            self._cache_dir / "filings_cache" / "sec" / "company_tickers.json" if self._cache_dir is not None else None
        )
        self._min_request_interval_seconds = max(0.0, float(min_request_interval_seconds or 0.0))
        self._request_lock = threading.Lock()
        self._last_request_monotonic = 0.0

    def fetch(self, *, stock_id: str, stock_name: str, ticker: str, limit: int = 12) -> List[Dict[str, Any]]:
        cik = self._lookup_cik(ticker)
        if not cik:
            return []
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        response = self._throttled_get(url)
        response.raise_for_status()
        payload = response.json()
        recent = ((payload or {}).get("filings") or {}).get("recent") or {}
        forms = list(recent.get("SAMPLE") or [])
        filing_dates = list(recent.get("filingDate") or [])
        primary_docs = list(recent.get("primaryDocument") or [])
        accession_numbers = list(recent.get("accessionNumber") or [])
        report_dates = list(recent.get("reportDate") or [])

        items: List[Dict[str, Any]] = []
        for index, SAMPLE in enumerate(forms):
            form_text = str(SAMPLE or "").strip().upper()
            if form_text not in SUPPORTED_US_FORMS:
                continue
            accession = str(accession_numbers[index] or "").replace("-", "")
            doc_name = str(primary_docs[index] or "").strip()
            url = ""
            if accession and doc_name:
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc_name}"
            items.append(
                normalize_sec_entry(
                    stock_id=stock_id,
                    ticker=ticker,
                    company_name=stock_name,
                    raw={
                        "SAMPLE": form_text,
                        "filing_date": filing_dates[index] if index < len(filing_dates) else "",
                        "title": form_text,
                        "url": url,
                        "period_of_report": report_dates[index] if index < len(report_dates) else "",
                    },
                )
            )
            if len(items) >= max(1, int(limit)):
                break
        return items

    def prewarm_ticker_map(self) -> int:
        payload = self._load_ticker_map_payload()
        return len(payload or {})

    def _lookup_cik(self, ticker: str) -> Optional[str]:
        text = str(ticker or "").strip().upper()
        if not text:
            return None
        payload = self._load_ticker_map_payload()
        for entry in (payload or {}).values():
            if str((entry or {}).get("ticker") or "").strip().upper() != text:
                continue
            cik_str = str((entry or {}).get("cik_str") or "").strip()
            if cik_str:
                return cik_str.zfill(10)
        return None

    def _load_ticker_map_payload(self) -> Dict[str, Any]:
        cached = self._read_ticker_map_cache()
        if cached is not None:
            return cached
        stale_cached = self._read_ticker_map_cache(allow_stale=True)
        try:
            response = self._throttled_get(SEC_TICKER_MAP_URL)
            response.raise_for_status()
            payload = response.json()
            self._write_ticker_map_cache(payload)
            return payload
        except Exception:
            if stale_cached is not None:
                return stale_cached
            raise

    def _throttled_get(self, url: str) -> requests.Response:
        with self._request_lock:
            now = time.monotonic()
            wait_seconds = self._min_request_interval_seconds - (now - self._last_request_monotonic)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            response = self._session.get(url, headers=SEC_HEADERS, timeout=self._timeout)
            self._last_request_monotonic = time.monotonic()
            return response

    def _read_ticker_map_cache(self, *, allow_stale: bool = False) -> Optional[Dict[str, Any]]:
        path = self._ticker_map_cache_path
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        updated_at = str((payload.get("_meta") or {}).get("updated_at") or "").strip()
        if not updated_at:
            return None
        try:
            updated_dt = datetime.fromisoformat(updated_at)
        except ValueError:
            return None
        if (not allow_stale) and datetime.now() - updated_dt > timedelta(hours=SEC_TICKER_MAP_TTL_HOURS):
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def _write_ticker_map_cache(self, payload: Dict[str, Any]) -> None:
        path = self._ticker_map_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        wrapped = {
            "_meta": {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "source": SEC_TICKER_MAP_URL,
            },
            "data": payload,
        }
        path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
