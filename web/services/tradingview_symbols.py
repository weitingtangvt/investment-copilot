from __future__ import annotations

from copy import deepcopy
from typing import Any


_SUFFIX_TO_EXCHANGE = {
    ".HK": "HKEX",
    ".SH": "SSE",
    ".SS": "SSE",
    ".SZ": "SZSE",
    ".US": "NASDAQ",
    ".AS": "EURONEXT",
    ".DE": "XETR",
    ".VI": "VIE",
    ".T": "TSE",
    ".KS": "KRX",
    ".KQ": "KRX",
}


def resolve_tradingview_symbol(ticker: Any) -> str:
    raw = str(ticker or "").strip().upper()
    if not raw:
        return ""
    if ":" in raw:
        return raw
    for suffix, exchange in _SUFFIX_TO_EXCHANGE.items():
        if raw.endswith(suffix):
            symbol = raw[: -len(suffix)]
            return f"{exchange}:{symbol}"
    return f"NASDAQ:{raw}"


def annotate_us_screener_item(item: dict[str, Any] | None) -> dict[str, Any]:
    record = dict(item or {})
    ticker = str(record.get("ticker") or record.get("stock_id") or "").strip().upper()
    if ticker:
        record["ticker"] = ticker
        record["tradingview_symbol"] = resolve_tradingview_symbol(ticker)
    else:
        record["tradingview_symbol"] = ""
    return record


def annotate_us_screener_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    annotated = deepcopy(payload or {})
    strategies = annotated.get("strategies")
    if not isinstance(strategies, dict):
        return annotated
    for strategy_payload in strategies.values():
        if not isinstance(strategy_payload, dict):
            continue
        items = strategy_payload.get("items")
        if isinstance(items, list):
            strategy_payload["items"] = [annotate_us_screener_item(item) for item in items]
        presets = strategy_payload.get("presets")
        if isinstance(presets, dict):
            for preset in presets.values():
                if not isinstance(preset, dict):
                    continue
                preset_items = preset.get("items")
                if isinstance(preset_items, list):
                    preset["items"] = [annotate_us_screener_item(item) for item in preset_items]
    return annotated
