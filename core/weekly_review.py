"""Weekly ReviewText"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import feedparser
import pandas as pd

logger = logging.getLogger(__name__)

from .storage import Storage
from .environment import EnvironmentCollector
from .deep_search_service import DeepSearchService
from .weekly_review_rebalancing import (
    auto_pair_trim_event,
    calc_trim_window_metrics,
    is_buy_like_op,
    is_sell_like_op,
    normalize_trim_reallocation_op,
)

try:
    from utils.akshare_client import get_weekly_performance as ak_get_performance
    from utils.akshare_client import get_portfolio_returns as ak_get_portfolio_returns
    from utils.akshare_client import get_portfolio_and_weekly as ak_get_portfolio_and_weekly
    from utils.akshare_client import get_close_price_on_date as ak_get_close_price
except ImportError:
    ak_get_performance = None
    ak_get_portfolio_returns = None
    ak_get_portfolio_and_weekly = None
    ak_get_close_price = None


def get_week_id(dt: Optional[datetime] = None) -> str:
    """Text ISO Text, Text YYYY-Www"""
    if dt is None:
        dt = datetime.now()
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_date_text(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return text[:10]


def _parse_date(value: str) -> Optional[datetime]:
    text = _normalize_date_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def _week_end_date_str(week_id: str) -> str:
    text = str(week_id or "").strip()
    match = re.match(r"^(\d{4})-W(\d{2})$", text)
    if not match:
        return datetime.now().strftime("%Y-%m-%d")
    year = int(match.group(1))
    week = int(match.group(2))
    return datetime.fromisocalendar(year, week, 7).strftime("%Y-%m-%d")


def _date_plus_days_str(value: str, days: int) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        return ""
    return (parsed + timedelta(days=days)).strftime("%Y-%m-%d")


def _history_frame_to_weekly_performance(frame: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    if frame is None or getattr(frame, "empty", True):
        return None, ""
    data = frame.copy()
    data.index = pd.to_datetime(data.index, errors="coerce")
    data = data[data.index.notna()].sort_index()
    close_col = "Close" if "Close" in data.columns else "close" if "close" in data.columns else None
    if close_col is None or data.empty:
        return None, ""
    close = pd.to_numeric(data[close_col], errors="coerce").dropna()
    if len(close) < 2:
        return None, ""
    weekly = close.resample("W-FRI").last().dropna()
    if len(weekly) < 2:
        return None, ""
    start_date = weekly.index[-2]
    end_date = weekly.index[-1]
    start_price = float(weekly.iloc[-2])
    end_price = float(weekly.iloc[-1])
    if start_price <= 0:
        return None, ""
    change = end_price - start_price
    change_pct = change / start_price * 100.0
    high_col = "High" if "High" in data.columns else "high" if "high" in data.columns else close_col
    low_col = "Low" if "Low" in data.columns else "low" if "low" in data.columns else close_col
    week_slice = data[(data.index > start_date - timedelta(days=7)) & (data.index <= end_date)]
    if week_slice.empty:
        week_slice = data.tail(5)
    high = float(pd.to_numeric(week_slice[high_col], errors="coerce").dropna().max())
    low = float(pd.to_numeric(week_slice[low_col], errors="coerce").dropna().min())
    payload = {
        "start_price": round(start_price, 2),
        "end_price": round(end_price, 2),
        "change_pct": round(change_pct, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "start_date": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
        "end_date": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
    }
    summary = (
        f"Text: {'Text' if change >= 0 else 'Text'} {abs(change_pct):.2f}% "
        f"(Last Week {payload['start_date']} Text {start_price:.2f} → This Week {payload['end_date']} Text {end_price:.2f}), "
        f"Text {high:.2f}, Text {low:.2f}"
    )
    return payload, summary


def _history_frame_to_portfolio_returns(frame: Any, buy_date: Any = None) -> Optional[Dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return None
    data = frame.copy()
    data.index = pd.to_datetime(data.index, errors="coerce")
    data = data[data.index.notna()].sort_index()
    close_col = "Close" if "Close" in data.columns else "close" if "close" in data.columns else None
    if close_col is None or data.empty:
        return None
    close = pd.to_numeric(data[close_col], errors="coerce").dropna()
    if close.empty:
        return None
    current_price = float(close.iloc[-1])
    latest = pd.Timestamp(close.index[-1]).to_pydatetime()
    result: Dict[str, Any] = {
        "return_since_buy": None,
        "ytd_return": None,
        "return_6m": None,
        "return_1y": None,
    }
    checkpoints = {
        "ytd_return": datetime(latest.year, 1, 1),
        "return_6m": latest - timedelta(days=185),
        "return_1y": latest - timedelta(days=370),
    }
    for key, target in checkpoints.items():
        historical = close[close.index <= pd.Timestamp(target)]
        if not historical.empty and float(historical.iloc[-1]) > 0:
            result[key] = round((current_price / float(historical.iloc[-1]) - 1.0) * 100.0, 2)
    buy_dt = _parse_date(str(buy_date or ""))
    if buy_dt is not None:
        historical = close[close.index <= pd.Timestamp(buy_dt)]
        if not historical.empty and float(historical.iloc[-1]) > 0:
            result["return_since_buy"] = round((current_price / float(historical.iloc[-1]) - 1.0) * 100.0, 2)
    return result


def _week_sort_key(week_id: str) -> Tuple[int, int]:
    text = str(week_id or "").strip()
    match = re.match(r"^(\d{4})-W(\d{2})$", text)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _looks_like_html_document(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    if value.startswith("<!doctype") or value.startswith("<html"):
        return True
    return ("<html" in value and "</html>" in value) or ("<body" in value and "</body>" in value)


def _clean_news_summary_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if _looks_like_html_document(value):
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_transient_llm_error(text: Any) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            "error code: 503",
            "503",
            "service temporarily unavailable",
            "temporarily unavailable",
            "server is busy",
            "server busy",
            "overloaded",
            "rate limit",
            "too many requests",
            "request timed out",
            "timed out",
            "timeout",
            "gateway timeout",
            "connection reset",
            "api_error",
        )
    )


def _normalize_macro_title(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip().lower())
    value = re.sub(r"[^\w\u4e00-\u9fff ]+", "", value)
    return value


def _google_news_macro_search(query: str, days: int = 7, limit: int = 6) -> List[Dict[str, Any]]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    parsed = feedparser.parse(url)
    cutoff = datetime.now() - timedelta(days=max(1, int(days)))
    rows: List[Dict[str, Any]] = []
    for entry in parsed.entries or []:
        published = getattr(entry, "published_parsed", None)
        published_dt = None
        if published:
            try:
                published_dt = datetime(*published[:6])
            except Exception:
                published_dt = None
        if published_dt and published_dt < cutoff:
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        summary = _clean_news_summary_text(entry.get("summary") or "")[:280]
        source = ""
        source_block = entry.get("source")
        if isinstance(source_block, dict):
            source = str(source_block.get("title") or "").strip()
        if not source:
            source = "Google News"
        rows.append(
            {
                "title": title,
                "summary": summary,
                "source": source,
                "url": str(entry.get("link") or "").strip(),
                "published_at": published_dt.isoformat(timespec="seconds") if published_dt else "",
                "query": query,
            }
        )
        if len(rows) >= max(1, int(limit)):
            break
    return rows


class WeeklyReviewManager:
    """Weekly ReviewText"""

    MARKET_SIGNAL_SPECS = [
        {"id": "SPY", "ticker": "SPY", "name": "SPY", "group": "Text", "proxy_note": "Text"},
        {"id": "QQQ", "ticker": "QQQ", "name": "QQQ", "group": "Text", "proxy_note": "Text/AI Text"},
        {"id": "IXN", "ticker": "IXN", "name": "IXN", "group": "Text", "proxy_note": "Text"},
        {"id": "FXI", "ticker": "FXI", "name": "FXI", "group": "Text", "proxy_note": "Text/Text"},
        {"id": "KWEB", "ticker": "KWEB", "name": "KWEB", "group": "Text", "proxy_note": "Text/Text(HSTECH Text)"},
        {"id": "VIXY", "ticker": "VIXY", "name": "VIXY", "group": "Text", "proxy_note": "VIX Text"},
        {"id": "TLT", "ticker": "TLT", "name": "TLT", "group": "Text", "proxy_note": "Text"},
        {"id": "SOXX", "ticker": "SOXX", "name": "SOXX", "group": "Text", "proxy_note": "Text"},
        {"id": "IGV", "ticker": "IGV", "name": "IGV", "group": "Text", "proxy_note": "Text/Text"},
        {"id": "XLI", "ticker": "XLI", "name": "XLI", "group": "Text", "proxy_note": "Text"},
        {"id": "XLB", "ticker": "XLB", "name": "XLB", "group": "Text", "proxy_note": "Text"},
        {"id": "IYT", "ticker": "IYT", "name": "IYT", "group": "Text", "proxy_note": "Text/Text"},
        {"id": "XLE", "ticker": "XLE", "name": "XLE", "group": "Text", "proxy_note": "Text"},
        {"id": "CPER", "ticker": "CPER", "name": "CPER", "group": "Text/Text", "proxy_note": "Text"},
        {"id": "USO", "ticker": "USO", "name": "USO", "group": "Text/Text", "proxy_note": "Text"},
        {"id": "GLD", "ticker": "GLD", "name": "GLD", "group": "Text/Text", "proxy_note": "Text"},
    ]

    HOLDING_BUCKETS = {
        "semiconductor": {
            "label": "Text",
            "signals": ["SOXX", "IXN", "QQQ"],
            "keywords": ["FORMFACTOR", "MACOM", "KEYSIGHT", "KEYS", "SANDISK", "WESTERN DIGITAL", "Text", "Text"],
        },
        "china_tech": {
            "label": "Text/TextRiskText",
            "signals": ["KWEB", "FXI"],
            "keywords": ["Text", "Text", "Text", "Text", "Text", "Text", "Text", "Text", "Text"],
        },
        "industrial_materials": {
            "label": "Text/Text",
            "signals": ["XLI", "XLB", "CPER", "IYT"],
            "keywords": ["ALMONTY", "ELEMENT", "Text", "Text", "Text", "Text", "Text", "Text"],
        },
        "rates": {
            "label": "Text",
            "signals": ["TLT", "QQQ"],
            "keywords": ["FORMFACTOR", "MACOM", "KEYSIGHT", "Text"],
        },
    }

    def __init__(
        self,
        client: Any,
        storage: Storage,
        env_collector: EnvironmentCollector,
        history_frame_loader: Optional[Any] = None,
    ):
        self.client = client
        self.storage = storage
        self.env_collector = env_collector
        self.deep_search_service = DeepSearchService(storage, client)
        self.history_frame_loader = history_frame_loader

    def get_or_create_review(self, week_id: Optional[str] = None) -> Dict:
        """TextCurrentTextReviewText"""
        if week_id is None:
            week_id = get_week_id()
        stocks = self.storage.list_stocks()
        return self.storage.get_weekly_review_with_portfolio_state(week_id, stock_list=stocks)

    def _normalize_trim_reallocation_op(self, op: Dict[str, Any]) -> Dict[str, Any]:
        return normalize_trim_reallocation_op(op)

    def _is_sell_like_op(self, op_type: str) -> bool:
        return is_sell_like_op(op_type)

    def _is_buy_like_op(self, op_type: str) -> bool:
        return is_buy_like_op(op_type)

    def _auto_pair_trim_event(self, trim_op: Dict[str, Any], buy_ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return auto_pair_trim_event(trim_op, buy_ops)

    def _calc_trim_window_metrics(
        self,
        released_amount: float,
        original_stock_id: str,
        trim_date: str,
        end_date: str,
        paired_buys: List[Dict[str, Any]],
        price_lookup: Dict[Tuple[str, str], float],
        buy_entry_dates: Dict[str, str],
    ) -> Dict[str, Any]:
        return calc_trim_window_metrics(
            released_amount=released_amount,
            original_stock_id=original_stock_id,
            trim_date=trim_date,
            end_date=end_date,
            paired_buys=paired_buys,
            price_lookup=price_lookup,
            buy_entry_dates=buy_entry_dates,
        )

    def _build_trim_reallocation_events(
        self,
        week_id: str,
        review: Dict[str, Any],
        price_lookup: Dict[Tuple[str, str], float],
        current_date: str,
    ) -> List[Dict[str, Any]]:
        normalized_ops = [
            self._normalize_trim_reallocation_op(op)
            for op in (review.get("rebalancing_ops") or [])
            if isinstance(op, dict)
        ]
        buy_ops = [op for op in normalized_ops if self._is_buy_like_op(op.get("op_type")) and op.get("quantity", 0) > 0]
        buy_entry_dates: Dict[str, str] = {}
        for buy in buy_ops:
            stock_id = str(buy.get("stock_id") or "").strip()
            if stock_id and stock_id not in buy_entry_dates and buy.get("date"):
                buy_entry_dates[stock_id] = buy["date"]

        cash_ledger_pairings = self._build_same_week_cash_ledger_pairings(normalized_ops, review)
        week_end = _week_end_date_str(week_id)
        events: List[Dict[str, Any]] = []
        for idx, op in enumerate(normalized_ops):
            if not self._is_sell_like_op(op.get("op_type")):
                continue
            stock_id = str(op.get("stock_id") or "").strip()
            trim_date = str(op.get("date") or "").strip()
            price = _safe_float(op.get("price"))
            quantity = _safe_float(op.get("quantity")) or 0.0
            released_amount_native = quantity * (price or 0.0)
            released_amount = self._amount_to_hkd(stock_id, released_amount_native, review)
            if not stock_id or not trim_date or released_amount <= 0:
                continue

            pairing_mode = op.get("pairing_mode") if op.get("pairing_mode") in {"auto", "manual"} else "auto"
            manual_pairs = [dict(row) for row in (op.get("paired_buys") or []) if isinstance(row, dict) and str(row.get("stock_id") or "").strip()]
            paired_buys = manual_pairs if pairing_mode == "manual" and manual_pairs else cash_ledger_pairings.get(idx) or self._auto_pair_trim_event(op, buy_ops)
            if not manual_pairs:
                pairing_mode = "auto"

            weekly_metrics = self._calc_trim_window_metrics(
                released_amount=released_amount,
                original_stock_id=stock_id,
                trim_date=trim_date,
                end_date=week_end,
                paired_buys=paired_buys,
                price_lookup=price_lookup,
                buy_entry_dates=buy_entry_dates,
            )
            current_metrics = self._calc_trim_window_metrics(
                released_amount=released_amount,
                original_stock_id=stock_id,
                trim_date=trim_date,
                end_date=current_date,
                paired_buys=paired_buys,
                price_lookup=price_lookup,
                buy_entry_dates=buy_entry_dates,
            )
            events.append(
                {
                    "event_id": f"{week_id}:{stock_id}:{trim_date}:{idx}",
                    "stock_id": stock_id,
                    "trim_date": trim_date,
                    "trim_price": price,
                    "trim_quantity": quantity,
                    "released_amount": released_amount,
                    "released_amount_native": released_amount_native,
                    "released_amount_currency": self._amount_currency(stock_id, review),
                    "pairing_mode": pairing_mode,
                    "paired_buys": paired_buys,
                    "pairing_note": op.get("pairing_note") or "",
                    "weekly": weekly_metrics,
                    "current": current_metrics,
                }
            )
        return events

    def build_trim_reallocation_analysis(
        self,
        week_id: str,
        review: Dict[str, Any],
        price_lookup: Dict[Tuple[str, str], float],
        current_date: str,
    ) -> Dict[str, Any]:
        events = self._build_trim_reallocation_events(
            week_id=week_id,
            review=review,
            price_lookup=price_lookup,
            current_date=current_date,
        )
        summary = {
            "analyzed_trim_events": len(events),
            "released_amount_total": round(sum(event.get("released_amount") or 0.0 for event in events), 2),
            "weekly_relative_pnl_total": round(sum((event.get("weekly") or {}).get("relative_pnl") or 0.0 for event in events), 2),
            "current_relative_pnl_total": round(sum((event.get("current") or {}).get("relative_pnl") or 0.0 for event in events), 2),
        }
        by_stock: Dict[str, Dict[str, Any]] = {}
        for event in events:
            stock_id = event.get("stock_id")
            if stock_id not in by_stock:
                by_stock[stock_id] = {
                    "stock_id": stock_id,
                    "trim_event_count": 0,
                    "released_amount_total": 0.0,
                    "weekly_relative_pnl": 0.0,
                    "current_relative_pnl": 0.0,
                }
            row = by_stock[stock_id]
            row["trim_event_count"] += 1
            row["released_amount_total"] += event.get("released_amount") or 0.0
            row["weekly_relative_pnl"] += (event.get("weekly") or {}).get("relative_pnl") or 0.0
            row["current_relative_pnl"] += (event.get("current") or {}).get("relative_pnl") or 0.0

        stocks = []
        for row in by_stock.values():
            stocks.append(
                {
                    "stock_id": row["stock_id"],
                    "trim_event_count": row["trim_event_count"],
                    "released_amount_total": round(row["released_amount_total"], 2),
                    "weekly_relative_pnl": round(row["weekly_relative_pnl"], 2),
                    "current_relative_pnl": round(row["current_relative_pnl"], 2),
                }
            )
        stocks.sort(key=lambda item: abs(item.get("weekly_relative_pnl") or 0.0), reverse=True)
        return {"summary": summary, "stocks": stocks, "events": events}

    def _compute_return(
        self,
        price_lookup: Dict[Tuple[str, str], float],
        stock_id: str,
        start_date: str,
        end_date: str,
    ) -> Optional[float]:
        start_price = self._lookup_price_on_or_before(price_lookup, stock_id, start_date)
        end_price = self._lookup_price_on_or_before(price_lookup, stock_id, end_date)
        if not start_price or not end_price:
            return None
        return (end_price / start_price) - 1

    def _lookup_price_on_or_before(
        self,
        price_lookup: Dict[Tuple[str, str], float],
        stock_id: str,
        target_date: str,
    ) -> Optional[float]:
        target = _parse_date(target_date)
        if target is None:
            return None
        exact = _safe_float(price_lookup.get((stock_id, _normalize_date_text(target_date))))
        if exact:
            return exact
        candidates: List[Tuple[datetime, float]] = []
        for (candidate_stock_id, candidate_date), value in price_lookup.items():
            if str(candidate_stock_id or "").strip() != str(stock_id or "").strip():
                continue
            parsed = _parse_date(str(candidate_date or ""))
            price = _safe_float(value)
            if parsed is not None and parsed <= target and price:
                candidates.append((parsed, price))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _outcome_block(
        self,
        sold_capital: float,
        return_value: Optional[float],
        attribution_state: str = "ok",
        paired_targets: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        pnl = None if return_value is None else round(sold_capital * return_value, 2)
        block = {
            "return": None if return_value is None else round(return_value, 6),
            "pnl": pnl,
            "attribution_state": attribution_state,
        }
        if paired_targets is not None:
            block["paired_targets"] = paired_targets
        return block

    def _is_cash_destination(self, event_or_op: Dict[str, Any]) -> bool:
        destination = str((event_or_op or {}).get("destination_type") or "").strip().lower()
        decision_type = str((event_or_op or {}).get("decision_type") or "").strip().lower()
        return destination == "cash" or decision_type == "raise_cash"

    def _cash_actual_block_if_applicable(self, event_or_op: Dict[str, Any], cash_block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self._is_cash_destination(event_or_op):
            return None
        pnl = _safe_float((cash_block or {}).get("pnl"))
        return_value = _safe_float((cash_block or {}).get("return"))
        if pnl is None:
            return None
        row = dict(cash_block or {})
        row["return"] = 0.0 if return_value is None else return_value
        row["pnl"] = pnl
        row["attribution_state"] = "cash"
        row.setdefault("paired_targets", [])
        row["pairing_mode"] = "cash"
        return row

    def _is_ibkr_ledger_op(self, op: Dict[str, Any]) -> bool:
        source = str((op or {}).get("source") or "").strip().lower()
        return source in {"ibkr_ledger", "ibkr_trade", "ibkr_derived_hot_patch", "ibkr_delta_ledger"}

    def _cash_redeploy_block(self, sold_capital: float) -> Dict[str, Any]:
        return {
            "return": 0.0,
            "pnl": 0.0,
            "attribution_state": "cash",
            "paired_targets": [],
            "pairing_mode": "cash",
            "matched_amount": 0.0,
            "unallocated_amount": round(max(sold_capital, 0.0), 2),
        }

    def _actual_result_for_contribution(self, event: Dict[str, Any], suffix: str = "12w") -> Optional[Dict[str, Any]]:
        actual = dict((event or {}).get(f"actual_result_{suffix}") or {})
        actual_pnl = _safe_float(actual.get("pnl"))
        if actual_pnl is not None:
            actual["pnl"] = actual_pnl
            return actual
        cash = (event or {}).get(f"cash_result_{suffix}") or {}
        return self._cash_actual_block_if_applicable(event, cash)

    def _event_sold_capital(self, event: Dict[str, Any]) -> float:
        return round(_safe_float((event or {}).get("sold_capital")) or 0.0, 2)

    def _sum_sold_capital(self, events: List[Dict[str, Any]]) -> float:
        return round(sum(self._event_sold_capital(event) for event in events or []), 2)

    def _decision_review_suffix_for_horizon(self, mark_horizon: Optional[str] = None) -> str:
        value = str(mark_horizon or "now").strip().lower()
        if value in {"30d", "30", "mark_30d"}:
            return "30d"
        if value in {"60d", "60", "mark_60d"}:
            return "60d"
        if value in {"90d", "90", "mark_90d"}:
            return "90d"
        return "12w"

    def _decision_review_horizon_key(self, mark_horizon: Optional[str] = None) -> str:
        suffix = self._decision_review_suffix_for_horizon(mark_horizon)
        if suffix == "12w":
            return "now"
        return suffix

    def _default_benchmark_for_op(self, op: Dict[str, Any]) -> str:
        stock_id = str(op.get("stock_id") or "").strip().upper()
        if stock_id.endswith(".HK"):
            return "FXI"
        semiconductor_markers = ("NVDA", "TSM", "MU", "AMD", "ASML", "MKSI", "SAMPLE", "SAMPLE")
        if stock_id in semiconductor_markers:
            return "SOXX"
        return "SPY"

    def _compute_actual_redeploy_block(
        self,
        op: Dict[str, Any],
        buy_ops: List[Dict[str, Any]],
        buy_entry_dates: Dict[str, str],
        price_lookup: Dict[Tuple[str, str], float],
        end_date: str,
        sold_capital: float,
    ) -> Dict[str, Any]:
        pairing_mode = op.get("pairing_mode") if op.get("pairing_mode") in {"auto", "manual"} else "auto"
        manual_pairs = [
            dict(row)
            for row in (op.get("paired_buys") or [])
            if isinstance(row, dict) and str(row.get("stock_id") or "").strip()
        ]
        if not manual_pairs and self._is_cash_destination(op):
            return self._cash_redeploy_block(sold_capital)
        paired_buys = manual_pairs if pairing_mode == "manual" and manual_pairs else self._auto_pair_trim_event(op, buy_ops)
        if not manual_pairs:
            pairing_mode = "auto"
        if not paired_buys:
            return {
                "return": None,
                "pnl": None,
                "attribution_state": "unallocated",
                "paired_targets": [],
                "pairing_mode": pairing_mode,
                "matched_amount": 0.0,
                "unallocated_amount": round(max(sold_capital, 0.0), 2),
            }

        reallocated_pnl = 0.0
        matched_amount = 0.0
        missing_price_data = False
        computed_targets = 0
        target_rows: List[Dict[str, Any]] = []
        trim_date = str(op.get("date") or "").strip()

        for paired in paired_buys:
            stock_id = str(paired.get("stock_id") or "").strip()
            allocated_amount = _safe_float(paired.get("amount")) or 0.0
            if not stock_id or allocated_amount <= 0:
                continue
            entry_date = _normalize_date_text(paired.get("entry_date") or paired.get("buy_date") or buy_entry_dates.get(stock_id) or trim_date)
            target_start = self._lookup_price_on_or_before(price_lookup, stock_id, entry_date) or self._lookup_price_on_or_before(price_lookup, stock_id, trim_date)
            target_end = self._lookup_price_on_or_before(price_lookup, stock_id, end_date)
            row = {
                "stock_id": stock_id,
                "entry_date": entry_date,
                "allocated_amount": allocated_amount,
            }
            if paired.get("buy_week_id"):
                row["buy_week_id"] = paired.get("buy_week_id")
            if not target_start or not target_end:
                missing_price_data = True
                row["missing_price_data"] = True
                target_rows.append(row)
                continue
            target_return = (target_end / target_start) - 1
            target_pnl = allocated_amount * target_return
            matched_amount += allocated_amount
            reallocated_pnl += target_pnl
            computed_targets += 1
            row.update(
                {
                    "target_return": round(target_return, 6),
                    "target_pnl": round(target_pnl, 2),
                    "missing_price_data": False,
                }
            )
            target_rows.append(row)

        if computed_targets <= 0 or sold_capital <= 0:
            return {
                "return": None,
                "pnl": None,
                "attribution_state": "missing_price_data" if missing_price_data else "unallocated",
                "paired_targets": target_rows,
                "pairing_mode": pairing_mode,
            }

        attribution_state = "ok"
        if missing_price_data:
            attribution_state = "missing_price_data"
        elif matched_amount + 0.01 < sold_capital:
            attribution_state = "partially_paired"
        return {
            "return": round(reallocated_pnl / sold_capital, 6),
            "pnl": round(reallocated_pnl, 2),
            "attribution_state": attribution_state,
            "paired_targets": target_rows,
            "pairing_mode": pairing_mode,
            "matched_amount": round(matched_amount, 2),
            "unallocated_amount": round(max(sold_capital - matched_amount, 0.0), 2),
        }

    def _build_same_week_cash_ledger_pairings(self, normalized_ops: List[Dict[str, Any]], review: Optional[Dict[str, Any]] = None) -> Dict[int, List[Dict[str, Any]]]:
        review_payload = review or {}
        sell_lots: List[Dict[str, Any]] = []
        pairings: Dict[int, List[Dict[str, Any]]] = {}
        for index, op in enumerate(normalized_ops):
            op_date = _parse_date(str(op.get("date") or ""))
            if op_date is None:
                continue
            raw_amount = (_safe_float(op.get("quantity")) or 0.0) * (_safe_float(op.get("price")) or 0.0)
            amount = self._amount_to_hkd(str(op.get("stock_id") or ""), raw_amount, review_payload)
            if amount <= 0:
                continue
            if self._is_sell_like_op(op.get("op_type")):
                sell_lots.append({"index": index, "remaining": amount, "total": amount})
                pairings.setdefault(index, [])
                continue
            if not self._is_buy_like_op(op.get("op_type")):
                continue
            remaining_buy = amount
            for lot in sell_lots:
                if remaining_buy <= 0:
                    break
                if lot["remaining"] <= 0:
                    continue
                allocated = min(lot["remaining"], remaining_buy)
                lot["remaining"] -= allocated
                remaining_buy -= allocated
                pairings.setdefault(lot["index"], []).append(
                    {
                        "stock_id": op.get("stock_id"),
                        "amount": round(allocated, 2),
                        "ratio": round(allocated / lot["total"], 6) if lot["total"] else None,
                        "buy_date": op.get("date"),
                        "entry_date": op.get("date"),
                        "source": "cash_ledger",
                    }
                )
        for rows in pairings.values():
            matched = sum(_safe_float(row.get("amount")) or 0.0 for row in rows)
            if matched <= 0:
                continue
            for row in rows:
                row["ratio"] = round((_safe_float(row.get("amount")) or 0.0) / matched, 6)
        return pairings

    def _amount_to_hkd(self, stock_id: str, amount: float, review: Dict[str, Any]) -> float:
        ticker = str(stock_id or "").strip().upper()
        stock_meta = self._review_stock_meta_by_alias(stock_id, review)
        meta_ticker = str((stock_meta or {}).get("ticker") or "").strip().upper()
        if meta_ticker:
            ticker = meta_ticker
        if ticker.endswith(".HK"):
            rate = 1.0
        elif ticker.endswith((".SH", ".SZ", ".SS")):
            rate = _safe_float(review.get("cny_to_hkd")) or 1.07
        elif ticker.endswith((".AS", ".DE", ".VI", ".PA", ".MI")):
            rate = _safe_float(review.get("eur_to_hkd")) or 8.4
        elif ticker.endswith(".T"):
            rate = _safe_float(review.get("jpy_to_hkd")) or 0.052
        elif ticker.endswith((".KS", ".KQ")):
            rate = _safe_float(review.get("krw_to_hkd")) or 0.0056
        else:
            rate = _safe_float(review.get("usd_to_hkd")) or 7.8
        return round(amount * rate, 2)

    def _amount_currency(self, stock_id: str, review: Dict[str, Any]) -> str:
        ticker = str(stock_id or "").strip().upper()
        stock_meta = self._review_stock_meta_by_alias(stock_id, review)
        meta_ticker = str((stock_meta or {}).get("ticker") or "").strip().upper()
        if meta_ticker:
            ticker = meta_ticker
        if ticker.endswith(".HK"):
            return "HKD"
        if ticker.endswith((".SH", ".SZ", ".SS")):
            return "CNY"
        if ticker.endswith((".AS", ".DE", ".VI", ".PA", ".MI")):
            return "EUR"
        if ticker.endswith(".T"):
            return "JPY"
        if ticker.endswith((".KS", ".KQ")):
            return "KRW"
        return "USD"

    def _fx_rate_for_currency(self, currency: str, review: Dict[str, Any]) -> float:
        code = str(currency or "").strip().upper()
        if code == "HKD":
            return 1.0
        if code == "CNY":
            return _safe_float((review or {}).get("cny_to_hkd")) or 1.07
        if code == "EUR":
            return _safe_float((review or {}).get("eur_to_hkd")) or 8.4
        if code == "JPY":
            return _safe_float((review or {}).get("jpy_to_hkd")) or 0.052
        if code == "KRW":
            return _safe_float((review or {}).get("krw_to_hkd")) or 0.0056
        return _safe_float((review or {}).get("usd_to_hkd")) or 7.8

    def _review_stock_meta_by_alias(self, stock_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        stocks = (review or {}).get("stocks") or {}
        raw = str(stock_id or "").strip()
        if not raw:
            return {}
        direct = stocks.get(raw)
        if isinstance(direct, dict):
            return direct
        target = self.storage._canonical_code(raw) if hasattr(self.storage, "_canonical_code") else raw.upper()
        for key, payload in stocks.items():
            if not isinstance(payload, dict):
                continue
            candidates = [key, payload.get("ticker"), payload.get("stock_name"), payload.get("search_name")]
            for candidate in candidates:
                text = str(candidate or "").strip()
                if not text:
                    continue
                canon = self.storage._canonical_code(text) if hasattr(self.storage, "_canonical_code") else text.upper()
                if canon == target:
                    return payload
        return {}

    def _build_cross_week_cash_ledger_pairings(self, week_ids: List[str]) -> Dict[Tuple[str, int], List[Dict[str, Any]]]:
        sell_lots: List[Dict[str, Any]] = []
        pairings: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        for week_id in sorted(week_ids, key=_week_sort_key):
            review = self.storage.get_weekly_review(week_id) or {}
            ops = [self._normalize_trim_reallocation_op(op) for op in (review.get("rebalancing_ops") or []) if isinstance(op, dict)]
            for index, op in enumerate(ops):
                raw_amount = (_safe_float(op.get("quantity")) or 0.0) * (_safe_float(op.get("price")) or 0.0)
                amount_hkd = self._amount_to_hkd(str(op.get("stock_id") or ""), raw_amount, review)
                if amount_hkd <= 0:
                    continue
                if self._is_sell_like_op(op.get("op_type")):
                    key = (week_id, index)
                    sell_lots.append({"key": key, "remaining": amount_hkd, "total": amount_hkd})
                    pairings.setdefault(key, [])
                    continue
                if not self._is_buy_like_op(op.get("op_type")):
                    continue
                remaining_buy = amount_hkd
                for lot in sell_lots:
                    if remaining_buy <= 0:
                        break
                    if lot["remaining"] <= 0:
                        continue
                    allocated = min(lot["remaining"], remaining_buy)
                    lot["remaining"] -= allocated
                    remaining_buy -= allocated
                    pairings.setdefault(lot["key"], []).append(
                        {
                            "stock_id": op.get("stock_id"),
                            "amount": round(allocated, 2),
                            "buy_amount": round(allocated, 2),
                            "buy_week_id": week_id,
                            "buy_date": op.get("date"),
                            "entry_date": op.get("date"),
                            "source": "cross_week_cash_ledger",
                        }
                    )
        return pairings

    def _build_decision_window(
        self,
        op: Dict[str, Any],
        buy_ops: List[Dict[str, Any]],
        buy_entry_dates: Dict[str, str],
        price_lookup: Dict[Tuple[str, str], float],
        end_date: str,
        review: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sold_capital_native = (_safe_float(op.get("quantity")) or 0.0) * (_safe_float(op.get("price")) or 0.0)
        sold_capital = self._amount_to_hkd(str(op.get("stock_id") or ""), sold_capital_native, review or {})
        hold_return = self._compute_return(price_lookup, op.get("stock_id") or "", op.get("date") or "", end_date)
        actual_block = self._compute_actual_redeploy_block(op, buy_ops, buy_entry_dates, price_lookup, end_date, sold_capital)
        benchmark_id = (op.get("benchmark") or self._default_benchmark_for_op(op)).strip().upper()
        benchmark_return = self._compute_return(price_lookup, benchmark_id, op.get("date") or "", end_date)
        cash_block = self._outcome_block(sold_capital, 0.0)
        normalized_actual = {
            "return": actual_block.get("return"),
            "pnl": actual_block.get("pnl"),
            "attribution_state": actual_block.get("attribution_state", "ok"),
            "paired_targets": actual_block.get("paired_targets") or [],
            "pairing_mode": actual_block.get("pairing_mode") or "auto",
            "matched_amount": actual_block.get("matched_amount"),
            "unallocated_amount": actual_block.get("unallocated_amount"),
        }
        cash_actual = self._cash_actual_block_if_applicable(op, cash_block)
        if cash_actual is not None and _safe_float(normalized_actual.get("pnl")) is None:
            normalized_actual = cash_actual
        return {
            "window_end": end_date,
            "hold_original": self._outcome_block(sold_capital, hold_return),
            "actual_redeploy": normalized_actual,
            "cash_outcome": cash_block,
            "benchmark_outcome": {
                "benchmark_id": benchmark_id,
                **self._outcome_block(sold_capital, benchmark_return),
            },
        }

    def _interpret_decision_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        decision_type = str(event.get("decision_type") or "unknown").strip().lower() or "unknown"
        hold_return = (event.get("hold_original") or {}).get("return")
        actual_return = (event.get("actual_redeploy") or {}).get("return")
        cash_return = (event.get("cash_outcome") or {}).get("return")

        if decision_type == "rotate":
            verdict = (
                "rotation_success"
                if actual_return is not None and hold_return is not None and actual_return > hold_return
                else "rotation_underperformed"
            )
        elif decision_type == "risk_reduction":
            verdict = (
                "risk_reduction_success"
                if hold_return is not None and cash_return is not None and hold_return < cash_return
                else "cash_preserved_capital_but_lost_upside"
            )
        elif decision_type == "thesis_change":
            verdict = "thesis_exit_validated" if hold_return is not None and hold_return < 0 else "sold_too_early"
        elif decision_type == "take_profit":
            verdict = "sold_too_early" if hold_return is not None and cash_return is not None and hold_return > cash_return else "risk_reduction_success"
        elif decision_type == "raise_cash":
            verdict = (
                "risk_reduction_success"
                if hold_return is not None and cash_return is not None and hold_return < cash_return
                else "cash_preserved_capital_but_lost_upside"
            )
        else:
            verdict = "decision_needs_context"
        event["verdict_label"] = verdict
        return event

    def _build_decision_events(
        self,
        week_id: str,
        review: Dict[str, Any],
        price_lookup: Dict[Tuple[str, str], float],
        current_date: str,
        cross_week_pairings: Optional[Dict[Tuple[str, int], List[Dict[str, Any]]]] = None,
    ) -> List[Dict[str, Any]]:
        normalized_ops = [
            self._normalize_trim_reallocation_op(op)
            for op in (review.get("rebalancing_ops") or [])
            if isinstance(op, dict)
        ]
        buy_ops = [op for op in normalized_ops if self._is_buy_like_op(op.get("op_type")) and op.get("quantity", 0) > 0]
        buy_entry_dates: Dict[str, str] = {}
        for buy in buy_ops:
            stock_id = str(buy.get("stock_id") or "").strip()
            if stock_id and stock_id not in buy_entry_dates and buy.get("date"):
                buy_entry_dates[stock_id] = buy["date"]

        cash_ledger_pairings = self._build_same_week_cash_ledger_pairings(normalized_ops, review)
        week_end = _week_end_date_str(week_id)
        events: List[Dict[str, Any]] = []
        for idx, op in enumerate(normalized_ops):
            if not self._is_sell_like_op(op.get("op_type")):
                continue
            stock_id = str(op.get("stock_id") or "").strip()
            trim_date = str(op.get("date") or "").strip()
            price = _safe_float(op.get("price"))
            quantity = _safe_float(op.get("quantity")) or 0.0
            sold_capital_native = quantity * (price or 0.0)
            sold_capital = self._amount_to_hkd(stock_id, sold_capital_native, review)
            if not stock_id or not trim_date or sold_capital <= 0:
                continue

            event_op = dict(op)
            if event_op.get("pairing_mode") != "manual" and cash_ledger_pairings.get(idx):
                event_op["pairing_mode"] = "manual"
                event_op["paired_buys"] = cash_ledger_pairings.get(idx)
            elif event_op.get("pairing_mode") != "manual" and cross_week_pairings and cross_week_pairings.get((week_id, idx)):
                event_op["pairing_mode"] = "manual"
                event_op["paired_buys"] = cross_week_pairings.get((week_id, idx))
            elif event_op.get("pairing_mode") != "manual" and self._is_ibkr_ledger_op(event_op):
                event_op["destination_type"] = "cash"
                event_op["pairing_mode"] = "cash"
                event_op["pairing_note"] = event_op.get("pairing_note") or "IBKR ledger sell proceeds were not matched to a later buy; treating them as cash."

            mark_30d_end = _date_plus_days_str(trim_date, 30)
            mark_60d_end = _date_plus_days_str(trim_date, 60)
            mark_90d_end = _date_plus_days_str(trim_date, 90)
            weekly_window = self._build_decision_window(event_op, buy_ops, buy_entry_dates, price_lookup, week_end, review=review)
            mark_30d_window = self._build_decision_window(event_op, buy_ops, buy_entry_dates, price_lookup, mark_30d_end, review=review) if mark_30d_end else {}
            mark_60d_window = self._build_decision_window(event_op, buy_ops, buy_entry_dates, price_lookup, mark_60d_end, review=review) if mark_60d_end else {}
            mark_90d_window = self._build_decision_window(event_op, buy_ops, buy_entry_dates, price_lookup, mark_90d_end, review=review) if mark_90d_end else {}
            current_window = self._build_decision_window(event_op, buy_ops, buy_entry_dates, price_lookup, current_date, review=review)
            for window in (weekly_window, mark_30d_window, mark_60d_window, mark_90d_window, current_window):
                if not window:
                    continue
                actual = window.get("actual_redeploy") or {}
                if _safe_float(actual.get("pnl")) is not None:
                    continue
                path_pnl = self._portfolio_path_pnl_from_review(
                    review,
                    sold_capital,
                    week_end,
                    window.get("window_end") or week_end,
                    price_lookup,
                )
                if path_pnl is None:
                    continue
                window["actual_redeploy"] = {
                    "return": round(path_pnl / sold_capital, 6) if sold_capital else None,
                    "pnl": path_pnl,
                    "attribution_state": "weekend_portfolio",
                    "paired_targets": [],
                    "pairing_mode": "weekend_portfolio",
                    "actual_basis": "weekend_portfolio",
                }
            primary_window = current_window if current_date and current_date != week_end else weekly_window
            if (primary_window.get("hold_original") or {}).get("return") is None and (current_window.get("hold_original") or {}).get("return") is not None:
                primary_window = current_window

            event = {
                "event_id": f"{week_id}:{stock_id}:{trim_date}:{idx}",
                "stock_id": stock_id,
                "date": trim_date,
                "decision_type": op.get("decision_type", "unknown"),
                "destination_type": op.get("destination_type", "unknown"),
                "review_horizon": op.get("review_horizon", "week_end"),
                "benchmark": (op.get("benchmark") or self._default_benchmark_for_op(op)).strip().upper(),
                "decision_note": op.get("decision_note") or "",
                "sold_capital": round(sold_capital, 2),
                "sold_capital_native": round(sold_capital_native, 2),
                "sold_currency": self._amount_currency(stock_id, review),
                "pairing_note": op.get("pairing_note") or "",
                "paired_buys": (weekly_window.get("actual_redeploy") or {}).get("paired_targets") or [],
                "weekly": weekly_window,
                "mark_30d": mark_30d_window,
                "mark_60d": mark_60d_window,
                "mark_90d": mark_90d_window,
                "current": current_window,
                "hold_original": dict(primary_window.get("hold_original") or {}),
                "actual_redeploy": dict(primary_window.get("actual_redeploy") or {}),
                "cash_outcome": dict(primary_window.get("cash_outcome") or {}),
                "benchmark_outcome": dict(primary_window.get("benchmark_outcome") or {}),
            }
            events.append(self._interpret_decision_event(event))
        return events

    def _build_decision_attribution_analysis(
        self,
        week_id: str,
        review: Dict[str, Any],
        price_lookup: Dict[Tuple[str, str], float],
        current_date: str,
    ) -> Dict[str, Any]:
        events = self._build_decision_events(
            week_id=week_id,
            review=review,
            price_lookup=price_lookup,
            current_date=current_date,
        )
        return {
            "summary": self._summarize_decision_events(events),
            "patterns": self._build_history_patterns(events),
            "stocks": self._summarize_decision_stocks(events),
            "events": events,
        }

    def _summarize_decision_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        capital_affected = round(sum(_safe_float(event.get("sold_capital")) or 0.0 for event in events), 2)
        relative_gain_from_actual = 0.0
        avoided_drawdown_vs_hold = 0.0
        foregone_upside_vs_hold = 0.0
        by_decision_type: Dict[str, Dict[str, Any]] = {}
        for event in events:
            decision_type = str(event.get("decision_type") or "unknown").strip().lower() or "unknown"
            hold_pnl = _safe_float((event.get("hold_original") or {}).get("pnl")) or 0.0
            actual_pnl = _safe_float((event.get("actual_redeploy") or {}).get("pnl")) or 0.0
            cash_pnl = _safe_float((event.get("cash_outcome") or {}).get("pnl")) or 0.0
            relative_gain_from_actual += actual_pnl - hold_pnl
            avoided_drawdown_vs_hold += max(0.0, cash_pnl - hold_pnl)
            foregone_upside_vs_hold += max(0.0, hold_pnl - cash_pnl)
            bucket = by_decision_type.setdefault(
                decision_type,
                {"count": 0, "capital_affected": 0.0},
            )
            bucket["count"] += 1
            bucket["capital_affected"] += _safe_float(event.get("sold_capital")) or 0.0
        return {
            "event_count": len(events),
            "capital_affected": capital_affected,
            "relative_gain_from_actual": round(relative_gain_from_actual, 2),
            "avoided_drawdown_vs_hold": round(avoided_drawdown_vs_hold, 2),
            "foregone_upside_vs_hold": round(foregone_upside_vs_hold, 2),
            "by_decision_type": {
                key: {
                    "count": value["count"],
                    "capital_affected": round(value["capital_affected"], 2),
                }
                for key, value in by_decision_type.items()
            },
        }

    def _summarize_decision_stocks(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for event in events:
            stock_id = str(event.get("stock_id") or "").strip()
            if not stock_id:
                continue
            grouped.setdefault(stock_id, []).append(event)
        rows: List[Dict[str, Any]] = []
        for stock_id, items in grouped.items():
            verdict_counts: Dict[str, int] = {}
            for item in items:
                verdict = str(item.get("verdict_label") or "").strip()
                if verdict:
                    verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            common_verdict = ""
            if verdict_counts:
                common_verdict = max(verdict_counts.items(), key=lambda pair: pair[1])[0]
            rows.append(
                {
                    "stock_id": stock_id,
                    "event_count": len(items),
                    "capital_affected": round(sum(_safe_float(item.get("sold_capital")) or 0.0 for item in items), 2),
                    "average_relative_pnl": round(
                        sum(
                            ((_safe_float((item.get("actual_redeploy") or {}).get("pnl")) or 0.0) - (_safe_float((item.get("hold_original") or {}).get("pnl")) or 0.0))
                            for item in items
                        )
                        / max(len(items), 1),
                        2,
                    ),
                    "common_verdict": common_verdict,
                }
            )
        rows.sort(key=lambda item: (-(item.get("event_count") or 0), -(item.get("capital_affected") or 0.0), item.get("stock_id") or ""))
        return rows

    def _summarize_decision_patterns(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_decision_type: Dict[str, Dict[str, Any]] = {}
        failure_counts: Dict[str, int] = {}
        success_counts: Dict[str, int] = {}
        success_labels = {"rotation_success", "risk_reduction_success", "thesis_exit_validated"}
        for event in events:
            decision_type = str(event.get("decision_type") or "unknown").strip().lower() or "unknown"
            verdict_label = str(event.get("verdict_label") or "").strip() or "decision_needs_context"
            bucket = by_decision_type.setdefault(
                decision_type,
                {"count": 0, "success_count": 0, "failure_count": 0},
            )
            bucket["count"] += 1
            if verdict_label in success_labels:
                bucket["success_count"] += 1
                success_counts[verdict_label] = success_counts.get(verdict_label, 0) + 1
            else:
                bucket["failure_count"] += 1
                failure_counts[verdict_label] = failure_counts.get(verdict_label, 0) + 1

        biggest_recurring_failure = None
        if failure_counts:
            biggest_failure_label = max(failure_counts.items(), key=lambda pair: pair[1])[0]
            biggest_recurring_failure = {
                "verdict_label": biggest_failure_label,
                "count": failure_counts[biggest_failure_label],
            }

        biggest_recurring_success = None
        if success_counts:
            biggest_success_label = max(success_counts.items(), key=lambda pair: pair[1])[0]
            biggest_recurring_success = {
                "verdict_label": biggest_success_label,
                "count": success_counts[biggest_success_label],
            }

        return {
            "by_decision_type": by_decision_type,
            "biggest_recurring_failure": biggest_recurring_failure,
            "biggest_recurring_success": biggest_recurring_success,
        }

    def _build_history_patterns(self, current_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        historical_events: List[Dict[str, Any]] = []
        for week_id in self.storage.get_weekly_review_history(limit=10_000):
            review = self.storage.get_weekly_review(week_id) or {}
            existing = ((review.get("decision_attribution_analysis") or {}).get("events")) or []
            historical_events.extend([event for event in existing if isinstance(event, dict)])
        return self._summarize_decision_patterns(historical_events + list(current_events))

    def _build_decision_review_price_context(
        self,
        week_ids: List[str],
        current_date: str,
    ) -> Tuple[Dict[str, Dict[Tuple[str, str], float]], Dict[Tuple[str, str], float]]:
        price_lookup_by_week: Dict[str, Dict[Tuple[str, str], float]] = {}
        global_price_lookup: Dict[Tuple[str, str], float] = {}
        for week_id in week_ids:
            review = self.storage.get_weekly_review(week_id) or {}
            if not review.get("rebalancing_ops"):
                continue
            lookup = self._build_trim_price_lookup(week_id=week_id, review=review, current_date=current_date)
            price_lookup_by_week[week_id] = lookup
            global_price_lookup.update(lookup)
        return price_lookup_by_week, global_price_lookup

    def _load_decision_review_events(
        self,
        *,
        week_ids: Optional[List[str]] = None,
        cross_week_pairings: Optional[Dict[Tuple[str, int], List[Dict[str, Any]]]] = None,
        current_date: Optional[str] = None,
        price_lookup_by_week: Optional[Dict[str, Dict[Tuple[str, str], float]]] = None,
        global_price_lookup: Optional[Dict[Tuple[str, str], float]] = None,
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        current_date = current_date or datetime.now().strftime("%Y-%m-%d")
        week_ids = list(week_ids) if week_ids is not None else list(self.storage.get_weekly_review_history(limit=10_000))
        cross_week_pairings = cross_week_pairings if cross_week_pairings is not None else self._build_cross_week_cash_ledger_pairings(week_ids)
        if price_lookup_by_week is None or global_price_lookup is None:
            price_lookup_by_week, global_price_lookup = self._build_decision_review_price_context(week_ids, current_date)
        for week_id in week_ids:
            review = self.storage.get_weekly_review(week_id) or {}
            existing = ((review.get("decision_attribution_analysis") or {}).get("events")) or []
            persisted_events = [event for event in existing if isinstance(event, dict)]
            generated_events: List[Dict[str, Any]] = []
            if review.get("rebalancing_ops"):
                try:
                    price_lookup = dict(global_price_lookup)
                    price_lookup.update(price_lookup_by_week.get(week_id) or {})
                    generated_events = self._build_decision_events(
                        week_id=week_id,
                        review=review,
                        price_lookup=price_lookup,
                        current_date=current_date,
                        cross_week_pairings=cross_week_pairings,
                    )
                except Exception:
                    logger.exception("failed to build decision review fallback events for week_id=%s", week_id)
                    generated_events = []
            source_by_id: Dict[str, Dict[str, Any]] = {}
            anonymous_events: List[Dict[str, Any]] = []
            for event in persisted_events:
                event["_decision_review_source"] = "persisted"
            for event in generated_events:
                event["_decision_review_source"] = "generated"
            for event in persisted_events + generated_events:
                event_id = str((event or {}).get("event_id") or "").strip()
                if event_id:
                    source_by_id[event_id] = event
                else:
                    anonymous_events.append(event)
            source_events = anonymous_events + list(source_by_id.values())
            for event in source_events:
                if not isinstance(event, dict):
                    continue
                row = dict(event)
                row["week_id"] = str(row.get("week_id") or week_id)
                events.append(row)
        return events

    def _normalize_decision_review_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(event or {})
        weekly = dict(row.get("weekly") or {})
        mark_30d = dict(row.get("mark_30d") or row.get("thirty_day") or {})
        mark_60d = dict(row.get("mark_60d") or {})
        mark_90d = dict(row.get("mark_90d") or {})
        current = dict(row.get("current") or {})
        weekly_hold = dict(weekly.get("hold_original") or {})
        weekly_actual = dict(weekly.get("actual_redeploy") or {})
        weekly_cash = dict(weekly.get("cash_outcome") or {})
        weekly_benchmark = dict(weekly.get("benchmark_outcome") or {})
        mark_30d_hold = dict(mark_30d.get("hold_original") or row.get("original_hold_30d") or {})
        mark_30d_actual = dict(mark_30d.get("actual_redeploy") or row.get("actual_result_30d") or {})
        mark_30d_cash = dict(mark_30d.get("cash_outcome") or row.get("cash_result_30d") or {})
        mark_30d_benchmark = dict(mark_30d.get("benchmark_outcome") or row.get("benchmark_result_30d") or {})
        mark_60d_hold = dict(mark_60d.get("hold_original") or row.get("original_hold_60d") or {})
        mark_60d_actual = dict(mark_60d.get("actual_redeploy") or row.get("actual_result_60d") or {})
        mark_60d_cash = dict(mark_60d.get("cash_outcome") or row.get("cash_result_60d") or {})
        mark_60d_benchmark = dict(mark_60d.get("benchmark_outcome") or row.get("benchmark_result_60d") or {})
        mark_90d_hold = dict(mark_90d.get("hold_original") or row.get("original_hold_90d") or {})
        mark_90d_actual = dict(mark_90d.get("actual_redeploy") or row.get("actual_result_90d") or {})
        mark_90d_cash = dict(mark_90d.get("cash_outcome") or row.get("cash_result_90d") or {})
        mark_90d_benchmark = dict(mark_90d.get("benchmark_outcome") or row.get("benchmark_result_90d") or {})
        current_hold = dict(current.get("hold_original") or {})
        current_actual = dict(current.get("actual_redeploy") or {})
        current_cash = dict(current.get("cash_outcome") or {})
        current_benchmark = dict(current.get("benchmark_outcome") or {})
        paired_buys = current_actual.get("paired_targets") or row.get("paired_buys") or []

        weekly_hold_pnl = _safe_float(weekly_hold.get("pnl"))
        weekly_actual_pnl = _safe_float(weekly_actual.get("pnl"))
        mark_30d_hold_pnl = _safe_float(mark_30d_hold.get("pnl"))
        mark_30d_actual_pnl = _safe_float(mark_30d_actual.get("pnl"))
        mark_60d_hold_pnl = _safe_float(mark_60d_hold.get("pnl"))
        mark_60d_actual_pnl = _safe_float(mark_60d_actual.get("pnl"))
        mark_90d_hold_pnl = _safe_float(mark_90d_hold.get("pnl"))
        mark_90d_actual_pnl = _safe_float(mark_90d_actual.get("pnl"))
        current_hold_pnl = _safe_float(current_hold.get("pnl"))
        current_actual_pnl = _safe_float(current_actual.get("pnl"))
        event_context = {
            "destination_type": str(row.get("destination_type") or "unknown").strip().lower() or "unknown",
            "decision_type": str(row.get("decision_type") or "unknown").strip().lower() or "unknown",
        }
        weekly_cash_actual = self._cash_actual_block_if_applicable(event_context, weekly_cash)
        mark_30d_cash_actual = self._cash_actual_block_if_applicable(event_context, mark_30d_cash)
        mark_60d_cash_actual = self._cash_actual_block_if_applicable(event_context, mark_60d_cash)
        mark_90d_cash_actual = self._cash_actual_block_if_applicable(event_context, mark_90d_cash)
        current_cash_actual = self._cash_actual_block_if_applicable(event_context, current_cash)
        if weekly_actual_pnl is None and weekly_cash_actual is not None:
            weekly_actual = weekly_cash_actual
            weekly_actual_pnl = _safe_float(weekly_actual.get("pnl"))
        if mark_30d_actual_pnl is None and mark_30d_cash_actual is not None:
            mark_30d_actual = mark_30d_cash_actual
            mark_30d_actual_pnl = _safe_float(mark_30d_actual.get("pnl"))
        if mark_60d_actual_pnl is None and mark_60d_cash_actual is not None:
            mark_60d_actual = mark_60d_cash_actual
            mark_60d_actual_pnl = _safe_float(mark_60d_actual.get("pnl"))
        if mark_90d_actual_pnl is None and mark_90d_cash_actual is not None:
            mark_90d_actual = mark_90d_cash_actual
            mark_90d_actual_pnl = _safe_float(mark_90d_actual.get("pnl"))
        if current_actual_pnl is None and current_cash_actual is not None:
            current_actual = current_cash_actual
            current_actual_pnl = _safe_float(current_actual.get("pnl"))
        opportunity_cost_4w = round(weekly_hold_pnl - weekly_actual_pnl, 2) if weekly_hold_pnl is not None and weekly_actual_pnl is not None else None
        opportunity_cost_30d = round(mark_30d_hold_pnl - mark_30d_actual_pnl, 2) if mark_30d_hold_pnl is not None and mark_30d_actual_pnl is not None else None
        opportunity_cost_60d = round(mark_60d_hold_pnl - mark_60d_actual_pnl, 2) if mark_60d_hold_pnl is not None and mark_60d_actual_pnl is not None else None
        opportunity_cost_90d = round(mark_90d_hold_pnl - mark_90d_actual_pnl, 2) if mark_90d_hold_pnl is not None and mark_90d_actual_pnl is not None else None
        opportunity_cost_12w = round(current_hold_pnl - current_actual_pnl, 2) if current_hold_pnl is not None and current_actual_pnl is not None else None

        def compare_returns(left: Any, right: Any) -> Optional[bool]:
            left_value = _safe_float(left)
            right_value = _safe_float(right)
            if left_value is None or right_value is None:
                return None
            return left_value > right_value

        def pnl_spread(left: Any, right: Any) -> Optional[float]:
            left_value = _safe_float(left)
            right_value = _safe_float(right)
            if left_value is None or right_value is None:
                return None
            return round(left_value - right_value, 2)

        normalized_event = {
            "event_id": str(row.get("event_id") or "").strip(),
            "_source": str(row.get("_decision_review_source") or row.get("_source") or "").strip(),
            "week_id": str(row.get("week_id") or "").strip(),
            "stock_id": str(row.get("stock_id") or "").strip(),
            "sell_date": str(row.get("sell_date") or row.get("date") or "").strip(),
            "decision_type": str(row.get("decision_type") or "unknown").strip().lower() or "unknown",
            "destination_type": str(row.get("destination_type") or "unknown").strip().lower() or "unknown",
            "sold_capital": round(_safe_float(row.get("sold_capital")) or 0.0, 2),
            "decision_note": str(row.get("decision_note") or "").strip(),
            "opportunity_cost_4w": opportunity_cost_4w,
            "opportunity_cost_30d": opportunity_cost_30d,
            "opportunity_cost_60d": opportunity_cost_60d,
            "opportunity_cost_90d": opportunity_cost_90d,
            "opportunity_cost_12w": opportunity_cost_12w,
            "sell_decision_wrong_4w": compare_returns(weekly_hold.get("return"), weekly_cash.get("return")),
            "sell_decision_wrong_30d": compare_returns(mark_30d_hold.get("return"), mark_30d_cash.get("return")),
            "sell_decision_wrong_12w": compare_returns(current_hold.get("return"), current_cash.get("return")),
            "redeploy_wrong_4w": compare_returns(weekly_hold.get("return"), weekly_actual.get("return")),
            "redeploy_wrong_30d": compare_returns(mark_30d_hold.get("return"), mark_30d_actual.get("return")),
            "redeploy_wrong_60d": compare_returns(mark_60d_hold.get("return"), mark_60d_actual.get("return")),
            "redeploy_wrong_90d": compare_returns(mark_90d_hold.get("return"), mark_90d_actual.get("return")),
            "redeploy_wrong_12w": compare_returns(current_hold.get("return"), current_actual.get("return")),
            "sell_decision_impact_4w": pnl_spread(weekly_cash.get("pnl"), weekly_hold.get("pnl")),
            "sell_decision_impact_30d": pnl_spread(mark_30d_cash.get("pnl"), mark_30d_hold.get("pnl")),
            "sell_decision_impact_12w": pnl_spread(current_cash.get("pnl"), current_hold.get("pnl")),
            "redeploy_impact_4w": pnl_spread(weekly_actual.get("pnl"), weekly_cash.get("pnl")),
            "redeploy_impact_30d": pnl_spread(mark_30d_actual.get("pnl"), mark_30d_cash.get("pnl")),
            "redeploy_impact_60d": pnl_spread(mark_60d_actual.get("pnl"), mark_60d_cash.get("pnl")),
            "redeploy_impact_90d": pnl_spread(mark_90d_actual.get("pnl"), mark_90d_cash.get("pnl")),
            "redeploy_impact_12w": pnl_spread(current_actual.get("pnl"), current_cash.get("pnl")),
            "data_quality": {
                "hold_4w_evaluable": weekly_hold_pnl is not None,
                "actual_4w_evaluable": weekly_actual_pnl is not None,
                "hold_30d_evaluable": mark_30d_hold_pnl is not None,
                "actual_30d_evaluable": mark_30d_actual_pnl is not None,
                "hold_60d_evaluable": mark_60d_hold_pnl is not None,
                "actual_60d_evaluable": mark_60d_actual_pnl is not None,
                "hold_90d_evaluable": mark_90d_hold_pnl is not None,
                "actual_90d_evaluable": mark_90d_actual_pnl is not None,
                "hold_12w_evaluable": current_hold_pnl is not None,
                "actual_12w_evaluable": current_actual_pnl is not None,
            },
            "mark_30d_end_date": str(mark_30d.get("window_end") or "").strip(),
            "mark_60d_end_date": str(mark_60d.get("window_end") or "").strip(),
            "mark_90d_end_date": str(mark_90d.get("window_end") or "").strip(),
            "original_hold_4w": weekly_hold,
            "actual_result_4w": weekly_actual,
            "cash_result_4w": weekly_cash,
            "benchmark_result_4w": weekly_benchmark,
            "original_hold_30d": mark_30d_hold,
            "actual_result_30d": mark_30d_actual,
            "cash_result_30d": mark_30d_cash,
            "benchmark_result_30d": mark_30d_benchmark,
            "original_hold_60d": mark_60d_hold,
            "actual_result_60d": mark_60d_actual,
            "cash_result_60d": mark_60d_cash,
            "benchmark_result_60d": mark_60d_benchmark,
            "original_hold_90d": mark_90d_hold,
            "actual_result_90d": mark_90d_actual,
            "cash_result_90d": mark_90d_cash,
            "benchmark_result_90d": mark_90d_benchmark,
            "original_hold_12w": current_hold,
            "actual_result_12w": current_actual,
            "cash_result_12w": current_cash,
            "benchmark_result_12w": current_benchmark,
            "paired_buys": paired_buys,
        }
        drilldown = self._drilldown_for_event(normalized_event)
        normalized_event["drilldown"] = drilldown
        normalized_event["deterministic_verdict"] = self._deterministic_verdict_for_stage_attribution(
            drilldown.get("stage_attribution") or {},
            (drilldown.get("fund_flow") or {}),
        )
        return normalized_event

    def _filter_decision_review_events(
        self,
        events: List[Dict[str, Any]],
        stock_id: Optional[str] = None,
        decision_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(events)
        if stock_id:
            target_stock = str(stock_id or "").strip().upper()
            rows = [event for event in rows if str(event.get("stock_id") or "").strip().upper() == target_stock]
        if decision_type:
            target_type = str(decision_type or "").strip().lower()
            rows = [event for event in rows if str(event.get("decision_type") or "").strip().lower() == target_type]
        return rows

    def _dedupe_decision_review_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        passthrough: List[Dict[str, Any]] = []
        for event in events or []:
            key = (
                str(event.get("week_id") or "").strip(),
                str(event.get("stock_id") or "").strip(),
                str(event.get("sell_date") or event.get("date") or "").strip(),
            )
            if not all(key):
                passthrough.append(event)
                continue
            grouped.setdefault(key, []).append(event)

        def score(event: Dict[str, Any]) -> Tuple[int, int, int, int]:
            source_bonus = 2 if str(event.get("_source") or "") == "persisted" else 0
            evaluable = 1 if self._decision_review_contribution(event) is not None else 0
            nonzero = 1 if abs(_safe_float(event.get("opportunity_cost_12w")) or 0.0) > 0.000001 else 0
            priced = sum(
                1
                for field in ("original_hold_12w", "actual_result_12w", "original_hold_30d", "actual_result_30d")
                if _safe_float((event.get(field) or {}).get("pnl")) is not None
            )
            return (source_bonus, evaluable, nonzero, priced)

        rows = list(passthrough)
        for items in grouped.values():
            rows.append(max(items, key=score))
        rows.sort(key=lambda event: (_week_sort_key(str(event.get("week_id") or "")), str(event.get("sell_date") or ""), str(event.get("stock_id") or "")))
        return rows

    def _summarize_decision_review(self, events: List[Dict[str, Any]], suffix: str = "12w") -> Dict[str, Any]:
        value_added = 0.0
        opportunity_cost = 0.0
        evaluated_count = 0
        effective_count = 0
        missing_hold_count = 0
        missing_redeploy_count = 0
        for event in events:
            hold_pnl = _safe_float((event.get(f"original_hold_{suffix}") or {}).get("pnl"))
            actual_pnl = _safe_float((self._actual_result_for_contribution(event, suffix) or {}).get("pnl"))
            if hold_pnl is None or actual_pnl is None:
                if hold_pnl is None:
                    missing_hold_count += 1
                if actual_pnl is None:
                    missing_redeploy_count += 1
                continue
            contribution = round(actual_pnl - hold_pnl, 2)
            if contribution >= 0:
                value_added += contribution
            else:
                opportunity_cost += abs(contribution)
            evaluated_count += 1
            if contribution >= 0:
                effective_count += 1
        return {
            "event_count": len(events),
            "total_opportunity_cost": round(opportunity_cost, 2),
            "total_value_added": round(value_added, 2),
            "net_result": round(value_added - opportunity_cost, 2),
            "net_contribution": round(value_added - opportunity_cost, 2),
            "evaluated_count": evaluated_count,
            "effective_count": effective_count,
            "effectiveness_rate": round(effective_count / evaluated_count, 4) if evaluated_count else None,
            "missing_hold_price_count": missing_hold_count,
            "missing_redeploy_price_count": missing_redeploy_count,
            "sold_too_early_4w": sum(1 for event in events if event.get("sell_decision_wrong_4w")),
            "sold_too_early_12w": sum(1 for event in events if event.get("sell_decision_wrong_12w")),
            "redeploy_wrong_4w": sum(1 for event in events if event.get("redeploy_wrong_4w")),
            "redeploy_wrong_12w": sum(1 for event in events if event.get("redeploy_wrong_12w")),
            "selected_suffix": suffix,
        }

    def _decision_review_contribution(self, event: Dict[str, Any], suffix: str = "12w") -> Optional[float]:
        hold_pnl = _safe_float((event.get(f"original_hold_{suffix}") or {}).get("pnl"))
        actual_pnl = _safe_float((self._actual_result_for_contribution(event, suffix) or {}).get("pnl"))
        if hold_pnl is None or actual_pnl is None:
            return None
        return round(actual_pnl - hold_pnl, 2)

    def _counterfactual_waterfall_for_event(self, event: Dict[str, Any], suffix: str = "12w") -> Dict[str, Any]:
        hold_pnl = _safe_float((event.get(f"original_hold_{suffix}") or {}).get("pnl"))
        actual_pnl = _safe_float((self._actual_result_for_contribution(event, suffix) or {}).get("pnl"))
        cash_pnl = _safe_float((event.get(f"cash_result_{suffix}") or {}).get("pnl"))
        benchmark_pnl = _safe_float((event.get(f"benchmark_result_{suffix}") or {}).get("pnl"))

        def spread(left: Optional[float], right: Optional[float]) -> Optional[float]:
            if left is None or right is None:
                return None
            return round(left - right, 2)

        sell_timing = spread(cash_pnl, hold_pnl)
        redeploy_selection = spread(actual_pnl, cash_pnl)
        cash_drag = spread(cash_pnl, benchmark_pnl)
        benchmark_drift = spread(benchmark_pnl, hold_pnl)
        actual_minus_hold = spread(actual_pnl, hold_pnl)
        bridge_total = None
        if sell_timing is not None and redeploy_selection is not None:
            bridge_total = round(sell_timing + redeploy_selection, 2)
        bridge_check = None
        if actual_minus_hold is not None and bridge_total is not None:
            bridge_check = round(actual_minus_hold - bridge_total, 2)

        steps = [
            {
                "key": "sell_timing",
                "label": "Sell timing",
                "value_hkd": sell_timing,
                "definition": "sell_to_cash.pnl - hold_original.pnl",
            },
            {
                "key": "cash_drag",
                "label": "Cash vs benchmark",
                "value_hkd": cash_drag,
                "definition": "sell_to_cash.pnl - benchmark.pnl",
            },
            {
                "key": "redeploy_selection",
                "label": "Redeploy selection",
                "value_hkd": redeploy_selection,
                "definition": "actual_redeploy.pnl - sell_to_cash.pnl",
            },
            {
                "key": "benchmark_drift",
                "label": "Benchmark drift",
                "value_hkd": benchmark_drift,
                "definition": "benchmark.pnl - hold_original.pnl",
            },
        ]
        return {
            "horizon": self._decision_review_horizon_key(suffix),
            "suffix": suffix,
            "actual_minus_hold_hkd": actual_minus_hold,
            "bridge_total_hkd": bridge_total,
            "bridge_check_hkd": bridge_check,
            "bridge_definition": "actual_minus_hold = sell_timing + redeploy_selection",
            "steps": steps,
            "data_quality": {
                "hold_evaluable": hold_pnl is not None,
                "actual_evaluable": actual_pnl is not None,
                "cash_evaluable": cash_pnl is not None,
                "benchmark_evaluable": benchmark_pnl is not None,
            },
        }

    def _pattern_tags_for_decision_event(self, event: Dict[str, Any], suffix: str = "12w") -> List[str]:
        contribution = self._decision_review_contribution(event, suffix)
        actual_pnl = _safe_float((self._actual_result_for_contribution(event, suffix) or {}).get("pnl"))
        cash_pnl = _safe_float((event.get(f"cash_result_{suffix}") or {}).get("pnl"))
        benchmark_pnl = _safe_float((event.get(f"benchmark_result_{suffix}") or {}).get("pnl"))
        tags: List[str] = []
        if contribution is None:
            tags.append("incomplete_data")
            return tags
        tags.append("effective_sell" if contribution >= 0 else "sold_too_early")
        if actual_pnl is not None and cash_pnl is not None:
            if actual_pnl > cash_pnl:
                tags.append("redeploy_helped")
            elif actual_pnl < cash_pnl:
                tags.append("redeploy_hurt")
            else:
                tags.append("redeploy_neutral")
        if cash_pnl is not None and benchmark_pnl is not None:
            tags.append("cash_lagged_benchmark" if cash_pnl < benchmark_pnl else "cash_beat_benchmark")
        return tags

    def _with_selected_decision_review_derivatives(self, event: Dict[str, Any], suffix: str = "12w") -> Dict[str, Any]:
        row = dict(event or {})
        row["selected_suffix"] = suffix
        row["counterfactual_waterfall"] = self._counterfactual_waterfall_for_event(row, suffix)
        row["pattern_tags"] = self._pattern_tags_for_decision_event(row, suffix)
        return row

    def _rank_top_decision_review_mistakes(self, events: List[Dict[str, Any]], limit: int = 10, suffix: str = "12w") -> List[Dict[str, Any]]:
        opportunity_key = f"opportunity_cost_{suffix}"
        evaluable = [event for event in events if self._decision_review_contribution(event, suffix) is not None]
        rows = sorted(
            evaluable,
            key=lambda event: (
                -(_safe_float(event.get(opportunity_key)) or 0.0),
                -abs(_safe_float(event.get("opportunity_cost_4w")) or 0.0),
                str(event.get("sell_date") or ""),
            ),
        )
        return rows[: max(int(limit or 10), 0)]

    def _rank_top_decision_review_effective(self, events: List[Dict[str, Any]], limit: int = 10, suffix: str = "12w") -> List[Dict[str, Any]]:
        opportunity_key = f"opportunity_cost_{suffix}"
        evaluable = [event for event in events if self._decision_review_contribution(event, suffix) is not None]
        rows = sorted(
            evaluable,
            key=lambda event: (
                _safe_float(event.get(opportunity_key)) or 0.0,
                _safe_float(event.get("opportunity_cost_4w")) or 0.0,
                str(event.get("sell_date") or ""),
            ),
        )
        return rows[: max(int(limit or 10), 0)]

    def _aggregate_decision_review_by_week(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in events:
            week_id = str(event.get("week_id") or "").strip()
            if not week_id:
                continue
            bucket = grouped.setdefault(week_id, {"week_id": week_id, "event_count": 0, "evaluated_count": 0, "total_opportunity_cost": 0.0, "total_value_added": 0.0})
            bucket["event_count"] += 1
            contribution = self._decision_review_contribution(event, suffix)
            if contribution is None:
                continue
            bucket["evaluated_count"] += 1
            if contribution >= 0:
                bucket["total_value_added"] += contribution
            else:
                bucket["total_opportunity_cost"] += abs(contribution)
        rows = list(grouped.values())
        rows.sort(key=lambda row: _week_sort_key(str(row.get("week_id") or "")), reverse=True)
        for row in rows:
            row["total_opportunity_cost"] = round(row["total_opportunity_cost"], 2)
            row["total_value_added"] = round(row["total_value_added"], 2)
            row["net_result"] = round(row["total_value_added"] - row["total_opportunity_cost"], 2)
        return rows

    def _aggregate_decision_review_by_stock(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in events:
            stock_id = str(event.get("stock_id") or "").strip()
            if not stock_id:
                continue
            bucket = grouped.setdefault(
                stock_id,
                {
                    "stock_id": stock_id,
                    "event_count": 0,
                    "evaluated_count": 0,
                    "total_opportunity_cost": 0.0,
                    "total_value_added": 0.0,
                    "net_contribution": 0.0,
                    "sold_capital": 0.0,
                },
            )
            bucket["event_count"] += 1
            bucket["sold_capital"] += _safe_float(event.get("sold_capital")) or 0.0
            contribution = self._decision_review_contribution(event, suffix)
            if contribution is not None:
                bucket["evaluated_count"] += 1
                bucket["net_contribution"] += contribution
                if contribution >= 0:
                    bucket["total_value_added"] += contribution
                else:
                    bucket["total_opportunity_cost"] += abs(contribution)
        rows = list(grouped.values())
        rows.sort(key=lambda row: (-abs(row["net_contribution"]), -row["event_count"], row["stock_id"]))
        for row in rows:
            row["total_opportunity_cost"] = round(row["total_opportunity_cost"], 2)
            row["total_value_added"] = round(row["total_value_added"], 2)
            row["net_contribution"] = round(row["net_contribution"], 2)
            row["sold_capital"] = round(row["sold_capital"], 2)
        return rows

    def _aggregate_decision_review_by_decision_type(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in events:
            decision_type = str(event.get("decision_type") or "unknown").strip().lower() or "unknown"
            bucket = grouped.setdefault(
                decision_type,
                {"decision_type": decision_type, "event_count": 0, "evaluated_count": 0, "total_opportunity_cost": 0.0, "total_value_added": 0.0, "net_contribution": 0.0},
            )
            bucket["event_count"] += 1
            contribution = self._decision_review_contribution(event, suffix)
            if contribution is not None:
                bucket["evaluated_count"] += 1
                bucket["net_contribution"] += contribution
                if contribution >= 0:
                    bucket["total_value_added"] += contribution
                else:
                    bucket["total_opportunity_cost"] += abs(contribution)
        rows = list(grouped.values())
        rows.sort(key=lambda row: (-abs(row["net_contribution"]), -row["event_count"], row["decision_type"]))
        for row in rows:
            row["total_opportunity_cost"] = round(row["total_opportunity_cost"], 2)
            row["total_value_added"] = round(row["total_value_added"], 2)
            row["net_contribution"] = round(row["net_contribution"], 2)
        return rows

    def _aggregate_decision_review_by_destination(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in events:
            destination_type = str(event.get("destination_type") or "unknown").strip().lower() or "unknown"
            bucket = grouped.setdefault(
                destination_type,
                {"destination_type": destination_type, "event_count": 0, "evaluated_count": 0, "total_opportunity_cost": 0.0, "total_value_added": 0.0, "net_contribution": 0.0},
            )
            bucket["event_count"] += 1
            contribution = self._decision_review_contribution(event, suffix)
            if contribution is not None:
                bucket["evaluated_count"] += 1
                bucket["net_contribution"] += contribution
                if contribution >= 0:
                    bucket["total_value_added"] += contribution
                else:
                    bucket["total_opportunity_cost"] += abs(contribution)
        rows = list(grouped.values())
        rows.sort(key=lambda row: (-abs(row["net_contribution"]), -row["event_count"], row["destination_type"]))
        for row in rows:
            row["total_opportunity_cost"] = round(row["total_opportunity_cost"], 2)
            row["total_value_added"] = round(row["total_value_added"], 2)
            row["net_contribution"] = round(row["net_contribution"], 2)
        return rows

    def _aggregate_trim_follow_through(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        return [
            {
                "stock_id": row["stock_id"],
                "event_count": row["event_count"],
                "total_opportunity_cost": row["total_opportunity_cost"],
                "total_value_added": row.get("total_value_added", 0.0),
                "net_contribution": row.get("net_contribution", 0.0),
            }
            for row in self._aggregate_decision_review_by_stock(events, suffix)
        ]

    def _decision_review_position_size_bucket(self, event: Dict[str, Any], events: List[Dict[str, Any]]) -> str:
        capital = _safe_float((event or {}).get("sold_capital")) or 0.0
        capitals = sorted(_safe_float(row.get("sold_capital")) or 0.0 for row in events or [])
        if not capitals:
            return "unknown"
        total = sum(capitals)
        if total > 0 and capital / total >= 0.25:
            return "large"
        midpoint = capitals[len(capitals) // 2]
        return "medium" if capital >= midpoint else "small"

    def _pattern_mining_group_rows(
        self,
        events: List[Dict[str, Any]],
        *,
        suffix: str,
        group_name: str,
        key_field: str,
        label_field: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in events:
            key = str(event.get(key_field) or "unknown").strip().lower() or "unknown"
            bucket = grouped.setdefault(
                key,
                {
                    "group": group_name,
                    "key": key,
                    label_field or key_field: key,
                    "event_count": 0,
                    "evaluated_count": 0,
                    "effective_count": 0,
                    "mistake_count": 0,
                    "sold_capital": 0.0,
                    "net_contribution": 0.0,
                    "total_value_added": 0.0,
                    "total_opportunity_cost": 0.0,
                },
            )
            bucket["event_count"] += 1
            bucket["sold_capital"] += _safe_float(event.get("sold_capital")) or 0.0
            contribution = self._decision_review_contribution(event, suffix)
            if contribution is None:
                continue
            bucket["evaluated_count"] += 1
            bucket["net_contribution"] += contribution
            if contribution >= 0:
                bucket["effective_count"] += 1
                bucket["total_value_added"] += contribution
            else:
                bucket["mistake_count"] += 1
                bucket["total_opportunity_cost"] += abs(contribution)
        rows = list(grouped.values())
        for row in rows:
            evaluated = row["evaluated_count"]
            row["effectiveness_rate"] = round(row["effective_count"] / evaluated, 4) if evaluated else None
            row["sold_capital"] = round(row["sold_capital"], 2)
            row["net_contribution"] = round(row["net_contribution"], 2)
            row["total_value_added"] = round(row["total_value_added"], 2)
            row["total_opportunity_cost"] = round(row["total_opportunity_cost"], 2)
        rows.sort(key=lambda row: (-row["total_opportunity_cost"], -abs(row["net_contribution"]), -row["event_count"], row["key"]))
        return rows

    def _aggregate_decision_review_by_position_size(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        bucketed: List[Dict[str, Any]] = []
        for event in events:
            row = dict(event or {})
            row["position_size_bucket"] = self._decision_review_position_size_bucket(row, events)
            bucketed.append(row)
        return self._pattern_mining_group_rows(
            bucketed,
            suffix=suffix,
            group_name="position_size",
            key_field="position_size_bucket",
            label_field="bucket",
        )

    def _aggregate_decision_review_by_tag(self, events: List[Dict[str, Any]], suffix: str = "12w") -> List[Dict[str, Any]]:
        expanded: List[Dict[str, Any]] = []
        for event in events:
            tags = event.get("pattern_tags") or self._pattern_tags_for_decision_event(event, suffix)
            for tag in tags:
                row = dict(event or {})
                row["pattern_tag"] = tag
                expanded.append(row)
        rows = self._pattern_mining_group_rows(
            expanded,
            suffix=suffix,
            group_name="tag",
            key_field="pattern_tag",
            label_field="tag",
        )
        tag_priority = {
            "sold_too_early": 4,
            "effective_sell": 3,
            "redeploy_hurt": 2,
            "redeploy_helped": 2,
            "cash_lagged_benchmark": 1,
            "cash_beat_benchmark": 1,
        }
        rows.sort(
            key=lambda row: (
                -(_safe_float(row.get("total_opportunity_cost")) or 0.0),
                -tag_priority.get(str(row.get("tag") or ""), 0),
                -abs(_safe_float(row.get("net_contribution")) or 0.0),
                str(row.get("tag") or ""),
            )
        )
        return rows

    def _build_decision_review_pattern_mining(self, events: List[Dict[str, Any]], suffix: str = "12w") -> Dict[str, Any]:
        by_decision_type = self._aggregate_decision_review_by_decision_type(events, suffix=suffix)
        for row in by_decision_type:
            row["group"] = "decision_type"
            row["key"] = row.get("decision_type") or "unknown"
        by_destination = self._aggregate_decision_review_by_destination(events, suffix=suffix)
        for row in by_destination:
            row["group"] = "destination"
            row["key"] = row.get("destination_type") or "unknown"
        by_position_size = self._aggregate_decision_review_by_position_size(events, suffix=suffix)
        by_tag = self._aggregate_decision_review_by_tag(events, suffix=suffix)
        cluster_candidates = by_decision_type + by_destination + by_position_size + by_tag
        largest_loss_cluster = None
        if cluster_candidates:
            loss_rows = [row for row in cluster_candidates if (_safe_float(row.get("total_opportunity_cost")) or 0.0) > 0]
            if loss_rows:
                group_priority = {"decision_type": 3, "destination": 2, "position_size": 1, "tag": 0}
                largest_loss_cluster = max(
                    loss_rows,
                    key=lambda row: (
                        _safe_float(row.get("total_opportunity_cost")) or 0.0,
                        group_priority.get(str(row.get("group") or ""), 0),
                        abs(_safe_float(row.get("net_contribution")) or 0.0),
                        row.get("event_count") or 0,
                    ),
                )
        evaluated_count = sum(1 for event in events if self._decision_review_contribution(event, suffix) is not None)
        effective_count = sum(1 for event in events if (self._decision_review_contribution(event, suffix) or 0.0) >= 0 and self._decision_review_contribution(event, suffix) is not None)
        mistake_count = evaluated_count - effective_count
        return {
            "horizon": self._decision_review_horizon_key(suffix),
            "suffix": suffix,
            "summary": {
                "event_count": len(events),
                "evaluated_count": evaluated_count,
                "effective_count": effective_count,
                "mistake_count": mistake_count,
                "effectiveness_rate": round(effective_count / evaluated_count, 4) if evaluated_count else None,
                "largest_loss_cluster": largest_loss_cluster,
            },
            "by_decision_type": by_decision_type,
            "by_destination": by_destination,
            "by_position_size": by_position_size,
            "by_tag": by_tag,
        }

    def _position_rows_from_review(self, review: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        stocks = review.get("stocks") or {}
        if not isinstance(stocks, dict):
            return rows
        for stock_id, payload in stocks.items():
            if not isinstance(payload, dict):
                continue
            shares = _safe_float(payload.get("shares") or payload.get("shares_held") or payload.get("quantity")) or 0.0
            avg_cost = _safe_float(payload.get("avg_cost") or payload.get("cost") or payload.get("current_price")) or 0.0
            perf = payload.get("performance_data") or {}
            if not avg_cost:
                avg_cost = _safe_float(perf.get("start_price") or perf.get("end_price")) or 0.0
            if shares <= 0:
                continue
            ticker = str(payload.get("ticker") or stock_id).strip()
            rows.append(
                {
                    "stock_id": ticker,
                    "ticker": ticker,
                    "stock_name": payload.get("stock_name") or payload.get("name") or stock_id,
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "currency": payload.get("currency") or self._amount_currency(ticker, review),
                }
            )
        return rows

    def _build_position_change_rows(
        self,
        pre_positions: List[Dict[str, Any]],
        post_positions: List[Dict[str, Any]],
        ops: Optional[List[Dict[str, Any]]] = None,
        week_id: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        def canonical(value: Any) -> str:
            return str(value or "").strip().upper()

        def position_key(row: Dict[str, Any]) -> str:
            return canonical(row.get("stock_name") or row.get("ticker") or row.get("stock_id"))

        def indexed(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
            result: Dict[str, Dict[str, Any]] = {}
            for row in rows or []:
                key = position_key(row)
                if key:
                    if key in result:
                        existing = result[key]
                        existing["shares"] = (_safe_float(existing.get("shares")) or 0.0) + (_safe_float(row.get("shares")) or 0.0)
                    else:
                        result[key] = dict(row)
            return result

        def aliases_for(row: Dict[str, Any], key: str) -> set[str]:
            return {
                canonical(key),
                canonical(row.get("stock_id")),
                canonical(row.get("ticker")),
                canonical(row.get("stock_name")),
            } - {""}

        catalog_aliases: Dict[str, set[str]] = {}
        for item in self.storage.list_stocks():
            item_aliases = {
                canonical(item.get("stock_id")),
                canonical(item.get("ticker")),
                canonical(item.get("stock_name")),
                canonical(item.get("search_name")),
            } - {""}
            for alias in item_aliases:
                catalog_aliases.setdefault(alias, set()).update(item_aliases)

        def expand_aliases(aliases: set[str]) -> set[str]:
            expanded = set(aliases)
            for alias in list(aliases):
                expanded.update(catalog_aliases.get(alias, set()))
            return expanded

        sell_ops: set[str] = set()
        buy_ops: set[str] = set()
        for op in ops or []:
            op_id = canonical((op or {}).get("stock_id"))
            if not op_id:
                continue
            if self._is_sell_like_op((op or {}).get("op_type")):
                sell_ops.add(op_id)
            elif self._is_buy_like_op((op or {}).get("op_type")):
                buy_ops.add(op_id)

        def op_allows(row: Dict[str, Any], key: str, bucket: str) -> bool:
            if ops is None:
                return True
            aliases = expand_aliases(aliases_for(row, key))
            if bucket in {"sold", "reduced"}:
                return bool(aliases & sell_ops)
            if bucket in {"added", "increased"}:
                return bool(aliases & buy_ops)
            return True

        before = indexed(pre_positions)
        after = indexed(post_positions)
        changes: Dict[str, List[Dict[str, Any]]] = {"sold": [], "reduced": [], "added": [], "increased": []}
        for key in sorted(set(before) | set(after)):
            pre = before.get(key) or {}
            post = after.get(key) or {}
            before_shares = _safe_float(pre.get("shares") or pre.get("shares_held") or pre.get("quantity")) or 0.0
            after_shares = _safe_float(post.get("shares") or post.get("shares_held") or post.get("quantity")) or 0.0
            delta = round(after_shares - before_shares, 6)
            if abs(delta) < 0.000001:
                continue
            source = post if after_shares > 0 else pre
            row = {
                "stock_id": str(source.get("stock_id") or source.get("ticker") or key).strip(),
                "ticker": str(source.get("ticker") or source.get("stock_id") or key).strip(),
                "stock_name": source.get("stock_name") or source.get("name") or key,
                "before_shares": round(before_shares, 6),
                "after_shares": round(after_shares, 6),
                "delta_shares": delta,
                "before_avg_cost": round(_safe_float(pre.get("avg_cost")) or 0.0, 6),
                "after_avg_cost": round(_safe_float(post.get("avg_cost")) or 0.0, 6),
            }
            if week_id:
                row["week_id"] = week_id
            if before_shares > 0 and after_shares <= 0 and op_allows(row, key, "sold"):
                changes["sold"].append(row)
            elif before_shares > 0 and after_shares < before_shares and op_allows(row, key, "reduced"):
                changes["reduced"].append(row)
            elif before_shares <= 0 and after_shares > 0 and op_allows(row, key, "added"):
                changes["added"].append(row)
            elif after_shares > before_shares and op_allows(row, key, "increased"):
                changes["increased"].append(row)
        for bucket in changes.values():
            bucket.sort(key=lambda row: (-abs(row["delta_shares"]), row["stock_id"]))
        return changes

    def _build_snapshot_block(
        self,
        positions: List[Dict[str, Any]],
        review_payload: Dict[str, Any],
        cash_hkd: float,
        start_date: str,
        current_date: str,
        price_lookup: Dict[Tuple[str, str], float],
        aliases: Dict[str, str],
    ) -> Dict[str, Any]:
        start_total = _safe_float(cash_hkd) or 0.0
        current_total = _safe_float(cash_hkd) or 0.0
        missing_price_count = 0
        top_positions: List[Dict[str, Any]] = []
        for position in positions or []:
            stock_id = str(position.get("stock_id") or position.get("ticker") or "").strip()
            lookup_id = str((aliases or {}).get(stock_id) or stock_id).strip()
            shares = _safe_float(position.get("shares") or position.get("shares_held") or position.get("quantity")) or 0.0
            if not stock_id or shares <= 0:
                continue
            start_price = _safe_float(price_lookup.get((lookup_id, start_date)) or price_lookup.get((stock_id, start_date)) or position.get("avg_cost"))
            current_price = _safe_float(price_lookup.get((lookup_id, current_date)) or price_lookup.get((stock_id, current_date)))
            fx = 1.0
            currency = str(position.get("currency") or "").upper()
            if currency == "USD":
                fx = _safe_float(review_payload.get("usd_to_hkd")) or 7.8
            elif currency == "CNY":
                fx = _safe_float(review_payload.get("cny_to_hkd")) or 1.07
            elif currency == "EUR":
                fx = _safe_float(review_payload.get("eur_to_hkd")) or 8.4
            if start_price:
                start_total += shares * start_price * fx
            if current_price:
                current_value = shares * current_price * fx
                current_total += current_value
                top_positions.append({"stock_id": stock_id, "current_value_hkd": round(current_value, 2)})
            else:
                missing_price_count += 1
        coverage_complete = missing_price_count == 0
        if not coverage_complete:
            current_total_value = None
            return_pct = None
        else:
            current_total_value = round(current_total, 2)
            return_pct = round((current_total - start_total) / start_total, 6) if start_total else None
        top_positions.sort(key=lambda row: row.get("current_value_hkd") or 0.0, reverse=True)
        return {
            "start_total_hkd": round(start_total, 2),
            "current_total_hkd": current_total_value,
            "return_pct": return_pct,
            "cash_hkd": round(_safe_float(cash_hkd) or 0.0, 2),
            "coverage_complete": coverage_complete,
            "missing_price_count": missing_price_count,
            "top_positions": top_positions[:10],
        }

    def _portfolio_path_pnl_from_review(
        self,
        review: Dict[str, Any],
        capital_hkd: float,
        start_date: str,
        end_date: str,
        price_lookup: Dict[Tuple[str, str], float],
    ) -> Optional[float]:
        capital = _safe_float(capital_hkd) or 0.0
        if capital <= 0:
            return None
        positions = self._position_rows_from_review(review)
        if not positions:
            return None
        weighted_return = 0.0
        weighted_capital = 0.0
        for position in positions:
            stock_id = str(position.get("stock_id") or position.get("ticker") or "").strip()
            shares = _safe_float(position.get("shares")) or 0.0
            if not stock_id or shares <= 0:
                continue
            start_price = self._lookup_price_on_or_before(price_lookup, stock_id, start_date) or _safe_float(position.get("avg_cost"))
            end_price = self._lookup_price_on_or_before(price_lookup, stock_id, end_date)
            if not start_price or not end_price:
                continue
            fx = self._fx_rate_for_currency(str(position.get("currency") or "USD"), review)
            position_value = shares * start_price * fx
            if position_value <= 0:
                continue
            weighted_capital += position_value
            weighted_return += position_value * ((end_price / start_price) - 1)
        if weighted_capital <= 0:
            return None
        return round(capital * (weighted_return / weighted_capital), 2)

    def _ops_amounts(self, review: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], float, float]:
        ops = [self._normalize_trim_reallocation_op(op) for op in (review.get("rebalancing_ops") or []) if isinstance(op, dict)]
        gross_sell = 0.0
        gross_buy = 0.0
        for op in ops:
            amount = (_safe_float(op.get("quantity")) or 0.0) * (_safe_float(op.get("price")) or 0.0)
            amount_hkd = self._amount_to_hkd(str(op.get("stock_id") or ""), amount, review)
            if self._is_sell_like_op(op.get("op_type")):
                gross_sell += amount_hkd
            elif self._is_buy_like_op(op.get("op_type")):
                gross_buy += amount_hkd
        return ops, round(gross_sell, 2), round(gross_buy, 2)

    def _phase_for_ops(self, ops: List[Dict[str, Any]]) -> str:
        has_sell = any(self._is_sell_like_op(op.get("op_type")) for op in ops)
        has_buy = any(self._is_buy_like_op(op.get("op_type")) for op in ops)
        if has_sell and not has_buy:
            return "derisk"
        if has_buy and not has_sell:
            return "rerisk"
        if has_sell and has_buy:
            return "rotate"
        return "adjust"

    def _default_counterfactual_pack(
        self,
        actual_value: float,
        hold_value: Optional[float] = None,
        cash_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        actual = _safe_float(actual_value) or 0.0
        hold = actual if hold_value is None else (_safe_float(hold_value) or 0.0)
        cash = actual if cash_value is None else (_safe_float(cash_value) or 0.0)
        rebuy = actual
        return {
            "actual_redeploy": {"current_total_hkd": round(actual, 2), "return_pct": 0.0, "vs_actual_hkd": 0.0},
            "hold_original": {"current_total_hkd": round(hold, 2), "return_pct": 0.0, "vs_actual_hkd": round(hold - actual, 2)},
            "sell_to_cash": {"current_total_hkd": round(cash, 2), "return_pct": 0.0, "vs_actual_hkd": round(cash - actual, 2)},
            "rebuy_original_leaders": {"current_total_hkd": round(rebuy, 2), "return_pct": 0.0, "vs_actual_hkd": round(rebuy - actual, 2)},
            "comparison_vs_hold_original": {"diff_hkd": round(actual - hold, 2)},
            "comparison_vs_sell_to_cash": {"diff_hkd": round(actual - cash, 2)},
            "comparison_vs_rebuy_original_leaders": {"diff_hkd": round(actual - rebuy, 2)},
        }

    def _decision_summary_for_counterfactuals(self, counterfactuals: Dict[str, Any], phase_sequence: List[str]) -> Dict[str, Any]:
        actual = _safe_float((counterfactuals.get("actual_redeploy") or {}).get("current_total_hkd")) or 0.0
        alternatives = {
            key: _safe_float((counterfactuals.get(key) or {}).get("current_total_hkd")) or 0.0
            for key in ("hold_original", "sell_to_cash", "rebuy_original_leaders")
        }
        best_key = max(alternatives, key=lambda key: alternatives[key]) if alternatives else "hold_original"
        best_diff = round(alternatives.get(best_key, 0.0) - actual, 2)
        verdict = "actual_best" if best_diff <= 0 else "hold_would_win"
        return {
            "best_alternative_key": best_key,
            "best_alternative_diff_hkd": best_diff,
            "weakest_stage_key": "sell_judgment" if best_key == "hold_original" else "re_risk_quality",
            "verdict_tag": verdict,
            "headline": "Actual path vs simple alternatives",
            "phase_flow": " -> ".join(phase_sequence),
            "explanation_lines": [f"Actual compared with {best_key}: {best_diff:.2f} HKD."],
        }

    def _timeline_for_counterfactuals(self, week_id: str, counterfactuals: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"label": week_id, "week_id": week_id, "universes": counterfactuals}]

    def _build_funding_ancestors(self, target_week_id: str, cross_week_pairings: Dict[Tuple[str, int], List[Dict[str, Any]]]) -> Dict[str, Any]:
        return self._build_funding_ancestor_index(cross_week_pairings).get(
            target_week_id,
            {
                "funded_buy_amount_hkd": 0.0,
                "uncovered_buy_amount_hkd": 0.0,
                "top_ancestor_weeks": [],
                "top_ancestor_sells": [],
            },
        )

    def _build_funding_ancestor_index(self, cross_week_pairings: Dict[Tuple[str, int], List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
        sell_op_by_key: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for sell_week_id, sell_index in cross_week_pairings.keys():
            key = (sell_week_id, sell_index)
            if key in sell_op_by_key:
                continue
            review = self.storage.get_weekly_review(sell_week_id) or {}
            ops = [self._normalize_trim_reallocation_op(op) for op in (review.get("rebalancing_ops") or []) if isinstance(op, dict)]
            sell_op_by_key[key] = ops[sell_index] if 0 <= sell_index < len(ops) else {}

        grouped: Dict[str, Dict[str, Any]] = {}
        for (sell_week_id, sell_index), pairs in cross_week_pairings.items():
            sell_op = sell_op_by_key.get((sell_week_id, sell_index), {})
            for pair in pairs:
                target_week_id = str(pair.get("buy_week_id") or "").strip()
                if not target_week_id or sell_week_id == target_week_id:
                    continue
                amount = _safe_float(pair.get("amount")) or 0.0
                if amount <= 0:
                    continue
                row = grouped.setdefault(
                    target_week_id,
                    {
                        "funded": 0.0,
                        "week_totals": {},
                        "sell_totals": {},
                    },
                )
                row["funded"] += amount
                row["week_totals"][sell_week_id] = row["week_totals"].get(sell_week_id, 0.0) + amount
                sell_key = (sell_week_id, str(sell_op.get("stock_id") or ""), str(sell_op.get("date") or ""))
                row["sell_totals"][sell_key] = row["sell_totals"].get(sell_key, 0.0) + amount

        result: Dict[str, Dict[str, Any]] = {}
        for target_week_id, row in grouped.items():
            week_totals = row.get("week_totals") or {}
            sell_totals = row.get("sell_totals") or {}
            result[target_week_id] = {
                "funded_buy_amount_hkd": round(_safe_float(row.get("funded")) or 0.0, 2),
                "uncovered_buy_amount_hkd": 0.0,
                "top_ancestor_weeks": [
                    {"week_id": week_id, "funded_amount_hkd": round(amount, 2)}
                    for week_id, amount in sorted(week_totals.items(), key=lambda item: item[1], reverse=True)
                ],
                "top_ancestor_sells": [
                    {"week_id": week_id, "stock_id": stock_id, "sell_date": sell_date, "funded_amount_hkd": round(amount, 2)}
                    for (week_id, stock_id, sell_date), amount in sorted(sell_totals.items(), key=lambda item: item[1], reverse=True)
                ],
            }
        return result

    def _build_adjustment_events(
        self,
        week_ids: List[str],
        cross_week_pairings: Dict[Tuple[str, int], List[Dict[str, Any]]],
        funding_ancestor_index: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        sorted_weeks = sorted(week_ids, key=_week_sort_key)
        funding_ancestor_index = funding_ancestor_index if funding_ancestor_index is not None else self._build_funding_ancestor_index(cross_week_pairings)
        for idx, week_id in enumerate(sorted_weeks):
            review = self.storage.get_weekly_review(week_id) or {}
            ops, gross_sell, gross_buy = self._ops_amounts(review)
            if not ops:
                continue
            previous_review = self.storage.get_weekly_review(sorted_weeks[idx - 1]) if idx > 0 else {}
            current_value = _safe_float(review.get("total_portfolio_value")) or (gross_buy - gross_sell)
            previous_value = _safe_float((previous_review or {}).get("total_portfolio_value")) or current_value
            diff = round(current_value - previous_value, 2)
            phase = self._phase_for_ops(ops)
            counterfactuals = self._default_counterfactual_pack(current_value, hold_value=previous_value, cash_value=previous_value + gross_sell - gross_buy)
            decision_summary = self._decision_summary_for_counterfactuals(counterfactuals, [phase])
            stage_breakdown = {
                "sell_judgment_hkd": round(diff if gross_sell else 0.0, 2),
                "cash_idle_hkd": round(max(gross_sell - gross_buy, 0.0), 2),
                "cash_idle_ratio": round(max(gross_sell - gross_buy, 0.0) / gross_sell, 6) if gross_sell else 0.0,
                "rebuy_quality_hkd": round(diff if gross_buy else 0.0, 2),
            }
            pre_positions = self._position_rows_from_review(previous_review or {})
            post_positions = self._position_rows_from_review(review)
            rows.append(
                {
                    "event_id": f"adjustment:{week_id}",
                    "week_id": week_id,
                    "event_start_date": _week_end_date_str(week_id),
                    "event_end_date": datetime.now().strftime("%Y-%m-%d"),
                    "ops_count": len(ops),
                    "phase": phase,
                    "pre_snapshot": {"positions": pre_positions, "total_value_hkd": round(previous_value, 2)},
                    "post_snapshot": {"positions": post_positions, "total_value_hkd": round(current_value, 2)},
                    "comparison": {"current_value_diff_hkd": diff, "return_diff_pct": round(diff / previous_value, 6) if previous_value else None},
                    "gross_sell_hkd": gross_sell,
                    "gross_buy_hkd": gross_buy,
                    "net_cash_hkd": round(gross_sell - gross_buy, 2),
                    "changes": self._build_position_change_rows(pre_positions, post_positions, ops=ops, week_id=week_id),
                    "action_diagnosis": {"primary_issue": decision_summary["weakest_stage_key"], "headline": "Adjustment summary", "messages": decision_summary["explanation_lines"]},
                    "action_breakdown": {"sell_drag_hkd": stage_breakdown["sell_judgment_hkd"], "cash_idle_hkd": stage_breakdown["cash_idle_hkd"], "rebuy_quality_hkd": stage_breakdown["rebuy_quality_hkd"]},
                    "funding_ancestors": funding_ancestor_index.get(
                        week_id,
                        {
                            "funded_buy_amount_hkd": 0.0,
                            "uncovered_buy_amount_hkd": 0.0,
                            "top_ancestor_weeks": [],
                            "top_ancestor_sells": [],
                        },
                    ),
                    "counterfactuals": counterfactuals,
                    "counterfactual_timeline": self._timeline_for_counterfactuals(week_id, counterfactuals),
                    "decision_summary": decision_summary,
                    "stage_breakdown": stage_breakdown,
                    "primary_issue": decision_summary["weakest_stage_key"],
                }
            )
        rows.sort(key=lambda row: _week_sort_key(row["week_id"]), reverse=True)
        return rows

    def _build_no_trade_checkpoint(self, week_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        value = round(_safe_float((review or {}).get("total_portfolio_value")) or 0.0, 2)
        counterfactuals = self._default_counterfactual_pack(value)
        return {
            "batch_id": f"checkpoint:{week_id}",
            "first_week_id": week_id,
            "last_week_id": week_id,
            "week_ids": [week_id],
            "event_count": 0,
            "phase_sequence": ["no_recorded_trades"],
            "batch_label": "no_recorded_trades",
            "checkpoint_only": True,
            "current_value_diff_hkd": 0.0,
            "gross_sell_hkd": 0.0,
            "gross_buy_hkd": 0.0,
            "net_cash_hkd": 0.0,
            "changes": {"sold": [], "reduced": [], "added": [], "increased": []},
            "counterfactuals": counterfactuals,
            "counterfactual_timeline": self._timeline_for_counterfactuals(week_id, counterfactuals),
            "decision_summary": {
                "best_alternative_key": "actual_redeploy",
                "best_alternative_diff_hkd": 0.0,
                "weakest_stage_key": "none",
                "verdict_tag": "actual_best",
                "headline": "No recorded trades",
                "phase_flow": "no recorded trades",
                "explanation_lines": ["This week has a saved review but no recorded rebalancing operations."],
            },
            "stage_breakdown": {"sell_judgment_hkd": 0.0, "cash_idle_hkd": 0.0, "cash_idle_ratio": 0.0, "rebuy_quality_hkd": 0.0},
            "primary_issue": "none",
            "funding_ancestors": {},
            "path_attribution": {"actual_redeploy": [], "hold_original": []},
        }

    def _build_path_attribution_for_batch(self, week_ids: List[str], decision_events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        target_weeks = set(str(week_id or "") for week_id in week_ids)
        actual_rows: List[Dict[str, Any]] = []
        hold_rows: List[Dict[str, Any]] = []
        for event in decision_events or []:
            week_id = str(event.get("week_id") or "").strip()
            if week_id not in target_weeks:
                continue
            stock_id = str(event.get("stock_id") or "").strip()
            sold_capital = round(_safe_float(event.get("sold_capital")) or 0.0, 2)
            hold = event.get("original_hold_12w") or event.get("hold_original") or {}
            hold_rows.append(
                {
                    "week_id": week_id,
                    "stock_id": stock_id,
                    "allocated_amount_hkd": sold_capital,
                    "return": hold.get("return"),
                    "pnl_hkd": round(_safe_float(hold.get("pnl")) or 0.0, 2),
                }
            )
            actual = self._actual_result_for_contribution(event, "12w") or event.get("actual_result_12w") or event.get("actual_redeploy") or {}
            for target in actual.get("paired_targets") or []:
                if not isinstance(target, dict):
                    continue
                amount = _safe_float(target.get("allocated_amount") or target.get("amount") or target.get("buy_amount")) or 0.0
                pnl = _safe_float(target.get("target_pnl")) or 0.0
                actual_rows.append(
                    {
                        "week_id": week_id,
                        "stock_id": str(target.get("stock_id") or "").strip(),
                        "allocated_amount_hkd": round(amount, 2),
                        "return": target.get("target_return"),
                        "pnl_hkd": round(pnl, 2),
                    }
                )
        actual_rows.sort(key=lambda row: (-abs(_safe_float(row.get("pnl_hkd")) or 0.0), row.get("week_id") or "", row.get("stock_id") or ""))
        hold_rows.sort(key=lambda row: (-abs(_safe_float(row.get("pnl_hkd")) or 0.0), row.get("week_id") or "", row.get("stock_id") or ""))
        return {"actual_redeploy": actual_rows, "hold_original": hold_rows}

    def _actual_targets_for_event(self, event: Dict[str, Any], suffix: str = "12w") -> List[Dict[str, Any]]:
        actual = self._actual_result_for_contribution(event, suffix) or {}
        rows: List[Dict[str, Any]] = []
        for target in actual.get("paired_targets") or []:
            if not isinstance(target, dict):
                continue
            stock_id = str(target.get("stock_id") or "").strip()
            amount = _safe_float(target.get("allocated_amount") or target.get("amount") or target.get("buy_amount")) or 0.0
            if not stock_id or amount <= 0:
                continue
            rows.append(
                {
                    "stock_id": stock_id,
                    "stock_name": target.get("stock_name") or target.get("name") or stock_id,
                    "allocated_amount_hkd": round(amount, 2),
                    "return": target.get("target_return"),
                    "pnl_hkd": round(_safe_float(target.get("target_pnl")) or 0.0, 2),
                    "buy_week_id": target.get("buy_week_id"),
                    "buy_date": target.get("buy_date") or target.get("entry_date"),
                }
            )
        return rows

    def _matching_summary_for_events(self, events: List[Dict[str, Any]], suffix: str = "12w") -> Dict[str, Any]:
        sold_capital = self._sum_sold_capital(events)
        matched_amount = 0.0
        cash_amount = 0.0
        target_count = 0
        stale_pair_count = 0
        bases: set[str] = set()
        for event in events or []:
            actual = self._actual_result_for_contribution(event, suffix) or {}
            if _safe_float(actual.get("pnl")) is None:
                continue
            basis = str(actual.get("actual_basis") or actual.get("pairing_mode") or actual.get("attribution_state") or "matched_redeploy").strip()
            if basis:
                bases.add(basis)
            event_sold_capital = self._event_sold_capital(event)
            actual_unallocated_amount = _safe_float(actual.get("unallocated_amount"))
            if actual_unallocated_amount is not None and actual_unallocated_amount > 0:
                cash_amount += min(actual_unallocated_amount, event_sold_capital)
            elif basis == "cash":
                cash_amount += event_sold_capital
            sell_date = str(event.get("sell_date") or event.get("date") or "").strip()
            for target in self._actual_targets_for_event(event, suffix):
                target_count += 1
                matched_amount += _safe_float(target.get("allocated_amount_hkd")) or 0.0
                buy_date = str(target.get("buy_date") or "").strip()
                if sell_date and buy_date and buy_date < sell_date:
                    stale_pair_count += 1

        matched_amount = round(matched_amount, 2)
        cash_amount = round(max(cash_amount, 0.0), 2)
        explained_amount = round(matched_amount + cash_amount, 2)
        unmatched_amount = round(max(sold_capital - explained_amount, 0.0), 2)
        coverage_ratio = round(explained_amount / sold_capital, 6) if sold_capital > 0 else None
        if sold_capital <= 0:
            coverage_state = "no_sell_capital"
        elif explained_amount > sold_capital + 0.01:
            coverage_state = "overmatched"
        elif abs(explained_amount - sold_capital) <= 0.01:
            if cash_amount > 0 and matched_amount > 0:
                coverage_state = "cash_partially_redeployed"
            elif cash_amount > 0:
                coverage_state = "cash"
            else:
                coverage_state = "fully_matched"
        elif matched_amount > 0:
            coverage_state = "partially_matched"
        else:
            coverage_state = "unmatched"
        if stale_pair_count:
            coverage_state = "stale_manual_suspect"

        basis = "matched_redeploy"
        if len(bases) == 1:
            basis = next(iter(bases))
        elif len(bases) > 1:
            basis = "mixed"
        return {
            "basis": basis,
            "coverage_state": coverage_state,
            "sold_capital_hkd": round(sold_capital, 2),
            "matched_amount_hkd": matched_amount,
            "cash_amount_hkd": cash_amount,
            "explained_amount_hkd": explained_amount,
            "unmatched_amount_hkd": unmatched_amount,
            "coverage_ratio": coverage_ratio,
            "target_count": target_count,
            "stale_pair_count": stale_pair_count,
        }

    def _fund_flow_for_events(self, events: List[Dict[str, Any]], suffix: str = "12w") -> Dict[str, Any]:
        matching = self._matching_summary_for_events(events, suffix)
        targets: List[Dict[str, Any]] = []
        for event in events or []:
            week_id = str(event.get("week_id") or "").strip()
            for target in self._actual_targets_for_event(event, suffix):
                targets.append({"week_id": week_id, **target})
        targets.sort(key=lambda row: (-abs(_safe_float(row.get("pnl_hkd")) or 0.0), row.get("week_id") or "", row.get("stock_id") or ""))
        return {**matching, "targets": targets}

    def _stage_attribution_for_pnls(self, hold_pnl: Optional[float], actual_pnl: Optional[float], cash_pnl: Optional[float]) -> Dict[str, Optional[float]]:
        if hold_pnl is None or actual_pnl is None:
            return {"sell_timing_hkd": None, "redeploy_effect_hkd": None, "net_result_hkd": None}
        cash_value = 0.0 if cash_pnl is None else cash_pnl
        return {
            "sell_timing_hkd": round(cash_value - hold_pnl, 2),
            "redeploy_effect_hkd": round(actual_pnl - cash_value, 2),
            "net_result_hkd": round(actual_pnl - hold_pnl, 2),
        }

    def _deterministic_verdict_for_stage_attribution(
        self,
        stage: Dict[str, Optional[float]],
        matching: Dict[str, Any],
    ) -> Dict[str, Any]:
        sell_timing = _safe_float(stage.get("sell_timing_hkd"))
        redeploy = _safe_float(stage.get("redeploy_effect_hkd"))
        net = _safe_float(stage.get("net_result_hkd"))
        coverage_state = str((matching or {}).get("coverage_state") or "").strip()
        if net is None:
            return {
                "key": "insufficient_data",
                "label": "Text, Text",
                "severity": "neutral",
                "primary_driver": "data_quality",
                "description": "Text P/L, TextSell ReviewText. ",
            }
        if coverage_state in {"unmatched", "partially_matched", "overmatched", "stale_manual_suspect"}:
            if net < 0 and coverage_state in {"unmatched", "stale_manual_suspect"}:
                return {
                    "key": "matching_quality_low",
                    "label": "Text, Text",
                    "severity": "warning",
                    "primary_driver": "matching_quality",
                    "description": "Text, TextSellText. ",
                }
        if net >= 0:
            if sell_timing is not None and sell_timing < 0 and redeploy is not None and redeploy > abs(sell_timing):
                return {
                    "key": "redeploy_saved_sale",
                    "label": "SellText, TextReturn",
                    "severity": "positive",
                    "primary_driver": "redeploy",
                    "description": "TextCash, TextContributionText, Text. ",
                }
            return {
                "key": "actual_path_best",
                "label": "Text",
                "severity": "positive",
                "primary_driver": "actual_path",
                "description": "SellTextResultText, TextCurrentTextSellText. ",
            }
        if sell_timing is not None and sell_timing < 0 and (redeploy is None or redeploy <= abs(sell_timing)):
            return {
                "key": "sell_timing_error",
                "label": "TextErrorText",
                "severity": "negative",
                "primary_driver": "sell_timing",
                "description": "TextCash, TextSellTextOpportunity Cost. ",
            }
        if redeploy is not None and redeploy < 0:
            return {
                "key": "redeploy_error",
                "label": "SellText, Text",
                "severity": "negative",
                "primary_driver": "redeploy",
                "description": "SellTextRiskTextCash, Text. ",
            }
        return {
            "key": "hold_would_win",
            "label": "Text",
            "severity": "negative",
            "primary_driver": "hold_original",
            "description": "TextCurrent mark, Text. ",
        }

    def _drilldown_for_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        hold_pnl = _safe_float((event.get("original_hold_12w") or {}).get("pnl"))
        actual_block = self._actual_result_for_contribution(event, "12w") or {}
        actual_pnl = _safe_float(actual_block.get("pnl"))
        cash_pnl = _safe_float((event.get("cash_result_12w") or {}).get("pnl"))
        hold_30d = _safe_float((event.get("original_hold_30d") or {}).get("pnl"))
        actual_30d = _safe_float((event.get("actual_result_30d") or {}).get("pnl"))
        stage = self._stage_attribution_for_pnls(hold_pnl, actual_pnl, cash_pnl)
        matching = self._matching_summary_for_events([event], "12w")
        mark_30d_opportunity = round(hold_30d - actual_30d, 2) if hold_30d is not None and actual_30d is not None else None
        now_opportunity = round(hold_pnl - actual_pnl, 2) if hold_pnl is not None and actual_pnl is not None else None
        return {
            "sold_capital_hkd": self._event_sold_capital(event),
            "hold_pnl_hkd": None if hold_pnl is None else round(hold_pnl, 2),
            "actual_pnl_hkd": None if actual_pnl is None else round(actual_pnl, 2),
            "cash_pnl_hkd": None if cash_pnl is None else round(cash_pnl, 2),
            "opportunity_cost_hkd": now_opportunity,
            "stage_attribution": stage,
            "fund_flow": self._fund_flow_for_events([event], "12w"),
            "mark_30d": {
                "hold_pnl_hkd": None if hold_30d is None else round(hold_30d, 2),
                "actual_pnl_hkd": None if actual_30d is None else round(actual_30d, 2),
                "opportunity_cost_hkd": mark_30d_opportunity,
            },
            "mark_now_gap_hkd": (
                round((actual_pnl - hold_pnl) - (actual_30d - hold_30d), 2)
                if hold_pnl is not None and actual_pnl is not None and hold_30d is not None and actual_30d is not None
                else None
            ),
        }

    def _build_decision_attribution_for_batch(self, week_ids: List[str], decision_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        target_weeks = set(str(week_id or "") for week_id in week_ids)
        scoped_events = [
            event
            for event in (decision_events or [])
            if str(event.get("week_id") or "").strip() in target_weeks
        ]
        hold_pnl = 0.0
        actual_pnl = 0.0
        cash_pnl = 0.0
        event_count = 0
        evaluated_count = 0
        missing_hold_count = 0
        missing_actual_count = 0
        actual_bases: set[str] = set()
        for event in scoped_events:
            event_count += 1
            hold_value = _safe_float((event.get("original_hold_12w") or {}).get("pnl"))
            actual_block = self._actual_result_for_contribution(event, "12w") or {}
            actual_value = _safe_float(actual_block.get("pnl"))
            cash_value = _safe_float((event.get("cash_result_12w") or {}).get("pnl"))
            if hold_value is None:
                missing_hold_count += 1
                continue
            if actual_value is None:
                missing_actual_count += 1
                continue
            basis = str(actual_block.get("actual_basis") or actual_block.get("pairing_mode") or actual_block.get("attribution_state") or "matched_redeploy").strip()
            if basis:
                actual_bases.add(basis)
            hold_pnl += hold_value
            actual_pnl += actual_value
            cash_pnl += cash_value if cash_value is not None else 0.0
            evaluated_count += 1
        net_result = round(actual_pnl - hold_pnl, 2)
        actual_basis = "matched_redeploy"
        if len(actual_bases) == 1:
            actual_basis = next(iter(actual_bases))
        elif len(actual_bases) > 1:
            actual_basis = "mixed"
        return {
            "event_count": event_count,
            "evaluated_count": evaluated_count,
            "missing_hold_count": missing_hold_count,
            "missing_actual_count": missing_actual_count,
            "hold_original_pnl_hkd": round(hold_pnl, 2),
            "actual_redeploy_pnl_hkd": round(actual_pnl, 2),
            "net_result_hkd": net_result,
            "opportunity_cost_hkd": round(abs(net_result), 2) if net_result < 0 else 0.0,
            "value_added_hkd": net_result if net_result > 0 else 0.0,
            "actual_basis": actual_basis,
            "matching_summary": self._matching_summary_for_events(scoped_events, "12w"),
            "fund_flow": self._fund_flow_for_events(scoped_events, "12w"),
            "stage_attribution": self._stage_attribution_for_pnls(round(hold_pnl, 2), round(actual_pnl, 2), round(cash_pnl, 2)),
        }

    def _decision_events_for_batch(self, week_ids: List[str], decision_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        target_weeks = set(str(week_id or "") for week_id in week_ids)
        return [event for event in (decision_events or []) if str(event.get("week_id") or "").strip() in target_weeks]

    def _buy_weekend_portfolio_path_for_batch(
        self,
        batch: Dict[str, Any],
        end_date: str,
        price_lookup_by_week: Dict[str, Dict[Tuple[str, str], float]],
        actual_pnl_override: Optional[float] = None,
        capital_override_hkd: Optional[float] = None,
    ) -> Dict[str, Any]:
        actual_pnl = actual_pnl_override
        if actual_pnl is None:
            actual_pnl = _safe_float((batch.get("decision_attribution") or {}).get("actual_redeploy_pnl_hkd"))
        gross_sell = _safe_float(capital_override_hkd)
        if gross_sell is None:
            gross_sell = _safe_float(batch.get("gross_sell_hkd")) or 0.0
        capital = gross_sell if gross_sell > 0 else 0.0
        if capital <= 0:
            return {"final_value_hkd": None, "pnl_hkd": None, "vs_actual_hkd": None}

        last_week_id = str(batch.get("last_week_id") or "").strip()
        review = self.storage.get_weekly_review(last_week_id) or {}
        positions = self._position_rows_from_review(review)
        if not positions:
            return {"final_value_hkd": None, "pnl_hkd": None, "vs_actual_hkd": None}

        week_end = _week_end_date_str(last_week_id)
        price_lookup = dict(price_lookup_by_week.get(last_week_id) or {})
        if not price_lookup:
            price_lookup = self._build_trim_price_lookup(last_week_id, review, end_date)
        position_stock_ids = {str(row.get("stock_id") or "").strip() for row in positions if str(row.get("stock_id") or "").strip()}
        if position_stock_ids:
            cached_prices = self._load_cached_close_prices(position_stock_ids, {week_end, end_date})
            price_lookup.update({key: value for key, value in cached_prices.items() if key not in price_lookup})
        weighted_return = 0.0
        weighted_capital = 0.0
        contributor_basis: List[Dict[str, Any]] = []
        for position in positions:
            stock_id = str(position.get("stock_id") or position.get("ticker") or "").strip()
            shares = _safe_float(position.get("shares")) or 0.0
            if not stock_id or shares <= 0:
                continue
            start_price = self._lookup_price_on_or_before(price_lookup, stock_id, week_end) or _safe_float(position.get("avg_cost"))
            end_price = self._lookup_price_on_or_before(price_lookup, stock_id, end_date)
            if not start_price or not end_price:
                continue
            currency = str(position.get("currency") or "USD").upper()
            fx = 1.0
            if currency == "USD":
                fx = _safe_float(review.get("usd_to_hkd")) or 7.8
            elif currency == "CNY":
                fx = _safe_float(review.get("cny_to_hkd")) or 1.07
            elif currency == "EUR":
                fx = _safe_float(review.get("eur_to_hkd")) or 8.4
            position_value = shares * start_price * fx
            if position_value <= 0:
                continue
            position_return = (end_price / start_price) - 1
            weighted_capital += position_value
            weighted_return += position_value * position_return
            contributor_basis.append(
                {
                    "stock_id": stock_id,
                    "stock_name": position.get("stock_name") or stock_id,
                    "position_value_hkd": position_value,
                    "return": position_return,
                }
            )
        if weighted_capital <= 0:
            return {"final_value_hkd": None, "pnl_hkd": None, "vs_actual_hkd": None}
        portfolio_return = weighted_return / weighted_capital
        pnl = round(capital * portfolio_return, 2)
        contributors = []
        for row in contributor_basis:
            weight = row["position_value_hkd"] / weighted_capital
            contributors.append(
                {
                    "stock_id": row["stock_id"],
                    "stock_name": row["stock_name"],
                    "weight": round(weight, 6),
                    "return": round(row["return"], 6),
                    "contribution_pnl_hkd": round(capital * weight * row["return"], 2),
                }
            )
        contributors.sort(key=lambda row: abs(row.get("contribution_pnl_hkd") or 0.0), reverse=True)
        return {
            "final_value_hkd": round(capital + pnl, 2),
            "pnl_hkd": pnl,
            "vs_actual_hkd": None if actual_pnl is None else round(pnl - actual_pnl, 2),
            "contributors": contributors[:5],
        }

    def _benchmark_path_for_event(self, event: Dict[str, Any], capital_hkd: float) -> Dict[str, Any]:
        benchmark_block = dict((event.get("benchmark_outcome") or event.get("benchmark_result_12w") or {}))
        benchmark_id = str(benchmark_block.get("benchmark_id") or event.get("benchmark") or "").strip().upper() or "BENCHMARK"
        benchmark_pnl = _safe_float(benchmark_block.get("pnl"))
        benchmark_return = _safe_float(benchmark_block.get("return"))
        capital = _safe_float(capital_hkd) or 0.0
        if benchmark_pnl is None and benchmark_return is not None and capital > 0:
            benchmark_pnl = round(capital * benchmark_return, 2)
        if benchmark_return is None and benchmark_pnl is not None and capital > 0:
            benchmark_return = round(benchmark_pnl / capital, 6)
        if benchmark_pnl is None:
            benchmark_pnl = 0.0
        if benchmark_return is None:
            benchmark_return = 0.0
        return {
            "benchmark_id": benchmark_id,
            "final_value_hkd": round(capital + benchmark_pnl, 2),
            "pnl_hkd": round(benchmark_pnl, 2),
            "vs_actual_hkd": None,
            "return_pct": round(benchmark_return, 6),
            "contributors": [],
        }

    def _benchmark_path_for_events(self, events: List[Dict[str, Any]], suffix: str, capital_hkd: float) -> Dict[str, Any]:
        capital = _safe_float(capital_hkd) or 0.0
        if capital <= 0:
            return {"benchmark_id": "BENCHMARK", "final_value_hkd": None, "pnl_hkd": None, "vs_actual_hkd": None, "return_pct": None, "contributors": []}
        total_pnl = 0.0
        benchmark_ids: set[str] = set()
        candidate_blocks = (
            lambda event: [
                event.get(f"benchmark_result_{suffix}") or {},
                event.get("benchmark_outcome") or {},
                (event.get("current") or {}).get(f"benchmark_result_{suffix}") or {},
                (event.get("current") or {}).get("benchmark_outcome") or {},
                (event.get("weekly") or {}).get(f"benchmark_result_{suffix}") or {},
                (event.get("weekly") or {}).get("benchmark_outcome") or {},
            ]
        )
        for event in events or []:
            event_capital = self._event_sold_capital(event)
            for block_like in candidate_blocks(event):
                block = dict(block_like or {})
                benchmark_id = str(block.get("benchmark_id") or event.get("benchmark") or "").strip().upper()
                if benchmark_id:
                    benchmark_ids.add(benchmark_id)
                pnl = _safe_float(block.get("pnl"))
                ret = _safe_float(block.get("return"))
                if pnl is None and ret is not None and event_capital > 0:
                    pnl = round(event_capital * ret, 2)
                if pnl is None:
                    continue
                total_pnl += pnl
                break
        benchmark_id = next(iter(sorted(benchmark_ids))) if len(benchmark_ids) == 1 else ("MIXED" if benchmark_ids else "BENCHMARK")
        return {
            "benchmark_id": benchmark_id,
            "final_value_hkd": round(capital + total_pnl, 2),
            "pnl_hkd": round(total_pnl, 2),
            "vs_actual_hkd": None,
            "return_pct": round(total_pnl / capital, 6) if capital > 0 else None,
            "contributors": [],
        }

    def _actual_path_pnl_for_window(self, attribution: Dict[str, Any], weekend_path: Dict[str, Any]) -> Tuple[Optional[float], str]:
        evaluated_count = int(_safe_float((attribution or {}).get("evaluated_count")) or 0)
        actual_pnl = _safe_float((attribution or {}).get("actual_redeploy_pnl_hkd"))
        if evaluated_count > 0 and actual_pnl is not None:
            return actual_pnl, str((attribution or {}).get("actual_basis") or "matched_redeploy")
        weekend_pnl = _safe_float((weekend_path or {}).get("pnl_hkd"))
        if weekend_pnl is not None:
            return weekend_pnl, "weekend_portfolio"
        return actual_pnl, "insufficient_data"

    def _actual_basis_for_events(self, events: List[Dict[str, Any]], suffix: str = "12w") -> str:
        bases: set[str] = set()
        for event in events or []:
            actual_block = self._actual_result_for_contribution(event, suffix) or {}
            actual_pnl = _safe_float(actual_block.get("pnl"))
            if actual_pnl is None:
                continue
            basis = str(actual_block.get("actual_basis") or actual_block.get("pairing_mode") or actual_block.get("attribution_state") or "matched_redeploy").strip()
            if basis:
                bases.add(basis)
        if not bases:
            return "insufficient_data"
        if len(bases) == 1:
            return next(iter(bases))
        return "mixed"

    def _counterfactual_window_explanation_lines(
        self,
        actual_pnl: Optional[float],
        hold_pnl: Optional[float],
        benchmark_path: Dict[str, Any],
        data_state: str,
        prefix: str = "",
    ) -> List[str]:
        def signed(value: float) -> str:
            return "0" if round(value) == 0 else f"{value:+.0f}"

        lead = f"{prefix}: " if prefix else ""
        if data_state != "ok":
            return [f"{lead}Data is incomplete for this window; use it as a checkpoint, not a sell-decision verdict."]
        lines: List[str] = []
        if actual_pnl is not None and hold_pnl is not None:
            lines.append(
                f"{lead}actual path P/L {signed(actual_pnl)} vs if-held P/L {signed(hold_pnl)}; decision net {signed(actual_pnl - hold_pnl)}."
            )
        benchmark_pnl = _safe_float(benchmark_path.get("pnl_hkd"))
        benchmark_vs_actual = _safe_float(benchmark_path.get("vs_actual_hkd"))
        if benchmark_pnl is not None and benchmark_vs_actual is not None:
            relation = "beating" if benchmark_vs_actual >= 0 else "lagging"
            lines.append(
                f"The benchmark path would have produced P/L {signed(benchmark_pnl)}, {relation} actual by {signed(benchmark_vs_actual)}."
            )
        return lines

    def _build_counterfactual_mark(
        self,
        *,
        actual_pnl: Optional[float],
        hold_pnl: Optional[float],
        cash_pnl: Optional[float],
        capital_hkd: float,
        benchmark_path: Dict[str, Any],
        labels: Dict[str, str],
        prefix: str,
        actual_basis: str = "matched_redeploy",
        stage_attribution: Optional[Dict[str, Optional[float]]] = None,
    ) -> Dict[str, Any]:
        capital = _safe_float(capital_hkd) or 0.0

        def final_value_for(pnl: Optional[float]) -> Optional[float]:
            value = _safe_float(pnl)
            if value is None:
                return None
            return round(capital + value, 2)

        def return_pct_for(pnl: Optional[float]) -> Optional[float]:
            value = _safe_float(pnl)
            if value is None or capital <= 0:
                return None
            return round(value / capital, 6)

        benchmark_row = dict(benchmark_path or {})
        if benchmark_row.get("pnl_hkd") is not None and actual_pnl is not None:
            benchmark_row["vs_actual_hkd"] = round(_safe_float(benchmark_row.get("pnl_hkd")) - actual_pnl, 2)
        path_specs = [
            ("actual_redeploy", final_value_for(actual_pnl), actual_pnl, 0.0 if actual_pnl is not None else None),
            ("hold_original", final_value_for(hold_pnl), hold_pnl, (hold_pnl - actual_pnl) if hold_pnl is not None and actual_pnl is not None else None),
            ("sell_to_cash", final_value_for(cash_pnl), cash_pnl, (cash_pnl - actual_pnl) if cash_pnl is not None and actual_pnl is not None else None),
            ("benchmark", benchmark_row.get("final_value_hkd"), benchmark_row.get("pnl_hkd"), benchmark_row.get("vs_actual_hkd")),
        ]
        end_states: List[Dict[str, Any]] = []
        for key, final_value, pnl, vs_actual in path_specs:
            if key == "benchmark" and pnl is not None and actual_pnl is not None:
                vs_actual = pnl - actual_pnl
            row = {
                "key": key,
                "label": labels[key],
                "capital_hkd": round(capital, 2),
                "final_value_hkd": None if final_value is None else round(final_value, 2),
                "pnl_hkd": None if pnl is None else round(pnl, 2),
                "return_pct": return_pct_for(pnl),
                "vs_actual_hkd": None if vs_actual is None else round(vs_actual, 2),
                "is_actual": key == "actual_redeploy",
            }
            if key == "benchmark":
                row["benchmark_id"] = benchmark_row.get("benchmark_id")
                row["contributors"] = benchmark_row.get("contributors") or []
            end_states.append(row)
        comparable = [row for row in end_states if row["vs_actual_hkd"] is not None]
        winner = max(comparable, key=lambda row: row["vs_actual_hkd"]) if comparable else end_states[0]
        data_state = "ok" if actual_pnl is not None and hold_pnl is not None else "insufficient_data"
        return {
            "winner_key": str(winner.get("key") or "actual_redeploy"),
            "best_gap_vs_actual_hkd": round(_safe_float(winner.get("vs_actual_hkd")) or 0.0, 2),
            "decision_net_hkd": None if actual_pnl is None or hold_pnl is None else round(actual_pnl - hold_pnl, 2),
            "actual_basis": actual_basis,
            "capital_hkd": round(capital, 2),
            "stage_attribution": stage_attribution or self._stage_attribution_for_pnls(hold_pnl, actual_pnl, cash_pnl),
            "end_states": end_states,
            "explanation_lines": self._counterfactual_window_explanation_lines(actual_pnl, hold_pnl, benchmark_row, data_state, prefix=prefix),
        }

    def _counterfactual_window_data_quality(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        deduped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for event in events or []:
            key = (
                str(event.get("week_id") or "").strip(),
                str(event.get("stock_id") or "").strip(),
                str(event.get("sell_date") or event.get("date") or "").strip(),
            )
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = event
                continue
            existing_score = sum(1 for field in ("original_hold_12w", "actual_result_12w", "original_hold_30d", "actual_result_30d") if _safe_float((existing.get(field) or {}).get("pnl")) is not None)
            event_score = sum(1 for field in ("original_hold_12w", "actual_result_12w", "original_hold_30d", "actual_result_30d") if _safe_float((event.get(field) or {}).get("pnl")) is not None)
            if event_score > existing_score:
                deduped[key] = event
        source_events = list(deduped.values())
        event_count = len(source_events)
        now_evaluable = 0
        mark_30d_evaluable = 0
        missing_now_hold = 0
        missing_now_actual = 0
        missing_30d_hold = 0
        missing_30d_actual = 0
        for event in source_events:
            now_hold = _safe_float((event.get("original_hold_12w") or {}).get("pnl"))
            now_actual = _safe_float((event.get("actual_result_12w") or {}).get("pnl"))
            mark_30d_hold = _safe_float((event.get("original_hold_30d") or {}).get("pnl"))
            mark_30d_actual = _safe_float((event.get("actual_result_30d") or {}).get("pnl"))
            if now_hold is not None and now_actual is not None:
                now_evaluable += 1
            else:
                if now_hold is None:
                    missing_now_hold += 1
                if now_actual is None:
                    missing_now_actual += 1
            if mark_30d_hold is not None and mark_30d_actual is not None:
                mark_30d_evaluable += 1
            else:
                if mark_30d_hold is None:
                    missing_30d_hold += 1
                if mark_30d_actual is None:
                    missing_30d_actual += 1
        issues: List[str] = []
        if missing_now_hold:
            issues.append("missing_now_hold")
        if missing_now_actual:
            issues.append("missing_now_actual")
        if missing_30d_hold:
            issues.append("missing_30d_hold")
        if missing_30d_actual:
            issues.append("missing_30d_actual")
        if not event_count:
            confidence = "checkpoint"
        elif now_evaluable == event_count and mark_30d_evaluable == event_count:
            confidence = "high"
        elif now_evaluable > 0 or mark_30d_evaluable > 0:
            confidence = "medium"
        else:
            confidence = "low"
        return {
            "confidence": confidence,
            "event_count": event_count,
            "now_evaluable_count": now_evaluable,
            "mark_30d_evaluable_count": mark_30d_evaluable,
            "missing_now_count": event_count - now_evaluable,
            "missing_30d_count": event_count - mark_30d_evaluable,
            "issues": issues,
        }

    def _build_counterfactual_windows(
        self,
        decision_batches: List[Dict[str, Any]],
        current_date: str,
        price_lookup_by_week: Optional[Dict[str, Dict[Tuple[str, str], float]]] = None,
    ) -> List[Dict[str, Any]]:
        windows: List[Dict[str, Any]] = []
        labels = {
            "actual_redeploy": "Text",
            "hold_original": "Text, Text",
            "sell_to_cash": "SellTextCash",
            "benchmark": "Text",
        }
        for batch in decision_batches or []:
            attribution = batch.get("decision_attribution") or {}
            actual_pnl = _safe_float(attribution.get("actual_redeploy_pnl_hkd"))
            hold_pnl = _safe_float(attribution.get("hold_original_pnl_hkd"))
            net_result = _safe_float(attribution.get("net_result_hkd"))
            evaluated_count = int(_safe_float(attribution.get("evaluated_count")) or 0)
            event_count = int(_safe_float(attribution.get("event_count")) or 0)
            events = batch.get("decision_events") or []
            event_capital_hkd = self._sum_sold_capital(events)
            capital_hkd = event_capital_hkd or (_safe_float(batch.get("gross_sell_hkd")) or 0.0)
            benchmark_path_now = self._benchmark_path_for_events(events, "12w", capital_hkd)
            benchmark_path_now["benchmark_id"] = benchmark_path_now.get("benchmark_id") or (
                next((str((event.get("benchmark_outcome") or event.get("benchmark_result_12w") or {}).get("benchmark_id") or "").strip().upper() for event in events if str((event.get("benchmark_outcome") or event.get("benchmark_result_12w") or {}).get("benchmark_id") or "").strip()), "BENCHMARK")
            )
            actual_mark_pnl, actual_basis = self._actual_path_pnl_for_window(attribution, {})
            if event_count == 0:
                actual_basis = "checkpoint"
            hold_mark_pnl = hold_pnl if hold_pnl is not None else None
            now_mark = self._build_counterfactual_mark(
                actual_pnl=actual_mark_pnl,
                hold_pnl=hold_mark_pnl,
                cash_pnl=0.0 if actual_mark_pnl is not None else None,
                capital_hkd=capital_hkd,
                benchmark_path=benchmark_path_now,
                labels=labels,
                prefix="Now mark",
                actual_basis=actual_basis,
                stage_attribution=attribution.get("stage_attribution"),
            )
            data_quality = self._counterfactual_window_data_quality(events)

            def horizon_mark(suffix: str, label: str) -> tuple[str, Dict[str, Any]]:
                hold_total = 0.0
                actual_total = 0.0
                cash_total = 0.0
                hold_count = 0
                actual_count = 0
                cash_count = 0
                mark_dates: List[str] = []
                for event in events:
                    hold_pnl = _safe_float((event.get(f"original_hold_{suffix}") or {}).get("pnl"))
                    actual_pnl = _safe_float((event.get(f"actual_result_{suffix}") or {}).get("pnl"))
                    cash_pnl = _safe_float((event.get(f"cash_result_{suffix}") or {}).get("pnl"))
                    if hold_pnl is not None:
                        hold_total += hold_pnl
                        hold_count += 1
                    if actual_pnl is not None:
                        actual_total += actual_pnl
                        actual_count += 1
                    if cash_pnl is not None:
                        cash_total += cash_pnl
                        cash_count += 1
                    mark_date = str(event.get(f"mark_{suffix}_end_date") or "").strip()
                    if mark_date:
                        mark_dates.append(mark_date)
                hold_value = round(hold_total, 2) if hold_count else None
                actual_value = round(actual_total, 2) if actual_count else None
                cash_value = round(cash_total, 2) if cash_count else None
                mark_date = max(mark_dates) if mark_dates else ""
                benchmark_path = self._benchmark_path_for_events(events, suffix, capital_hkd) if event_count else {"final_value_hkd": None, "pnl_hkd": None, "vs_actual_hkd": None, "contributors": []}
                benchmark_path["benchmark_id"] = benchmark_path.get("benchmark_id") or next((str((event.get(f"benchmark_result_{suffix}") or event.get("benchmark_outcome") or {}).get("benchmark_id") or "").strip().upper() for event in events if str((event.get(f"benchmark_result_{suffix}") or event.get("benchmark_outcome") or {}).get("benchmark_id") or "").strip()), "BENCHMARK")
                actual_mark, actual_basis = (
                    (actual_value, self._actual_basis_for_events(events, suffix))
                    if actual_value is not None
                    else self._actual_path_pnl_for_window({"evaluated_count": 0, "actual_redeploy_pnl_hkd": None}, {})
                )
                if event_count == 0:
                    actual_basis = "checkpoint"
                mark = self._build_counterfactual_mark(
                    actual_pnl=actual_mark,
                    hold_pnl=hold_value,
                    cash_pnl=cash_value,
                    capital_hkd=capital_hkd,
                    benchmark_path=benchmark_path,
                    labels=labels,
                    prefix=f"{label} mark",
                    actual_basis=actual_basis,
                )
                return mark_date, mark

            mark_30d_date, thirty_day_mark = horizon_mark("30d", "30D")
            mark_60d_date, sixty_day_mark = horizon_mark("60d", "60D")
            mark_90d_date, ninety_day_mark = horizon_mark("90d", "90D")
            winner_key = now_mark["winner_key"]
            best_gap = now_mark["best_gap_vs_actual_hkd"]
            data_state = "checkpoint" if event_count == 0 else ("ok" if now_mark.get("decision_net_hkd") is not None else "insufficient_data")
            if data_state == "ok" and winner_key == "actual_redeploy":
                headline = "Text"
            elif data_state == "ok":
                headline = f"{labels.get(winner_key, 'Text')} Text"
            elif data_state == "checkpoint":
                headline = "TextTrade, TextStatusText"
            else:
                headline = "Text, Text"

            windows.append(
                {
                    "window_id": batch.get("batch_id"),
                    "batch_id": batch.get("batch_id"),
                    "title": batch.get("first_week_id") if batch.get("first_week_id") == batch.get("last_week_id") else f"{batch.get('first_week_id')} -> {batch.get('last_week_id')}",
                    "week_ids": batch.get("week_ids") or [],
                    "headline": headline,
                    "winner_key": winner_key,
                    "best_gap_vs_actual_hkd": round(best_gap, 2),
                    "decision_net_hkd": now_mark.get("decision_net_hkd"),
                    "decision_attribution": attribution,
                    "fund_flow": attribution.get("fund_flow") or self._fund_flow_for_events(events, "12w"),
                    "data_state": data_state,
                    "selected_mark": "now",
                    "mark_dates": {"30d": mark_30d_date, "60d": mark_60d_date, "90d": mark_90d_date, "now": current_date},
                    "data_quality": data_quality,
                    "marks": {"30d": thirty_day_mark, "60d": sixty_day_mark, "90d": ninety_day_mark, "now": now_mark},
                    "explanation_lines": now_mark["explanation_lines"],
                    "end_states": now_mark["end_states"],
                    "details_batch": batch,
                }
            )
        return windows

    def _decision_summary_for_batch_attribution(self, attribution: Dict[str, Any], fallback_summary: Dict[str, Any]) -> Dict[str, Any]:
        if not attribution.get("event_count"):
            return fallback_summary
        if not attribution.get("evaluated_count"):
            return {
                "best_alternative_key": "actual_redeploy",
                "best_alternative_diff_hkd": 0.0,
                "weakest_stage_key": "insufficient_data",
                "verdict_tag": "decision_needs_context",
                "headline": "Sell attribution needs price data",
                "phase_flow": fallback_summary.get("phase_flow") or "",
                "explanation_lines": [
                    f"{attribution.get('event_count', 0)} sell events found, but none have both if-held and actual redeploy P/L.",
                    "Portfolio move is shown separately and is not used as sell-decision verdict.",
                ],
            }
        net_result = _safe_float(attribution.get("net_result_hkd")) or 0.0
        verdict = "actual_best" if net_result >= 0 else "hold_would_win"
        return {
            "best_alternative_key": "actual_redeploy" if net_result >= 0 else "hold_original",
            "best_alternative_diff_hkd": round(abs(net_result), 2),
            "weakest_stage_key": "none" if net_result >= 0 else "re_risk_quality",
            "verdict_tag": verdict,
            "headline": "Sell attribution vs if-held path",
            "phase_flow": fallback_summary.get("phase_flow") or "",
            "explanation_lines": [
                f"Actual redeploy P/L {attribution.get('actual_redeploy_pnl_hkd', 0):.2f} HKD vs if-held P/L {attribution.get('hold_original_pnl_hkd', 0):.2f} HKD.",
                f"Net sell-decision result {net_result:.2f} HKD across {attribution.get('evaluated_count', 0)} evaluated sell events.",
            ],
        }

    def _build_decision_batches(
        self,
        adjustment_events: List[Dict[str, Any]],
        week_ids: Optional[List[str]] = None,
        decision_events: Optional[List[Dict[str, Any]]] = None,
        merge_adjacent_derisk_rerisk: bool = True,
    ) -> List[Dict[str, Any]]:
        chronological = sorted(adjustment_events, key=lambda row: _week_sort_key(row.get("week_id") or ""))
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        for event in chronological:
            if not current:
                current = [event]
                continue
            prev_key = _week_sort_key(current[-1].get("week_id") or "")
            this_key = _week_sort_key(event.get("week_id") or "")
            adjacent = this_key[0] == prev_key[0] and this_key[1] == prev_key[1] + 1
            transition = len(current) == 1 and current[-1].get("phase") == "derisk" and event.get("phase") == "rerisk"
            if merge_adjacent_derisk_rerisk and adjacent and transition:
                current.append(event)
            else:
                groups.append(current)
                current = [event]
        if current:
            groups.append(current)

        batches: List[Dict[str, Any]] = []
        for group in groups:
            group_week_ids = [row["week_id"] for row in group]
            phases = [row.get("phase") or "adjust" for row in group]
            unique_phases: List[str] = []
            for phase in phases:
                if not unique_phases or unique_phases[-1] != phase:
                    unique_phases.append(phase)
            label = "derisk_to_rerisk" if unique_phases == ["derisk", "rerisk"] else (unique_phases[-1] if unique_phases else "adjust")
            first_event = group[0]
            last_event = group[-1]
            current_value = _safe_float((last_event.get("post_snapshot") or {}).get("total_value_hkd")) or 0.0
            previous_value = _safe_float((first_event.get("pre_snapshot") or {}).get("total_value_hkd")) or current_value
            gross_sell_hkd = round(sum(_safe_float(row.get("gross_sell_hkd")) or 0.0 for row in group), 2)
            gross_buy_hkd = round(sum(_safe_float(row.get("gross_buy_hkd")) or 0.0 for row in group), 2)
            net_cash_hkd = round(gross_sell_hkd - gross_buy_hkd, 2)
            batch_changes: Dict[str, List[Dict[str, Any]]] = {"sold": [], "reduced": [], "added": [], "increased": []}
            for event in group:
                event_changes = event.get("changes") or {}
                for bucket in batch_changes:
                    batch_changes[bucket].extend([dict(row) for row in event_changes.get(bucket) or []])
            counterfactuals = self._default_counterfactual_pack(
                current_value,
                hold_value=previous_value,
                cash_value=previous_value + net_cash_hkd,
            )
            fallback_decision_summary = self._decision_summary_for_counterfactuals(counterfactuals, unique_phases)
            decision_attribution = self._build_decision_attribution_for_batch(group_week_ids, decision_events or [])
            batch_decision_events = self._decision_events_for_batch(group_week_ids, decision_events or [])
            decision_summary = self._decision_summary_for_batch_attribution(decision_attribution, fallback_decision_summary)
            stage_breakdown = {
                "sell_judgment_hkd": round(sum(_safe_float((row.get("stage_breakdown") or {}).get("sell_judgment_hkd")) or 0.0 for row in group), 2),
                "cash_idle_hkd": round(sum(_safe_float((row.get("stage_breakdown") or {}).get("cash_idle_hkd")) or 0.0 for row in group), 2),
                "cash_idle_ratio": 0.0,
                "rebuy_quality_hkd": round(sum(_safe_float((row.get("stage_breakdown") or {}).get("rebuy_quality_hkd")) or 0.0 for row in group), 2),
            }
            batches.append(
                {
                    "batch_id": f"batch:{group_week_ids[0]}:{group_week_ids[-1]}",
                    "first_week_id": group_week_ids[0],
                    "last_week_id": group_week_ids[-1],
                    "week_ids": group_week_ids,
                    "event_count": len(group),
                    "phase_sequence": unique_phases,
                    "batch_label": label,
                    "current_value_diff_hkd": decision_attribution.get("net_result_hkd") if decision_attribution.get("event_count") else round(current_value - previous_value, 2),
                    "portfolio_value_diff_hkd": round(current_value - previous_value, 2),
                    "decision_attribution": decision_attribution,
                    "gross_sell_hkd": gross_sell_hkd,
                    "gross_buy_hkd": gross_buy_hkd,
                    "net_cash_hkd": net_cash_hkd,
                    "changes": batch_changes,
                    "counterfactuals": counterfactuals,
                    "counterfactual_timeline": [self._timeline_for_counterfactuals(row["week_id"], row["counterfactuals"])[0] for row in group],
                    "path_attribution": self._build_path_attribution_for_batch(group_week_ids, decision_events or []),
                    "decision_events": batch_decision_events,
                    "decision_summary": decision_summary,
                    "stage_breakdown": stage_breakdown,
                    "primary_issue": decision_summary["weakest_stage_key"],
                    "funding_ancestors": group[-1].get("funding_ancestors") or {},
                }
            )
        event_week_ids = {str(row.get("week_id") or "").strip() for row in adjustment_events}
        for week_id in week_ids or []:
            if week_id in event_week_ids:
                continue
            review = self.storage.get_weekly_review(week_id) or {}
            if not review or review.get("rebalancing_ops"):
                continue
            batches.append(self._build_no_trade_checkpoint(week_id, review))
        batches.sort(key=lambda row: _week_sort_key(row["last_week_id"]), reverse=True)
        return batches

    def _build_stage_summary(self, adjustment_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "sell_judgment": {
                "negative_count": sum(1 for row in adjustment_events if (_safe_float((row.get("stage_breakdown") or {}).get("sell_judgment_hkd")) or 0.0) < 0),
                "negative_total": round(sum(abs(_safe_float((row.get("stage_breakdown") or {}).get("sell_judgment_hkd")) or 0.0) for row in adjustment_events if (_safe_float((row.get("stage_breakdown") or {}).get("sell_judgment_hkd")) or 0.0) < 0), 2),
            },
            "cash_redeployment": {
                "idle_count": sum(1 for row in adjustment_events if (_safe_float((row.get("stage_breakdown") or {}).get("cash_idle_hkd")) or 0.0) > 0),
                "idle_cash_total": round(sum(_safe_float((row.get("stage_breakdown") or {}).get("cash_idle_hkd")) or 0.0 for row in adjustment_events), 2),
            },
            "re_risk_quality": {
                "negative_count": sum(1 for row in adjustment_events if (_safe_float((row.get("stage_breakdown") or {}).get("rebuy_quality_hkd")) or 0.0) < 0),
                "negative_total": round(sum(abs(_safe_float((row.get("stage_breakdown") or {}).get("rebuy_quality_hkd")) or 0.0) for row in adjustment_events if (_safe_float((row.get("stage_breakdown") or {}).get("rebuy_quality_hkd")) or 0.0) < 0), 2),
            },
        }

    def _build_integrity_audit(self, week_ids: List[str], cross_week_pairings: Dict[Tuple[str, int], List[Dict[str, Any]]]) -> Dict[str, Any]:
        blocked: List[Dict[str, Any]] = []
        blocking_week_counts: Dict[str, int] = {}
        blocking_sell_counts: Dict[Tuple[str, str, str], int] = {}
        for week_id in sorted(week_ids, key=_week_sort_key):
            review = self.storage.get_weekly_review(week_id) or {}
            ops = [self._normalize_trim_reallocation_op(op) for op in (review.get("rebalancing_ops") or []) if isinstance(op, dict)]
            buy_ops = [op for op in ops if self._is_buy_like_op(op.get("op_type"))]
            if not buy_ops:
                continue
            blocking_pairs = [
                ((sell_week_id, sell_index), pair)
                for (sell_week_id, sell_index), pairs in cross_week_pairings.items()
                for pair in pairs
                if pair.get("buy_week_id") == week_id and sell_week_id != week_id
            ]
            if not blocking_pairs:
                continue
            for index, op in enumerate(ops):
                if not self._is_sell_like_op(op.get("op_type")):
                    continue
                same_week_pairs = cross_week_pairings.get((week_id, index)) or []
                if same_week_pairs:
                    continue
                blocking_sells = []
                for (sell_week_id, sell_index), pair in blocking_pairs:
                    sell_review = self.storage.get_weekly_review(sell_week_id) or {}
                    sell_ops = [self._normalize_trim_reallocation_op(row) for row in (sell_review.get("rebalancing_ops") or []) if isinstance(row, dict)]
                    sell_op = sell_ops[sell_index] if 0 <= sell_index < len(sell_ops) else {}
                    blocking_sells.append({"week_id": sell_week_id, "stock_id": sell_op.get("stock_id"), "sell_date": sell_op.get("date"), "amount_hkd": pair.get("amount")})
                    blocking_week_counts[sell_week_id] = blocking_week_counts.get(sell_week_id, 0) + 1
                    sell_key = (sell_week_id, str(sell_op.get("stock_id") or ""), str(sell_op.get("date") or ""))
                    blocking_sell_counts[sell_key] = blocking_sell_counts.get(sell_key, 0) + 1
                blocked.append(
                    {
                        "week_id": week_id,
                        "stock_id": op.get("stock_id"),
                        "sell_date": op.get("date"),
                        "future_buys": [{"week_id": week_id, "stock_id": buy.get("stock_id"), "buy_date": buy.get("date")} for buy in buy_ops],
                        "blocking_sells": blocking_sells,
                    }
                )
        return {
            "summary": {
                "malformed_op_count": 0,
                "future_buy_unmatched_count": 0,
                "blocked_by_prior_sell_count": len(blocked),
                "residual_future_buy_unmatched_count": 0,
            },
            "malformed_ops": [],
            "future_buy_unmatched": [],
            "blocked_by_prior_sells": blocked,
            "cash_chain_summary": {
                "top_blocking_weeks": [
                    {"week_id": week_id, "blocked_event_count": count}
                    for week_id, count in sorted(blocking_week_counts.items(), key=lambda item: item[1], reverse=True)
                ],
                "top_blocking_sells": [
                    {"week_id": week_id, "stock_id": stock_id, "sell_date": sell_date, "blocked_event_count": count}
                    for (week_id, stock_id, sell_date), count in sorted(blocking_sell_counts.items(), key=lambda item: item[1], reverse=True)
                ],
            },
        }

    def build_decision_review_index(
        self,
        limit: int = 10,
        stock_id: Optional[str] = None,
        decision_type: Optional[str] = None,
        window_mode: Optional[str] = None,
        mark_horizon: Optional[str] = None,
    ) -> Dict[str, Any]:
        selected_window_mode = "auto" if str(window_mode or "").strip().lower() == "auto" else "single_week"
        selected_mark_horizon = self._decision_review_horizon_key(mark_horizon)
        selected_suffix = self._decision_review_suffix_for_horizon(selected_mark_horizon)
        week_ids = list(self.storage.get_weekly_review_history(limit=10_000))
        current_date = datetime.now().strftime("%Y-%m-%d")
        cross_week_pairings = self._build_cross_week_cash_ledger_pairings(week_ids)
        funding_ancestor_index = self._build_funding_ancestor_index(cross_week_pairings)
        price_lookup_by_week, global_price_lookup = self._build_decision_review_price_context(week_ids, current_date)
        adjustment_events = self._build_adjustment_events(
            week_ids,
            cross_week_pairings,
            funding_ancestor_index=funding_ancestor_index,
        )
        events = self._load_decision_review_events(
            week_ids=week_ids,
            cross_week_pairings=cross_week_pairings,
            current_date=current_date,
            price_lookup_by_week=price_lookup_by_week,
            global_price_lookup=global_price_lookup,
        )
        normalized = self._dedupe_decision_review_events([self._normalize_decision_review_event(event) for event in events])
        filtered_base = self._filter_decision_review_events(normalized, stock_id=stock_id, decision_type=decision_type)
        filtered = [self._with_selected_decision_review_derivatives(event, selected_suffix) for event in filtered_base]
        decision_batches = self._build_decision_batches(
            adjustment_events,
            week_ids=week_ids,
            decision_events=filtered,
            merge_adjacent_derisk_rerisk=selected_window_mode == "auto",
        )
        all_windows = self._build_counterfactual_windows(decision_batches, current_date, price_lookup_by_week)
        decision_windows = [window for window in all_windows if window.get("data_state") != "checkpoint"]
        checkpoint_windows = [window for window in all_windows if window.get("data_state") == "checkpoint"]
        return {
            "selected_window_mode": selected_window_mode,
            "available_window_modes": ["single_week", "auto"],
            "selected_mark_horizon": selected_mark_horizon,
            "available_mark_horizons": ["30d", "60d", "90d", "now"],
            "summary": self._summarize_decision_review(filtered, suffix=selected_suffix),
            "all_events": filtered,
            "top_mistakes": self._rank_top_decision_review_mistakes(filtered, limit=limit, suffix=selected_suffix),
            "top_effective": self._rank_top_decision_review_effective(filtered, limit=limit, suffix=selected_suffix),
            "by_stock": self._aggregate_decision_review_by_stock(filtered, suffix=selected_suffix),
            "by_decision_type": self._aggregate_decision_review_by_decision_type(filtered, suffix=selected_suffix),
            "by_redeployment_path": self._aggregate_decision_review_by_destination(filtered, suffix=selected_suffix),
            "trim_follow_through": self._aggregate_trim_follow_through(filtered, suffix=selected_suffix),
            "pattern_mining": self._build_decision_review_pattern_mining(filtered, suffix=selected_suffix),
            "by_week": self._aggregate_decision_review_by_week(filtered, suffix=selected_suffix),
            "counterfactual_windows": decision_windows,
            "checkpoint_windows": checkpoint_windows,
            "all_counterfactual_windows": all_windows,
            "decision_batches": decision_batches,
            "adjustment_events": adjustment_events,
            "stage_summary": self._build_stage_summary(adjustment_events),
            "integrity_audit": self._build_integrity_audit(week_ids, cross_week_pairings),
            "selected_counterfactual_label": "benchmark",
        }

    def _load_cached_close_prices(
        self,
        stock_ids: set[str],
        dates_needed: set[str],
    ) -> Dict[Tuple[str, str], float]:
        db_path = self.storage.base_dir / "market_history_cache.sqlite3"
        if not db_path.exists() or not stock_ids or not dates_needed:
            return {}
        result: Dict[Tuple[str, str], float] = {}
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            for raw_stock_id in sorted(stock_ids):
                ticker = str(raw_stock_id or "").strip().upper()
                if not ticker:
                    continue
                for raw_date in sorted(dates_needed):
                    target_date = str(raw_date or "").strip()[:10]
                    if not target_date:
                        continue
                    row = conn.execute(
                        """
                        SELECT close
                        FROM daily_ohlcv
                        WHERE ticker = ? AND trade_date <= ? AND close IS NOT NULL
                        ORDER BY trade_date DESC
                        LIMIT 1
                        """,
                        (ticker, target_date),
                    ).fetchone()
                    if row and row[0] not in (None, ""):
                        result[(raw_stock_id, target_date)] = float(row[0])
        except Exception:
            logger.exception("failed to load local OHLCV prices for decision review")
        finally:
            if conn is not None:
                conn.close()
        return result

    def _build_trim_price_lookup(
        self,
        week_id: str,
        review: Dict[str, Any],
        current_date: str,
    ) -> Dict[Tuple[str, str], float]:
        normalized_ops = [
            self._normalize_trim_reallocation_op(op)
            for op in (review.get("rebalancing_ops") or [])
            if isinstance(op, dict)
        ]
        if not normalized_ops:
            return {}

        week_end = _week_end_date_str(week_id)
        dates_needed = {week_end, current_date}
        stock_ids: set[str] = set()
        price_lookup: Dict[Tuple[str, str], float] = {}
        explicit_trade_price_keys: set[Tuple[str, str]] = set()

        for op in normalized_ops:
            stock_id = str(op.get("stock_id") or "").strip()
            op_date = str(op.get("date") or "").strip()
            op_price = _safe_float(op.get("price"))
            if stock_id:
                stock_ids.add(stock_id)
            if stock_id and op_date:
                dates_needed.add(op_date)
                mark_30d_date = _date_plus_days_str(op_date, 30)
                if mark_30d_date:
                    dates_needed.add(mark_30d_date)
                mark_60d_date = _date_plus_days_str(op_date, 60)
                if mark_60d_date:
                    dates_needed.add(mark_60d_date)
                mark_90d_date = _date_plus_days_str(op_date, 90)
                if mark_90d_date:
                    dates_needed.add(mark_90d_date)
                if op_price and op_price > 0:
                    price_lookup[(stock_id, op_date)] = op_price
                    explicit_trade_price_keys.add((stock_id, op_date))
            for pair in (op.get("paired_buys") or []):
                pair_stock_id = str((pair or {}).get("stock_id") or "").strip()
                if pair_stock_id:
                    stock_ids.add(pair_stock_id)
                pair_entry_date = _normalize_date_text((pair or {}).get("entry_date") or (pair or {}).get("buy_date"))
                mark_30d_date = _date_plus_days_str(pair_entry_date, 30)
                if mark_30d_date:
                    dates_needed.add(mark_30d_date)
                mark_60d_date = _date_plus_days_str(pair_entry_date, 60)
                if mark_60d_date:
                    dates_needed.add(mark_60d_date)
                mark_90d_date = _date_plus_days_str(pair_entry_date, 90)
                if mark_90d_date:
                    dates_needed.add(mark_90d_date)
            benchmark_id = str(op.get("benchmark") or self._default_benchmark_for_op(op)).strip().upper()
            if benchmark_id:
                stock_ids.add(benchmark_id)

        for op in normalized_ops:
            if not self._is_sell_like_op(op.get("op_type")):
                continue
            mark_30d_date = _date_plus_days_str(str(op.get("date") or "").strip(), 30)
            if mark_30d_date:
                dates_needed.add(mark_30d_date)
            mark_60d_date = _date_plus_days_str(str(op.get("date") or "").strip(), 60)
            if mark_60d_date:
                dates_needed.add(mark_60d_date)
            mark_90d_date = _date_plus_days_str(str(op.get("date") or "").strip(), 90)
            if mark_90d_date:
                dates_needed.add(mark_90d_date)

        review_stocks = (review.get("stocks") or {}) if isinstance(review, dict) else {}
        alias_groups: Dict[str, set[str]] = {}
        stock_list = list(self.storage.list_stocks() or [])
        for row_key, row_payload in review_stocks.items():
            payload = row_payload if isinstance(row_payload, dict) else {}
            aliases = {
                str(row_key or "").strip(),
                str(payload.get("ticker") or "").strip(),
                str(payload.get("stock_name") or "").strip(),
                str(payload.get("search_name") or "").strip(),
            } - {""}
            for item in stock_list:
                item_aliases = {
                    str(item.get("stock_id") or "").strip(),
                    str(item.get("ticker") or "").strip(),
                    str(item.get("stock_name") or "").strip(),
                    str(item.get("search_name") or "").strip(),
                } - {""}
                if any(self.storage._canonical_code(alias) == self.storage._canonical_code(item_alias) for alias in aliases for item_alias in item_aliases):
                    aliases.update(item_aliases)
            for alias in list(aliases):
                alias_groups.setdefault(self.storage._canonical_code(alias), set()).update(aliases)

        def aliases_for(stock_id: str) -> set[str]:
            raw = str(stock_id or "").strip()
            if not raw:
                return set()
            return set(alias_groups.get(self.storage._canonical_code(raw), {raw}))

        def review_payload_for(stock_id: str) -> Dict[str, Any]:
            for alias in aliases_for(stock_id):
                payload = review_stocks.get(alias)
                if isinstance(payload, dict):
                    return payload
            payload = review_stocks.get(stock_id)
            return payload if isinstance(payload, dict) else {}

        def set_price_for_aliases(stock_id: str, date_text: str, price: float) -> None:
            target_date = str(date_text or "").strip()[:10]
            if not target_date or not price:
                return
            for alias in aliases_for(stock_id) or {stock_id}:
                price_lookup.setdefault((alias, target_date), price)

        for stock_id in stock_ids:
            stock_payload = review_payload_for(stock_id)
            perf = (stock_payload.get("performance_data") or {}) if isinstance(stock_payload, dict) else {}
            start_price = _safe_float(perf.get("start_price"))
            end_price = _safe_float(perf.get("end_price"))
            start_date = str(perf.get("start_date") or "").strip()[:10]
            end_date = str(perf.get("end_date") or "").strip()[:10]
            if start_price and not start_date:
                op_dates = [
                    str((op or {}).get("date") or "").strip()[:10]
                    for op in normalized_ops
                    if str((op or {}).get("stock_id") or "").strip() in aliases_for(stock_id)
                ]
                start_date = next((date for date in op_dates if date), "")
            if end_price and not end_date:
                end_date = current_date or week_end
            if start_price and start_date:
                set_price_for_aliases(stock_id, start_date, start_price)
            if end_price and end_date:
                set_price_for_aliases(stock_id, end_date, end_price)
                # Weekly review page must stay fast: reuse the latest cached review price
                # instead of doing blocking live price fetches during page render/API load.
                for target_date in sorted(dates_needed):
                    if target_date and target_date >= end_date:
                        set_price_for_aliases(stock_id, target_date, end_price)
                if week_end and week_end not in (end_date, current_date):
                    set_price_for_aliases(stock_id, week_end, end_price)
                if current_date and current_date not in (end_date, week_end):
                    set_price_for_aliases(stock_id, current_date, end_price)

        position_ids = {
            str(row.get("stock_id") or row.get("ticker") or "").strip()
            for row in self._position_rows_from_review(review)
            if str(row.get("stock_id") or row.get("ticker") or "").strip()
        }
        for stock_id in position_ids:
            stock_ids.add(stock_id)
            stock_payload = review_payload_for(stock_id)
            perf = (stock_payload.get("performance_data") or {}) if isinstance(stock_payload, dict) else {}
            end_price = _safe_float(perf.get("end_price"))
            if end_price:
                set_price_for_aliases(stock_id, week_end, end_price)
                set_price_for_aliases(stock_id, current_date, end_price)
            mark_dates = [
                mark_date
                for op in normalized_ops
                if self._is_sell_like_op(op.get("op_type"))
                for mark_date in (
                    _date_plus_days_str(str(op.get("date") or "").strip(), 30),
                    _date_plus_days_str(str(op.get("date") or "").strip(), 60),
                    _date_plus_days_str(str(op.get("date") or "").strip(), 90),
                )
            ]
            for mark_date in mark_dates:
                if mark_date and end_price:
                    set_price_for_aliases(stock_id, mark_date, end_price)

        cached_prices = self._load_cached_close_prices(stock_ids, dates_needed)
        for key, value in cached_prices.items():
            if key in explicit_trade_price_keys:
                continue
            price_lookup[key] = value

        return price_lookup

    def attach_trim_reallocation_analysis(self, week_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(review or {})
        current_date = datetime.now().strftime("%Y-%m-%d")
        try:
            price_lookup = self._build_trim_price_lookup(week_id=week_id, review=payload, current_date=current_date)
            payload["trim_reallocation_analysis"] = self.build_trim_reallocation_analysis(
                week_id=week_id,
                review=payload,
                price_lookup=price_lookup,
                current_date=current_date,
            )
        except Exception as exc:
            logger.exception("attach_trim_reallocation_analysis failed for week_id=%s", week_id)
            payload["trim_reallocation_analysis"] = {
                "summary": {"error": str(exc)},
                "stocks": [],
                "events": [],
            }
        return payload

    def attach_decision_attribution_analysis(self, week_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(review or {})
        current_date = datetime.now().strftime("%Y-%m-%d")
        try:
            price_lookup = self._build_trim_price_lookup(week_id=week_id, review=payload, current_date=current_date)
            payload["decision_attribution_analysis"] = self._build_decision_attribution_analysis(
                week_id=week_id,
                review=payload,
                price_lookup=price_lookup,
                current_date=current_date,
            )
        except Exception as exc:
            logger.exception("attach_decision_attribution_analysis failed for week_id=%s", week_id)
            payload["decision_attribution_analysis"] = {
                "summary": {"error": str(exc)},
                "patterns": {},
                "stocks": [],
                "events": [],
            }
        return payload

    # ------------------------------------------------------------------
    # LLM Text
    # ------------------------------------------------------------------

    def _safe_llm_chat(
        self,
        prompt: str,
        max_retries: int = 4,
        force_refresh: bool = False,
        timeout_sec: Optional[float] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Text LLM, Text (result, error). AutoTextError(503Text), Text HTML Text, Text. """
        last_error = None
        retry_delays = [2, 6, 15, 30]
        for attempt in range(1, max_retries + 1):
            try:
                import inspect
                sig = inspect.signature(self.client.chat)
                kwargs: Dict[str, Any] = {}
                if 'force_refresh' in sig.parameters:
                    kwargs['force_refresh'] = force_refresh
                if timeout_sec is not None and 'timeout_sec' in sig.parameters:
                    kwargs['timeout_sec'] = timeout_sec
                result = self.client.chat(prompt, **kwargs) if kwargs else self.client.chat(prompt)
            except Exception as e:
                logger.warning("_safe_llm_chat attempt %d/%d exception: %s", attempt, max_retries, e)
                last_error = f"LLM Text: {e}"
                if attempt < max_retries:
                    time.sleep(retry_delays[min(attempt - 1, len(retry_delays) - 1)])
                continue
            result = str(result or "").strip()
            if not result:
                last_error = "Text"
                if attempt < max_retries:
                    time.sleep(3 * attempt)
                continue
            if _looks_like_html_document(result):
                logger.warning("_safe_llm_chat attempt %d/%d got HTML response (first 200 chars): %s", attempt, max_retries, result[:200])
                last_error = "Text HTML Text"
                if attempt < max_retries:
                    time.sleep(3 * attempt)
                continue
            if result.startswith("TextFailed"):
                logger.warning("_safe_llm_chat attempt %d/%d failure: %s", attempt, max_retries, result[:300])
                last_error = result
                if attempt < max_retries:
                    time.sleep(3 * attempt)
                continue
            return result, None
        return None, last_error

    def _collect_macro_event_candidates(self, days: int = 7) -> List[Dict[str, Any]]:
        queries = [
            "Trump address to the nation tariffs OR Iran OR trade",
            "White House address to the nation Trump Wednesday 9 p.m.",
            "Federal Reserve tariffs inflation recession Reuters",
            "China retaliatory tariffs United States Reuters",
            "global trade war tariffs Reuters AP Bloomberg",
            "OPEC oil output geopolitical Reuters",
            "Ukraine Russia major escalation Reuters",
            "global markets biggest macro events this week Reuters",
        ]
        collected: List[Dict[str, Any]] = []
        seen = set()
        for query in queries:
            try:
                for item in _google_news_macro_search(query, days=days, limit=6):
                    title_key = _normalize_macro_title(item.get("title") or "")
                    if not title_key or title_key in seen:
                        continue
                    seen.add(title_key)
                    score = 0
                    haystack = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
                    if "address to the nation" in haystack:
                        score += 8
                    if "speech" in haystack or "remarks" in haystack:
                        score += 3
                    if "trump" in haystack:
                        score += 4
                    if "tariff" in haystack or "trade war" in haystack:
                        score += 5
                    if "fed" in haystack or "federal reserve" in haystack:
                        score += 4
                    if "china" in haystack and "tariff" in haystack:
                        score += 4
                    if any(src in (item.get("source") or "").lower() for src in ("reuters", "associated press", "ap news", "bloomberg")):
                        score += 2
                    row = dict(item)
                    row["score"] = score
                    collected.append(row)
            except Exception:
                logger.debug("macro candidate search failed for query=%s", query, exc_info=True)
        collected.sort(key=lambda row: (int(row.get("score") or 0), str(row.get("published_at") or "")), reverse=True)
        return collected[:24]

    # ------------------------------------------------------------------
    # Text: This WeekText(Text)
    # ------------------------------------------------------------------

    def refresh_macro_events(self, week_id: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
        """??????/??/??????"""
        if week_id is None:
            week_id = get_week_id()

        # ??????? LLM ?????
        stocks = self.storage.list_stocks()
        stock_lines = []
        for s in stocks:
            sid = s.get("stock_id", "")
            pb = self.storage.get_stock_playbook(sid) or {}
            ticker = pb.get("ticker", "") or sid
            name = pb.get("stock_name", sid) or s.get("stock_name", sid)
            industry = pb.get("industry", "") or ""
            stock_lines.append(f"- {sid} (ticker: {ticker}, ??: {name}, ??: {industry})")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        date_range = f"{start_date.strftime('%Y-%m-%d')} ? {end_date.strftime('%Y-%m-%d')}"
        candidates = self._collect_macro_event_candidates(days=days)
        candidate_lines = []
        for idx, item in enumerate(candidates, 1):
            published = str(item.get("published_at") or "").replace("T", " ")[:19]
            candidate_lines.append(
                f"{idx}. [{published or '--'}] {item.get('title') or ''} | "
                f"source={item.get('source') or 'unknown'} | query={item.get('query') or ''}\n"
                f"summary={item.get('summary') or ''}"
            )

        if candidate_lines:
            prompt = f"""?????????????????????????????????? {date_range} ???????????????????????

?????
1. ???????????????????????????????????????/??????????????????????
2. ??????????????????????
3. ????????????????
4. ???? 8 ??????????
5. affected_stocks ??? stock_id?? "NVDA"?"CORNING"????? ticker
6. importance ??? "high"?"medium"?"low"
7. category ?????????????????????????????????????
8. date ???????????? YYYY-MM-DD
9. source ???????????
10. why_it_matters TextThis WeekText
11. impact_direction Text risk-on/risk-off/Text/Text/Text
12. related_assets Text, Text semiconductors, China tech, oil, rates
13. ?????? JSON ?????? markdown ?????

???????
{chr(10).join(stock_lines)}

???????
{chr(10).join(candidate_lines)}

????? JSON?
{{"events": [{{"date": "2026-04-01", "title": "????", "source": "Reuters", "category": "??", "summary": "2-3????", "why_it_matters": "Text", "impact_direction": "Text", "related_assets": ["rates", "semiconductors"], "portfolio_impact": "??????????", "affected_stocks": ["STOCK_ID1", "STOCK_ID2"], "importance": "high"}}]}}"""
        else:
            prompt = f"""????????????????? {date_range} ???????????????????????

???
1. ?????????????????????? 8 ????????
2. ???????????????
3. affected_stocks ??? stock_id?? "NVDA"?"CORNING"????? ticker
4. importance ??? "high"?"medium"?"low"
5. category ???????????????????????/??????????/??
6. why_it_matters / impact_direction / related_assets Text, Text big picture
7. ?????? JSON ?????? markdown ?????

???????
{chr(10).join(stock_lines)}

????? JSON?
{{"events": [{{"title": "????", "category": "??", "summary": "2-3????", "why_it_matters": "Text", "impact_direction": "Text", "related_assets": ["rates", "China tech"], "portfolio_impact": "??????????", "affected_stocks": ["STOCK_ID1", "STOCK_ID2"], "importance": "high/medium/low"}}]}}"""

        result_text, error = self._safe_llm_chat(
            prompt,
            max_retries=1,
            timeout_sec=25,
        )
        now_str = datetime.now().isoformat()

        if error:
            logger.warning("refresh_macro_events LLM failed: %s", error)
            existing_review = self.storage.get_weekly_review(week_id) or {}
            existing_macro = existing_review.get("macro_events") or {}
            existing_events = existing_macro.get("events") or []
            if existing_events and _is_transient_llm_error(error):
                top_events = existing_macro.get("top_events") or self._build_macro_top_events(existing_events)
                degraded = {
                    "events": existing_events,
                    "top_events": top_events,
                    "refreshed_at": str(existing_macro.get("refreshed_at") or now_str),
                    "error": None,
                    "candidate_count": len(candidates),
                    "degraded": True,
                    "warning": f"TextRefreshText, TextResult: {error}",
                }
                self.storage.update_weekly_macro_events(
                    week_id,
                    {
                        "events": existing_events,
                        "top_events": top_events,
                        "refreshed_at": str(existing_macro.get("refreshed_at") or now_str),
                        "error": None,
                        "candidate_count": len(candidates),
                    },
                )
                return {"success": True, "week_id": week_id, **degraded}
            macro_data = {"events": [], "top_events": [], "refreshed_at": now_str, "error": error, "candidate_count": len(candidates)}
            self.storage.update_weekly_macro_events(week_id, macro_data)
            return {"success": False, "week_id": week_id, **macro_data}

        # ?? JSON
        events = self._parse_macro_events_json(result_text)
        if not events and result_text:
            parse_error = "TextFailed: LLM Text JSON. "
            logger.warning("refresh_macro_events: LLM returned text but JSON parse failed")
            macro_data = {"events": [], "top_events": [], "refreshed_at": now_str, "error": parse_error, "candidate_count": len(candidates)}
            self.storage.update_weekly_macro_events(week_id, macro_data)
            return {"success": False, "week_id": week_id, **macro_data}
        macro_data = {
            "events": events,
            "top_events": self._build_macro_top_events(events),
            "refreshed_at": now_str,
            "error": None,
            "candidate_count": len(candidates),
        }
        self.storage.update_weekly_macro_events(week_id, macro_data)
        return {"success": True, "week_id": week_id, **macro_data}

    def _build_macro_top_events(self, events: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        """Normalize macro events into a compact reference list for the weekly review."""
        importance_rank = {"high": 3, "medium": 2, "low": 1}

        def rank(item: Dict[str, Any]) -> Tuple[int, str]:
            importance = str(item.get("importance") or "").strip().lower()
            return (importance_rank.get(importance, 0), str(item.get("date") or item.get("published_at") or ""))

        rows: List[Dict[str, Any]] = []
        for item in sorted((events or []), key=rank, reverse=True):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            related_assets = item.get("related_assets")
            if not isinstance(related_assets, list):
                related_assets = []
            affected = item.get("affected_stocks")
            if not isinstance(affected, list):
                affected = []
            rows.append(
                {
                    "date": str(item.get("date") or item.get("published_at") or "").strip()[:10],
                    "title": title,
                    "category": str(item.get("category") or "macro").strip() or "macro",
                    "importance": str(item.get("importance") or "medium").strip().lower() or "medium",
                    "why_it_matters": str(item.get("why_it_matters") or item.get("summary") or "").strip(),
                    "impact_direction": str(item.get("impact_direction") or item.get("portfolio_impact") or "").strip(),
                    "related_assets": [str(value).strip() for value in related_assets + affected if str(value).strip()][:6],
                    "source": str(item.get("source") or "").strip(),
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def _parse_macro_events_json(self, text: str) -> List[Dict[str, Any]]:
        """Text LLM Text JSON"""
        # Text
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "events" in data:
                return data["events"]
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        # Text markdown TickerText
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict) and "events" in data:
                    return data["events"]
            except json.JSONDecodeError:
                pass

        # Text { Text }
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace >= 0 and last_brace > first_brace:
            try:
                data = json.loads(text[first_brace:last_brace + 1])
                if isinstance(data, dict) and "events" in data:
                    return data["events"]
            except json.JSONDecodeError:
                pass

        logger.warning("Failed to parse macro events JSON from LLM response")
        return []

    # ------------------------------------------------------------------
    # Text: NewsText
    # ------------------------------------------------------------------

    def _filter_news_by_relevance(self, week_id: Optional[str] = None) -> Dict[str, Any]:
        """TextStockTextNewsText LLM Text, Text is_relevant Text"""
        if week_id is None:
            week_id = get_week_id()
        review = self.get_or_create_review(week_id)
        stocks_data = review.get("stocks") or {}

        # TextNews
        all_news_items = []  # (stock_id, news_index, title, snippet)
        cached_relevance_count = 0
        cached_filtered_count = 0
        stock_context = []   # StockText

        for stock_id, sdata in stocks_data.items():
            news = sdata.get("news") or []
            if not news:
                continue
            pb = self.storage.get_stock_playbook(stock_id) or {}
            ticker = pb.get("ticker", "") or stock_id
            name = pb.get("stock_name", stock_id) or sdata.get("stock_name", stock_id)
            core_biz = ((pb.get("core_thesis") or {}).get("summary") or "")[:80]
            stock_context.append({"stock_id": stock_id, "name": name, "ticker": ticker, "core_biz": core_biz})

            for idx, n in enumerate(news):
                if not isinstance(n, dict):
                    continue
                if isinstance(n.get("is_relevant"), bool):
                    cached_relevance_count += 1
                    if not n.get("is_relevant"):
                        cached_filtered_count += 1
                    continue
                title = str(n.get("title", ""))[:100]
                snippet = str(n.get("summary", ""))[:100]
                if not title:
                    continue
                all_news_items.append({
                    "id": f"{stock_id}_{idx}",
                    "stock_id": stock_id,
                    "idx": idx,
                    "title": title,
                    "snippet": snippet,
                })

        if not all_news_items:
            return {
                "success": True,
                "filtered": cached_filtered_count,
                "total": cached_relevance_count,
                "cached": cached_relevance_count,
            }

        # Text(Text 50 Text)
        BATCH_SIZE = 50
        all_results = {}
        for batch_start in range(0, len(all_news_items), BATCH_SIZE):
            batch = all_news_items[batch_start:batch_start + BATCH_SIZE]
            batch_results = self._filter_news_batch(batch, stock_context)
            all_results.update(batch_results)

        # Text is_relevant Text
        filtered_count = cached_filtered_count
        for stock_id, sdata in stocks_data.items():
            news = sdata.get("news") or []
            for idx, n in enumerate(news):
                if not isinstance(n, dict):
                    continue
                key = f"{stock_id}_{idx}"
                if key in all_results:
                    n["is_relevant"] = all_results[key]
                    if not all_results[key]:
                        filtered_count += 1

        # SaveTextNewsText
        for stock_id, sdata in stocks_data.items():
            news = sdata.get("news") or []
            if news:
                self.storage.update_stock_weekly_data(
                    week_id=week_id,
                    stock_id=stock_id,
                    stock_name=sdata.get("stock_name", stock_id),
                    news=news,
                )

        return {
            "success": True,
            "filtered": filtered_count,
            "total": len(all_news_items) + cached_relevance_count,
            "cached": cached_relevance_count,
        }

    def _filter_news_batch(self, batch: List[Dict], stock_context: List[Dict]) -> Dict[str, bool]:
        """TextNewsText LLM Text, Text {id: is_relevant}"""
        stock_lines = "\n".join(
            f"- {s['stock_id']}: {s['name']} ({s['ticker']}), {s['core_biz']}"
            for s in stock_context
        )
        news_lines = "\n".join(
            f"- id={item['id']}, stock={item['stock_id']}, title=\"{item['title']}\", snippet=\"{item['snippet']}\""
            for item in batch
        )

        prompt = f"""TextNewsTextStockText. TextNewsTextStockText, Text, Text, Text, Text, Text false. 

StockText: 
{stock_lines}

NewsText: 
{news_lines}

Text JSON Text, Text markdown TickerText: 
[{{"id": "...", "relevant": true/false}}]"""

        result_text, error = self._safe_llm_chat(prompt)
        if error:
            logger.warning("News relevance filter LLM failed: %s", error)
            return {}  # fallback: TextSettings is_relevant, TextNewsText

        # TextResult
        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError:
            # Text JSON Text
            match = re.search(r'\[.*\]', result_text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("Failed to parse news relevance JSON")
                    return {}
            else:
                return {}

        results = {}
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "id" in item:
                    results[item["id"]] = bool(item.get("relevant", True))
        return results

    def _proxy_notes(self) -> List[str]:
        return [f"{item['name']}: {item['proxy_note']}" for item in self.MARKET_SIGNAL_SPECS]

    def _signal_map(self, signals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {str(item.get("id") or ""): item for item in (signals or []) if item.get("id")}

    def _build_signal_tags(self, signals: List[Dict[str, Any]]) -> List[str]:
        by_id = self._signal_map(signals)
        tags: List[str] = []

        def cp(symbol: str) -> Optional[float]:
            value = (by_id.get(symbol) or {}).get("change_pct")
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        spy = cp("SPY")
        qqq = cp("QQQ")
        vixy = cp("VIXY")
        soxx = cp("SOXX")
        igv = cp("IGV")
        kweb = cp("KWEB")
        tlt = cp("TLT")
        cper = cp("CPER")
        uso = cp("USO")

        if spy is not None and qqq is not None and vixy is not None:
            if spy > 0 and qqq > 0 and vixy <= 0:
                tags.append("risk-on")
            if (spy < 0 or qqq < 0) and vixy > 0:
                tags.append("risk-off")
            if qqq - spy >= 1.0:
                tags.append("growth leadership")
        if soxx is not None and igv is not None and soxx + 0.8 < igv:
            tags.append("semis lagging")
        if kweb is not None and qqq is not None and kweb + 1.0 < qqq:
            tags.append("China tech weak")
        if tlt is not None and tlt < -1.0:
            tags.append("rates headwind")
        if cper is not None and cper > 0 and soxx is not None and soxx > 0:
            tags.append("cyclical support")
        if uso is not None and cper is not None and uso - cper >= 1.0:
            tags.append("commodity pressure")

        seen: List[str] = []
        for tag in tags:
            if tag not in seen:
                seen.append(tag)
        return seen

    def _build_market_big_picture(self, signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a compact market map: regime, grouped signals, and one-line context."""
        by_id = self._signal_map([item for item in (signals or []) if item.get("success")])

        def cp(symbol: str) -> Optional[float]:
            value = (by_id.get(symbol) or {}).get("change_pct")
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        spy = cp("SPY")
        qqq = cp("QQQ")
        vixy = cp("VIXY")
        if spy is not None and qqq is not None and vixy is not None and spy > 0 and qqq > 0 and vixy <= 0:
            regime = "risk-on"
            summary = "Text, Text, This WeekText risk-on. "
        elif spy is not None and qqq is not None and vixy is not None and (spy < 0 or qqq < 0) and vixy > 0:
            regime = "risk-off"
            summary = "Text, Text, This WeekText risk-off. "
        elif spy is not None and qqq is not None and abs(qqq - spy) >= 1.0:
            regime = "style-split"
            leader = "Text/Text" if qqq > spy else "Text/Text"
            summary = f"{leader}Text, Text, Text. "
        else:
            regime = "mixed"
            summary = "Text, This WeekText. "

        groups = [
            {
                "id": "broad_style",
                "label": "Text",
                "note": "TextRiskText. ",
                "signal_ids": ["SPY", "QQQ", "IXN", "FXI", "KWEB", "VIXY", "TLT"],
            },
            {
                "id": "key_industries",
                "label": "Text",
                "note": "Text, Text. ",
                "signal_ids": ["SOXX", "IGV", "XLI", "XLB", "IYT", "XLE"],
            },
            {
                "id": "macro_assets",
                "label": "Text",
                "note": "Text, Text. ",
                "signal_ids": ["TLT", "CPER", "USO", "GLD", "VIXY"],
            },
        ]

        shaped_groups = []
        for group in groups:
            shaped = []
            for signal_id in group["signal_ids"]:
                signal = by_id.get(signal_id)
                if not signal:
                    continue
                shaped.append(
                    {
                        "id": signal_id,
                        "name": signal.get("name") or signal_id,
                        "ticker": signal.get("ticker") or signal_id,
                        "change_pct": signal.get("change_pct"),
                        "proxy_note": signal.get("proxy_note") or "",
                        "read": self._market_signal_read(signal),
                    }
                )
            shaped_groups.append({key: value for key, value in group.items() if key != "signal_ids"} | {"signals": shaped[:6]})

        return {
            "regime": regime,
            "summary": summary,
            "groups": shaped_groups,
            "updated_at": datetime.now().isoformat(),
        }

    def _market_signal_read(self, signal: Dict[str, Any]) -> str:
        try:
            change = float(signal.get("change_pct"))
        except (TypeError, ValueError):
            return "This WeekNo dataText. "
        name = signal.get("name") or signal.get("ticker") or signal.get("id") or "Text"
        if change >= 2.0:
            return f"{name}Text, TextThis WeekText. "
        if change >= 0.5:
            return f"{name}Text, Text. "
        if change <= -2.0:
            return f"{name}Text, TextThis WeekText. "
        if change <= -0.5:
            return f"{name}Text, TextWatchText. "
        return f"{name}Text, Text. "

    def _build_portfolio_relevance(self, review: Dict[str, Any]) -> List[Dict[str, Any]]:
        stocks = review.get("stocks") or {}
        texts = []
        for stock_id, sdata in stocks.items():
            shares = sdata.get("shares_held")
            try:
                if shares is None or float(shares) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            texts.append(f"{stock_id} {(sdata.get('stock_name') or '')}".upper())

        scored = []
        for bucket in self.HOLDING_BUCKETS.values():
            score = 0
            for text in texts:
                score += sum(1 for kw in bucket["keywords"] if kw.upper() in text)
            if score > 0:
                scored.append({"label": bucket["label"], "score": score, "signals": bucket["signals"]})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:4]

    def _build_watch_items(self, signals: List[Dict[str, Any]], relevance: List[Dict[str, Any]]) -> List[str]:
        by_id = self._signal_map(signals)
        watch: List[str] = []
        if "SOXX" in by_id and "IGV" in by_id:
            watch.append("TextWatchText, Text, Text/TextHoldingsText. ")
        if "VIXY" in by_id and "TLT" in by_id:
            watch.append("Text; Text VIXY Text TLT Text, TextRiskText. ")
        if "KWEB" in by_id and "FXI" in by_id:
            watch.append("Text/Text; Text, TextRiskText. ")
        if "CPER" in by_id and "USO" in by_id:
            watch.append("Text, Text, Text, Text. ")
        if relevance:
            labels = ", ".join(item["label"] for item in relevance[:3])
            watch.append(f"TextCurrentText, Text: {labels}. ")
        return watch[:4]

    def get_market_context(self, week_id: Optional[str] = None) -> Dict[str, Any]:
        if week_id is None:
            week_id = get_week_id()
        review = self.get_or_create_review(week_id)
        market_context = review.get("market_context") or {}
        signals = market_context.get("signals") or []
        return {
            "week_id": week_id,
            "as_of_date": market_context.get("as_of_date"),
            "signals": signals,
            "big_picture": market_context.get("big_picture") or self._build_market_big_picture(signals),
            "ai_summary": market_context.get("ai_summary") or "",
            "portfolio_relevance": market_context.get("portfolio_relevance") or self._build_portfolio_relevance(review),
            "watch_items": market_context.get("watch_items") or [],
            "proxy_notes": market_context.get("proxy_notes") or self._proxy_notes(),
            "updated_at": market_context.get("updated_at"),
        }

    def refresh_market_context(self, week_id: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
        if week_id is None:
            week_id = get_week_id()
        review = self.get_or_create_review(week_id)
        signals: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        latest_dates: List[str] = []

        for spec in self.MARKET_SIGNAL_SPECS:
            result = ak_get_performance(spec["ticker"], days) if ak_get_performance else {"success": False, "error": "AKShare Text"}
            if result.get("success"):
                data = result.get("data") or {}
                tags: List[str] = []
                try:
                    cp = float(data.get("change_pct"))
                    if spec["id"] == "VIXY":
                        tags.append("risk-off" if cp > 0 else "risk-on")
                    elif cp >= 1.0:
                        tags.append("strong")
                    elif cp <= -1.0:
                        tags.append("weak")
                except (TypeError, ValueError):
                    pass
                signals.append(
                    {
                        "id": spec["id"],
                        "ticker": spec["ticker"],
                        "name": spec["name"],
                        "group": spec["group"],
                        "proxy_note": spec["proxy_note"],
                        "success": True,
                        "performance_summary": result.get("performance_summary") or "",
                        "change_pct": data.get("change_pct"),
                        "start_price": data.get("start_price"),
                        "end_price": data.get("end_price"),
                        "high": data.get("high"),
                        "low": data.get("low"),
                        "start_date": data.get("start_date"),
                        "end_date": data.get("end_date"),
                        "tags": tags,
                    }
                )
                if data.get("end_date"):
                    latest_dates.append(str(data.get("end_date")))
            else:
                err = str(result.get("error") or "unknown error")
                errors.append({"id": spec["id"], "ticker": spec["ticker"], "error": err})
                signals.append(
                    {
                        "id": spec["id"],
                        "ticker": spec["ticker"],
                        "name": spec["name"],
                        "group": spec["group"],
                        "proxy_note": spec["proxy_note"],
                        "success": False,
                        "error": err,
                        "tags": [],
                    }
                )

        global_tags = self._build_signal_tags(signals)
        for item in signals:
            if item.get("success"):
                item["tags"] = list(dict.fromkeys((item.get("tags") or []) + global_tags))

        relevance = self._build_portfolio_relevance(review)
        watch_items = self._build_watch_items(signals, relevance)
        as_of_date = max(latest_dates) if latest_dates else datetime.now().strftime("%Y-%m-%d")
        big_picture = self._build_market_big_picture(signals)

        market_context = {
            "as_of_date": as_of_date,
            "signals": signals,
            "big_picture": big_picture,
            "ai_summary": "",
            "portfolio_relevance": relevance,
            "watch_items": watch_items,
            "proxy_notes": self._proxy_notes(),
        }
        self.storage.update_weekly_market_context(week_id, market_context)
        return {"success": True, "week_id": week_id, **market_context, "errors": errors}

    def summarize_market_context(self, week_id: Optional[str] = None, factor_analysis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if week_id is None:
            week_id = get_week_id()
        review = self.get_or_create_review(week_id)
        market_context = review.get("market_context") or {}
        signals = [s for s in (market_context.get("signals") or []) if s.get("success")]
        if not signals:
            return {"error": "No dataTextStatusText, TextTextRefreshTextStatus"}

        lines = []
        for signal in signals:
            lines.append(
                f"- {signal.get('name')} ({signal.get('ticker')}): Text {signal.get('change_pct')}%, "
                f"TextTradeText {signal.get('end_date')}, Text {', '.join(signal.get('tags') or []) or 'Text'}"
            )
        relevance = market_context.get("portfolio_relevance") or self._build_portfolio_relevance(review)
        relevance_text = "\n".join(
            f"- {item.get('label')}: Text {', '.join(item.get('signals') or [])}"
            for item in relevance
        ) or "(No dataText)"
        watch_items = market_context.get("watch_items") or self._build_watch_items(signals, relevance)

        # TextAnalysisSummary(Text)
        factor_section = ""
        if factor_analysis is None:
            factor_analysis = review.get("factor_analysis") or {}
        if factor_analysis:
            factor_lines = []
            diagnosis = factor_analysis.get("portfolio_diagnosis") or []
            for item in diagnosis[:3]:
                label = str(item.get("label") or "").strip()
                summary = str(item.get("summary") or item.get("description") or "").strip()
                value = item.get("value")
                value_text = f"({value})" if value not in (None, "") else ""
                if label:
                    factor_lines.append(f"{label}{value_text}: {summary}")

            exposure_change = factor_analysis.get("exposure_change") or {}
            for alert in (exposure_change.get("drift_alerts") or [])[:3]:
                factor_lines.append(f"Text: {alert}")

            attribution = factor_analysis.get("attribution_summary") or {}
            attr_summary = str(attribution.get("summary") or "").strip()
            if attr_summary:
                factor_lines.append(f"Text: {attr_summary}")

            if factor_lines:
                factor_section = f"""

TextAnalysisSummary: 
{chr(10).join('- ' + x for x in factor_lines)}"""

        prompt = f"""TextReviewText. TextThis WeekText, TextStatusText. 

Text: 
1. Text, Text: This WeekText, Text/Text, TextCurrentText, Text
2. Text, Text, Text
3. Text, Text, TextReview
4. Text, Text
5. TextAnalysisText, Text"TextCurrentText"Text, Text+Text

Text: 
{chr(10).join(lines)}

Text: 
{relevance_text}

DefaultWatchText: 
{chr(10).join('- ' + x for x in watch_items)}
{factor_section}"""

        summary, error = self._safe_llm_chat(prompt)
        if error:
            existing_summary = str(market_context.get("ai_summary") or "").strip()
            if existing_summary and _is_transient_llm_error(error):
                payload = {
                    "as_of_date": market_context.get("as_of_date"),
                    "signals": market_context.get("signals") or [],
                    "big_picture": market_context.get("big_picture") or self._build_market_big_picture(market_context.get("signals") or []),
                    "ai_summary": existing_summary,
                    "portfolio_relevance": relevance,
                    "watch_items": watch_items,
                    "proxy_notes": market_context.get("proxy_notes") or self._proxy_notes(),
                    "degraded": True,
                    "warning": f"AI TextGenerateText, Text: {error}",
                }
                return {"success": True, "week_id": week_id, **payload}
            return {"error": f"GenerateTextFailed: {error}"}

        payload = {
            "as_of_date": market_context.get("as_of_date"),
            "signals": market_context.get("signals") or [],
            "big_picture": market_context.get("big_picture") or self._build_market_big_picture(market_context.get("signals") or []),
            "ai_summary": summary,
            "portfolio_relevance": relevance,
            "watch_items": watch_items,
            "proxy_notes": market_context.get("proxy_notes") or self._proxy_notes(),
        }
        self.storage.update_weekly_market_context(week_id, payload)
        return {"success": True, "week_id": week_id, **payload}

    def refresh_stock_news(self, stock_id: str, days: int = 7, force_refresh: bool = False) -> Dict:
        """TextRefreshTextStockTextNews"""
        playbook = self.storage.get_stock_playbook(stock_id)
        stock_name = playbook.get("stock_name", stock_id) if playbook else stock_id
        week_id = get_week_id()

        news_result = self.env_collector.collect_news(
            stock_id,
            stock_name,
            time_range_days=days,
            force_refresh=force_refresh,
            ai_enrich=False,
        )
        news = news_result.get("news", [])
        search_metadata = news_result.get("search_metadata") or {}
        combined_warnings = [str(item).strip() for item in (search_metadata.get("search_warnings") or []) if str(item).strip()]
        deep_search_summary = ""
        deep_search_meta: Dict[str, Any] = {}
        deep_search_cache_hit = False
        try:
            deep_result = self.deep_search_service.enhance_weekly_review_news(
                stock_id=stock_id,
                stock_name=stock_name,
                days=days,
                playbook=playbook or {},
                base_news=news,
                force_refresh=force_refresh,
            )
            news = deep_result.get("news") or news
            deep_search_summary = str(deep_result.get("deep_search_summary") or "").strip()
            deep_search_meta = dict(deep_result.get("deep_search_meta") or {})
            deep_search_cache_hit = bool(deep_result.get("cache_hit"))
            combined_warnings.extend(
                [str(item).strip() for item in (deep_result.get("search_warnings") or []) if str(item).strip()]
            )
        except Exception as exc:
            logger.warning("deep search enhancement failed for %s: %s", stock_id, exc)
            combined_warnings.append(f"TextFailed: {str(exc)[:80]}")

        self.storage.update_stock_weekly_data(
            week_id=week_id,
            stock_id=stock_id,
            stock_name=stock_name,
            news=news,
            news_search_warnings=list(dict.fromkeys([item for item in combined_warnings if item])),
            news_fallback_summary=news_result.get("fallback_summary", ""),
            news_cache_hit=bool(search_metadata.get("cache_hit") or (news_result.get("runtime_meta") or {}).get("cache_hit") or deep_search_cache_hit),
            news_deep_search_summary=deep_search_summary,
            news_deep_search_meta=deep_search_meta,
        )

        out = {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "news": news,
            "news_count": len(news),
            "search_metadata": search_metadata,
            "runtime_meta": news_result.get("runtime_meta"),
            "fallback_summary": news_result.get("fallback_summary", ""),
            "cache_hit": bool(search_metadata.get("cache_hit") or (news_result.get("runtime_meta") or {}).get("cache_hit") or deep_search_cache_hit),
            "deep_search_summary": deep_search_summary,
            "deep_search_meta": deep_search_meta,
        }
        if combined_warnings:
            out["search_warnings"] = list(dict.fromkeys([item for item in combined_warnings if item]))
        return out

    def refresh_stock_performance(self, stock_id: str, days: int = 7, week_id: Optional[str] = None) -> Dict:
        """RefreshTextStockText(Text/AText/Text AKShare). TextHoldings(Text playbook Text stock_id Text ticker)"""
        if week_id is None:
            week_id = get_week_id()
        requested_stock_id = str(stock_id or "").strip()
        review = self.storage.get_weekly_review(week_id)

        def _canon(value: str) -> str:
            if hasattr(self.storage, "_canonical_code"):
                return self.storage._canonical_code(value)
            return str(value or "").strip().upper()

        def _resolve_stock_key(raw_id: str) -> str:
            raw = str(raw_id or "").strip()
            if not raw:
                return raw
            raw_canon = _canon(raw)
            for candidate_id, candidate_data in ((review or {}).get("stocks") or {}).items():
                candidate_playbook = self.storage.get_stock_playbook(candidate_id) or {}
                aliases = [
                    candidate_id,
                    (candidate_data or {}).get("ticker"),
                    (candidate_data or {}).get("stock_name"),
                    (candidate_data or {}).get("search_name"),
                    candidate_playbook.get("stock_id"),
                    candidate_playbook.get("ticker"),
                    candidate_playbook.get("stock_name"),
                    candidate_playbook.get("search_name"),
                ]
                if any(alias and _canon(str(alias)) == raw_canon for alias in aliases):
                    return str(candidate_id).strip()
            playbook = self.storage.get_stock_playbook(raw) or {}
            if playbook.get("stock_id"):
                return str(playbook.get("stock_id") or raw).strip() or raw
            for item in self.storage.list_stocks():
                aliases = [item.get("stock_id"), item.get("ticker"), item.get("stock_name")]
                if any(alias and _canon(str(alias)) == raw_canon for alias in aliases):
                    return str(item.get("stock_id") or raw).strip() or raw
            return raw

        storage_stock_id = _resolve_stock_key(requested_stock_id)
        playbook = self.storage.get_stock_playbook(storage_stock_id)
        stock_name = playbook.get("stock_name", storage_stock_id) if playbook else storage_stock_id
        ticker = (playbook.get("ticker", "") if playbook else "") or storage_stock_id
        if not ticker:
            ticker = storage_stock_id
        if review and storage_stock_id in review.get("stocks", {}):
            stock_data = review["stocks"][storage_stock_id]
            stock_name = stock_data.get("stock_name", stock_name)
            ticker = str(stock_data.get("ticker") or ticker).strip() or ticker

        performance_data = None
        performance_summary = ""
        history_frame = None
        if self.history_frame_loader:
            try:
                history_map = self.history_frame_loader([ticker], lookback_days=540) or {}
                lookup = str(ticker or "").strip().upper()
                history_frame = history_map.get(lookup)
                if history_frame is None:
                    history_frame = history_map.get(ticker)
                performance_data, performance_summary = _history_frame_to_weekly_performance(history_frame)
            except Exception as e:
                logger.warning("Text %s TextMarket DataHistoryFailed: %s", ticker, e)

        if performance_data:
            result = {"success": True, "performance_summary": performance_summary, "data": performance_data}
        elif ak_get_performance:
            result = ak_get_performance(ticker, days)
        else:
            result = {"success": False, "performance_summary": "", "error": "AKShare Text, Text: pip install akshare"}

        performance_summary = result["performance_summary"] if result.get("success") else result.get("error", "")
        if result.get("success") and result.get("data") and not performance_data:
            d = result["data"]
            performance_data = {
                "start_price": d.get("start_price"),
                "end_price": d.get("end_price"),
                "change_pct": d.get("change_pct"),
            }

        # Text portfolio_returns(BuyTo Date, YTD, 6Text, 1TextReturnText)
        review = self.storage.get_weekly_review(week_id)
        buy_date = ""
        if review and storage_stock_id in review.get("stocks", {}):
            buy_date = review["stocks"][storage_stock_id].get("buy_date") or ""
        portfolio_returns_result = None
        if history_frame is not None:
            portfolio_returns_result = _history_frame_to_portfolio_returns(history_frame, buy_date)
        if not portfolio_returns_result and ak_get_portfolio_returns:
            try:
                ret = ak_get_portfolio_returns(ticker, buy_date if buy_date else None)
                if ret.get("success"):
                    portfolio_returns_result = {
                        "return_since_buy": ret.get("return_since_buy"),
                        "ytd_return": ret.get("ytd_return"),
                        "return_6m": ret.get("return_6m"),
                        "return_1y": ret.get("return_1y"),
                    }
            except Exception as e:
                logger.warning("Text %s portfolio_returns Failed: %s", stock_id, e)

        # Text
        self.storage.update_stock_weekly_data(
            week_id=week_id,
            stock_id=storage_stock_id,
            stock_name=stock_name,
            performance_summary=performance_summary,
            performance_data=performance_data,
            portfolio_returns=portfolio_returns_result,
            ticker=ticker,
        )

        out = {
            "stock_id": storage_stock_id,
            "requested_stock_id": requested_stock_id,
            "stock_name": stock_name,
            "ticker": ticker,
            "performance_summary": performance_summary,
        }
        if performance_data:
            out["performance_data"] = performance_data
        if portfolio_returns_result:
            out["portfolio_returns"] = portfolio_returns_result

        return out

    def refresh_all_news(self, days: int = 7, force_refresh: bool = False) -> List[Dict]:
        """RefreshTextStockTextNews, Text LLM Text"""
        stocks = self.storage.list_stocks()
        results = []
        for stock in stocks:
            stock_id = stock.get("stock_id", "")
            if stock_id:
                try:
                    result = self.refresh_stock_news(stock_id, days, force_refresh=force_refresh)
                    results.append(result)
                except Exception as e:
                    results.append({
                        "stock_id": stock_id,
                        "stock_name": stock.get("stock_name", stock_id),
                        "error": str(e),
                        "news": []
                    })

        # NewsText, Text LLM Text
        try:
            filter_result = self._filter_news_by_relevance()
            logger.info("NewsText: Text %d Text, Text %d Text",
                        filter_result.get("total", 0), filter_result.get("filtered", 0))
            # TextResult
            if results:
                results[0]["_filter_stats"] = filter_result
        except Exception as e:
            logger.warning("NewsTextFailed, TextNewsText: %s", e)

        return results

    def refresh_portfolio_prices(
        self,
        week_id: Optional[str] = None,
        days: int = 7,
        *,
        force_refresh: bool = False,
        max_age_minutes: int = 15,
    ) -> Dict[str, Any]:
        """Synchronously refresh portfolio prices, skipping fresh cached rows."""
        if week_id is None:
            week_id = get_week_id()
        review = self.storage.get_or_create_weekly_review(week_id, self.storage.list_stocks())
        stocks_data = review.get("stocks") or {}
        now = datetime.now()

        def _is_fresh_enough(sdata: dict) -> bool:
            if force_refresh:
                return False
            perf = sdata.get("performance_data") or {}
            if not isinstance(perf, dict):
                return False
            if self.storage._safe_float(perf.get("start_price")) is None or self.storage._safe_float(perf.get("end_price")) is None:
                return False
            if str(sdata.get("buy_date") or "").strip() and self.storage._safe_float(sdata.get("avg_cost")) in (None, 0.0):
                return False
            updated_at = str(sdata.get("performance_updated_at") or "").strip()
            if not updated_at:
                return False
            try:
                updated_dt = datetime.fromisoformat(updated_at)
            except ValueError:
                return False
            return (now - updated_dt).total_seconds() / 60.0 <= max(max_age_minutes, 1)

        def _fetch_one(stock_id: str, sdata: dict) -> dict:
            playbook = self.storage.get_stock_playbook(stock_id)
            stock_name = playbook.get("stock_name", stock_id) if playbook else sdata.get("stock_name", stock_id)
            ticker = (playbook.get("ticker", "") if playbook else "") or stock_id
            buy_date = sdata.get("buy_date") or ""
            try:
                if _is_fresh_enough(sdata):
                    return {
                        "stock_id": stock_id,
                        "stock_name": stock_name,
                        "performance_data": sdata.get("performance_data"),
                        "portfolio_returns": sdata.get("portfolio_returns") or {},
                        "avg_cost": sdata.get("avg_cost"),
                        "status": "skipped",
                        "reason": "fresh_cache",
                    }
                perf_data = None
                pr = {}
                if ak_get_portfolio_and_weekly:
                    ret = ak_get_portfolio_and_weekly(ticker, buy_date if buy_date else None)
                    perf_data = ret.get("performance_data")
                    pr = ret.get("portfolio_returns") or {}
                    perf_summary = ret.get("performance_summary") or ret.get("error", "")
                    if ret.get("success"):
                        self.storage.update_stock_weekly_data(
                            week_id=week_id,
                            stock_id=stock_id,
                            stock_name=stock_name,
                            performance_summary=perf_summary,
                            performance_data=perf_data,
                            portfolio_returns=pr,
                        )
                    else:
                        logger.warning("Refresh %s price failed: %s", stock_id, ret.get("error", ""))
                        return {
                            "stock_id": stock_id,
                            "stock_name": stock_name,
                            "error": ret.get("error", "") or "refresh failed",
                            "avg_cost": sdata.get("avg_cost"),
                            "status": "failed",
                        }
                else:
                    perf = self.refresh_stock_performance(stock_id, days, week_id)
                    perf_data = perf.get("performance_data")
                    pr = perf.get("portfolio_returns") or {}
                return {
                    "stock_id": stock_id,
                    "stock_name": stock_name,
                    "performance_data": perf_data,
                    "portfolio_returns": pr,
                    "avg_cost": sdata.get("avg_cost"),
                    "status": "refreshed",
                }
            except Exception as exc:
                logger.error("Refresh %s price raised: %s", stock_id, exc)
                return {"stock_id": stock_id, "stock_name": stock_name, "error": str(exc), "avg_cost": sdata.get("avg_cost"), "status": "failed"}

        active_items = []
        skipped_inactive = 0
        for sid, sd in stocks_data.items():
            if not sid or not isinstance(sd, dict):
                continue
            shares = self.storage._safe_float(sd.get("shares_held")) or 0.0
            if shares <= 0:
                skipped_inactive += 1
                continue
            active_items.append((sid, sd))

        results = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_fetch_one, sid, sd): sid for sid, sd in active_items}
            for future in as_completed(futures):
                results.append(future.result())

        cost_filled = 0
        if ak_get_close_price:
            for sid, sd in active_items:
                if sd.get("avg_cost"):
                    continue
                buy_date = sd.get("buy_date")
                if not buy_date:
                    continue
                playbook = self.storage.get_stock_playbook(sid)
                ticker = (playbook.get("ticker", "") if playbook else "") or sid
                try:
                    buy_price = ak_get_close_price(ticker, buy_date)
                    if buy_price and buy_price > 0:
                        self.storage.update_stock_weekly_data(
                            week_id=week_id,
                            stock_id=sid,
                            stock_name=sd.get("stock_name", sid),
                            avg_cost=buy_price,
                        )
                        for row in results:
                            if row.get("stock_id") == sid:
                                row["avg_cost"] = buy_price
                                row["cost_filled"] = True
                                break
                        cost_filled += 1
                except Exception as exc:
                    logger.warning("Fetch %s buy-date close failed: %s", sid, exc)

        self.storage.update_prices_refreshed_at(week_id)
        refreshed = sum(1 for row in results if row.get("status") == "refreshed")
        skipped = sum(1 for row in results if row.get("status") == "skipped")
        failed = sum(1 for row in results if row.get("status") == "failed")
        review_payload = self.storage.get_weekly_review_with_portfolio_state(week_id, stock_list=self.storage.list_stocks())
        data_health = {}
        if hasattr(self.storage, "build_weekly_review_data_health"):
            data_health = self.storage.build_weekly_review_data_health(
                review_payload,
                stock_list=self.storage.list_stocks(),
            )
        return {
            "results": results,
            "summary": {
                "target_count": len(active_items),
                "refreshed_count": refreshed,
                "skipped_count": skipped,
                "failed_count": failed,
                "inactive_skipped_count": skipped_inactive,
                "cost_filled_count": cost_filled,
                "max_age_minutes": max(max_age_minutes, 1),
                "force_refresh": bool(force_refresh),
            },
            "data_health": data_health,
            "prices_refreshed_at": str((self.storage.get_weekly_review(week_id) or {}).get("prices_refreshed_at") or ""),
        }

    def refresh_all_performance(self, days: int = 7) -> List[Dict]:
        """RefreshTextStockText(Text AKShare, Text)"""
        stocks = self.storage.list_stocks()
        valid = [s for s in stocks if s.get("stock_id")]

        def _fetch(stock: dict) -> dict:
            stock_id = stock["stock_id"]
            try:
                return self.refresh_stock_performance(stock_id, days)
            except Exception as e:
                logger.error("Refresh %s Text: %s", stock_id, e)
                return {
                    "stock_id": stock_id,
                    "stock_name": stock.get("stock_name", stock_id),
                    "error": str(e),
                    "performance_summary": "",
                }

        results = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_fetch, s): s["stock_id"] for s in valid}
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def generate_news_summary(self, stock_id: str, week_id: Optional[str] = None) -> Dict:
        """AI TextNewsText(Text is_relevant != false TextNews)"""
        if week_id is None:
            week_id = get_week_id()
        review = self.storage.get_or_create_weekly_review(week_id, self.storage.list_stocks())
        stocks_data = review.get("stocks") or {}
        sdata = stocks_data.get(stock_id) or {}
        news = sdata.get("news") or []

        if not news:
            return {"stock_id": stock_id, "news_summary": "", "error": "No dataNews, TextRefreshNews"}

        playbook = self.storage.get_stock_playbook(stock_id)
        stock_name = playbook.get("stock_name", stock_id) if playbook else stock_id

        news_text = []
        for n in news[:15]:
            if n is None:
                continue
            if isinstance(n, dict):
                # TextNews
                if n.get("is_relevant") is False:
                    continue
                title = _clean_news_summary_text(n.get("title", ""))
                summary = _clean_news_summary_text(n.get("summary", ""))
                date = n.get("date", "")
                key_facts = [
                    _clean_news_summary_text(item)
                    for item in (n.get("deep_search_key_facts") or [])
                    if _clean_news_summary_text(item)
                ]
                if not title and not summary:
                    continue
                line = f"- [{date}] {title}" + (f": {summary}" if summary else "")
                if key_facts:
                    line += f"; Text: {' / '.join(key_facts[:2])}"
                news_text.append(line)
            else:
                cleaned = _clean_news_summary_text(n)
                if cleaned:
                    news_text.append(f"- {cleaned}")

        if not news_text:
            return {"stock_id": stock_id, "news_summary": "", "error": "NewsText, TextRefreshNews"}

        prompt = f"""TextResearchText. Text{stock_name}TextThis WeekNews, Text 2-4 Text. 

Text: 
1. Text
2. Text, Text
3. Text, Text

## This WeekNews
{chr(10).join(news_text)}

## Text(2-4Text)
"""

        summary, error = self._safe_llm_chat(prompt)
        if error:
            logger.warning("generate_news_summary failed for %s: %s", stock_id, error)
            return {
                "stock_id": stock_id,
                "stock_name": stock_name,
                "news_summary": "",
                "error": f"GenerateTextFailed: {error}",
            }
        self.storage.update_stock_weekly_data(
            week_id=week_id,
            stock_id=stock_id,
            stock_name=stock_name,
            news_summary=summary
        )
        return {"stock_id": stock_id, "stock_name": stock_name, "news_summary": summary}

    def save_user_view(self, stock_id: str, user_view: str, week_id: Optional[str] = None) -> bool:
        """SaveTextStockText"""
        if week_id is None:
            week_id = get_week_id()
        playbook = self.storage.get_stock_playbook(stock_id)
        stock_name = playbook.get("stock_name", stock_id) if playbook else stock_id
        self.storage.update_stock_weekly_data(
            week_id=week_id,
            stock_id=stock_id,
            stock_name=stock_name,
            user_view=user_view
        )
        return True

    def _stock_summary_fallback(self, stock_name: str, reason: str = "") -> str:
        note = reason or "This WeekText, TextWatch. "
        return "\n".join(
            [
                "[This WeekText]",
                f"{stock_name} This WeekText, Text. ",
                "",
                "[Text]",
                note,
                "",
                "[Text thesis Text]",
                "Text, Text thesis Text, Text. ",
                "",
                "[Text]",
                "Text, Text. ",
            ]
        ).strip()

    def _build_weekly_stock_summary_payload(
        self,
        week_id: str,
        stock_id: str,
        review: Dict[str, Any],
        commentary_entry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        stocks_data = (review or {}).get("stocks") or {}
        stock_data = dict(stocks_data.get(stock_id) or {})
        playbook = self.storage.get_stock_playbook(stock_id) or {}
        stock_name = str(stock_data.get("stock_name") or playbook.get("stock_name") or stock_id).strip() or stock_id
        ticker = str(playbook.get("ticker") or stock_id).strip()
        core_thesis = str((playbook.get("core_thesis") or {}).get("summary") or "").strip()
        user_view = str(stock_data.get("user_view") or "").strip()
        performance_summary = str(stock_data.get("performance_summary") or "").strip()
        portfolio_returns = dict(stock_data.get("portfolio_returns") or {})
        performance_data = dict(stock_data.get("performance_data") or {})

        relevant_news: List[Dict[str, str]] = []
        for item in stock_data.get("news") or []:
            if not isinstance(item, dict) or item.get("is_relevant") is False:
                continue
            title = _clean_news_summary_text(item.get("title", ""))
            summary = _clean_news_summary_text(item.get("summary", ""))
            key_facts = [
                _clean_news_summary_text(fact)
                for fact in (item.get("deep_search_key_facts") or [])
                if _clean_news_summary_text(fact)
            ]
            if not title and not summary:
                continue
            relevant_news.append(
                {
                    "date": str(item.get("date") or "").strip(),
                    "title": title,
                    "summary": summary,
                    "key_facts": key_facts,
                }
            )

        commentary_items: List[Dict[str, Any]] = []
        for item in list((commentary_entry or {}).get("items") or []):
            if not isinstance(item, dict):
                continue
            commentary_items.append(
                {
                    "id": str(item.get("id") or "").strip(),
                    "published_at": str(item.get("published_at") or "").strip(),
                    "header_lines": [str(line).strip() for line in (item.get("header_lines") or []) if str(line).strip()],
                    "matched_keywords": [str(line).strip() for line in (item.get("matched_keywords") or []) if str(line).strip()],
                    "body": str(item.get("body") or "").strip(),
                }
            )

        research_history: List[Dict[str, str]] = []
        for item in self.storage.get_recent_research(stock_id, limit=3) or []:
            if not isinstance(item, dict):
                continue
            result = item.get("research_result") or {}
            feedback = item.get("user_feedback") or {}
            research_history.append(
                {
                    "date": str(item.get("date") or "")[:10],
                    "recommendation": str(result.get("recommendation") or "").strip(),
                    "confidence": str(result.get("confidence") or "").strip(),
                    "key_finding": str(result.get("key_finding") or "").strip(),
                    "decision": str(feedback.get("decision") or "").strip(),
                }
            )

        signature_payload = {
            "week_id": week_id,
            "stock_id": stock_id,
            "stock_name": stock_name,
            "ticker": ticker,
            "core_thesis": core_thesis,
            "user_view": user_view,
            "performance_summary": performance_summary,
            "performance_data": performance_data,
            "portfolio_returns": portfolio_returns,
            "relevant_news": relevant_news,
            "commentary_items": commentary_items,
            "research_history": research_history,
        }
        signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return {
            "week_id": week_id,
            "stock_id": stock_id,
            "stock_name": stock_name,
            "ticker": ticker,
            "core_thesis": core_thesis,
            "user_view": user_view,
            "performance_summary": performance_summary,
            "performance_data": performance_data,
            "portfolio_returns": portfolio_returns,
            "relevant_news": relevant_news,
            "commentary_items": commentary_items,
            "research_history": research_history,
            "signature": signature,
            "existing_summary": str(stock_data.get("broker_commentary_ai_summary") or "").strip(),
            "existing_signature": str(stock_data.get("broker_commentary_ai_summary_signature") or "").strip(),
        }

    def generate_stock_weekly_ai_summary(
        self,
        stock_id: str,
        week_id: Optional[str] = None,
        force: bool = False,
        commentary_entry: Optional[Dict[str, Any]] = None,
        review: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if week_id is None:
            week_id = get_week_id()
        review_data = review or self.storage.get_or_create_weekly_review(week_id, self.storage.list_stocks())
        if stock_id not in ((review_data or {}).get("stocks") or {}):
            return {
                "success": False,
                "stock_id": stock_id,
                "summary": "",
                "error": "TextStockTextCurrentTextReviewText",
            }

        payload = self._build_weekly_stock_summary_payload(week_id, stock_id, review_data, commentary_entry)

        if not payload.get("commentary_items"):
            needs_fetch = not commentary_entry or not (
                commentary_entry.get("items") if isinstance(commentary_entry, dict) else []
            )
            if needs_fetch:
                try:
                    from .stock_commentary import get_stock_commentary

                    commentary_payload = get_stock_commentary(
                        self.storage,
                        [
                            {
                                "stock_id": stock_id,
                                "stock_name": payload.get("stock_name") or stock_id,
                                "ticker": payload.get("ticker") or stock_id,
                            }
                        ],
                        week_id=week_id,
                    )
                    fallback_entry = (commentary_payload.get("stocks") or {}).get(stock_id) or {}
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("auto commentary fetch failed for %s %s: %s", stock_id, week_id, exc)
                    fallback_entry = {}

                if fallback_entry.get("items"):
                    payload = self._build_weekly_stock_summary_payload(week_id, stock_id, review_data, fallback_entry)
        if (not force) and payload["existing_summary"] and payload["existing_signature"] == payload["signature"]:
            existing_error = str(
                (((review_data or {}).get("stocks") or {}).get(stock_id) or {}).get("broker_commentary_ai_summary_error") or ""
            ).strip()
            if existing_error:
                self.storage.update_stock_weekly_data(
                    week_id=week_id,
                    stock_id=stock_id,
                    stock_name=payload["stock_name"],
                    broker_commentary_ai_summary_error="",
                )
            return {
                "success": True,
                "stock_id": stock_id,
                "stock_name": payload["stock_name"],
                "summary": payload["existing_summary"],
                "skipped": True,
                "signature": payload["signature"],
            }

        has_inputs = any(
            [
                payload["relevant_news"],
                payload["commentary_items"],
                payload["performance_summary"],
                payload["user_view"],
                payload["research_history"],
            ]
        )
        if not has_inputs:
            summary = self._stock_summary_fallback(payload["stock_name"])
            self.storage.update_stock_weekly_data(
                week_id=week_id,
                stock_id=stock_id,
                stock_name=payload["stock_name"],
                broker_commentary_ai_summary=summary,
                broker_commentary_ai_summary_signature=payload["signature"],
                broker_commentary_ai_summary_error="",
            )
            return {
                "success": True,
                "stock_id": stock_id,
                "stock_name": payload["stock_name"],
                "summary": summary,
                "signature": payload["signature"],
                "skipped": False,
            }

        news_lines = []
        for item in payload["relevant_news"][:8]:
            header = f"[{item['date']}]" if item["date"] else "-"
            body = item["title"] or item["summary"]
            if item["summary"]:
                body = f"{item['title']}: {item['summary']}" if item["title"] else item["summary"]
            if item.get("key_facts"):
                body += f"; Text: {' / '.join(item['key_facts'][:2])}"
            news_lines.append(f"{header} {body}".strip())

        commentary_lines = []
        for item in payload["commentary_items"]:
            header = " / ".join(item["header_lines"][:2]).strip()
            body = item["body"] or header
            extra = f"; Text: {' / '.join(item['matched_keywords'])}" if item["matched_keywords"] else ""
            commentary_lines.append(
                f"- Text: {item['published_at'] or '--'}\n  Text: {header or '--'}\n  Text: {body}{extra}"
            )

        history_lines = []
        for item in payload["research_history"]:
            pieces = [item["date"], item["recommendation"], item["confidence"], item["key_finding"], item["decision"]]
            text = " | ".join([piece for piece in pieces if piece])
            if text:
                history_lines.append(f"- {text}")

        prompt = f"""TextReviewText. TextStockTextThis WeekText, Text, Text, Text AI Summary. 

Text: 
1. Text, TextNews, Text, TextHistory thesis. 
2. Text, TextText. 
3. Text, Text, Text. 
4. Text: 
[This WeekText]
[Text]
[Text thesis Text]
[Text]

## Stock
- Text: {payload['stock_name']}
- Ticker: {payload['ticker'] or payload['stock_id']}
- Text thesis: {payload['core_thesis'] or 'No dataText'}
- This WeekText: {payload['performance_summary'] or 'No dataText'}
- TextThis WeekText: {payload['user_view'] or 'No data'}

## This WeekNews
{chr(10).join(news_lines) if news_lines else '- No dataTextNews'}

## This WeekText
{chr(10).join(commentary_lines) if commentary_lines else '- This WeekNo dataText'}

## HistoryResearch
{chr(10).join(history_lines) if history_lines else '- No dataHistoryResearchSummary'}
"""

        summary, error = self._safe_llm_chat(prompt)
        if error:
            self.storage.update_stock_weekly_data(
                week_id=week_id,
                stock_id=stock_id,
                stock_name=payload["stock_name"],
                broker_commentary_ai_summary_error=error,
            )
            return {
                "success": False,
                "stock_id": stock_id,
                "stock_name": payload["stock_name"],
                "summary": payload["existing_summary"],
                "error": error,
                "signature": payload["signature"],
            }

        self.storage.update_stock_weekly_data(
            week_id=week_id,
            stock_id=stock_id,
            stock_name=payload["stock_name"],
            broker_commentary_ai_summary=summary,
            broker_commentary_ai_summary_signature=payload["signature"],
            broker_commentary_ai_summary_error="",
        )
        return {
            "success": True,
            "stock_id": stock_id,
            "stock_name": payload["stock_name"],
            "summary": summary,
            "signature": payload["signature"],
            "skipped": False,
        }

    def generate_all_stock_weekly_ai_summaries(
        self,
        week_id: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        if week_id is None:
            week_id = get_week_id()
        review = self.storage.get_or_create_weekly_review(week_id, self.storage.list_stocks())
        stocks_data = (review or {}).get("stocks") or {}
        records: List[Dict[str, Any]] = []
        for stock_id, stock_data in stocks_data.items():
            playbook = self.storage.get_stock_playbook(stock_id) or {}
            records.append(
                {
                    "stock_id": stock_id,
                    "stock_name": str((stock_data or {}).get("stock_name") or playbook.get("stock_name") or stock_id).strip(),
                    "ticker": str(playbook.get("ticker") or stock_id).strip(),
                }
            )

        if records:
            from .stock_commentary import get_stock_commentary

            commentary_payload = get_stock_commentary(self.storage, records, week_id=week_id)
            commentary_map = commentary_payload.get("stocks") or {}
        else:
            commentary_map = {}

        results = []
        for record in records:
            stock_id = str(record.get("stock_id") or "").strip()
            if not stock_id:
                continue
            results.append(
                self.generate_stock_weekly_ai_summary(
                    stock_id=stock_id,
                    week_id=week_id,
                    force=force,
                    commentary_entry=commentary_map.get(stock_id) or {},
                    review=review,
                )
            )

        return {
            "success": True,
            "week_id": week_id,
            "results": results,
        }

    def _build_synthesis_context(self, week_id: Optional[str] = None) -> str:
        """TextReviewText"""
        if week_id is None:
            week_id = get_week_id()
        review = self.get_or_create_review(week_id)
        portfolio = self.storage.get_portfolio_playbook()
        market_context = review.get("market_context") or {}
        factor_analysis = review.get("factor_analysis") or {}
        macro_events = review.get("macro_events") or {}

        lines = ["## Text Playbook\n"]
        if portfolio:
            mv = portfolio.get("market_views") or {}
            main_themes = mv.get("main_themes") or []
            if main_themes:
                themes = [str((t or {}).get("theme", "")) for t in main_themes]
                lines.append("Text: " + ", ".join(themes))
            focus_stocks = mv.get("focus_stocks") or []
            if focus_stocks:
                stocks = [f"{(s or {}).get('stock','')}({(s or {}).get('theme','')})" for s in focus_stocks]
                lines.append("Text: " + ", ".join(stocks))
        lines.append("")

        # This WeekText(Text)
        events = macro_events.get("top_events") or macro_events.get("events") or []
        if events:
            lines.append("## This WeekText")
            lines.append("")
            for evt in events[:5]:
                title = evt.get("title", "")
                category = evt.get("category", "")
                summary = evt.get("why_it_matters") or evt.get("summary", "")
                impact = evt.get("impact_direction") or evt.get("portfolio_impact", "")
                affected = ", ".join(evt.get("related_assets") or evt.get("affected_stocks") or [])
                importance = evt.get("importance", "")
                lines.append(f"### {title} [{category}] (Text: {importance})")
                if summary:
                    lines.append(f"Summary: {summary}")
                if impact:
                    lines.append(f"TextHoldingsText: {impact}")
                if affected:
                    lines.append(f"TextHoldings: {affected}")
                lines.append("")

        if market_context:
            lines.append("## This WeekTextStatus")
            lines.append("")
            big_picture = market_context.get("big_picture") or {}
            if big_picture:
                if big_picture.get("summary"):
                    lines.append(str(big_picture.get("summary")))
                if big_picture.get("regime"):
                    lines.append(f"- Regime: {big_picture.get('regime')}")
                for group in big_picture.get("groups") or []:
                    lines.append(f"**{group.get('label') or group.get('id')}**")
                    for signal in (group.get("signals") or [])[:6]:
                        lines.append(
                            f"- {signal.get('name') or signal.get('ticker')}: "
                            f"{signal.get('change_pct')}% | {signal.get('read') or signal.get('proxy_note') or ''}"
                        )
                lines.append("")
            ai_summary = str(market_context.get("ai_summary") or "").strip()
            if ai_summary:
                lines.append(ai_summary)
                lines.append("")
            signals = market_context.get("signals") or []
            if signals:
                lines.append("**Text:**")
                for signal in signals:
                    if not signal.get("success"):
                        continue
                    lines.append(f"- {signal.get('name')}({signal.get('ticker')}): {signal.get('performance_summary') or ''}")
                lines.append("")
            for item in market_context.get("watch_items") or []:
                lines.append(f"- Text: {item}")
            lines.append("")


        if factor_analysis:
            lines.append("## Text")
            lines.append("")

            primary_model = factor_analysis.get("primary_model") or {}
            if primary_model:
                lines.append("**This WeekText:**")
                lines.append(
                    f"- {(primary_model.get('label') or primary_model.get('key') or 'Text')}"
                    f" | Text {primary_model.get('r_squared', 0):.2f}"
                    f" | Text {primary_model.get('stability_score', 0):.2f}"
                )
                if primary_model.get("reason"):
                    lines.append(f"- Text: {primary_model.get('reason')}")
                lines.append("")

            diagnosis = factor_analysis.get("portfolio_diagnosis") or []
            if diagnosis:
                lines.append("**TextRisk:**")
                for item in diagnosis[:5]:
                    label = str(item.get("label") or "").strip()
                    description = str(item.get("summary") or item.get("description") or "").strip()
                    value = item.get("value")
                    value_text = f" ({value})" if value not in (None, "") else ""
                    lines.append(f"- {label}{value_text}: {description}")
                lines.append("")

            attribution = factor_analysis.get("attribution_summary") or {}
            if attribution:
                lines.append("**This WeekText:**")
                summary = str(attribution.get("summary") or "").strip()
                if summary:
                    lines.append(f"- {summary}")
                for bucket in (attribution.get("dominant_buckets") or [])[:3]:
                    label = bucket.get("label") or bucket.get("bucket") or ""
                    lines.append(f"- Text: {label}")
                for proxy in (attribution.get("proxy_focus") or [])[:3]:
                    label = proxy.get("label") or proxy.get("factor") or ""
                    lines.append(f"- Text / Text: {label}")
                lines.append("")

            exposure_change = factor_analysis.get("exposure_change") or {}
            if exposure_change.get("available"):
                lines.append("**TextLast WeekText:**")
                for alert in exposure_change.get("drift_alerts") or []:
                    lines.append(f"- Text: {alert}")
                for item in (exposure_change.get("holdings") or [])[:3]:
                    stock_id = item.get("stock_id") or ""
                    delta = item.get("weight_change")
                    if delta is None:
                        continue
                    lines.append(f"- HoldingsText: {stock_id} {delta:+.2%}")
                lines.append("")

            unsupported = factor_analysis.get("unsupported_holdings") or []
            if unsupported:
                lines.append("**TextAnalysisTextHoldings:**")
                for item in unsupported[:5]:
                    name = item.get("stock_name") or item.get("stock_id") or "Text"
                    reason = item.get("reason") or "Text"
                    lines.append(f"- {name}: {reason}")
                lines.append("")
        for stock_id, sdata in (review.get("stocks") or {}).items():
            sdata = sdata or {}
            playbook = self.storage.get_stock_playbook(stock_id) or {}
            stock_name = sdata.get("stock_name") or stock_id
            core_thesis = (playbook.get("core_thesis") or {}).get("summary") or "(TextSettings)"

            lines.append(f"### {stock_name} ({stock_id})")
            lines.append(f"Text: {core_thesis}")
            lines.append("")

            ai_summary = str(sdata.get("broker_commentary_ai_summary") or "").strip()
            if ai_summary:
                lines.append("**This Week AI Summary:**")
                lines.append(ai_summary)
            else:
                fallback_view = str(sdata.get("user_view") or "").strip()
                lines.append("**This Week AI Summary:** (No data, TextGenerateText AI Summary)")
                if fallback_view:
                    lines.append(f"**TextThis WeekText:** {fallback_view}")
            lines.append("")
            lines.append("---")

        # TextCommunityText
        zsxq_insights_path = self.storage.base_dir / "zsxq_insights" / "48418411254128_Text.md"
        if zsxq_insights_path.exists():
            try:
                with open(zsxq_insights_path, "r", encoding="utf-8") as f:
                    insights_content = f.read()
                if insights_content.strip():
                    lines.append("")
                    lines.append("## CommunityText")
                    lines.append("")
                    lines.append(insights_content)
                    lines.append("")
            except Exception:
                pass

        return "\n".join(lines)

    def _decision_log_candidate_id(self, week_id: str, index: int, op: Dict[str, Any]) -> str:
        stock_id = str(op.get("stock_id") or "").strip()
        action = str(op.get("op_type") or "").strip()
        date = str(op.get("date") or "").strip()
        return f"{week_id}:{index}:{stock_id}:{action}:{date}"

    def build_weekly_decision_log_candidates(self, week_id: str, review: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        payload = review if isinstance(review, dict) else (self.storage.get_weekly_review(week_id) or {})
        stocks = payload.get("stocks") or {}
        rows: List[Dict[str, Any]] = []
        for index, raw_op in enumerate(payload.get("rebalancing_ops") or []):
            if not isinstance(raw_op, dict):
                continue
            op = self._normalize_trim_reallocation_op(raw_op)
            stock_id = str(op.get("stock_id") or "").strip()
            if not stock_id:
                continue
            stock_data = stocks.get(stock_id) or {}
            linked_id = self._decision_log_candidate_id(week_id, index, op)
            rows.append({
                "id": linked_id,
                "week_id": week_id,
                "stock_id": stock_id,
                "ticker": str(stock_data.get("ticker") or stock_id).strip(),
                "stock_name": stock_data.get("stock_name") or stock_id,
                "action": str(op.get("op_type") or "").strip() or "unknown",
                "quantity": op.get("quantity"),
                "price": op.get("price"),
                "date": op.get("date"),
                "thesis": op.get("decision_note") or op.get("note") or "",
                "linked_rebalancing_op_id": linked_id,
            })
        return rows

    def build_weekly_decision_log_context(self, week_id: str) -> Dict[str, Any]:
        review = self.storage.get_weekly_review(week_id) or {}
        candidates = self.build_weekly_decision_log_candidates(week_id, review)
        current_logs = self.storage.list_decision_logs(week_id=week_id)
        linked_ids = {str(row.get("linked_rebalancing_op_id") or "") for row in current_logs}
        for candidate in candidates:
            candidate["already_logged"] = candidate.get("linked_rebalancing_op_id") in linked_ids

        candidate_tickers = {
            str(candidate.get("ticker") or candidate.get("stock_id") or "").strip().upper()
            for candidate in candidates
            if str(candidate.get("ticker") or candidate.get("stock_id") or "").strip()
        }
        all_logs = self.storage.list_decision_logs()
        open_related = [
            row for row in all_logs
            if str(row.get("status") or "").lower() == "open"
            and str(row.get("week_id") or "") != week_id
            and str(row.get("ticker") or row.get("stock_id") or "").strip().upper() in candidate_tickers
        ]
        now = datetime.now()
        due_for_review = []
        for row in all_logs:
            if str(row.get("status") or "").lower() != "open":
                continue
            created = _parse_date(str(row.get("created_at") or ""))
            try:
                horizon = int(float(row.get("horizon_days") or 0))
            except (TypeError, ValueError):
                horizon = 0
            if horizon <= 0 or created is None or created + timedelta(days=horizon) <= now:
                due_for_review.append(row)
        recent_resolved = [row for row in all_logs if str(row.get("status") or "").lower() == "resolved"][:5]
        return {
            "current_week_candidates": candidates,
            "current_week_logs": current_logs,
            "open_related_logs": open_related[:10],
            "due_for_review": due_for_review[:10],
            "recent_resolved_lessons": recent_resolved,
        }

    def _format_decision_log_context_for_prompt(self, week_id: str) -> str:
        context = self.build_weekly_decision_log_context(week_id)
        lines = ["## Persistent Decision Log Context"]
        for row in context.get("open_related_logs") or []:
            lines.append(f"- Open {row.get('ticker') or row.get('stock_id')}: {row.get('thesis') or row.get('action') or ''}")
        for row in context.get("due_for_review") or []:
            lines.append(f"- Due {row.get('ticker') or row.get('stock_id')}: {row.get('action') or ''}")
        lines.append("## Decision Log Reflection")
        for row in context.get("recent_resolved_lessons") or []:
            outcome = row.get("outcome") or {}
            lines.append(f"- {row.get('ticker') or row.get('stock_id')}: {outcome.get('reflection') or outcome.get('decision_result') or ''}")
        return "\n".join(lines)

    def synthesize_thesis_update(self, week_id: Optional[str] = None) -> str:
        """AI Text: This WeekNews+Text+ResearchHistory → Text"""
        if week_id is None:
            week_id = get_week_id()
        self.generate_all_stock_weekly_ai_summaries(week_id=week_id, force=False)
        context = self._build_synthesis_context(week_id)
        decision_log_context = self._format_decision_log_context_for_prompt(week_id)

        prompt = f"""TextAnalysisText. Text, TextAnalysis: 

1. TextStockTextThis Week AI Summary, TextSummaryTextNews, Text, Text, TextHistory thesis. 
2. TextSummary, TextReview, Text. 
3. Text: Text thesis Text, Text, Text, Text. 

## Text
{context}

{decision_log_context}

TextAnalysis, Text: 
- Text/Text/TextWatchText
- Text
- Text"""

        return self.client.chat(prompt)

    def chat_about_thesis(
        self, user_message: str, history: list,
        synthesis_result: Optional[str] = None, week_id: Optional[str] = None
    ) -> str:
        """Text"""
        if week_id is None:
            week_id = get_week_id()
        context = self._build_synthesis_context(week_id)

        system = f"""TextResearchText. TextThis WeekTextReviewText. """

        if synthesis_result:
            system += f"""

## TextAnalysis(TextGenerate)
{synthesis_result[:2000]}{"..." if len(synthesis_result) > 2000 else ""}
"""

        system += f"""

## ReviewTextSummary
{context[:1500]}{"..." if len(context) > 1500 else ""}

Text, Text, RiskText. """

        hist = []
        for m in (history or [])[-10:]:
            role = m.get("role", "user")
            if role == "assistant":
                role = "model"
            hist.append({"role": role, "content": m.get("content", "")})
        return self.client.chat(system + "\n\n---\n\nText: " + user_message, history=hist)
