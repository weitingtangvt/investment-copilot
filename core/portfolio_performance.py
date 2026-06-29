from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from core.portfolio_quantstats import build_portfolio_quant_analytics
from core.weekly_review_rebalancing import is_buy_like_op, is_sell_like_op


DEFAULT_FX = {
    "HKD": 1.0,
    "USD": 7.8,
    "CNY": 1.07,
    "EUR": 8.4,
    "JPY": 0.052,
    "KRW": 0.0056,
}

MANUAL_TICKER_ALIASES = {
    "NEBIUS": "SAMPLE",
}


def _normalized_rebalancing_op_type(value: Any) -> str:
    if is_buy_like_op(value):
        return "buy"
    if is_sell_like_op(value):
        return "sell"
    return ""


def _canonical_key(value: Any) -> str:
    return str(value or "").strip().upper()


def week_end_date(week_id: str) -> Optional[pd.Timestamp]:
    text = str(week_id or "").strip().upper()
    if "-W" not in text:
        return None
    try:
        year_text, week_text = text.split("-W", 1)
        return pd.Timestamp(datetime.fromisocalendar(int(year_text), int(week_text), 5)).normalize()
    except (TypeError, ValueError):
        return None


def _effective_anchor_date(week_id: str, *, today: Optional[pd.Timestamp] = None) -> Optional[pd.Timestamp]:
    week_end = week_end_date(week_id)
    if week_end is None:
        return None
    now = pd.Timestamp(today if today is not None else datetime.now()).normalize()
    week_start = week_end - pd.Timedelta(days=4)
    if week_start > now:
        return None
    if week_end > now:
        return now
    return week_end


def detect_currency(ticker: str) -> str:
    code = str(ticker or "").strip().upper()
    if code.endswith(".HK"):
        return "HKD"
    if code.endswith((".SH", ".SZ", ".SS")):
        return "CNY"
    if code.endswith((".DE", ".AS", ".VI")):
        return "EUR"
    if code.endswith(".T"):
        return "JPY"
    if code.endswith((".KS", ".KQ")):
        return "KRW"
    return "USD"


def fx_rate_for(review: Dict[str, Any], currency: str) -> float:
    ccy = str(currency or "USD").upper()
    if ccy == "HKD":
        return 1.0
    key = {
        "USD": "usd_to_hkd",
        "CNY": "cny_to_hkd",
        "EUR": "eur_to_hkd",
        "JPY": "jpy_to_hkd",
        "KRW": "krw_to_hkd",
    }.get(ccy)
    if key:
        try:
            value = float((review or {}).get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return DEFAULT_FX.get(ccy, DEFAULT_FX["USD"])


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _close_series(frame: pd.DataFrame) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    data = frame.copy()
    data.index = pd.to_datetime(data.index).normalize()
    close_col = "Close" if "Close" in data.columns else "close" if "close" in data.columns else None
    if close_col is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(data[close_col], errors="coerce").dropna().sort_index()


def _price_on_or_before(series: pd.Series, day: pd.Timestamp) -> Optional[float]:
    if series.empty:
        return None
    eligible = series[series.index <= day]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1])


def _price_on_or_after(series: pd.Series, day: pd.Timestamp) -> Optional[float]:
    if series.empty:
        return None
    eligible = series[series.index >= day]
    if eligible.empty:
        return None
    return float(eligible.iloc[0])


def _price_on_or_near(series: pd.Series, day: pd.Timestamp) -> Optional[float]:
    return _price_on_or_before(series, day) or _price_on_or_after(series, day)


def resolve_portfolio_ticker_alias(ticker: Any, ticker_aliases: Optional[Dict[str, str]] = None) -> str:
    raw = _canonical_key(ticker)
    if not raw:
        return ""
    return (ticker_aliases or {}).get(raw) or MANUAL_TICKER_ALIASES.get(raw) or raw


