from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from core.filings import (
    build_filing_window,
    build_filings_payload,
    detect_stock_market,
    filter_filings_for_window,
    read_filings_payload,
    sort_filings,
    write_filings_payload,
)
from .helpers import build_filings_cache_path
from .providers import CNInfoFilingsProvider, SECFilingsProvider

logger = logging.getLogger(__name__)


@dataclass
class FilingsService:
    cache_dir: Path
    sec_provider: Any
    cninfo_provider: Any
    watchlist_ttl_hours: int = 12
    weekly_ttl_hours: int = 24

    def get_stock_filings(
        self,
        *,
        stock_id: str,
        stock_name: str,
        ticker: str,
        market: str | None = None,
        week_id: str | None = None,
        rolling_days: int | None = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        resolved_market = str(market or detect_stock_market(stock_id=stock_id, ticker=ticker) or "UNKNOWN").upper()
        mode = "weekly" if week_id else "rolling_days"
        window = build_filing_window(mode=mode, week_id=week_id, rolling_days=rolling_days or 7)
        cache_source = "sec" if resolved_market == "US" else "cninfo"
        cache_path = build_filings_cache_path(self.cache_dir, source=cache_source, ticker=ticker or stock_id)
        cached = read_filings_payload(cache_path)
        if (not force_refresh) and self._cache_is_fresh(cached, mode=mode):
            items = list((cached or {}).get("items") or [])
            filtered = filter_filings_for_window(items, window)
            payload = build_filings_payload(
                stock_id=stock_id,
                stock_name=stock_name,
                ticker=ticker,
                market=resolved_market,
                window=window,
                items=filtered or items[:3],
                cache_hit=True,
                updated_at=((cached or {}).get("cache") or {}).get("updated_at") or "",
            )
            payload["error"] = str((cached or {}).get("error") or "").strip()
            return payload

        fetch_error = ""
        try:
            items = self._fetch_items(stock_id=stock_id, stock_name=stock_name, ticker=ticker, market=resolved_market)
        except Exception as exc:
            logger.warning(
                "filings fetch failed for stock_id=%s ticker=%s market=%s: %s",
                stock_id,
                ticker,
                resolved_market,
                exc,
                exc_info=True,
            )
            items = []
            fetch_error = self._humanize_fetch_error(exc, market=resolved_market)
        payload = build_filings_payload(
            stock_id=stock_id,
            stock_name=stock_name,
            ticker=ticker,
            market=resolved_market,
            window=window,
            items=filter_filings_for_window(items, window) or sort_filings(items)[:3],
            cache_hit=False,
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )
        payload["error"] = fetch_error
        cache_payload = dict(payload)
        cache_payload["items"] = sort_filings(items)
        write_filings_payload(cache_path, cache_payload)
        return payload

    def _fetch_items(self, *, stock_id: str, stock_name: str, ticker: str, market: str) -> list[Dict[str, Any]]:
        if market == "US":
            return list(self.sec_provider.fetch(stock_id=stock_id, stock_name=stock_name, ticker=ticker))
        if market == "CN":
            return list(self.cninfo_provider.fetch(stock_id=stock_id, stock_name=stock_name, ticker=ticker))
        return []

    def _cache_is_fresh(self, payload: Optional[Dict[str, Any]], *, mode: str) -> bool:
        if not payload:
            return False
        updated_at = str((((payload or {}).get("cache") or {}).get("updated_at")) or "").strip()
        if not updated_at:
            return False
        try:
            updated = datetime.fromisoformat(updated_at)
        except ValueError:
            return False
        ttl_hours = self.weekly_ttl_hours if mode == "weekly" else self.watchlist_ttl_hours
        return datetime.now() - updated <= timedelta(hours=max(1, int(ttl_hours)))

    def _humanize_fetch_error(self, exc: Exception, *, market: str) -> str:
        text = str(exc or "").strip()
        lowered = text.lower()
        if market == "US" and ("403" in lowered or "forbidden" in lowered):
            return "SEC TextCurrentText, TextFilingsText. Text. "
        if market == "CN" and ("403" in lowered or "forbidden" in lowered):
            return "TextCurrentText, TextFilingsText. Text. "
        if market == "US":
            return f"SEC FilingsTextFailed: {text or 'unknown error'}"
        if market == "CN":
            return f"ATextFilingsTextFailed: {text or 'unknown error'}"
        return f"FilingsTextFailed: {text or 'unknown error'}"


def build_default_filings_service(base_dir: Path | str) -> FilingsService:
    resolved_base_dir = Path(base_dir)
    return FilingsService(
        cache_dir=resolved_base_dir,
        sec_provider=SECFilingsProvider(cache_dir=resolved_base_dir),
        cninfo_provider=CNInfoFilingsProvider(),
    )
