from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd


DEFAULT_FX_RATES = {
    "HKD": 1.0,
    "USD": 7.8,
    "EUR": 8.4,
    "JPY": 0.052,
    "KRW": 0.0056,
    "CNY": 1.07,
}


IBKR_DERIVED_EXCLUDED_INITIAL_POSITION_TICKERS = {"SAMPLE"}
IBKR_DELTA_BASELINE_WEEK_ID = "2026-W25"
IBKR_DELTA_BASELINE_SOURCE = "verified_weekly_review"
IBKR_DELTA_PROJECTION_SOURCE = "ibkr_w25_baseline_delta"
IBKR_DERIVED_SYNTHETIC_TRADES = [
    {
        "date": "2026-03-11",
        "trade_datetime": "2026-03-11T00:00:00",
        "source_row_number": 0,
        "week_id": "2026-W11",
        "ticker": "SAMPLE",
        "side": "SELL",
        "quantity": 150.0,
        "price": 135.0,
        "currency": "USD",
        "commission": 0.0,
        "net_cash": 20250.0,
        "net_cash_hkd": None,
        "base_currency": "",
        "description": "CORNING INC",
        "ibkr_symbol": "SAMPLE",
        "source": "ibkr_derived_hot_patch",
        "hot_patch_reason": "User-confirmed SAMPLE correction: assume 150 shares sold at 135 on 2026-03-11.",
    },
    {
        "date": "2026-05-11",
        "trade_datetime": "2026-05-11T00:00:00",
        "source_row_number": 0,
        "week_id": "2026-W20",
        "ticker": "SAMPLE",
        "side": "SELL",
        "quantity": 200.0,
        "price": 45.11,
        "currency": "USD",
        "commission": 0.0,
        "net_cash": 9022.0,
        "net_cash_hkd": None,
        "base_currency": "",
        "description": "ELEMENT SOLUTIONS INC",
        "ibkr_symbol": "SAMPLE",
        "source": "ibkr_derived_hot_patch",
        "hot_patch_reason": "User-confirmed SAMPLE correction: assume 200 shares sold at 45.11 on 2026-05-11.",
    },
    {
        "date": "2026-04-20",
        "trade_datetime": "2026-04-20T00:00:00",
        "source_row_number": 0,
        "week_id": "2026-W17",
        "ticker": "SAMPLE",
        "side": "SELL",
        "quantity": 2000.0,
        "price": 21.08,
        "currency": "HKD",
        "commission": 0.0,
        "net_cash": 42160.0,
        "net_cash_hkd": None,
        "base_currency": "",
        "description": "REDACTED CO LTD-H",
        "ibkr_symbol": "SAMPLE",
        "source": "ibkr_derived_hot_patch",
        "hot_patch_reason": "User-confirmed SAMPLE correction: assume 2000 shares sold at 21.08 on 2026-04-20.",
    }
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_from_text(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


def _week_id_for_day(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _week_end_date(week_id: str) -> date | None:
    text = str(week_id or "").strip().upper()
    if "-W" not in text:
        return None
    try:
        year_text, week_text = text.split("-W", 1)
        return datetime.fromisocalendar(int(year_text), int(week_text), 5).date()
    except (TypeError, ValueError):
        return None


def _currency_for_ticker(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.endswith(".HK"):
        return "HKD"
    if text.endswith((".SH", ".SZ", ".SS")):
        return "CNY"
    if text.endswith((".DE", ".AS", ".VI")):
        return "EUR"
    if text.endswith(".T"):
        return "JPY"
    if text.endswith((".KS", ".KQ")):
        return "KRW"
    return "USD"


def canonical_ibkr_trade_ticker(ticker: Any, currency: Any = None) -> str:
    text = str(ticker or "").strip().upper()
    ccy = str(currency or "").strip().upper()
    if text == "SAMPLE" and ccy == "EUR":
        return "SAMPLE"
    return text


def _fx_rates_for_review(review: dict[str, Any], base_rates: dict[str, float]) -> dict[str, float]:
    rates = dict(base_rates)
    for currency, key in (
        ("USD", "usd_to_hkd"),
        ("CNY", "cny_to_hkd"),
        ("EUR", "eur_to_hkd"),
        ("JPY", "jpy_to_hkd"),
        ("KRW", "krw_to_hkd"),
    ):
        value = _safe_float((review or {}).get(key), default=0.0)
        if value > 0:
            rates[currency] = value
    rates["HKD"] = 1.0
    return rates


def _fx_rates_for_day(
    day: date | None,
    fallback_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None,
) -> dict[str, float]:
    rates = dict(fallback_rates)
    if day is not None:
        daily = (fx_rates_by_date or {}).get(day.isoformat()) or {}
        for key, value in daily.items():
            fx = _safe_float(value, default=0.0)
            if fx > 0:
                rates[str(key).upper()] = fx
    rates["HKD"] = 1.0
    return rates


def _fx_rate_audit(
    currency: str,
    day: date | None,
    fallback_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None,
) -> dict[str, Any]:
    currency = str(currency or "HKD").upper()
    if currency == "HKD":
        return {"fx_to_hkd": 1.0, "fx_date_used": day.isoformat() if day else "", "fx_source": "base_currency"}
    daily = (fx_rates_by_date or {}).get(day.isoformat() if day else "") or {}
    daily_value = _safe_float(daily.get(currency), default=0.0)
    if daily_value > 0:
        return {"fx_to_hkd": round(daily_value, 6), "fx_date_used": day.isoformat() if day else "", "fx_source": "daily_fx"}
    fallback = _safe_float(fallback_rates.get(currency), DEFAULT_FX_RATES.get(currency, 1.0))
    return {"fx_to_hkd": round(fallback, 6), "fx_date_used": day.isoformat() if day else "", "fx_source": "weekly_review_or_default"}


def _close_series(frame: Any) -> pd.Series:
    if frame is None or getattr(frame, "empty", True):
        return pd.Series(dtype=float)
    data = frame.copy()
    data.index = pd.to_datetime(data.index).normalize()
    column = "Close" if "Close" in data.columns else "close" if "close" in data.columns else None
    if column is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(data[column], errors="coerce").dropna().sort_index()


def _price_on_or_before(price_frames: dict[str, Any], ticker: str, day: date) -> float | None:
    series = _close_series(price_frames.get(ticker))
    if series.empty:
        return None
    stamp = pd.Timestamp(day).normalize()
    eligible = series[series.index <= stamp]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1])


def _price_audit_on_or_before(price_frames: dict[str, Any], ticker: str, day: date) -> dict[str, Any] | None:
    series = _close_series(price_frames.get(ticker))
    if series.empty:
        return None
    stamp = pd.Timestamp(day).normalize()
    eligible = series[series.index <= stamp]
    if eligible.empty:
        return None
    used_stamp = eligible.index[-1]
    used_day = used_stamp.date()
    return {
        "close_price": float(eligible.iloc[-1]),
        "price_date_used": used_day.isoformat(),
        "stale_days": max(0, (day - used_day).days),
    }


def _initial_positions(first_review: dict[str, Any], fx_rates: dict[str, float]) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for stock_id, payload in ((first_review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        ticker = str(payload.get("ticker") or stock_id or "").strip().upper()
        shares = _safe_float(payload.get("shares_held"))
        if not ticker or ticker in IBKR_DERIVED_EXCLUDED_INITIAL_POSITION_TICKERS or shares <= 0:
            continue
        currency = _currency_for_ticker(ticker)
        avg_cost = _safe_float(payload.get("avg_cost"))
        positions[ticker] = {
            "ticker": ticker,
            "stock_name": str(payload.get("stock_name") or ticker),
            "shares": shares,
            "avg_cost": avg_cost,
            "currency": currency,
            "cost_basis_local": round(shares * avg_cost, 6),
            "cost_basis_hkd": round(shares * avg_cost * _safe_float(fx_rates.get(currency), DEFAULT_FX_RATES.get(currency, 1.0)), 6),
            "source": "weekly_review_initial_position",
        }
    return positions


def _initial_lots(first_review: dict[str, Any], fx_rates: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
    lots: dict[str, list[dict[str, Any]]] = {}
    for stock_id, payload in ((first_review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        ticker = str(payload.get("ticker") or stock_id or "").strip().upper()
        shares = _safe_float(payload.get("shares_held"))
        avg_cost = _safe_float(payload.get("avg_cost"))
        if not ticker or ticker in IBKR_DERIVED_EXCLUDED_INITIAL_POSITION_TICKERS or shares <= 0 or avg_cost <= 0:
            continue
        currency = _currency_for_ticker(ticker)
        fx = _safe_float(fx_rates.get(currency), DEFAULT_FX_RATES.get(currency, 1.0))
        lots.setdefault(ticker, []).append(
            {
                "ticker": ticker,
                "quantity": shares,
                "cost_basis_hkd": shares * avg_cost * fx,
                "entry_price": avg_cost,
                "entry_date": str(payload.get("buy_date") or ""),
                "currency": currency,
                "source": "weekly_review_initial_position",
            }
        )
    return lots


def build_ibkr_portfolio_baseline_snapshot(
    review: dict[str, Any],
    *,
    baseline_week_id: str = IBKR_DELTA_BASELINE_WEEK_ID,
) -> dict[str, Any]:
    fx_rates = _fx_rates_for_review(review or {}, DEFAULT_FX_RATES)
    positions: list[dict[str, Any]] = []
    for stock_id, payload in ((review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        shares = _safe_float(payload.get("shares_held"))
        if shares <= 0:
            continue
        ticker = str(payload.get("ticker") or stock_id or "").strip().upper()
        if not ticker:
            continue
        currency = str(payload.get("currency") or _currency_for_ticker(ticker)).strip().upper()
        avg_cost = _safe_float(payload.get("avg_cost"))
        fx = _safe_float(fx_rates.get(currency), DEFAULT_FX_RATES.get(currency, 1.0))
        positions.append(
            {
                "ticker": ticker,
                "stock_name": str(payload.get("stock_name") or ticker),
                "shares": round(shares, 8),
                "avg_cost": round(avg_cost, 6),
                "currency": currency,
                "cost_basis_local": round(shares * avg_cost, 6),
                "cost_basis_hkd": round(shares * avg_cost * fx, 6),
                "source": IBKR_DELTA_BASELINE_SOURCE,
            }
        )
    positions.sort(key=lambda row: str(row.get("ticker") or ""))
    baseline_date = _week_end_date(baseline_week_id)
    return {
        "baseline_week_id": baseline_week_id,
        "baseline_date": baseline_date.isoformat() if baseline_date else "",
        "source": IBKR_DELTA_BASELINE_SOURCE,
        "cash_balance_hkd": _safe_float((review or {}).get("cash_balance")),
        "positions": positions,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "notes": "User-verified W25 holdings baseline for IBKR-derived delta projection.",
    }


def _normalize_trade_side(value: Any) -> str:
    side = str(value or "").strip().upper()
    if side in {"BOT", "BUY", "B"}:
        return "BUY"
    if side in {"SLD", "SELL", "S"}:
        return "SELL"
    return side


def filter_ibkr_delta_trades(
    ledger: dict[str, Any],
    *,
    baseline_date: str | date,
) -> list[dict[str, Any]]:
    cutoff = _date_from_text(baseline_date)
    if cutoff is None:
        return []
    rows: list[dict[str, Any]] = []
    seen_execution_keys: set[tuple[str, ...]] = set()
    seen_economic_keys: set[tuple[Any, ...]] = set()
    for trade in (ledger or {}).get("trades") or []:
        if str(trade.get("asset_category") or "").upper() not in {"STK", "STOCK", "STOCKS"}:
            continue
        trade_day = _date_from_text(trade.get("trade_date") or trade.get("date") or trade.get("trade_datetime"))
        if trade_day is None or trade_day <= cutoff:
            continue
        currency = str(trade.get("currency") or "").strip().upper()
        ticker = canonical_ibkr_trade_ticker(
            trade.get("ticker") or trade.get("stock_id") or trade.get("symbol"),
            currency,
        )
        side = _normalize_trade_side(trade.get("side") or trade.get("action"))
        qty = abs(_safe_float(trade.get("quantity")))
        price = _safe_float(trade.get("price"))
        if not ticker or side not in {"BUY", "SELL"} or qty <= 0 or price <= 0:
            continue
        row = {
            **dict(trade),
            "date": trade_day.isoformat(),
            "trade_datetime": str(trade.get("trade_datetime") or trade.get("trade_date") or trade_day.isoformat()),
            "week_id": _week_id_for_day(trade_day),
            "ticker": ticker,
            "side": side,
            "quantity": qty,
            "price": price,
            "currency": currency or _currency_for_ticker(ticker),
            "commission": _safe_float(trade.get("commission")),
            "net_cash": trade.get("net_cash"),
            "net_cash_hkd": trade.get("net_cash_hkd") or trade.get("base_net_cash_hkd") or trade.get("base_net_cash"),
            "description": str(trade.get("description") or ""),
            "ibkr_symbol": str(trade.get("ibkr_symbol") or trade.get("symbol") or trade.get("ticker") or ""),
            "source": str(trade.get("source") or "ibkr_delta_ledger"),
        }
        execution_key = _trade_execution_dedupe_key(row)
        if execution_key is not None:
            if execution_key in seen_execution_keys:
                continue
            seen_execution_keys.add(execution_key)
        else:
            economic_key = _trade_economic_dedupe_key(row)
            if economic_key in seen_economic_keys:
                continue
            seen_economic_keys.add(economic_key)
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("trade_datetime") or row.get("date") or ""),
            int(_safe_float(row.get("source_row_number"), default=0.0)),
            str(row.get("ticker") or ""),
            str(row.get("side") or ""),
        ),
    )


def _baseline_positions_by_ticker(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for row in (baseline or {}).get("positions") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        shares = _safe_float(row.get("shares"))
        if not ticker or shares <= 0:
            continue
        positions[ticker] = {
            "ticker": ticker,
            "stock_name": str(row.get("stock_name") or ticker),
            "shares": round(shares, 8),
            "avg_cost": _safe_float(row.get("avg_cost")),
            "currency": str(row.get("currency") or _currency_for_ticker(ticker)).strip().upper(),
            "cost_basis_local": _safe_float(row.get("cost_basis_local"), shares * _safe_float(row.get("avg_cost"))),
            "cost_basis_hkd": _safe_float(row.get("cost_basis_hkd")),
            "source": str(row.get("source") or IBKR_DELTA_BASELINE_SOURCE),
        }
    return positions


def _external_cash_flow_amount(
    cash_flows: Iterable[dict[str, Any]],
    *,
    after_date: date | None,
    through_date: date | None,
) -> float:
    total = 0.0
    for flow in cash_flows or []:
        day = _date_from_text((flow or {}).get("date"))
        if day is None:
            continue
        if after_date is not None and day <= after_date:
            continue
        if through_date is not None and day > through_date:
            continue
        total += _safe_float((flow or {}).get("amount_hkd"))
    return round(total, 2)


def apply_ibkr_delta_trades_to_baseline(
    baseline: dict[str, Any],
    trades: list[dict[str, Any]],
    *,
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
    through_date: str | date | None = None,
    fx_rates: dict[str, float] | None = None,
    external_cash_flows_hkd: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cutoff = _date_from_text(through_date) if through_date else None
    baseline_day = _date_from_text((baseline or {}).get("baseline_date"))
    fallback_fx = dict(DEFAULT_FX_RATES)
    fallback_fx.update({str(k).upper(): float(v) for k, v in (fx_rates or {}).items() if v is not None})
    positions = _baseline_positions_by_ticker(baseline)
    cash_balance = _safe_float((baseline or {}).get("cash_balance_hkd"))
    diagnostics: dict[str, Any] = {"oversells": [], "applied_trade_count": 0}
    for trade in trades or []:
        trade_day = _date_from_text(trade.get("date") or trade.get("trade_date") or trade.get("trade_datetime"))
        if trade_day is None:
            continue
        if cutoff is not None and trade_day > cutoff:
            continue
        if baseline_day is not None and trade_day <= baseline_day:
            continue
        ticker = str(trade.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        side = _normalize_trade_side(trade.get("side"))
        qty = abs(_safe_float(trade.get("quantity")))
        price = _safe_float(trade.get("price"))
        if side not in {"BUY", "SELL"} or qty <= 0 or price <= 0:
            continue
        currency = str(trade.get("currency") or _currency_for_ticker(ticker)).strip().upper()
        rates = _fx_rates_for_day(trade_day, fallback_fx, fx_rates_by_date)
        fx = _safe_float(rates.get(currency), DEFAULT_FX_RATES.get(currency, 1.0))
        amount_hkd = _trade_cash_amount_hkd(trade, fallback_fx, fx_rates_by_date)
        if amount_hkd <= 0:
            amount_hkd = qty * price * fx
        current = positions.get(ticker) or {
            "ticker": ticker,
            "stock_name": str(trade.get("description") or ticker),
            "shares": 0.0,
            "avg_cost": 0.0,
            "currency": currency,
            "cost_basis_local": 0.0,
            "cost_basis_hkd": 0.0,
            "source": "ibkr_delta_ledger",
        }
        shares = _safe_float(current.get("shares"))
        cost_basis_local = _safe_float(current.get("cost_basis_local"), shares * _safe_float(current.get("avg_cost")))
        cost_basis_hkd = _safe_float(current.get("cost_basis_hkd"))
        if side == "BUY":
            new_shares = shares + qty
            trade_cost_local = _trade_cash_amount_local(trade)
            if trade_cost_local <= 0:
                trade_cost_local = qty * price
            new_cost_basis_local = cost_basis_local + trade_cost_local
            new_cost_basis = cost_basis_hkd + amount_hkd
            current["shares"] = round(new_shares, 8)
            current["cost_basis_local"] = round(new_cost_basis_local, 6)
            current["cost_basis_hkd"] = round(new_cost_basis, 6)
            current["avg_cost"] = round(new_cost_basis_local / new_shares, 6) if new_shares > 0 else price
            current["currency"] = currency
            positions[ticker] = current
            cash_balance -= amount_hkd
        else:
            if qty > shares + 1e-6:
                applied_qty = max(0.0, shares)
                credited_amount_hkd = amount_hkd * (applied_qty / qty) if qty > 0 and applied_qty > 0 else 0.0
                diagnostics["oversells"].append(
                    {
                        "ticker": ticker,
                        "date": trade_day.isoformat(),
                        "shares_before": round(shares, 8),
                        "sell_quantity": round(qty, 8),
                        "excess_quantity": round(qty - shares, 8),
                        "ignored_quantity": round(max(0.0, qty - applied_qty), 8),
                        "credited_quantity": round(applied_qty, 8),
                        "credited_amount_hkd": round(credited_amount_hkd, 2),
                    }
                )
                positions.pop(ticker, None)
                cash_balance += credited_amount_hkd
                diagnostics["applied_trade_count"] += 1
                continue
            remaining = max(0.0, shares - qty)
            current["cost_basis_local"] = round(cost_basis_local * remaining / shares, 6) if shares > 0 else 0.0
            current["cost_basis_hkd"] = round(cost_basis_hkd * remaining / shares, 6) if shares > 0 else 0.0
            current["shares"] = round(remaining, 8)
            cash_balance += amount_hkd
            if remaining <= 1e-6:
                positions.pop(ticker, None)
            else:
                positions[ticker] = current
        diagnostics["applied_trade_count"] += 1
    cash_balance += _external_cash_flow_amount(
        external_cash_flows_hkd or [],
        after_date=baseline_day,
        through_date=cutoff,
    )
    return {
        "baseline_week_id": (baseline or {}).get("baseline_week_id"),
        "baseline_date": (baseline or {}).get("baseline_date"),
        "positions": sorted(positions.values(), key=lambda row: str(row.get("ticker") or "")),
        "cash_balance_hkd": round(cash_balance, 2),
        "diagnostics": diagnostics,
    }


def _integrity_issue_key(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(issue.get("issue") or ""),
        str(issue.get("ticker") or "").upper(),
        str(issue.get("date") or ""),
        round(_safe_float(issue.get("sell_quantity")), 8),
        round(_safe_float(issue.get("excess_quantity")), 8),
        round(_safe_float(issue.get("ignored_quantity")), 8),
    )


def _trade_execution_dedupe_key(trade: dict[str, Any]) -> tuple[str, ...] | None:
    execution_id = str(trade.get("external_trade_id") or trade.get("execution_id") or "").strip()
    if execution_id:
        return (
            "execution_id",
            execution_id,
            str(trade.get("date") or ""),
            str(trade.get("trade_datetime") or "")[:19],
        )
    dedupe_key = str(trade.get("dedupe_key") or "").strip()
    if dedupe_key:
        return ("dedupe_key", dedupe_key)
    return None


def _trade_economic_dedupe_key(trade: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(trade.get("date") or ""),
        str(trade.get("trade_datetime") or "")[:19],
        str(trade.get("ticker") or ""),
        str(trade.get("side") or ""),
        round(_safe_float(trade.get("quantity")), 8),
        round(_safe_float(trade.get("price")), 8),
        str(trade.get("currency") or ""),
        round(_safe_float(trade.get("net_cash")), 6),
        round(_safe_float(trade.get("commission")), 6),
    )


def _normalized_trades(
    ledger: dict[str, Any],
    allowed_year: int,
    *,
    hot_patch_tickers: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_count = 0
    skipped_duplicate_count = 0
    seen_execution_keys: set[tuple[str, ...]] = set()
    seen_economic_keys: set[tuple[Any, ...]] = set()
    for index, trade in enumerate(ledger.get("trades") or []):
        if str(trade.get("asset_category") or "").upper() not in {"STK", "STOCK", "STOCKS"}:
            continue
        trade_datetime = str(trade.get("trade_datetime") or trade.get("trade_date") or "").strip()
        day = _date_from_text(trade.get("trade_date") or trade_datetime)
        if day is None or day.year != allowed_year:
            continue
        raw_count += 1
        currency = str(trade.get("currency") or "").upper()
        ticker = canonical_ibkr_trade_ticker(trade.get("ticker") or trade.get("stock_id") or trade.get("symbol"), currency)
        side = str(trade.get("side") or "").strip().upper()
        qty = abs(_safe_float(trade.get("quantity")))
        price = _safe_float(trade.get("price"))
        if not ticker or side not in {"BUY", "SELL"} or qty <= 0 or price <= 0:
            continue
        row = {
            "date": day.isoformat(),
            "trade_datetime": trade_datetime,
            "source_row_number": trade.get("source_row_number"),
            "week_id": _week_id_for_day(day),
            "ticker": ticker,
            "side": side,
            "quantity": qty,
            "price": price,
            "currency": currency or _currency_for_ticker(ticker),
            "commission": _safe_float(trade.get("commission")),
            "net_cash": trade.get("net_cash"),
            "net_cash_hkd": trade.get("net_cash_hkd") or trade.get("base_net_cash_hkd") or trade.get("base_net_cash"),
            "base_currency": str(trade.get("base_currency") or "").upper(),
            "description": str(trade.get("description") or ""),
            "ibkr_symbol": str(trade.get("ibkr_symbol") or ""),
            "source": str(trade.get("source") or "ibkr_ledger"),
        }
        execution_key = _trade_execution_dedupe_key(trade)
        if execution_key is not None:
            if execution_key in seen_execution_keys:
                skipped_duplicate_count += 1
                continue
            seen_execution_keys.add(execution_key)
            rows.append(row)
            continue
        key = _trade_economic_dedupe_key(row)
        if key in seen_economic_keys:
            skipped_duplicate_count += 1
            continue
        seen_economic_keys.add(key)
        rows.append(row)
    enabled_hot_patch_tickers = {str(ticker or "").strip().upper() for ticker in (hot_patch_tickers or set())}
    for synthetic in IBKR_DERIVED_SYNTHETIC_TRADES:
        day = _date_from_text(synthetic.get("date") or synthetic.get("trade_datetime"))
        if day is None or day.year != allowed_year:
            continue
        row = dict(synthetic)
        row["date"] = day.isoformat()
        row["week_id"] = _week_id_for_day(day)
        row["ticker"] = canonical_ibkr_trade_ticker(row.get("ticker"), row.get("currency"))
        if row["ticker"] not in enabled_hot_patch_tickers:
            continue
        execution_key = _trade_execution_dedupe_key(row)
        if execution_key is not None:
            if execution_key in seen_execution_keys:
                continue
            seen_execution_keys.add(execution_key)
            rows.append(row)
            continue
        key = _trade_economic_dedupe_key(row)
        if key in seen_economic_keys:
            continue
        seen_economic_keys.add(key)
        rows.append(row)
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("trade_datetime") or row.get("date") or ""),
            int(_safe_float(row.get("source_row_number"), default=0.0)),
            str(row.get("ticker") or ""),
            str(row.get("side") or ""),
        ),
    )
    return sorted_rows, {
        "raw_trade_count": raw_count,
        "deduped_trade_count": skipped_duplicate_count,
        "synthetic_trade_count": sum(
            1
            for row in sorted_rows
            if str(row.get("source") or "") == "ibkr_derived_hot_patch"
        ),
    }


def _ibkr_derived_hot_patch_tickers(first_review: dict[str, Any], ledger: dict[str, Any]) -> set[str]:
    tickers: set[str] = set()
    for stock_id, payload in ((first_review or {}).get("stocks") or {}).items():
        if not isinstance(payload, dict):
            continue
        ticker = str(payload.get("ticker") or stock_id or "").strip().upper()
        if ticker:
            tickers.add(ticker)
    for trade in (ledger or {}).get("trades") or []:
        ticker = canonical_ibkr_trade_ticker(
            trade.get("ticker") or trade.get("stock_id") or trade.get("symbol"),
            trade.get("currency"),
        )
        if ticker:
            tickers.add(ticker)
    return tickers


def _trade_key(trade: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(trade.get("trade_datetime") or ""),
        str(trade.get("date") or ""),
        str(trade.get("ticker") or ""),
        str(trade.get("side") or ""),
        str(trade.get("source_row_number") or ""),
    )


def _trade_cash_amount_local(trade: dict[str, Any]) -> float:
    net_cash = _safe_float(trade.get("net_cash"), default=0.0)
    quantity = _safe_float(trade.get("quantity"))
    price = _safe_float(trade.get("price"))
    gross = quantity * price
    currency = str(trade.get("currency") or _currency_for_ticker(str(trade.get("ticker") or ""))).upper()
    if abs(net_cash) > 1e-9:
        if currency != "HKD" and gross > 0 and abs(net_cash) > gross * 2 and abs(_safe_float(trade.get("net_cash_hkd"), default=0.0)) <= 1e-9:
            return gross
        return abs(net_cash)
    commission = abs(_safe_float(trade.get("commission"), default=0.0))
    if str(trade.get("side") or "").upper() == "SELL":
        return max(0.0, gross - commission)
    return gross + commission


def _trade_cash_amount_hkd(
    trade: dict[str, Any],
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
) -> float:
    direct_hkd = _safe_float(trade.get("net_cash_hkd"), default=0.0)
    if abs(direct_hkd) > 1e-9:
        return abs(direct_hkd)
    net_cash = _safe_float(trade.get("net_cash"), default=0.0)
    currency = str(trade.get("currency") or _currency_for_ticker(str(trade.get("ticker") or ""))).upper()
    quantity = _safe_float(trade.get("quantity"))
    price = _safe_float(trade.get("price"))
    gross = quantity * price
    if currency != "HKD" and abs(net_cash) > 1e-9 and gross > 0 and abs(net_cash) > gross * 2:
        return abs(net_cash)
    day = _date_from_text(trade.get("date") or trade.get("trade_datetime"))
    daily_rates = _fx_rates_for_day(day, fx_rates, fx_rates_by_date)
    fx = _safe_float(daily_rates.get(currency), DEFAULT_FX_RATES.get(currency, 1.0))
    return _trade_cash_amount_local(trade) * fx


def _apply_trade_to_lots(
    lots: dict[str, list[dict[str, Any]]],
    trade: dict[str, Any],
    *,
    stock_name: str,
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any] | None:
    ticker = str(trade.get("ticker") or "").upper()
    side = str(trade.get("side") or "").upper()
    qty = _safe_float(trade.get("quantity"))
    if not ticker or qty <= 0:
        return None
    cash_hkd = _trade_cash_amount_hkd(trade, fx_rates, fx_rates_by_date)
    if side == "BUY":
        lots.setdefault(ticker, []).append(
            {
                "ticker": ticker,
                "quantity": qty,
                "cost_basis_hkd": cash_hkd,
                "entry_price": _safe_float(trade.get("price")),
                "entry_date": str(trade.get("date") or ""),
                "currency": str(trade.get("currency") or _currency_for_ticker(ticker)).upper(),
                "source": "ibkr_ledger",
            }
        )
        return None
    if side != "SELL":
        return None

    remaining = qty
    consumed_cost = 0.0
    consumed_qty = 0.0
    consumed_lots: list[dict[str, Any]] = []
    queue = lots.setdefault(ticker, [])
    while remaining > 1e-9 and queue:
        lot = queue[0]
        lot_qty = _safe_float(lot.get("quantity"))
        if lot_qty <= 1e-9:
            queue.pop(0)
            continue
        used = min(remaining, lot_qty)
        lot_cost = _safe_float(lot.get("cost_basis_hkd"))
        used_cost = lot_cost * (used / lot_qty) if lot_qty > 0 else 0.0
        consumed_qty += used
        consumed_cost += used_cost
        consumed_lots.append(
            {
                "entry_date": lot.get("entry_date") or "",
                "quantity": round(used, 8),
                "cost_basis_hkd": round(used_cost, 2),
                "source": lot.get("source") or "",
            }
        )
        lot["quantity"] = round(lot_qty - used, 8)
        lot["cost_basis_hkd"] = round(max(0.0, lot_cost - used_cost), 6)
        remaining -= used
        if _safe_float(lot.get("quantity")) <= 1e-9:
            queue.pop(0)

    if consumed_qty <= 0:
        return None
    sale_proceeds = cash_hkd * (consumed_qty / qty) if qty > 0 else cash_hkd
    currency = str(trade.get("currency") or _currency_for_ticker(ticker)).upper()
    trade_day = _date_from_text(trade.get("date") or trade.get("trade_datetime"))
    fx_audit = _fx_rate_audit(currency, trade_day, fx_rates, fx_rates_by_date)
    return {
        "stock_id": ticker,
        "ticker": ticker,
        "stock_name": stock_name or ticker,
        "date": str(trade.get("date") or ""),
        "sell_date": str(trade.get("date") or ""),
        "shares_sold": round(consumed_qty, 8),
        "sell_price": _safe_float(trade.get("price")),
        "currency": currency,
        "trade_date_fx_to_hkd": fx_audit["fx_to_hkd"],
        "trade_date_fx_source": fx_audit["fx_source"],
        "cost_basis_hkd": round(consumed_cost, 2),
        "sale_proceeds_hkd": round(sale_proceeds, 2),
        "realized_pnl_hkd": round(sale_proceeds - consumed_cost, 2),
        "realized_pnl": round(sale_proceeds - consumed_cost, 2),
        "lots": consumed_lots,
        "source": "ibkr_ledger",
    }


def _apply_trade(
    positions: dict[str, dict[str, Any]],
    trade: dict[str, Any],
    integrity_issues: list[dict[str, Any]],
    *,
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
) -> None:
    ticker = str(trade.get("ticker") or "").upper()
    qty = _safe_float(trade.get("quantity"))
    price = _safe_float(trade.get("price"))
    effective_price = _trade_cash_amount_local(trade) / qty if qty > 0 and _trade_cash_amount_local(trade) > 0 else price
    current = positions.setdefault(
        ticker,
        {
            "ticker": ticker,
            "stock_name": str(trade.get("description") or ticker),
            "shares": 0.0,
            "avg_cost": 0.0,
            "currency": str(trade.get("currency") or _currency_for_ticker(ticker)).upper(),
            "source": "ibkr_trade",
        },
    )
    shares = _safe_float(current.get("shares"))
    if trade.get("side") == "BUY":
        new_shares = shares + qty
        current["avg_cost"] = round(((shares * _safe_float(current.get("avg_cost"))) + qty * effective_price) / new_shares, 6) if new_shares else effective_price
        current["shares"] = round(new_shares, 8)
        current["cost_basis_hkd"] = round(_safe_float(current.get("cost_basis_hkd")) + _trade_cash_amount_hkd(trade, fx_rates, fx_rates_by_date), 6)
    elif trade.get("side") == "SELL":
        if qty - shares > 1e-8:
            integrity_issues.append(
                {
                    "issue": "sell_exceeds_position",
                    "ticker": ticker,
                    "date": str(trade.get("date") or ""),
                    "available_shares": round(shares, 8),
                    "sell_quantity": round(qty, 8),
                    "excess_quantity": round(qty - shares, 8),
                }
            )
        applied_qty = min(qty, max(shares, 0.0))
        current_cost = _safe_float(current.get("cost_basis_hkd"))
        if shares > 0 and applied_qty > 0:
            current["cost_basis_hkd"] = round(max(0.0, current_cost - current_cost * (applied_qty / shares)), 6)
        current["shares"] = round(max(0.0, shares - qty), 8)
        if _safe_float(current.get("shares")) <= 1e-9:
            current["cost_basis_hkd"] = 0.0
            positions.pop(ticker, None)


def _build_reallocation_events(
    weekly_trades: list[dict[str, Any]],
    *,
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str, str, str], list[dict[str, Any]]]]:
    events: list[dict[str, Any]] = []
    pairings: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    buys = [trade for trade in weekly_trades if str(trade.get("side") or "").upper() == "BUY"]
    for sell in [trade for trade in weekly_trades if str(trade.get("side") or "").upper() == "SELL"]:
        sell_time = str(sell.get("trade_datetime") or sell.get("date") or "")
        sell_amount = _trade_cash_amount_hkd(sell, fx_rates, fx_rates_by_date)
        if sell_amount <= 0:
            continue
        remaining = sell_amount
        paired: list[dict[str, Any]] = []
        for buy in buys:
            buy_time = str(buy.get("trade_datetime") or buy.get("date") or "")
            if buy_time < sell_time:
                continue
            buy_amount = _trade_cash_amount_hkd(buy, fx_rates, fx_rates_by_date)
            if buy_amount <= 0 or remaining <= 0:
                continue
            allocated = min(remaining, buy_amount)
            remaining -= allocated
            paired.append(
                {
                    "stock_id": str(buy.get("ticker") or "").upper(),
                    "ticker": str(buy.get("ticker") or "").upper(),
                    "amount": round(allocated, 2),
                    "ratio": round(allocated / sell_amount, 6) if sell_amount > 0 else 0.0,
                    "buy_week_id": str(buy.get("week_id") or ""),
                    "buy_date": str(buy.get("date") or ""),
                    "source": "ibkr_ledger_auto",
                }
            )
        if paired:
            key = _trade_key(sell)
            pairings[key] = paired
            events.append(
                {
                    "source": "ibkr_ledger_auto",
                    "sell_ticker": str(sell.get("ticker") or "").upper(),
                    "sell_date": str(sell.get("date") or ""),
                    "sell_amount_hkd": round(sell_amount, 2),
                    "paired_buys": paired,
                    "unmatched_amount_hkd": round(max(0.0, remaining), 2),
                }
            )
    return events, pairings


def _position_rows(
    positions: dict[str, dict[str, Any]],
    *,
    week_end: date,
    price_frames: dict[str, Any],
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
) -> tuple[list[dict[str, Any]], float, list[str]]:
    rows: list[dict[str, Any]] = []
    total = 0.0
    missing: list[str] = []
    for ticker, pos in sorted(positions.items()):
        shares = _safe_float(pos.get("shares"))
        if shares <= 0:
            continue
        currency = str(pos.get("currency") or _currency_for_ticker(ticker)).upper()
        fx_audit = _fx_rate_audit(currency, week_end, fx_rates, fx_rates_by_date)
        fx = _safe_float(fx_audit.get("fx_to_hkd"), DEFAULT_FX_RATES.get(currency, 1.0))
        price_audit = _price_audit_on_or_before(price_frames, ticker, week_end)
        price = _safe_float((price_audit or {}).get("close_price"), default=0.0)
        price_source = "market_history"
        price_date_used = (price_audit or {}).get("price_date_used")
        stale_days = (price_audit or {}).get("stale_days")
        if price_audit is None:
            price = _safe_float(pos.get("avg_cost"))
            price_source = "avg_cost_fallback"
            price_date_used = None
            stale_days = None
            missing.append(ticker)
        value_hkd = shares * price * fx
        total += value_hkd
        rows.append(
            {
                "ticker": ticker,
                "stock_name": pos.get("stock_name") or ticker,
                "shares": round(shares, 4),
                "avg_cost": round(_safe_float(pos.get("avg_cost")), 4),
                "currency": currency,
                "fx_to_hkd": round(fx, 6),
                "fx_date_used": fx_audit.get("fx_date_used"),
                "fx_source": fx_audit.get("fx_source"),
                "cost_basis_hkd": round(_safe_float(pos.get("cost_basis_hkd")), 2),
                "mark_price": round(price, 4),
                "price_date_used": price_date_used,
                "price_source": price_source,
                "stale_days": stale_days,
                "market_value_hkd": round(value_hkd, 2),
            }
        )
    return sorted(rows, key=lambda row: row["market_value_hkd"], reverse=True), round(total, 2), sorted(set(missing))

def _zero_position_row_for_ticker(
    ticker: str,
    *,
    previous_rows: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    previous = next((row for row in previous_rows if str(row.get("ticker") or "").upper() == ticker), {})
    pos = positions.get(ticker) or {}
    currency = str(pos.get("currency") or previous.get("currency") or _currency_for_ticker(ticker)).upper()
    return {
        "ticker": ticker,
        "stock_name": pos.get("stock_name") or previous.get("stock_name") or ticker,
        "shares": 0.0,
        "avg_cost": round(_safe_float(pos.get("avg_cost"), _safe_float(previous.get("avg_cost"))), 4),
        "currency": currency,
        "fx_to_hkd": previous.get("fx_to_hkd") or DEFAULT_FX_RATES.get(currency, 1.0),
        "fx_date_used": previous.get("fx_date_used") or "",
        "fx_source": previous.get("fx_source") or "previous_position",
        "cost_basis_hkd": round(_safe_float(pos.get("cost_basis_hkd")), 2),
        "mark_price": previous.get("mark_price") or 0.0,
        "price_date_used": previous.get("price_date_used"),
        "price_source": "closed_position",
        "stale_days": previous.get("stale_days"),
        "market_value_hkd": 0.0,
    }


def _price_return_pct(
    price_frames: dict[str, Any],
    ticker: str,
    current_price: float,
    anchor_day: date,
    *,
    latest_day: date,
) -> float | None:
    anchor_price = _price_on_or_after(price_frames, ticker, anchor_day, latest_day=latest_day)
    if anchor_price is None or anchor_price <= 0 or current_price <= 0:
        return None
    return round((current_price / anchor_price - 1) * 100, 4)


def _price_on_or_after(price_frames: dict[str, Any], ticker: str, day: date, *, latest_day: date | None = None) -> float | None:
    series = _close_series(price_frames.get(ticker))
    if series.empty:
        return None
    stamp = pd.Timestamp(day).normalize()
    eligible = series[series.index >= stamp]
    if latest_day is not None:
        eligible = eligible[eligible.index <= pd.Timestamp(latest_day).normalize()]
    if not eligible.empty:
        return float(eligible.iloc[0])
    return _price_on_or_before(price_frames, ticker, day)


def _attach_weekly_contribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(_safe_float(row.get("weekly_pnl_hkd")) for row in rows)
    denominator = abs(total)
    for row in rows:
        row["pnl_contribution"] = round((_safe_float(row.get("weekly_pnl_hkd")) / denominator * 100), 4) if denominator > 0 else None
    return rows


def _enrich_closed_positions_for_week(
    closed_positions: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_ticker = {str(row.get("ticker") or "").upper(): row for row in previous_rows}
    enriched: list[dict[str, Any]] = []
    for item in closed_positions:
        row = dict(item)
        ticker = str(row.get("ticker") or row.get("stock_id") or "").upper()
        previous = previous_by_ticker.get(ticker) or {}
        previous_shares = _safe_float(previous.get("shares"))
        previous_value = _safe_float(previous.get("market_value_hkd"))
        shares_sold = _safe_float(row.get("shares_sold"))
        previous_value_sold = previous_value * (shares_sold / previous_shares) if previous_shares > 0 and shares_sold > 0 else 0.0
        sale_proceeds = _safe_float(row.get("sale_proceeds_hkd"))
        row["previous_week_end_value_hkd"] = round(previous_value_sold, 2)
        row["weekly_closed_position_pnl_hkd"] = round(sale_proceeds - previous_value_sold, 2)
        enriched.append(row)
    return enriched


def _portfolio_rows(
    rows: list[dict[str, Any]],
    *,
    previous_rows: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    weekly_trades: list[dict[str, Any]],
    week_closed_positions: list[dict[str, Any]],
    reported_nav_hkd: float | None,
    market_value_hkd: float,
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None,
    price_frames: dict[str, Any],
    week_end: date,
) -> list[dict[str, Any]]:
    previous_by_ticker = {str(row.get("ticker") or ""): row for row in previous_rows}
    buys_by_ticker: dict[str, float] = {}
    sells_by_ticker: dict[str, float] = {}
    closed_pnl_by_ticker: dict[str, float] = {}
    for closed in week_closed_positions:
        ticker = str(closed.get("ticker") or closed.get("stock_id") or "").upper()
        closed_pnl_by_ticker[ticker] = closed_pnl_by_ticker.get(ticker, 0.0) + _safe_float(closed.get("weekly_closed_position_pnl_hkd"))
    for trade in weekly_trades:
        ticker = str(trade.get("ticker") or "").upper()
        amount = _trade_cash_amount_hkd(trade, fx_rates, fx_rates_by_date)
        if trade.get("side") == "BUY":
            buys_by_ticker[ticker] = buys_by_ticker.get(ticker, 0.0) + amount
        elif trade.get("side") == "SELL":
            sells_by_ticker[ticker] = sells_by_ticker.get(ticker, 0.0) + amount

    denominator = reported_nav_hkd if reported_nav_hkd and reported_nav_hkd > 0 else market_value_hkd
    portfolio_rows: list[dict[str, Any]] = []
    current_by_ticker = {str(row.get("ticker") or "").upper(): row for row in rows}
    active_tickers = set(current_by_ticker)
    active_tickers.update(previous_by_ticker)
    active_tickers.update(buys_by_ticker)
    active_tickers.update(sells_by_ticker)
    for ticker in sorted(t for t in active_tickers if t):
        row = current_by_ticker.get(ticker)
        if row is None:
            row = _zero_position_row_for_ticker(ticker, previous_rows=previous_rows, positions=positions)
        ticker = str(row.get("ticker") or "").upper()
        holding_value = _safe_float(row.get("market_value_hkd"))
        previous_value = _safe_float((previous_by_ticker.get(ticker) or {}).get("market_value_hkd"))
        buy_amount = buys_by_ticker.get(ticker, 0.0)
        sell_amount = sells_by_ticker.get(ticker, 0.0)
        weekly_pnl = holding_value + sell_amount - buy_amount - previous_value
        weekly_base = previous_value + buy_amount
        avg_cost = _safe_float(row.get("avg_cost"))
        shares = _safe_float(row.get("shares"))
        mark_price = _safe_float(row.get("mark_price"))
        cost_basis_hkd = _safe_float(row.get("cost_basis_hkd"))
        unrealized = (holding_value - cost_basis_hkd) if cost_basis_hkd > 0 or shares <= 0 else None
        portfolio_rows.append(
            {
                "ticker": ticker,
                "stock_name": row.get("stock_name") or ticker,
                "shares_held": row.get("shares"),
                "avg_cost": row.get("avg_cost"),
                "currency": row.get("currency"),
                "fx_to_hkd": row.get("fx_to_hkd"),
                "mark_price": row.get("mark_price"),
                "price_source": row.get("price_source"),
                "price_date_used": row.get("price_date_used"),
                "fx_date_used": row.get("fx_date_used"),
                "fx_source": row.get("fx_source"),
                "stale_days": row.get("stale_days"),
                "cost_basis_hkd": round(cost_basis_hkd, 2),
                "holding_value_hkd": round(holding_value, 2),
                "holding_pct": round((holding_value / denominator * 100), 4) if denominator and denominator > 0 else None,
                "unrealized_pnl_hkd": round(unrealized, 2) if unrealized is not None else None,
                "return_since_buy_pct": round(((mark_price - avg_cost) / avg_cost * 100), 4) if avg_cost > 0 else None,
                "ytd_return": None if _safe_float(row.get("shares")) <= 0 else _price_return_pct(
                    price_frames,
                    ticker,
                    mark_price,
                    date(week_end.year, 1, 1),
                    latest_day=week_end,
                ),
                "return_6m": None if _safe_float(row.get("shares")) <= 0 else _price_return_pct(
                    price_frames,
                    ticker,
                    mark_price,
                    week_end - timedelta(days=185),
                    latest_day=week_end,
                ),
                "return_1y": None if _safe_float(row.get("shares")) <= 0 else _price_return_pct(
                    price_frames,
                    ticker,
                    mark_price,
                    week_end - timedelta(days=370),
                    latest_day=week_end,
                ),
                "weekly_pnl_hkd": round(weekly_pnl, 2),
                "weekly_closed_position_pnl_hkd": round(closed_pnl_by_ticker.get(ticker, 0.0), 2),
                "weekly_capital_hkd": round(weekly_base, 2) if weekly_base > 0 else None,
                "weekly_return_pct": round((weekly_pnl / weekly_base * 100), 4) if weekly_base > 0 else None,
                "weekly_buy_amount_hkd": round(buy_amount, 2),
                "weekly_sell_amount_hkd": round(sell_amount, 2),
            }
        )
    return _attach_weekly_contribution(
        sorted(portfolio_rows, key=lambda item: _safe_float(item.get("holding_value_hkd")), reverse=True)
    )


def _reported_nav_hkd(review: dict[str, Any]) -> float | None:
    for key in ("total_portfolio_value", "portfolio_value_hkd", "account_nav_hkd", "net_liquidation_hkd"):
        value = _safe_float(review.get(key), default=0.0)
        if value > 0:
            return round(value, 2)
    return None


def _cash_flows_for_week(cash_flows: Iterable[dict[str, Any]], week_id: str) -> float:
    total = 0.0
    for flow in cash_flows or []:
        day = _date_from_text((flow or {}).get("date"))
        if day is None or _week_id_for_day(day) != week_id:
            continue
        total += _safe_float((flow or {}).get("amount_hkd"))
    return round(total, 2)


def _build_price_audit(week: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit: list[dict[str, Any]] = []
    week_id = str(week.get("week_id") or "")
    week_end = str(week.get("week_end") or "")
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        stale_days = row.get("stale_days")
        price_source = str(row.get("price_source") or "")
        severity = "ok"
        fallback_reason = ""
        if price_source != "market_history":
            severity = "warn"
            fallback_reason = "missing_market_price"
        elif stale_days is not None and _safe_float(stale_days) > 5:
            severity = "warn"
            fallback_reason = "stale_price"
        audit.append(
            {
                "week_id": week_id,
                "week_end": week_end,
                "ticker": ticker,
                "currency": row.get("currency"),
                "close_price": row.get("mark_price"),
                "price_date_used": row.get("price_date_used"),
                "price_source": price_source,
                "stale_days": stale_days,
                "fallback_reason": fallback_reason,
                "fx_to_hkd": row.get("fx_to_hkd"),
                "fx_date_used": row.get("fx_date_used"),
                "fx_source": row.get("fx_source"),
                "severity": severity,
            }
        )
    return audit


def _build_weekly_reconciliation(
    *,
    week_id: str,
    start_nav_hkd: float | None,
    end_nav_hkd: float | None,
    portfolio_rows: list[dict[str, Any]],
    external_cash_flow_hkd: float,
) -> dict[str, Any]:
    derived_position_pnl = round(sum(_safe_float(row.get("weekly_pnl_hkd")) for row in portfolio_rows), 2)
    reported_pnl = None
    gap = None
    threshold = None
    status = "no_nav"
    if start_nav_hkd is not None and end_nav_hkd is not None:
        reported_pnl = round(_safe_float(end_nav_hkd) - _safe_float(start_nav_hkd) - external_cash_flow_hkd, 2)
        gap = round(reported_pnl - derived_position_pnl, 2)
        threshold = round(max(100.0, abs(_safe_float(end_nav_hkd)) * 0.001), 2)
        status = "warn" if abs(gap) > threshold else "ok"
    return {
        "week_id": week_id,
        "start_nav_hkd": start_nav_hkd,
        "end_nav_hkd": end_nav_hkd,
        "external_cash_flow_hkd": round(external_cash_flow_hkd, 2),
        "reported_pnl_hkd": reported_pnl,
        "derived_position_pnl_hkd": derived_position_pnl,
        "gap_hkd": gap,
        "threshold_hkd": threshold,
        "status": status,
        "method": "end_nav_minus_start_nav_minus_external_cash_flow_vs_position_pnl",
    }


def _transition_diagnostics(
    *,
    source: str,
    weeks: list[dict[str, Any]],
    price_fallback_tickers: set[str],
    integrity_issues: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
    reallocation_events: list[dict[str, Any]],
) -> dict[str, Any]:
    fallback_rows = [
        {
            "kind": "price",
            "ticker": ticker,
            "severity": "warn",
            "message": "No market history was available; avg_cost fallback was used and surfaced in data_quality.",
        }
        for ticker in sorted(price_fallback_tickers)
    ]
    latest = weeks[-1] if weeks else {}
    latest_market_value = _safe_float(latest.get("market_value_hkd"), default=0.0)
    latest_reported_nav = latest.get("reported_nav_hkd")
    inferred_cash = None
    if latest_reported_nav is not None:
        inferred_cash = round(_safe_float(latest_reported_nav) - latest_market_value, 2)
    reconciliation_gaps = [
        dict(week.get("reconciliation") or {})
        for week in weeks
        if (week.get("reconciliation") or {}).get("status") == "warn"
    ]
    stale_price_tickers = sorted(
        {
            str(row.get("ticker") or "").upper()
            for week in weeks
            for row in (week.get("price_audit") or [])
            if row.get("fallback_reason") == "stale_price"
        }
    )
    latest_reconciliation = latest.get("reconciliation") or {}
    return {
        "source": source,
        "latest_week_id": latest.get("week_id") or "",
        "latest_market_value_hkd": round(latest_market_value, 2),
        "latest_reported_nav_hkd": latest_reported_nav,
        "inferred_cash_hkd": inferred_cash,
        "latest_reconciliation_gap_hkd": latest_reconciliation.get("gap_hkd"),
        "reconciliation_gap_count": len(reconciliation_gaps),
        "reconciliation_gaps": reconciliation_gaps,
        "price_fallback_tickers": sorted(price_fallback_tickers),
        "stale_price_tickers": stale_price_tickers,
        "fallbacks": fallback_rows,
        "silent_fallback_count": 0,
        "position_integrity_issues": [dict(item) for item in integrity_issues],
        "closed_position_count": len(closed_positions),
        "reallocation_event_count": len(reallocation_events),
    }


def build_ibkr_derived_ytd_portfolio(
    weekly_reviews: Iterable[dict[str, Any]],
    broker_trade_ledger: dict[str, Any],
    *,
    price_frames: dict[str, Any] | None = None,
    fx_rates: dict[str, float] | None = None,
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
    external_cash_flows_hkd: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reviews = sorted(
        [review for review in weekly_reviews if isinstance(review, dict) and str(review.get("week_id") or "").strip()],
        key=lambda review: _week_end_date(str(review.get("week_id"))) or date.min,
    )
    if not reviews:
        return {"success": False, "error": "no_weekly_reviews", "weeks": []}
    first_review = reviews[0]
    start_week_id = str(first_review.get("week_id") or "")
    start_day = _week_end_date(start_week_id)
    if start_day is None:
        return {"success": False, "error": "invalid_start_week", "weeks": []}

    price_frames = price_frames or {}
    fx = dict(DEFAULT_FX_RATES)
    fx.update({str(k).upper(): float(v) for k, v in (fx_rates or {}).items() if v is not None})
    start_fx = _fx_rates_for_review(first_review, fx)
    positions = _initial_positions(first_review, start_fx)
    lots = _initial_lots(first_review, start_fx)
    hot_patch_tickers = _ibkr_derived_hot_patch_tickers(first_review, broker_trade_ledger or {})
    trades, trade_normalization = _normalized_trades(
        broker_trade_ledger or {},
        start_day.year,
        hot_patch_tickers=hot_patch_tickers,
    )
    trades_by_week: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        trades_by_week.setdefault(str(trade.get("week_id")), []).append(trade)

    weeks: list[dict[str, Any]] = []
    all_missing_prices: set[str] = set()
    all_stale_prices: set[str] = set()
    previous_position_rows: list[dict[str, Any]] = []
    previous_reported_nav: float | None = None
    integrity_issues: list[dict[str, Any]] = []
    closed_positions_all: list[dict[str, Any]] = []
    reallocation_events_all: list[dict[str, Any]] = []
    external_cash_flows = [dict(item) for item in (external_cash_flows_hkd or [])]
    for review in reviews:
        week_id = str(review.get("week_id") or "")
        week_end = _week_end_date(week_id)
        if week_end is None or week_end.year != start_day.year:
            continue
        weekly_trades = [dict(item) for item in trades_by_week.get(week_id, [])]
        before = deepcopy(positions)
        week_fx = _fx_rates_for_review(review, fx)
        week_closed_positions: list[dict[str, Any]] = []
        for trade in weekly_trades:
            closed = _apply_trade_to_lots(
                lots,
                trade,
                stock_name=str((positions.get(str(trade.get("ticker") or "").upper()) or {}).get("stock_name") or trade.get("description") or ""),
                fx_rates=week_fx,
                fx_rates_by_date=fx_rates_by_date,
            )
            if closed:
                week_closed_positions.append(closed)
            _apply_trade(positions, trade, integrity_issues, fx_rates=week_fx, fx_rates_by_date=fx_rates_by_date)
        week_closed_positions = _enrich_closed_positions_for_week(week_closed_positions, previous_position_rows)
        closed_positions_all.extend([dict(item) for item in week_closed_positions])
        week_reallocation_events, week_pairings = _build_reallocation_events(
            weekly_trades,
            fx_rates=week_fx,
            fx_rates_by_date=fx_rates_by_date,
        )
        reallocation_events_all.extend(week_reallocation_events)
        rows, market_value_hkd, missing_prices = _position_rows(
            positions,
            week_end=week_end,
            price_frames=price_frames,
            fx_rates=week_fx,
            fx_rates_by_date=fx_rates_by_date,
        )
        reported_nav = _reported_nav_hkd(review)
        portfolio_rows = _portfolio_rows(
            rows,
            previous_rows=previous_position_rows,
            positions=positions,
            weekly_trades=weekly_trades,
            week_closed_positions=week_closed_positions,
            reported_nav_hkd=reported_nav,
            market_value_hkd=market_value_hkd,
            fx_rates=week_fx,
            fx_rates_by_date=fx_rates_by_date,
            price_frames=price_frames,
            week_end=week_end,
        )
        week_shell = {"week_id": week_id, "week_end": week_end.isoformat()}
        price_audit = _build_price_audit(week_shell, rows)
        stale_prices = sorted(
            {
                str(item.get("ticker") or "").upper()
                for item in price_audit
                if item.get("fallback_reason") == "stale_price"
            }
        )
        all_stale_prices.update(stale_prices)
        external_cash_flow = _cash_flows_for_week(external_cash_flows, week_id)
        reconciliation = _build_weekly_reconciliation(
            week_id=week_id,
            start_nav_hkd=previous_reported_nav,
            end_nav_hkd=reported_nav,
            portfolio_rows=portfolio_rows,
            external_cash_flow_hkd=external_cash_flow,
        )
        all_missing_prices.update(missing_prices)
        week_data_quality = {
            "price_fallback_tickers": missing_prices,
            "missing_price_tickers": missing_prices,
            "stale_price_tickers": stale_prices,
            "reconciliation_status": reconciliation.get("status"),
            "reconciliation_gap_hkd": reconciliation.get("gap_hkd"),
            "position_integrity_issues": [dict(item) for item in integrity_issues],
        }
        weeks.append(
            {
                "week_id": week_id,
                "week_end": week_end.isoformat(),
                "reported_nav_hkd": reported_nav,
                "trades": weekly_trades,
                "trade_count": len(weekly_trades),
                "positions": deepcopy(positions),
                "position_rows": rows,
                "portfolio_rows": portfolio_rows,
                "position_count": len(rows),
                "market_value_hkd": market_value_hkd,
                "missing_price_tickers": missing_prices,
                "price_audit": price_audit,
                "reconciliation": reconciliation,
                "data_quality": week_data_quality,
                "position_integrity_issues": [dict(item) for item in integrity_issues],
                "closed_positions": [dict(item) for item in week_closed_positions],
                "reallocation_events": [dict(item) for item in week_reallocation_events],
                "reallocation_pairings": {repr(key): [dict(item) for item in value] for key, value in week_pairings.items()},
                "position_changes": _position_changes(before, positions),
            }
        )
        previous_position_rows = rows
        if reported_nav is not None:
            previous_reported_nav = reported_nav

    diagnostics = _transition_diagnostics(
        source="ibkr_derived_ledger",
        weeks=weeks,
        price_fallback_tickers=all_missing_prices,
        integrity_issues=integrity_issues,
        closed_positions=closed_positions_all,
        reallocation_events=reallocation_events_all,
    )
    return {
        "success": True,
        "source": "ibkr_ledger",
        "canonical_source": "ibkr_derived_ledger",
        "summary": {
            "start_week_id": weeks[0]["week_id"] if weeks else start_week_id,
            "end_week_id": weeks[-1]["week_id"] if weeks else "",
            "week_count": len(weeks),
            "trade_count": len([trade for trade in trades if str(trade.get("source") or "ibkr_ledger") != "ibkr_derived_hot_patch"]),
            "raw_trade_count": trade_normalization.get("raw_trade_count", len(trades)),
            "deduped_trade_count": trade_normalization.get("deduped_trade_count", 0),
            "synthetic_trade_count": trade_normalization.get("synthetic_trade_count", 0),
            "latest_market_value_hkd": weeks[-1]["market_value_hkd"] if weeks else 0.0,
            "latest_reported_nav_hkd": weeks[-1]["reported_nav_hkd"] if weeks else None,
            "latest_position_count": weeks[-1]["position_count"] if weeks else 0,
            "missing_price_tickers": sorted(all_missing_prices),
            "stale_price_tickers": sorted(all_stale_prices),
            "position_integrity_issues": [dict(item) for item in integrity_issues],
            "closed_position_count": len(closed_positions_all),
            "reallocation_event_count": len(reallocation_events_all),
            "data_quality": {
                "price_fallback_tickers": sorted(all_missing_prices),
                "missing_price_tickers": sorted(all_missing_prices),
                "stale_price_tickers": sorted(all_stale_prices),
                "reconciliation_gap_count": sum(1 for week in weeks if (week.get("reconciliation") or {}).get("status") == "warn"),
                "position_integrity_issues": [dict(item) for item in integrity_issues],
                "silent_fallback_count": 0,
            },
        },
        "diagnostics": diagnostics,
        "cash_flows": external_cash_flows,
        "closed_positions": [dict(item) for item in closed_positions_all],
        "reallocation_events": [dict(item) for item in reallocation_events_all],
        "weeks": weeks,
    }


def _trade_to_rebalancing_op(
    trade: dict[str, Any],
    stock_name: str,
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
    paired_buys: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ticker = str(trade.get("ticker") or "").strip().upper()
    currency = str(trade.get("currency") or _currency_for_ticker(ticker)).upper()
    amount_hkd = _trade_cash_amount_hkd(trade, fx_rates, fx_rates_by_date)
    op = {
        "stock_id": ticker,
        "ticker": ticker,
        "stock_name": stock_name or ticker,
        "op_type": "buy" if str(trade.get("side") or "").upper() == "BUY" else "sell",
        "quantity": _safe_float(trade.get("quantity")),
        "price": _safe_float(trade.get("price")),
        "date": str(trade.get("date") or ""),
        "currency": currency,
        "amount_hkd": round(amount_hkd, 2),
        "source": str(trade.get("source") or "ibkr_ledger"),
    }
    if trade.get("hot_patch_reason"):
        op["hot_patch_reason"] = trade.get("hot_patch_reason")
    if op["op_type"] == "sell" and paired_buys:
        op["pairing_mode"] = "auto"
        op["paired_buys"] = [dict(item) for item in paired_buys]
        op["pairing_note"] = "IBKR ledger auto-paired sell proceeds to later buys in the same week."
    return op


def _project_week_to_review(
    base_review: dict[str, Any],
    week: dict[str, Any],
    *,
    fx_rates: dict[str, float],
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    projected = deepcopy(base_review or {})
    projected["week_id"] = str(week.get("week_id") or projected.get("week_id") or "")
    projected["portfolio_source"] = "ibkr_derived_ledger"
    projected["canonical_source"] = "ibkr_derived_ledger"
    projected["projection_source"] = "ibkr_derived_review_projection"
    projected["ibkr_derived_week_end"] = week.get("week_end")
    projected["ibkr_derived_market_value_hkd"] = week.get("market_value_hkd")
    projected["ibkr_derived_data_quality"] = week.get("data_quality") or {}
    projected["total_portfolio_value"] = week.get("reported_nav_hkd", projected.get("total_portfolio_value"))
    projected["trim_reallocation_analysis"] = {"summary": {}, "stocks": [], "events": []}
    projected["decision_attribution_analysis"] = {"summary": {}, "patterns": {}, "stocks": [], "events": []}
    projected.pop("portfolio_decision_memo", None)

    existing_stocks = projected.get("stocks") or {}
    stock_names: dict[str, str] = {}
    next_stocks: dict[str, dict[str, Any]] = {}
    for row in week.get("portfolio_rows") or []:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        existing = dict(existing_stocks.get(ticker) or {})
        for key, payload in existing_stocks.items():
            if not isinstance(payload, dict):
                continue
            existing_ticker = str(payload.get("ticker") or key or "").strip().upper()
            if existing_ticker == ticker:
                existing = dict(payload)
                break
        stock_name = str(row.get("stock_name") or existing.get("stock_name") or ticker)
        stock_names[ticker] = stock_name
        performance = dict(existing.get("performance_data") or {})
        if row.get("mark_price") is not None:
            performance["end_price"] = row.get("mark_price")
        metrics = {
            "ticker": ticker,
            "currency": row.get("currency"),
            "cost_basis_hkd": row.get("cost_basis_hkd"),
            "holding_value_hkd": row.get("holding_value_hkd"),
            "holding_pct": row.get("holding_pct"),
            "weekly_pnl_hkd": row.get("weekly_pnl_hkd"),
            "weekly_capital_hkd": row.get("weekly_capital_hkd"),
            "weekly_return_pct": row.get("weekly_return_pct"),
            "unrealized_pnl_hkd": row.get("unrealized_pnl_hkd"),
            "return_since_buy": row.get("return_since_buy_pct"),
            "pnl_contribution": row.get("pnl_contribution"),
            "source": "ibkr_derived_ledger",
        }
        next_stocks[ticker] = {
            **existing,
            "ticker": ticker,
            "stock_name": stock_name,
            "shares_held": row.get("shares_held"),
            "avg_cost": row.get("avg_cost"),
            "performance_data": performance,
            "position_metrics": metrics,
            "portfolio_returns": {
                "return_since_buy": row.get("return_since_buy_pct"),
                "ytd_return": row.get("ytd_return"),
                "return_6m": row.get("return_6m"),
                "return_1y": row.get("return_1y"),
            },
        }
    projected["stocks"] = next_stocks
    _, pairings = _build_reallocation_events(
        [dict(item) for item in week.get("trades") or []],
        fx_rates=_fx_rates_for_review(base_review, fx_rates),
        fx_rates_by_date=fx_rates_by_date,
    )
    projected["rebalancing_ops"] = [
        _trade_to_rebalancing_op(
            trade,
            stock_names.get(str(trade.get("ticker") or "").strip().upper(), ""),
            _fx_rates_for_review(base_review, fx_rates),
            fx_rates_by_date,
            pairings.get(_trade_key(trade)),
        )
        for trade in week.get("trades") or []
    ]
    projected["closed_positions"] = [
        dict(item)
        for item in (projected.get("closed_positions") or [])
        if isinstance(item, dict) and str(item.get("source") or "") != "ibkr_ledger"
    ] + [dict(item) for item in (week.get("closed_positions") or []) if isinstance(item, dict)]
    return projected


def build_ibkr_derived_review_projection(
    weekly_reviews: Iterable[dict[str, Any]],
    broker_trade_ledger: dict[str, Any],
    *,
    price_frames: dict[str, Any] | None = None,
    fx_rates: dict[str, float] | None = None,
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
    external_cash_flows_hkd: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reviews = [
        dict(review)
        for review in weekly_reviews
        if isinstance(review, dict) and str(review.get("week_id") or "").strip()
    ]
    ytd = build_ibkr_derived_ytd_portfolio(
        reviews,
        broker_trade_ledger,
        price_frames=price_frames,
        fx_rates=fx_rates,
        fx_rates_by_date=fx_rates_by_date,
        external_cash_flows_hkd=external_cash_flows_hkd,
    )
    if not ytd.get("success"):
        return {**ytd, "reviews_by_week": {}}

    fx = dict(DEFAULT_FX_RATES)
    fx.update({str(k).upper(): float(v) for k, v in (fx_rates or {}).items() if v is not None})
    by_week = {str(review.get("week_id") or ""): review for review in reviews}
    projected = {}
    for week in ytd.get("weeks") or []:
        week_id = str(week.get("week_id") or "")
        if not week_id:
            continue
        projected[week_id] = _project_week_to_review(
            by_week.get(week_id) or {"week_id": week_id},
            week,
            fx_rates=fx,
            fx_rates_by_date=fx_rates_by_date,
        )
    return {
        **ytd,
        "source": "ibkr_ledger",
        "projection_source": "ibkr_derived_review_projection",
        "reviews_by_week": projected,
    }


def build_ibkr_w25_baseline_delta_projection(
    weekly_reviews: Iterable[dict[str, Any]],
    broker_trade_ledger: dict[str, Any],
    *,
    baseline: dict[str, Any],
    price_frames: dict[str, Any] | None = None,
    fx_rates: dict[str, float] | None = None,
    fx_rates_by_date: dict[str, dict[str, float]] | None = None,
    external_cash_flows_hkd: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reviews = sorted(
        [dict(review) for review in weekly_reviews if isinstance(review, dict) and str(review.get("week_id") or "").strip()],
        key=lambda review: _week_end_date(str(review.get("week_id"))) or date.min,
    )
    if not reviews:
        return {"success": False, "error": "no_weekly_reviews", "reviews_by_week": {}, "weeks": []}
    baseline_week_id = str((baseline or {}).get("baseline_week_id") or IBKR_DELTA_BASELINE_WEEK_ID)
    baseline_day = _date_from_text((baseline or {}).get("baseline_date")) or _week_end_date(baseline_week_id)
    if baseline_day is None:
        return {"success": False, "error": "invalid_baseline_date", "reviews_by_week": {}, "weeks": []}

    price_frames = price_frames or {}
    fx = dict(DEFAULT_FX_RATES)
    fx.update({str(k).upper(): float(v) for k, v in (fx_rates or {}).items() if v is not None})
    delta_trades = filter_ibkr_delta_trades(broker_trade_ledger or {}, baseline_date=baseline_day)
    trades_by_week: dict[str, list[dict[str, Any]]] = {}
    for trade in delta_trades:
        trades_by_week.setdefault(str(trade.get("week_id") or ""), []).append(trade)

    by_week = {str(review.get("week_id") or ""): review for review in reviews}
    weeks: list[dict[str, Any]] = []
    projected: dict[str, dict[str, Any]] = {}
    all_missing_prices: set[str] = set()
    all_stale_prices: set[str] = set()
    all_integrity_issues: list[dict[str, Any]] = []
    all_integrity_issue_keys: set[tuple[Any, ...]] = set()
    previous_positions = _baseline_positions_by_ticker(baseline)
    baseline_review = by_week.get(baseline_week_id) or {}
    baseline_fx = _fx_rates_for_review(baseline_review, fx)
    previous_position_rows, _, baseline_missing_prices = _position_rows(
        previous_positions,
        week_end=baseline_day,
        price_frames=price_frames,
        fx_rates=baseline_fx,
        fx_rates_by_date=fx_rates_by_date,
    )
    all_missing_prices.update(baseline_missing_prices)
    previous_reported_nav: float | None = _reported_nav_hkd(by_week.get(baseline_week_id) or {})
    external_cash_flows = [dict(item) for item in (external_cash_flows_hkd or []) if isinstance(item, dict)]

    for review in reviews:
        week_id = str(review.get("week_id") or "")
        week_end = _week_end_date(week_id)
        if week_end is None or week_end <= baseline_day:
            continue
        week_fx = _fx_rates_for_review(review, fx)
        applied = apply_ibkr_delta_trades_to_baseline(
            baseline,
            delta_trades,
            fx_rates_by_date=fx_rates_by_date,
            through_date=week_end,
            fx_rates=week_fx,
            external_cash_flows_hkd=external_cash_flows,
        )
        positions = {
            str(row.get("ticker") or "").upper(): dict(row)
            for row in applied.get("positions") or []
            if str(row.get("ticker") or "").strip()
        }
        week_trades = [dict(item) for item in trades_by_week.get(week_id, [])]
        rows, market_value_hkd, missing_prices = _position_rows(
            positions,
            week_end=week_end,
            price_frames=price_frames,
            fx_rates=week_fx,
            fx_rates_by_date=fx_rates_by_date,
        )
        reported_nav = _reported_nav_hkd(review)
        cash_balance_hkd = _safe_float(applied.get("cash_balance_hkd"))
        derived_nav_hkd = round(market_value_hkd + cash_balance_hkd, 2)
        portfolio_rows = _portfolio_rows(
            rows,
            previous_rows=previous_position_rows,
            positions=positions,
            weekly_trades=week_trades,
            week_closed_positions=[],
            reported_nav_hkd=reported_nav or derived_nav_hkd,
            market_value_hkd=market_value_hkd,
            fx_rates=week_fx,
            fx_rates_by_date=fx_rates_by_date,
            price_frames=price_frames,
            week_end=week_end,
        )
        week_shell = {"week_id": week_id, "week_end": week_end.isoformat()}
        price_audit = _build_price_audit(week_shell, rows)
        stale_prices = sorted(
            {
                str(item.get("ticker") or "").upper()
                for item in price_audit
                if item.get("fallback_reason") == "stale_price"
            }
        )
        all_missing_prices.update(missing_prices)
        all_stale_prices.update(stale_prices)
        integrity_issues = [
            {
                "issue": "sell_exceeds_position",
                **dict(item),
            }
            for item in (applied.get("diagnostics") or {}).get("oversells", [])
        ]
        week_integrity_issues: list[dict[str, Any]] = []
        for issue in integrity_issues:
            key = _integrity_issue_key(issue)
            if key in all_integrity_issue_keys:
                continue
            all_integrity_issue_keys.add(key)
            all_integrity_issues.append(issue)
            week_integrity_issues.append(issue)
        external_cash_flow = _cash_flows_for_week(external_cash_flows, week_id)
        reconciliation = _build_weekly_reconciliation(
            week_id=week_id,
            start_nav_hkd=previous_reported_nav,
            end_nav_hkd=reported_nav,
            portfolio_rows=portfolio_rows,
            external_cash_flow_hkd=external_cash_flow,
        )
        week_reallocation_events, week_pairings = _build_reallocation_events(
            week_trades,
            fx_rates=week_fx,
            fx_rates_by_date=fx_rates_by_date,
        )
        week_data_quality = {
            "price_fallback_tickers": missing_prices,
            "missing_price_tickers": missing_prices,
            "stale_price_tickers": stale_prices,
            "reconciliation_status": reconciliation.get("status"),
            "reconciliation_gap_hkd": reconciliation.get("gap_hkd"),
            "position_integrity_issues": week_integrity_issues,
            "baseline_week_id": baseline_week_id,
        }
        week = {
            "week_id": week_id,
            "week_end": week_end.isoformat(),
            "reported_nav_hkd": reported_nav if reported_nav is not None else derived_nav_hkd,
            "derived_nav_hkd": derived_nav_hkd,
            "cash_balance_hkd": cash_balance_hkd,
            "trades": week_trades,
            "trade_count": len(week_trades),
            "positions": deepcopy(positions),
            "position_rows": rows,
            "portfolio_rows": portfolio_rows,
            "position_count": len(rows),
            "market_value_hkd": market_value_hkd,
            "missing_price_tickers": missing_prices,
            "price_audit": price_audit,
            "reconciliation": reconciliation,
            "data_quality": week_data_quality,
            "position_integrity_issues": week_integrity_issues,
            "closed_positions": [],
            "reallocation_events": [dict(item) for item in week_reallocation_events],
            "reallocation_pairings": {repr(key): [dict(item) for item in value] for key, value in week_pairings.items()},
            "position_changes": _position_changes(previous_positions, positions),
        }
        weeks.append(week)
        base_review = by_week.get(week_id) or {"week_id": week_id}
        projected_review = _project_week_to_review(
            base_review,
            week,
            fx_rates=fx,
            fx_rates_by_date=fx_rates_by_date,
        )
        projected_review["portfolio_source"] = IBKR_DELTA_PROJECTION_SOURCE
        projected_review["canonical_source"] = IBKR_DELTA_PROJECTION_SOURCE
        projected_review["projection_source"] = IBKR_DELTA_PROJECTION_SOURCE
        projected_review["ibkr_delta_baseline"] = {
            "week_id": baseline_week_id,
            "date": baseline_day.isoformat(),
            "source": (baseline or {}).get("source") or IBKR_DELTA_BASELINE_SOURCE,
        }
        projected_review["ibkr_delta_diagnostics"] = applied.get("diagnostics") or {}
        projected_review["cash_balance"] = cash_balance_hkd
        projected_review["total_portfolio_value"] = reported_nav if reported_nav is not None else derived_nav_hkd
        projected[week_id] = projected_review
        previous_position_rows = rows
        previous_positions = positions
        if reported_nav is not None:
            previous_reported_nav = reported_nav

    diagnostics = _transition_diagnostics(
        source=IBKR_DELTA_PROJECTION_SOURCE,
        weeks=weeks,
        price_fallback_tickers=all_missing_prices,
        integrity_issues=all_integrity_issues,
        closed_positions=[],
        reallocation_events=[event for week in weeks for event in week.get("reallocation_events", [])],
    )
    diagnostics["baseline_week_id"] = baseline_week_id
    diagnostics["baseline_date"] = baseline_day.isoformat()
    diagnostics["delta_trade_count"] = len(delta_trades)
    return {
        "success": True,
        "source": "ibkr_ledger",
        "canonical_source": IBKR_DELTA_PROJECTION_SOURCE,
        "projection_source": IBKR_DELTA_PROJECTION_SOURCE,
        "summary": {
            "baseline_week_id": baseline_week_id,
            "baseline_date": baseline_day.isoformat(),
            "start_week_id": weeks[0]["week_id"] if weeks else "",
            "end_week_id": weeks[-1]["week_id"] if weeks else "",
            "week_count": len(weeks),
            "delta_trade_count": len(delta_trades),
            "latest_market_value_hkd": weeks[-1]["market_value_hkd"] if weeks else 0.0,
            "latest_reported_nav_hkd": weeks[-1]["reported_nav_hkd"] if weeks else None,
            "latest_position_count": weeks[-1]["position_count"] if weeks else 0,
            "missing_price_tickers": sorted(all_missing_prices),
            "stale_price_tickers": sorted(all_stale_prices),
            "position_integrity_issues": all_integrity_issues,
            "data_quality": {
                "price_fallback_tickers": sorted(all_missing_prices),
                "missing_price_tickers": sorted(all_missing_prices),
                "stale_price_tickers": sorted(all_stale_prices),
                "position_integrity_issues": all_integrity_issues,
                "silent_fallback_count": 0,
            },
        },
        "diagnostics": diagnostics,
        "cash_flows": external_cash_flows,
        "weeks": weeks,
        "reviews_by_week": projected,
    }


def _position_changes(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for ticker in sorted(set(before) | set(after)):
        before_qty = _safe_float((before.get(ticker) or {}).get("shares"))
        after_qty = _safe_float((after.get(ticker) or {}).get("shares"))
        delta = after_qty - before_qty
        if abs(delta) <= 1e-9:
            continue
        rows.append(
            {
                "ticker": ticker,
                "before_shares": round(before_qty, 4),
                "after_shares": round(after_qty, 4),
                "delta_shares": round(delta, 4),
            }
        )
    return rows