def build_portfolio_ticker_aliases(reviews: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for raw, canonical in MANUAL_TICKER_ALIASES.items():
        aliases[_canonical_key(raw)] = _canonical_key(canonical)
    for review in reviews or []:
        for stock_id, payload in ((review or {}).get("stocks") or {}).items():
            if not isinstance(payload, dict):
                continue
            ticker = _canonical_key(payload.get("ticker"))
            if not ticker:
                continue
            canonical = aliases.get(ticker, ticker)
            for value in (stock_id, payload.get("ticker"), payload.get("stock_name"), payload.get("search_name")):
                key = _canonical_key(value)
                if key:
                    aliases[key] = canonical
        for op in (review or {}).get("rebalancing_ops") or []:
            if not isinstance(op, dict):
                continue
            ticker = _canonical_key(op.get("ticker"))
            stock_id = _canonical_key(op.get("stock_id"))
            canonical = aliases.get(ticker) or aliases.get(stock_id) or ticker
            if not canonical:
                continue
            if ticker:
                aliases[ticker] = canonical
            if stock_id:
                aliases[stock_id] = canonical
    return aliases


def _review_anchor_price_with_source(
    review: Dict[str, Any],
    ticker: str,
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[float], Optional[str]]:
    lookup = resolve_portfolio_ticker_alias(ticker, ticker_aliases)
    if not lookup:
        return None, None
    for stock_id, payload in ((review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        candidate = resolve_portfolio_ticker_alias(payload.get("ticker") or stock_id, ticker_aliases)
        if candidate != lookup:
            continue
        perf = payload.get("performance_data") or {}
        price = _safe_float(perf.get("end_price"))
        if price is not None and price > 0:
            return price, "performance_data.end_price"
        price = _safe_float(payload.get("avg_cost"))
        if price is not None and price > 0:
            return price, "avg_cost"
    for op in ((review or {}).get("rebalancing_ops") or []):
        if not isinstance(op, dict):
            continue
        op_type = _normalized_rebalancing_op_type(op.get("op_type") or op.get("action"))
        if op_type != "buy":
            continue
        raw_stock_id = resolve_portfolio_ticker_alias(op.get("stock_id") or op.get("ticker"), ticker_aliases)
        if raw_stock_id != lookup:
            continue
        price = _safe_float(op.get("price"))
        if price is not None and price > 0:
            return price, "rebalancing_op.price"
    return None, None


def _review_anchor_price(
    review: Dict[str, Any],
    ticker: str,
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> Optional[float]:
    price, _ = _review_anchor_price_with_source(review, ticker, ticker_aliases)
    return price


def _review_price_on_or_before(
    review: Dict[str, Any],
    ticker: str,
    day: pd.Timestamp,
    price_series: Dict[str, pd.Series],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[float], Optional[str]]:
    canonical_ticker = resolve_portfolio_ticker_alias(ticker, ticker_aliases)
    history_price = _price_on_or_before(price_series.get(canonical_ticker, pd.Series(dtype=float)), day)
    if history_price is not None:
        return history_price, "history"
    return _review_anchor_price_with_source(review, canonical_ticker, ticker_aliases)


def _review_positions(
    review: Dict[str, Any],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    rows = []
    for stock_id, payload in ((review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        shares = _safe_float(payload.get("shares_held")) or 0.0
        if shares <= 0:
            continue
        ticker = resolve_portfolio_ticker_alias(payload.get("ticker") or stock_id, ticker_aliases)
        if not ticker:
            continue
        rows.append(
            {
                "stock_id": str(stock_id),
                "ticker": ticker,
                "shares": shares,
                "currency": detect_currency(ticker),
            }
        )
    return rows


def _review_positions_by_ticker(
    review: Dict[str, Any],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    positions: Dict[str, float] = {}
    for position in _review_positions(review, ticker_aliases):
        positions[position["ticker"]] = positions.get(position["ticker"], 0.0) + float(position["shares"])
    return positions


def _ticker_alias_map(
    review: Dict[str, Any],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for stock_id, payload in ((review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        ticker = resolve_portfolio_ticker_alias(payload.get("ticker") or stock_id, ticker_aliases)
        if not ticker:
            continue
        for value in (stock_id, payload.get("ticker"), payload.get("stock_name")):
            key = _canonical_key(value)
            if key:
                aliases[key] = ticker
    for key, value in (ticker_aliases or {}).items():
        if key and value:
            aliases[key] = value
    return aliases


def _review_rebalancing_cash_hkd(
    review: Dict[str, Any],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> float:
    """Return cash generated by internal weekly rebalancing in HKD.

    Positive means sells raised cash; negative means buys consumed cash. This is
    deliberately not an external cash flow for TWR.
    """
    aliases = _ticker_alias_map(review, ticker_aliases)
    total = 0.0
    for op in ((review or {}).get("rebalancing_ops") or []):
        if not isinstance(op, dict):
            continue
        op_type = _normalized_rebalancing_op_type(op.get("op_type") or op.get("action"))
        if not op_type:
            continue
        quantity = _safe_float(op.get("quantity") or op.get("shares"))
        price = _safe_float(op.get("price"))
        if quantity is None or price is None or quantity <= 0 or price <= 0:
            continue
        raw_stock_id = _canonical_key(op.get("stock_id") or op.get("ticker"))
        ticker = aliases.get(raw_stock_id) or resolve_portfolio_ticker_alias(raw_stock_id, ticker_aliases)
        currency = detect_currency(ticker)
        amount = quantity * price * fx_rate_for(review, currency)
        total += amount if op_type == "sell" else -amount
    return round(total, 2)


def _review_rebalancing_cash_by_date_hkd(
    review: Dict[str, Any],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> Dict[pd.Timestamp, float]:
    aliases = _ticker_alias_map(review, ticker_aliases)
    cash_by_date: Dict[pd.Timestamp, float] = {}
    fallback_day = week_end_date(str((review or {}).get("week_id") or ""))
    for op in ((review or {}).get("rebalancing_ops") or []):
        if not isinstance(op, dict):
            continue
        op_type = _normalized_rebalancing_op_type(op.get("op_type") or op.get("action"))
        if not op_type:
            continue
        quantity = _safe_float(op.get("quantity") or op.get("shares"))
        price = _safe_float(op.get("price"))
        if quantity is None or price is None or quantity <= 0 or price <= 0:
            continue
        raw_stock_id = _canonical_key(op.get("stock_id") or op.get("ticker"))
        ticker = aliases.get(raw_stock_id) or resolve_portfolio_ticker_alias(raw_stock_id, ticker_aliases)
        currency = detect_currency(ticker)
        amount = quantity * price * fx_rate_for(review, currency)
        signed = amount if op_type == "sell" else -amount
        parsed_day = pd.to_datetime(op.get("date"), errors="coerce")
        day = pd.Timestamp(parsed_day).normalize() if not pd.isna(parsed_day) else fallback_day
        if day is None:
            continue
        cash_by_date[day] = round(cash_by_date.get(day, 0.0) + signed, 2)
    return cash_by_date


def _resolve_rebalancing_op_ticker(
    raw_stock_id: str,
    op_type: str,
    quantity: float,
    aliases: Dict[str, str],
    previous_positions: Dict[str, float],
    anchor_positions: Dict[str, float],
) -> str:
    ticker = aliases.get(raw_stock_id, raw_stock_id)
    if ticker in previous_positions or ticker in anchor_positions:
        return ticker

    candidates = []
    for candidate in sorted(set(previous_positions) | set(anchor_positions)):
        before = float(previous_positions.get(candidate, 0.0) or 0.0)
        after = float(anchor_positions.get(candidate, 0.0) or 0.0)
        if op_type == "sell" and before > after and abs((before - after) - quantity) <= 1e-6:
            candidates.append(candidate)
        elif op_type == "buy" and after > before and abs((after - before) - quantity) <= 1e-6:
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    return ticker


def _review_rebalancing_ops_by_date(
    review: Dict[str, Any],
    *,
    previous_review: Optional[Dict[str, Any]] = None,
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> Dict[pd.Timestamp, List[Dict[str, Any]]]:
    aliases = _ticker_alias_map(previous_review or {}, ticker_aliases)
    aliases.update(_ticker_alias_map(review, ticker_aliases))
    previous_positions = _review_positions_by_ticker(previous_review or {}, ticker_aliases)
    anchor_positions = _review_positions_by_ticker(review, ticker_aliases)
    ops_by_date: Dict[pd.Timestamp, List[Dict[str, Any]]] = {}
    fallback_day = week_end_date(str((review or {}).get("week_id") or ""))
    for op in ((review or {}).get("rebalancing_ops") or []):
        if not isinstance(op, dict):
            continue
        op_type = _normalized_rebalancing_op_type(op.get("op_type") or op.get("action"))
        if not op_type:
            continue
        quantity = _safe_float(op.get("quantity") or op.get("shares"))
        price = _safe_float(op.get("price"))
        if quantity is None or price is None or quantity <= 0 or price <= 0:
            continue
        raw_stock_id = _canonical_key(op.get("stock_id") or op.get("ticker"))
        ticker = _resolve_rebalancing_op_ticker(
            raw_stock_id,
            op_type,
            float(quantity),
            aliases,
            previous_positions,
            anchor_positions,
        )
        if not ticker:
            continue
        ticker = resolve_portfolio_ticker_alias(ticker, ticker_aliases)
        amount_hkd = _safe_float(op.get("amount_hkd"))
        price_hkd = (amount_hkd / quantity) if amount_hkd is not None and amount_hkd > 0 else price * fx_rate_for(review, detect_currency(ticker))
        parsed_day = pd.to_datetime(op.get("date"), errors="coerce")
        day = pd.Timestamp(parsed_day).normalize() if not pd.isna(parsed_day) else fallback_day
        if day is None:
            continue
        ops_by_date.setdefault(day, []).append(
            {
                "ticker": ticker,
                "op_type": op_type,
                "quantity": float(quantity),
                "price_hkd": float(price_hkd),
            }
        )
    return ops_by_date


def _apply_position_ops(positions: Dict[str, float], ops: List[Dict[str, Any]]) -> float:
    internal_cash = 0.0
    for op in ops or []:
        ticker = str(op.get("ticker") or "").strip().upper()
        quantity = _safe_float(op.get("quantity")) or 0.0
        price_hkd = _safe_float(op.get("price_hkd")) or 0.0
        if not ticker or quantity <= 0:
            continue
        if str(op.get("op_type") or "").lower() == "sell":
            held = max(float(positions.get(ticker, 0.0) or 0.0), 0.0)
            applied_quantity = min(quantity, held)
            if applied_quantity <= 0:
                continue
            positions[ticker] = held - applied_quantity
            internal_cash += applied_quantity * price_hkd
        else:
            positions[ticker] = positions.get(ticker, 0.0) + quantity
            internal_cash -= quantity * price_hkd
        if abs(positions.get(ticker, 0.0)) < 1e-9:
            positions[ticker] = 0.0
    return round(internal_cash, 2)


def _positions_market_value_hkd(
    positions: Dict[str, float],
    review: Dict[str, Any],
    day: pd.Timestamp,
    price_series: Dict[str, pd.Series],
    fallback_values_hkd: Optional[Dict[str, float]] = None,
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[float], List[str]]:
    total = 0.0
    missing = []
    for ticker, shares in positions.items():
        ticker = resolve_portfolio_ticker_alias(ticker, ticker_aliases)
        if abs(float(shares or 0.0)) <= 1e-9:
            continue
        price = _price_on_or_before(price_series.get(ticker, pd.Series(dtype=float)), day)
        if price is None:
            price, _ = _review_anchor_price_with_source(review, ticker, ticker_aliases)
        if price is None:
            fallback_value = (fallback_values_hkd or {}).get(ticker)
            if fallback_value is not None:
                total += float(fallback_value)
                continue
            missing.append(ticker)
            continue
        total += float(shares) * price * fx_rate_for(review, detect_currency(ticker))
    if missing:
        return None, sorted(set(missing))
    return round(total, 2), []


def _security_return_context(
    positions: Dict[str, float],
    review: Dict[str, Any],
    day: pd.Timestamp,
    previous_total_hkd: float,
    price_series: Dict[str, pd.Series],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Dict[str, float], Dict[str, float]]:
    security_returns: Dict[str, float] = {}
    security_weights: Dict[str, float] = {}
    previous_day = day - pd.Timedelta(days=1)
    for raw_ticker, shares in positions.items():
        ticker = resolve_portfolio_ticker_alias(raw_ticker, ticker_aliases)
        share_count = float(shares or 0.0)
        if not ticker or abs(share_count) <= 1e-9:
            continue
        series = price_series.get(ticker, pd.Series(dtype=float))
        previous_price = _price_on_or_before(series, previous_day)
        current_price = _price_on_or_before(series, day)
        if previous_price is None or current_price is None or previous_price <= 0:
            continue
        fx_rate = fx_rate_for(review, detect_currency(ticker))
        previous_value = share_count * previous_price * fx_rate
        if previous_total_hkd > 0:
            security_weights[ticker] = round(previous_value / previous_total_hkd, 8)
        security_returns[ticker] = round(current_price / previous_price - 1.0, 8)
    return security_returns, security_weights


def _position_delta_cash_hkd(
    before_positions: Dict[str, float],
    after_positions: Dict[str, float],
    review: Dict[str, Any],
    day: pd.Timestamp,
    price_series: Dict[str, pd.Series],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[float], List[str]]:
    """Cash effect needed to reconcile simulated shares to anchor shares.

    Positive means the unrecorded delta raised cash; negative means it consumed
    cash. This is an internal adjustment, not external flow.
    """
    total = 0.0
    missing = []
    canonical_before: Dict[str, float] = {}
    canonical_after: Dict[str, float] = {}
    for raw_ticker, shares in before_positions.items():
        ticker = resolve_portfolio_ticker_alias(raw_ticker, ticker_aliases)
        canonical_before[ticker] = canonical_before.get(ticker, 0.0) + float(shares or 0.0)
    for raw_ticker, shares in after_positions.items():
        ticker = resolve_portfolio_ticker_alias(raw_ticker, ticker_aliases)
        canonical_after[ticker] = canonical_after.get(ticker, 0.0) + float(shares or 0.0)
    canonical_tickers = set(canonical_before) | set(canonical_after)
    for ticker in sorted(canonical_tickers):
        before = float(canonical_before.get(ticker, 0.0) or 0.0)
        after = float(canonical_after.get(ticker, 0.0) or 0.0)
        delta = after - before
        if abs(delta) <= 1e-9:
            continue
        price = _price_on_or_before(price_series.get(ticker, pd.Series(dtype=float)), day)
        if price is None:
            price, _ = _review_anchor_price_with_source(review, ticker, ticker_aliases)
        if price is None:
            missing.append(ticker)
            continue
        total -= delta * price * fx_rate_for(review, detect_currency(ticker))
    if missing:
        return None, sorted(set(missing))
    return round(total, 2), []


def _review_position_values_by_ticker_hkd(
    review: Dict[str, Any],
    day: pd.Timestamp,
    price_series: Dict[str, pd.Series],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[Dict[str, float]], List[str], List[Dict[str, str]]]:
    values: Dict[str, float] = {}
    missing = []
    estimated_sources = []
    for position in _review_positions(review, ticker_aliases):
        ticker = position["ticker"]
        price, source = _review_price_on_or_before(review, ticker, day, price_series, ticker_aliases)
        if price is None:
            missing.append(ticker)
            continue
        if source in {"avg_cost", "rebalancing_op.price"}:
            estimated_sources.append(
                {
                    "ticker": ticker,
                    "week_id": str((review or {}).get("week_id") or ""),
                    "source": source,
                }
            )
        values[ticker] = round(position["shares"] * price * fx_rate_for(review, position["currency"]), 2)
    if missing:
        return None, sorted(set(missing)), estimated_sources
    return values, [], estimated_sources


def _portfolio_market_value_hkd(
    review: Dict[str, Any],
    day: pd.Timestamp,
    price_series: Dict[str, pd.Series],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[float], List[str], List[Dict[str, str]]]:
    total = 0.0
    missing = []
    estimated_sources = []
    for position in _review_positions(review, ticker_aliases):
        ticker = position["ticker"]
        price, source = _review_price_on_or_before(review, ticker, day, price_series, ticker_aliases)
        if price is None:
            missing.append(ticker)
            continue
        if source == "avg_cost":
            estimated_sources.append(
                {
                    "ticker": ticker,
                    "week_id": str((review or {}).get("week_id") or ""),
                    "source": source,
                }
            )
        total += position["shares"] * price * fx_rate_for(review, position["currency"])
    if missing:
        return None, sorted(set(missing)), estimated_sources
    return round(total, 2), [], estimated_sources


def _estimate_anchor_total_value_hkd(
    review: Dict[str, Any],
    day: pd.Timestamp,
    price_series: Dict[str, pd.Series],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> tuple[Optional[float], List[str], List[Dict[str, str]]]:
    values, missing, estimated_sources = _review_position_values_by_ticker_hkd(review, day, price_series, ticker_aliases)
    if missing:
        return None, missing, estimated_sources
    return round(sum((values or {}).values()), 2), [], estimated_sources


def _normalize_external_cash_flows(
    external_cash_flows_hkd: Optional[Iterable[Dict[str, Any]]],
) -> Dict[pd.Timestamp, List[Dict[str, Any]]]:
    flows: Dict[pd.Timestamp, List[Dict[str, Any]]] = {}
    for item in external_cash_flows_hkd or []:
        if not isinstance(item, dict):
            continue
        amount = _safe_float(item.get("amount_hkd", item.get("amount")))
        parsed_day = pd.to_datetime(item.get("date"), errors="coerce")
        if amount is None or pd.isna(parsed_day) or abs(amount) <= 0.01:
            continue
        day = pd.Timestamp(parsed_day).normalize()
        flows.setdefault(day, []).append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "amount_hkd": round(amount, 2),
                "source": str(item.get("source") or "known_external_cash_flow"),
            }
        )
    return flows


def _sum_flow_items(items: List[Dict[str, Any]]) -> float:
    return round(sum(float(item.get("amount_hkd") or 0.0) for item in items), 2)


def _ibkr_projected_data_quality(reviews: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    fallback_tickers: set[str] = set()
    missing_tickers: set[str] = set()
    integrity_issues: List[Dict[str, Any]] = []
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        quality = review.get("ibkr_derived_data_quality") or {}
        for ticker in quality.get("price_fallback_tickers") or []:
            text = str(ticker or "").strip().upper()
            if text:
                fallback_tickers.add(text)
        for ticker in quality.get("missing_price_tickers") or []:
            text = str(ticker or "").strip().upper()
            if text:
                missing_tickers.add(text)
        for issue in quality.get("position_integrity_issues") or []:
            if isinstance(issue, dict):
                integrity_issues.append(dict(issue))
    return {
        "ibkr_price_fallback_tickers": sorted(fallback_tickers),
        "ibkr_missing_price_tickers": sorted(missing_tickers),
        "ibkr_position_integrity_issues": integrity_issues,
    }


def _is_ibkr_derived_review(review: Dict[str, Any]) -> bool:
    return str((review or {}).get("portfolio_source") or "") == "ibkr_derived_ledger"


def _first_reported_anchor_index(normalized: List[Dict[str, Any]]) -> Optional[int]:
    for index, row in enumerate(normalized):
        if str(row.get("total_value_source") or "") == "reported_total_portfolio_value":
            return index
    return None


def _infer_ibkr_starting_cash_hkd(
    normalized: List[Dict[str, Any]],
    first_reported_index: Optional[int],
    price_series: Dict[str, pd.Series],
    external_flows_by_date: Dict[pd.Timestamp, List[Dict[str, Any]]],
    ticker_aliases: Optional[Dict[str, str]] = None,
) -> float:
    """Infer cash missing from early IBKR market-value-only anchors.

    Before the first reported account NAV, IBKR-derived weekly reviews may only
    have positions marked to market. Those estimated anchors should not force
    modeled cash back to zero. The first reported NAV gap is therefore usually
    starting cash that existed before the YTD period, not a deposit on the first
    reported NAV date.
    """
    if first_reported_index is None or first_reported_index <= 0:
        return 0.0
    first_reported_row = normalized[first_reported_index]
    if not _is_ibkr_derived_review(first_reported_row.get("review") or {}):
        return 0.0
    prefix = normalized[:first_reported_index]
    if not prefix:
        return 0.0
    if any(str(row.get("total_value_source") or "") != "estimated_positions_market_value" for row in prefix):
        return 0.0
    if any(not _is_ibkr_derived_review(row.get("review") or {}) for row in normalized[: first_reported_index + 1]):
        return 0.0

    modeled_cash = 0.0
    for index, row in enumerate(normalized[: first_reported_index + 1]):
        anchor_day = row["date"]
        review = row["review"]
        reported_total = float(row["total_value"] or 0.0)
        anchor_values, missing, _ = _review_position_values_by_ticker_hkd(
            review,
            anchor_day,
            price_series,
            ticker_aliases,
        )
        if missing:
            return 0.0
        market_value = round(sum((anchor_values or {}).values()), 2)
        if index == 0:
            continue

        previous_day = normalized[index - 1]["date"]
        previous_review = normalized[index - 1]["review"]
        simulated_positions = _review_positions_by_ticker(previous_review, ticker_aliases)
        anchor_positions = _review_positions_by_ticker(review, ticker_aliases)
        ops_by_date = _review_rebalancing_ops_by_date(
            review,
            previous_review=previous_review,
            ticker_aliases=ticker_aliases,
        )
        calendar = pd.date_range(previous_day + pd.Timedelta(days=1), anchor_day, freq="D")
        for day in calendar:
            modeled_cash += _sum_flow_items(external_flows_by_date.get(day, []))
            modeled_cash += _apply_position_ops(simulated_positions, ops_by_date.get(day, []))
            is_anchor = day == anchor_day
            if not is_anchor:
                continue

            anchor_delta_cash, missing = _position_delta_cash_hkd(
                simulated_positions,
                anchor_positions,
                review,
                day,
                price_series,
                ticker_aliases,
            )
            if missing:
                return 0.0
            modeled_cash += anchor_delta_cash or 0.0
            market_value, missing = _positions_market_value_hkd(
                dict(anchor_positions),
                review,
                day,
                price_series,
                ticker_aliases=ticker_aliases,
            )
            if missing:
                return 0.0
            implied_cash = round(reported_total - float(market_value or 0.0) - modeled_cash, 2)
            if index == first_reported_index:
                return implied_cash if implied_cash > 0 else 0.0

    return 0.0


def calculate_weekly_portfolio_performance(
    reviews: Iterable[Dict[str, Any]],
    price_frames: Dict[str, pd.DataFrame],
    *,
    benchmark: str = "QQQ",
    external_cash_flows_hkd: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a daily NAV/TWR payload for the weekly review page.

    Daily marks are simulated between weekly review anchors. Known deposits or
    withdrawals are applied on their actual date. Remaining account-value gaps
    are reconciled at weekly anchors as implied cash that adjusts the modeled
    cash balance so NAV matches the reported total.

    Trade cash from weekly rebalancing operations is internal portfolio cash,
    not external flow for TWR.

    Implied reconciliation cash is treated as an external flow for TWR only
    when anchor prices are reliable (from market history). When prices use
    avg_cost or rebalancing_op fallbacks, the implied cash gap is dominated by
    valuation errors rather than real deposits/withdrawals, so it is excluded
    from the TWR denominator to avoid diluting returns far below the broker's
    TWR. This matches IBKR's methodology where actual account NAV is used at
    each valuation point.

    Daily TWR formula with start-of-day external flows:
        return_t = end_nav_t / (start_nav_t + external_flow_t) - 1
    """

    review_list = [review for review in reviews if isinstance(review, dict)]
    ibkr_data_quality = _ibkr_projected_data_quality(review_list)
    ticker_aliases = build_portfolio_ticker_aliases(review_list)
    aliased_price_frames: Dict[str, pd.DataFrame] = {}
    for ticker, frame in (price_frames or {}).items():
        canonical = resolve_portfolio_ticker_alias(ticker, ticker_aliases)
        if not canonical:
            continue
        existing = aliased_price_frames.get(canonical)
        if existing is None or existing.empty:
            aliased_price_frames[canonical] = frame
        elif frame is not None and not frame.empty:
            merged = pd.concat([existing, frame]).sort_index()
            aliased_price_frames[canonical] = merged[~merged.index.duplicated(keep="last")]

    normalized = []
    estimated_anchor_values: List[Dict[str, Any]] = []
    seen_reported_nav_anchor = False
    preflight_price_series = {
        str(ticker).strip().upper(): _close_series(frame)
        for ticker, frame in aliased_price_frames.items()
    }
    for index, review in enumerate(review_list):
        day = _effective_anchor_date(str((review or {}).get("week_id") or ""))
        if day is None:
            continue
        total_value = _safe_float((review or {}).get("total_portfolio_value"))
        total_value_source = "reported_total_portfolio_value"
        if total_value is None:
            estimated_total, missing, estimated_sources = _estimate_anchor_total_value_hkd(
                review,
                day,
                preflight_price_series,
                ticker_aliases,
            )
            if missing:
                continue
            is_ibkr_derived = str((review or {}).get("portfolio_source") or "") == "ibkr_derived_ledger"
            if is_ibkr_derived and seen_reported_nav_anchor:
                estimated_anchor_values.append(
                    {
                        "week_id": str((review or {}).get("week_id") or ""),
                        "date": day.strftime("%Y-%m-%d"),
                        "estimated_total_value_hkd": round(estimated_total or 0.0, 2),
                        "source": "estimated_positions_market_value_not_used_as_nav",
                        "estimated_price_sources": estimated_sources,
                    }
                )
                continue
            total_value = estimated_total
            total_value_source = "estimated_positions_market_value"
            estimated_anchor_values.append(
                {
                    "week_id": str((review or {}).get("week_id") or ""),
                    "date": day.strftime("%Y-%m-%d"),
                    "estimated_total_value_hkd": round(total_value or 0.0, 2),
                    "source": total_value_source,
                    "estimated_price_sources": estimated_sources,
                }
            )
        normalized.append({"date": day, "review": review, "total_value": total_value, "total_value_source": total_value_source})
        if total_value_source == "reported_total_portfolio_value":
            seen_reported_nav_anchor = True
    normalized.sort(key=lambda row: row["date"])
    if not normalized:
        error = "insufficient_reported_nav_anchors" if estimated_anchor_values else "no_weekly_portfolio_values"
        return {
            "success": False,
            "error": error,
            "series": [],
            "cash_flows": [],
            "estimated_anchor_values": estimated_anchor_values,
            "data_quality": ibkr_data_quality,
        }

    price_series = {str(ticker).strip().upper(): _close_series(frame) for ticker, frame in aliased_price_frames.items()}
    all_required = {resolve_portfolio_ticker_alias(benchmark or "QQQ", ticker_aliases)}
    for row in normalized:
        for position in _review_positions(row["review"], ticker_aliases):
            all_required.add(position["ticker"])
    missing_tickers = sorted(
        ticker
        for ticker in all_required
        if price_series.get(ticker, pd.Series(dtype=float)).empty
        and all(_review_anchor_price(row["review"], ticker, ticker_aliases) is None for row in normalized)
    )
    if missing_tickers:
        return {
            "success": False,
            "error": "insufficient_price_data",
            "missing_tickers": missing_tickers,
            "series": [],
            "cash_flows": [],
        }
    if len(normalized) < 2:
        return {
            "success": False,
            "error": "insufficient_reported_nav_anchors",
            "series": [],
            "cash_flows": [],
            "estimated_anchor_values": estimated_anchor_values,
            "data_quality": ibkr_data_quality,
        }

    benchmark_key = resolve_portfolio_ticker_alias(benchmark or "QQQ", ticker_aliases)
    benchmark_base = _price_on_or_before(price_series.get(benchmark_key, pd.Series(dtype=float)), normalized[0]["date"])
    if benchmark_base is None:
        return {"success": False, "error": "missing_benchmark_start_price", "series": [], "cash_flows": []}

    external_flows_by_date = _normalize_external_cash_flows(external_cash_flows_hkd)
    pre_start_known_flows: List[Dict[str, Any]] = []
    first_anchor = normalized[0]["date"]
    for flow_day, flow_items in list(external_flows_by_date.items()):
        if flow_day <= first_anchor:
            pre_start_known_flows.extend(flow_items)
            del external_flows_by_date[flow_day]
    first_reported_index = _first_reported_anchor_index(normalized)
    inferred_starting_cash = _infer_ibkr_starting_cash_hkd(
        normalized,
        first_reported_index,
        price_series,
        external_flows_by_date,
        ticker_aliases,
    )
    modeled_cash = inferred_starting_cash
    previous_total = 0.0
    cumulative_growth = 1.0
    series_rows: List[Dict[str, Any]] = []
    cash_flow_rows: List[Dict[str, Any]] = []
    if inferred_starting_cash > 0.01 and first_reported_index is not None:
        cash_flow_rows.append(
            {
                "date": normalized[first_reported_index]["date"].strftime("%Y-%m-%d"),
                "amount_hkd": round(inferred_starting_cash, 2),
                "source": "inferred_starting_cash_reconciliation",
            }
        )
    estimated_price_sources: List[Dict[str, str]] = []
    all_missing: set[str] = set()

    for index, row in enumerate(normalized):
        anchor_day = row["date"]
        review = row["review"]
        reported_total = row["total_value"]
        total_value_source = str(row.get("total_value_source") or "reported_total_portfolio_value")
        is_ibkr_derived_anchor = _is_ibkr_derived_review(review)
        anchor_values, missing, anchor_estimated_sources = _review_position_values_by_ticker_hkd(review, anchor_day, price_series, ticker_aliases)
        if missing:
            all_missing.update(missing)
            continue
        estimated_price_sources.extend(anchor_estimated_sources)
        anchor_values = anchor_values or {}
        market_value = round(sum(anchor_values.values()), 2)
        anchor_uses_estimated_prices = bool(anchor_estimated_sources)
        pre_reported_ibkr_estimated_anchor = (
            first_reported_index is not None
            and index < first_reported_index
            and inferred_starting_cash > 0.01
            and total_value_source == "estimated_positions_market_value"
        )
        if index == 0:
            cash_flow_rows.extend(pre_start_known_flows)
            if pre_reported_ibkr_estimated_anchor:
                implied_cash = 0.0
            else:
                implied_cash = round(reported_total - market_value - modeled_cash, 2)
                modeled_cash += implied_cash
            external_flow = 0.0
            nav = round(market_value + modeled_cash, 2)
            benchmark_price = _price_on_or_before(price_series.get(benchmark_key, pd.Series(dtype=float)), anchor_day)
            series_rows.append(
                {
                    "date": anchor_day.strftime("%Y-%m-%d"),
                    "week_id": str(review.get("week_id") or ""),
                    "is_reconciliation_anchor": True,
                    "portfolio_value_hkd": nav,
                    "reported_total_value_hkd": round(reported_total, 2),
                    "reported_total_value_source": total_value_source,
                    "market_value_hkd": round(market_value, 2),
                    "cash_balance_hkd": round(modeled_cash, 2),
                    "internal_rebalancing_cash_hkd": 0.0,
                    "implied_cash_flow_hkd": 0.0,
                    "explicit_cash_flow_hkd": 0.0,
                    "period_return": 0.0,
                    "portfolio_twr": 0.0,
                    "benchmark_return": None if benchmark_price is None else benchmark_price / benchmark_base - 1.0,
                    "security_returns": {},
                    "security_weights": {},
                }
            )
            previous_total = nav
            continue

        previous_day = normalized[index - 1]["date"]
        previous_review = normalized[index - 1]["review"]
        prev_values, missing, estimated_sources = _review_position_values_by_ticker_hkd(
            previous_review,
            previous_day,
            price_series,
            ticker_aliases,
        )
        if missing:
            all_missing.update(missing)
            continue
        estimated_price_sources.extend(estimated_sources)
        prev_values = prev_values or {}
        simulated_positions = _review_positions_by_ticker(previous_review, ticker_aliases)
        anchor_positions = _review_positions_by_ticker(review, ticker_aliases)
        ops_by_date = _review_rebalancing_ops_by_date(review, previous_review=previous_review, ticker_aliases=ticker_aliases)
        calendar = pd.date_range(previous_day + pd.Timedelta(days=1), anchor_day, freq="D")

        for offset, day in enumerate(calendar, start=1):
            explicit_items = external_flows_by_date.get(day, [])
            explicit_flow = _sum_flow_items(explicit_items)
            for item in explicit_items:
                cash_flow_rows.append(item)
            modeled_cash += explicit_flow

            internal_rebalancing_cash = _apply_position_ops(simulated_positions, ops_by_date.get(day, []))
            modeled_cash += internal_rebalancing_cash

            implied_cash = 0.0
            is_anchor = day == anchor_day
            anchor_delta_cash = 0.0
            if is_anchor:
                anchor_delta_cash, missing = _position_delta_cash_hkd(
                    simulated_positions,
                    anchor_positions,
                    review,
                    day,
                    price_series,
                    ticker_aliases,
                )
                if missing:
                    all_missing.update(missing)
                    continue
                anchor_delta_cash = anchor_delta_cash or 0.0
                internal_rebalancing_cash = round(internal_rebalancing_cash + anchor_delta_cash, 2)
                modeled_cash += anchor_delta_cash
            valuation_positions = dict(anchor_positions if is_anchor else simulated_positions)
            fallback_values = None if is_anchor else {**prev_values, **anchor_values}
            market_value, missing = _positions_market_value_hkd(
                valuation_positions,
                review,
                day,
                price_series,
                fallback_values_hkd=fallback_values,
                ticker_aliases=ticker_aliases,
            )
            if missing:
                all_missing.update(missing)
                continue
            market_value = market_value or 0.0
            if is_anchor:
                if pre_reported_ibkr_estimated_anchor:
                    implied_cash = 0.0
                    treat_implied_as_starting_cash = False
                    implied_cash_for_twr = 0.0
                else:
                    implied_cash = round(reported_total - market_value - modeled_cash, 2)
                    treat_implied_as_starting_cash = (
                        inferred_starting_cash > 0.01
                        and first_reported_index is not None
                        and index == first_reported_index
                        and abs(implied_cash) <= 0.01
                    )
                    if treat_implied_as_starting_cash:
                        implied_cash = inferred_starting_cash
                        implied_cash_for_twr = 0.0
                    else:
                        implied_cash_for_twr = implied_cash
                        modeled_cash += implied_cash
                if abs(implied_cash) > 0.01 and not treat_implied_as_starting_cash:
                    cash_flow_rows.append(
                        {
                            "date": day.strftime("%Y-%m-%d"),
                            "amount_hkd": round(implied_cash, 2),
                            "source": "implied_weekly_total_reconciliation",
                        }
                    )

            nav = round(market_value + modeled_cash, 2)
            # TWR denominator: include implied_cash as external flow only when
            # anchor prices are reliable (from market history). When prices use
            # avg_cost/rebalancing_op fallback, implied_cash is dominated by
            # valuation errors, not real deposits/withdrawals - including it
            # would dilute TWR far below the broker's value. When prices are
            # reliable, implied_cash genuinely represents unrecorded
            # deposits/withdrawals and must be in the denominator to avoid
            # false TWR gains from cash movements.
            if is_anchor and anchor_uses_estimated_prices:
                twr_external_flow = explicit_flow
            else:
                twr_external_flow = explicit_flow + (implied_cash_for_twr if is_anchor else implied_cash)
            investable_start = previous_total + twr_external_flow
            if abs(investable_start) <= 1e-9:
                daily_return = 0.0
            else:
                daily_return = nav / investable_start - 1.0
            cumulative_growth *= 1.0 + daily_return
            benchmark_price = _price_on_or_near(price_series.get(benchmark_key, pd.Series(dtype=float)), day)
            benchmark_return = None if benchmark_price is None else benchmark_price / benchmark_base - 1.0
            security_returns, security_weights = _security_return_context(
                valuation_positions,
                review,
                day,
                previous_total,
                price_series,
                ticker_aliases,
            )

            series_rows.append(
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "week_id": str(review.get("week_id") or ""),
                    "is_reconciliation_anchor": is_anchor,
                    "portfolio_value_hkd": nav,
                    "reported_total_value_hkd": round(reported_total, 2) if is_anchor else None,
                    "reported_total_value_source": total_value_source if is_anchor else None,
                    "market_value_hkd": round(market_value, 2),
                    "cash_balance_hkd": round(modeled_cash, 2),
                    "internal_rebalancing_cash_hkd": round(internal_rebalancing_cash, 2),
                    "implied_cash_flow_hkd": round(implied_cash, 2),
                    "explicit_cash_flow_hkd": round(explicit_flow, 2),
                    "twr_external_cash_flow_hkd": round(twr_external_flow, 2),
                    "period_return": daily_return,
                    "portfolio_twr": cumulative_growth - 1.0,
                    "benchmark_return": benchmark_return,
                    "security_returns": security_returns,
                    "security_weights": security_weights,
                }
            )
            previous_total = nav

    if all_missing:
        return {
            "success": False,
            "error": "insufficient_price_data",
            "missing_tickers": sorted(all_missing),
            "series": series_rows,
            "cash_flows": cash_flow_rows,
            "data_quality": ibkr_data_quality,
        }
    if len(series_rows) < 2:
        return {"success": False, "error": "insufficient_series_points", "series": series_rows, "cash_flows": cash_flow_rows}

    deduped_estimated_sources: List[Dict[str, str]] = []
    seen_estimated_sources: set[tuple[str, str, str]] = set()
    for item in estimated_price_sources:
        key = (
            str(item.get("ticker") or ""),
            str(item.get("week_id") or ""),
            str(item.get("source") or ""),
        )
        if key in seen_estimated_sources:
            continue
        seen_estimated_sources.add(key)
        deduped_estimated_sources.append(item)

    last = series_rows[-1]
    portfolio_twr_pct = (last["portfolio_twr"] or 0.0) * 100.0
    benchmark_return_pct = (last["benchmark_return"] or 0.0) * 100.0
    reconciliation_sources = {"implied_weekly_total_reconciliation", "inferred_starting_cash_reconciliation"}
    explicit_total = sum(
        row["amount_hkd"]
        for row in cash_flow_rows
        if str(row.get("source") or "") not in reconciliation_sources
    )
    implied_total = sum(
        row["amount_hkd"]
        for row in cash_flow_rows
        if str(row.get("source") or "") in reconciliation_sources
    )
    quant_analytics = build_portfolio_quant_analytics(series_rows, benchmark=benchmark_key, reviews=review_list)
    return {
        "success": True,
        "benchmark": benchmark_key,
        "series": series_rows,
        "cash_flows": cash_flow_rows,
        "estimated_price_sources": deduped_estimated_sources,
        "estimated_anchor_values": estimated_anchor_values,
        "data_quality": ibkr_data_quality,
        "data_trust": {
            "summary": {
                "missing_price_count": len(ibkr_data_quality.get("ibkr_missing_price_tickers") or []),
                "fallback_price_count": len(ibkr_data_quality.get("ibkr_price_fallback_tickers") or []),
                "stale_price_count": len(ibkr_data_quality.get("stale_price_tickers") or []),
                "reconciliation_gap_count": int(ibkr_data_quality.get("reconciliation_gap_count") or 0),
            },
            "rows": [
                {"label": "Missing prices", "value": len(ibkr_data_quality.get("ibkr_missing_price_tickers") or [])},
                {"label": "Fallback prices", "value": len(ibkr_data_quality.get("ibkr_price_fallback_tickers") or [])},
                {"label": "Stale prices", "value": len(ibkr_data_quality.get("stale_price_tickers") or [])},
                {"label": "Reconciliation gaps", "value": int(ibkr_data_quality.get("reconciliation_gap_count") or 0)},
            ],
        },
        "quant_analytics": quant_analytics,
        "summary": {
            "start_date": series_rows[0]["date"],
            "end_date": series_rows[-1]["date"],
            "portfolio_twr_pct": round(portfolio_twr_pct, 4),
            "benchmark_return_pct": round(benchmark_return_pct, 4),
            "active_return_ppt": round(portfolio_twr_pct - benchmark_return_pct, 4),
            "explicit_cash_flow_total_hkd": round(explicit_total, 2),
            "implied_cash_flow_total_hkd": round(implied_total, 2),
            "point_count": len(series_rows),
        },
        "notes": [
            "TWR now uses a daily ledger simulation between weekly reconciliation anchors.",
            "Known external deposits are applied on their actual dates; remaining weekly account-value gaps are reported as implied reconciliation flows.",
            "Weekly buy/sell rebalancing cash is treated as internal portfolio cash, not external flow.",
            "Daily holdings between weekly end states are approximated from rebalancing ops and anchor holdings; broker-grade daily TWR still requires complete trade/cash ledger imports.",
            "Anchor prices fall back to weekly performance_data.end_price when OHLCV history is unavailable; avg_cost fallbacks are reported in estimated_price_sources.",
        ],
    }
