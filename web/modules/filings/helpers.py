from __future__ import annotations

from pathlib import Path

from core.filings import detect_stock_market


def build_filings_cache_path(base_dir: Path, *, source: str, ticker: str) -> Path:
    safe_source = str(source or "unknown").strip().lower() or "unknown"
    safe_ticker = str(ticker or "unknown").strip().upper().replace("/", "_").replace("\\", "_")
    return Path(base_dir) / "filings_cache" / safe_source / f"{safe_ticker}.json"


def resolve_filings_market(*, stock_id: str = "", ticker: str = "") -> str:
    return detect_stock_market(stock_id=stock_id, ticker=ticker)
