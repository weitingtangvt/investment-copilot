from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from typing import Any


_HK_SYMBOL_RE = re.compile(r"^\d{1,5}$")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_float(value: Any) -> float | None:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_side(raw_side: Any, quantity: float | None) -> str:
    text = _clean(raw_side).upper()
    if text in {"BUY", "BOT", "B"}:
        return "BUY"
    if text in {"SELL", "SLD", "S"}:
        return "SELL"
    if quantity is not None and quantity < 0:
        return "SELL"
    return "BUY"


def _normalize_datetime(raw_value: Any) -> tuple[str, str]:
    text = _clean(raw_value)
    if not text:
        return "", ""
    normalized = text.replace("/", "-")
    normalized = re.sub(r"\s+[A-Z]{2,5}$", "", normalized).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d, %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.isoformat(timespec="seconds"), parsed.date().isoformat()
        except ValueError:
            continue
    return normalized, normalized[:10]


def _ticker_from_ibkr_symbol(symbol: str, currency: str, exchange: str) -> str:
    clean_symbol = symbol.strip().upper()
    clean_currency = currency.strip().upper()
    clean_exchange = exchange.strip().upper()
    if clean_symbol and "." in clean_symbol:
        return clean_symbol
    if clean_symbol == "SAMPLE" and clean_currency == "EUR":
        return "SAMPLE"
    if clean_currency == "HKD" and _HK_SYMBOL_RE.fullmatch(clean_symbol):
        return f"{clean_symbol}.HK"
    if clean_exchange in {"SEHK", "HKSE"} and _HK_SYMBOL_RE.fullmatch(clean_symbol):
        return f"{clean_symbol}.HK"
    return clean_symbol


def _canonical_alias_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _resolve_import_ticker(symbol: str, currency: str, exchange: str, ticker_aliases: dict[str, str] | None) -> str:
    fallback = _ticker_from_ibkr_symbol(symbol, currency, exchange)
    aliases = {str(key or "").strip().upper(): str(value or "").strip().upper() for key, value in (ticker_aliases or {}).items()}
    for key, value in list(aliases.items()):
        for suffix in (".HK", ".DE", ".AS", ".VI", ".T", ".KS", ".KQ", ".US", ".SH", ".SZ", ".SS"):
            if key.endswith(suffix):
                aliases.setdefault(key[: -len(suffix)], value)
            if value.endswith(suffix):
                aliases.setdefault(value[: -len(suffix)], value)
    candidates = [
        _canonical_alias_key(symbol),
        _canonical_alias_key(fallback),
    ]
    if fallback.endswith(".HK"):
        candidates.append(_canonical_alias_key(fallback[:-3]))
    for candidate in candidates:
        resolved = aliases.get(candidate)
        if resolved:
            return _canonical_alias_key(resolved)
    return fallback


