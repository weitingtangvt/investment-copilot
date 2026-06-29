from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SUPPORTED_US_FORMS = {"10-K", "10-Q", "8-K", "4", "6-K"}


def normalize_filing_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": str(item.get("source") or "").strip().lower(),
        "market": str(item.get("market") or "").strip().upper(),
        "stock_id": str(item.get("stock_id") or "").strip(),
        "ticker": str(item.get("ticker") or "").strip(),
        "company_name": str(item.get("company_name") or "").strip(),
        "filed_at": str(item.get("filed_at") or "").strip()[:10],
        "doc_type": str(item.get("doc_type") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "summary": str(item.get("summary") or "").strip(),
        "url": str(item.get("url") or "").strip(),
        "period_of_report": str(item.get("period_of_report") or "").strip()[:10],
        "importance": _normalize_importance(item.get("importance")),
        "tags": [str(tag).strip() for tag in list(item.get("tags") or []) if str(tag).strip()],
    }


def _normalize_importance(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    return "low"


def build_filing_window(*, mode: str, week_id: str | None = None, rolling_days: int | None = None) -> Dict[str, str]:
    if mode == "weekly":
        text = str(week_id or "").strip()
        match = re.match(r"^(?P<year>\d{4})-W(?P<week>\d{2})$", text)
        if not match:
            raise ValueError("week_id is required for weekly mode")
        start = date.fromisocalendar(int(match.group("year")), int(match.group("week")), 1)
        end = start + timedelta(days=6)
        return {"mode": "weekly", "start_date": start.isoformat(), "end_date": end.isoformat()}

    if mode == "rolling_days":
        days = max(1, int(rolling_days or 7))
        end = date.today()
        start = end - timedelta(days=days - 1)
        return {"mode": "rolling_days", "start_date": start.isoformat(), "end_date": end.isoformat()}

    raise ValueError(f"unsupported mode: {mode}")


def filing_date_in_window(filed_at: str, window: Dict[str, str]) -> bool:
    filed = _parse_day(filed_at)
    start = _parse_day(window.get("start_date"))
    end = _parse_day(window.get("end_date"))
    if not filed or not start or not end:
        return False
    return start <= filed <= end


def filter_filings_for_window(items: Iterable[Dict[str, Any]], window: Dict[str, str]) -> List[Dict[str, Any]]:
    return [item for item in items if filing_date_in_window(str(item.get("filed_at") or ""), window)]


def sort_filings(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        [normalize_filing_item(item) for item in items],
        key=lambda item: (
            _importance_rank(item.get("importance")),
            str(item.get("filed_at") or ""),
            str(item.get("doc_type") or ""),
        ),
        reverse=True,
    )


def summarize_filings_for_prompt(items: Iterable[Dict[str, Any]], *, limit: int = 3) -> str:
    selected = [item for item in sort_filings(items) if str(item.get("importance") or "") in {"high", "medium"}][: max(0, int(limit))]
    if not selected:
        return ""
    lines = ["Filing facts:"]
    for index, item in enumerate(selected, start=1):
        lines.append(
            f"{index}. {item.get('filed_at') or '--'} | {item.get('doc_type') or '--'} | {item.get('title') or '--'} | {item.get('summary') or '--'}"
        )
    return "\n".join(lines)


def detect_stock_market(*, stock_id: str = "", ticker: str = "") -> str:
    text = str(ticker or stock_id or "").strip().upper()
    if not text:
        return "UNKNOWN"
    if text.endswith((".SH", ".SZ", ".SS")) or (text.isdigit() and len(text) == 6):
        return "CN"
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", text):
        return "US"
    return "UNKNOWN"


def build_filings_payload(
    *,
    stock_id: str,
    stock_name: str,
    ticker: str,
    market: str,
    window: Dict[str, str],
    items: Iterable[Dict[str, Any]],
    cache_hit: bool,
    updated_at: str,
) -> Dict[str, Any]:
    sorted_items = sort_filings(items)
    return {
        "success": True,
        "stock_id": str(stock_id or "").strip(),
        "stock_name": str(stock_name or stock_id or "").strip(),
        "ticker": str(ticker or stock_id or "").strip(),
        "market": str(market or "UNKNOWN").strip().upper() or "UNKNOWN",
        "window": dict(window or {}),
        "counts": {
            "total": len(sorted_items),
            "high_importance": sum(1 for item in sorted_items if str(item.get("importance") or "") == "high"),
        },
        "items": sorted_items,
        "cache": {"hit": bool(cache_hit), "updated_at": str(updated_at or "").strip()},
    }


def read_filings_payload(cache_path: Path) -> Optional[Dict[str, Any]]:
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_filings_payload(cache_path: Path, payload: Dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_day(value: Any) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _importance_rank(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text == "high":
        return 3
    if text == "medium":
        return 2
    return 1