def _dedupe_key(trade: dict[str, Any]) -> str:
    external_trade_id = _clean(trade.get("external_trade_id"))
    parts = [
        trade.get("broker", ""),
        external_trade_id,
    ]
    if external_trade_id:
        parts = [trade.get("broker", ""), external_trade_id]
    else:
        parts.extend(
            [
                trade.get("trade_datetime", ""),
                trade.get("symbol", ""),
                trade.get("side", ""),
                trade.get("quantity", ""),
                trade.get("price", ""),
                trade.get("currency", ""),
                trade.get("net_cash", ""),
                trade.get("net_cash_hkd", ""),
                trade.get("commission", ""),
            ]
        )
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_value(row: dict[str, str], *names: str) -> str:
    lowered = {str(key or "").strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return _clean(value)
    return ""


def _build_trade_from_flat_row(
    row: dict[str, str],
    *,
    source_filename: str,
    row_number: int,
    ticker_aliases: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    symbol = _row_value(row, "Symbol")
    trade_date_raw = _row_value(row, "TradeDate", "Trade Date")
    price = _parse_float(_row_value(row, "TradePrice", "Trade Price", "Price"))
    quantity = _parse_float(_row_value(row, "Quantity", "Qty"))
    if not symbol or quantity is None or price is None:
        return None, {"row": row_number, "symbol": symbol, "error": "Text"}
    side = _normalize_side(_row_value(row, "Buy/Sell", "Side"), quantity)
    trade_date, _ = _normalize_datetime(trade_date_raw)
    if trade_date and len(trade_date) == 8 and trade_date.isdigit():
        trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    if len(trade_date) == 10 and trade_date[4] == "-" and trade_date[7] == "-":
        trade_datetime = f"{trade_date}T00:00:00"
    else:
        trade_datetime = trade_date
    currency = _row_value(row, "CurrencyPrimary", "Currency")
    exchange = _row_value(row, "Exchange", "Listing Exchange")
    resolved_ticker = _resolve_import_ticker(symbol, currency, exchange, ticker_aliases)
    trade = {
        "broker": "IBKR",
        "source_filename": source_filename,
        "source_row_number": row_number,
        "account_id": _row_value(row, "ClientAccountID", "Account ID"),
        "asset_category": _row_value(row, "AssetClass", "Asset Class"),
        "currency": currency,
        "ibkr_symbol": symbol,
        "symbol": resolved_ticker,
        "ticker": resolved_ticker,
        "stock_id": resolved_ticker,
        "trade_datetime": trade_datetime,
        "trade_date": trade_date[:10] if trade_date else "",
        "side": side,
        "quantity": abs(quantity),
        "signed_quantity": quantity,
        "price": price,
        "commission": _parse_float(_row_value(row, "IBCommission", "Comm/Fee", "Commission")) or 0.0,
        "net_cash": _parse_float(_row_value(row, "NetCash", "Net Cash")),
        "net_cash_hkd": _parse_float(
            _row_value(
                row,
                "NetCashHKD",
                "Net Cash HKD",
                "Base Net Cash",
                "Net Cash (Base)",
                "NetCashBase",
            )
        ),
        "description": _row_value(row, "Description"),
        "order_id": _row_value(row, "Order ID", "OrderID"),
        "external_trade_id": _row_value(row, "Trade ID", "TradeID", "Exec ID", "Execution ID"),
        "raw": {str(k): v for k, v in row.items() if k is not None},
    }
    trade["dedupe_key"] = _dedupe_key(trade)
    return trade, None


def _is_ibkr_trade_row(row: dict[str, str]) -> bool:
    section = _row_value(row, "Trades")
    if section and section.lower() != "trades":
        return False
    row_type = _row_value(row, "Header")
    if row_type and row_type.lower() != "data":
        return False
    data_discriminator = _row_value(row, "DataDiscriminator")
    symbol = _row_value(row, "Symbol")
    quantity = _row_value(row, "Quantity", "Qty")
    price = _row_value(row, "T. Price", "Trade Price", "Price")
    return bool(symbol and quantity and price and (not data_discriminator or data_discriminator.lower() == "order"))


def parse_ibkr_csv_text(
    csv_text: str,
    *,
    source_filename: str = "",
    ticker_aliases: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return [], [{"row": 0, "error": "CSV Text"}]

    fieldnames = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
    flat_export = "tradedate" in fieldnames and "tradeprice" in fieldnames

    for row_number, row in enumerate(reader, start=2):
        if flat_export:
            trade, error = _build_trade_from_flat_row(
                row,
                source_filename=source_filename,
                row_number=row_number,
                ticker_aliases=ticker_aliases,
            )
            if error:
                errors.append(error)
            elif trade:
                trades.append(trade)
            continue
        if _is_ibkr_trade_row(row):
            symbol = _row_value(row, "Symbol")
            currency = _row_value(row, "Currency")
            exchange = _row_value(row, "Exchange", "Listing Exchange")
            quantity = _parse_float(_row_value(row, "Quantity", "Qty"))
            price = _parse_float(_row_value(row, "T. Price", "Trade Price", "Price"))
            commission = _parse_float(_row_value(row, "Comm/Fee", "Commission", "IB Commission")) or 0.0
            if quantity is None or price is None:
                errors.append({"row": row_number, "symbol": symbol, "error": "Text"})
                continue
            side = _normalize_side(_row_value(row, "Buy/Sell", "Side"), quantity)
            trade_datetime, trade_date = _normalize_datetime(_row_value(row, "Date/Time", "Trade Date", "Date"))
            resolved_ticker = _resolve_import_ticker(symbol, currency, exchange, ticker_aliases)
            trade = {
                "broker": "IBKR",
                "source_filename": source_filename,
                "source_row_number": row_number,
                "asset_category": _row_value(row, "Asset Category"),
                "currency": currency,
                "ibkr_symbol": symbol,
                "symbol": resolved_ticker,
                "ticker": resolved_ticker,
                "stock_id": resolved_ticker,
                "trade_datetime": trade_datetime,
                "trade_date": trade_date,
                "side": side,
                "quantity": abs(quantity),
                "signed_quantity": quantity,
                "price": price,
                "commission": commission,
                "net_cash_hkd": _parse_float(
                    _row_value(
                        row,
                        "NetCashHKD",
                        "Net Cash HKD",
                        "Base Net Cash",
                        "Net Cash (Base)",
                        "NetCashBase",
                    )
                ),
                "exchange": exchange,
                "order_id": _row_value(row, "Order ID", "OrderID"),
                "external_trade_id": _row_value(row, "Trade ID", "TradeID", "Exec ID", "Execution ID"),
                "raw": {str(k): v for k, v in row.items() if k is not None},
            }
            trade["dedupe_key"] = _dedupe_key(trade)
            trades.append(trade)
    return trades, errors


def import_ibkr_csv_text(
    storage: Any,
    csv_text: str,
    *,
    source_filename: str = "",
    ticker_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    trades, errors = parse_ibkr_csv_text(csv_text, source_filename=source_filename, ticker_aliases=ticker_aliases)
    result = storage.replace_broker_trade_ledger(
        broker="IBKR",
        source_filename=source_filename,
        trades=trades,
        errors=errors,
        raw_text=csv_text,
    )
    return result
