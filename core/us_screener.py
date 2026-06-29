from __future__ import annotations

import os
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import pandas as pd
import requests
import yfinance as yf

from .data_sources import AlpacaUSMarketDataClient
from .local_ohlcv_cache import SQLiteOHLCVCache, SQLiteOHLCVCacheConfig

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 500) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


MARKET_CAP_MIN = 1_000_000_000
BATCH_SIZE = _env_int("US_SCREENER_BATCH_SIZE", 80, minimum=10, maximum=250)
BATCH_SLEEP_SECONDS = 1.0
BATCH_BACKOFF_SECONDS = (2.0, 5.0)
PROFILE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
YFINANCE_DOWNLOAD_TIMEOUT_SECONDS = _env_int("US_SCREENER_HISTORY_TIMEOUT_SECONDS", 15, minimum=5, maximum=60)
YFINANCE_DOWNLOAD_THREADS = _env_bool("US_SCREENER_YFINANCE_THREADS", True)
HISTORY_FALLBACK_WORKERS = _env_int("US_SCREENER_HISTORY_FALLBACK_WORKERS", 8, minimum=1, maximum=16)
PROFILE_FETCH_WORKERS = _env_int("US_SCREENER_PROFILE_WORKERS", 6, minimum=1, maximum=12)
UNIVERSE_SNAPSHOT_TTL_HOURS = _env_int("US_SCREENER_UNIVERSE_SNAPSHOT_TTL_HOURS", 168, minimum=1, maximum=24 * 30)
HISTORY_PERIOD = "18mo"

STRATEGY_MOMENTUM_SPIKE = "momentum_spike"
STRATEGY_POST_52W_LOW_REVERSAL = "post_52w_low_reversal"

MOMENTUM_PRESET_CLASSIC = "classic_spike"
MOMENTUM_PRESET_GAIN_20D_BREAKOUT = "breakout_20d"
MOMENTUM_PRESET_MA200_EXTENSION = "ma200_extension"

REVERSAL_RECENT_TRADING_DAYS = 126
REVERSAL_LOOKBACK_TRADING_DAYS = 252
REVERSAL_DISTANCE_ABOVE_200MA_PCT = 20.0

PROVIDER_SYMBOL_ALIASES = {
    "BRKA": "BRK.A",
    "BRKB": "BRK.B",
}

EXCLUDED_NAME_TOKENS = (
    "ETF",
    "SAMPLE",
    "FUND",
    "TRUST",
    "WARRANT",
    "RIGHT",
    "UNIT",
    "PREFERRED",
)

EXCLUDED_SECTOR_TOKENS = (
    "HEALTHCARE",
    "HEALTH CARE",
    "Text",
    "Text",
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("$", "").replace("%", "")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _normalize_ticker(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    text = text.replace(".", "").replace("/", "")
    text = re.sub(r"[^A-Z0-9\-]", "", text)
    return text


def _to_provider_symbol(ticker: str) -> str:
    symbol = str(ticker or "").strip().upper()
    return PROVIDER_SYMBOL_ALIASES.get(symbol, symbol)


def _from_provider_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    for internal, provider_symbol in PROVIDER_SYMBOL_ALIASES.items():
        if text == provider_symbol.upper():
            return internal
    return text


def _normalize_universe_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ticker = _normalize_ticker(
        row.get("ticker")
        or row.get("symbol")
        or row.get("Text")
        or row.get("Ticker")
    )
    stock_name = str(row.get("stock_name") or row.get("name") or row.get("Text") or "").strip()
    market_cap = _safe_float(row.get("market_cap") or row.get("Text"))
    if not ticker or not stock_name or market_cap is None:
        return None
    return {
        "ticker": ticker,
        "stock_name": stock_name,
        "market_cap": market_cap,
        "sector": str(row.get("sector") or row.get("Text") or "").strip(),
        "industry": str(row.get("industry") or row.get("Text") or "").strip(),
    }


def _is_supported_common_stock(item: Dict[str, Any]) -> bool:
    name = str(item.get("stock_name") or "").upper()
    ticker = str(item.get("ticker") or "").upper()
    sector = str(item.get("sector") or "").upper()
    if not ticker or not re.fullmatch(r"[A-Z0-9\-]{1,10}", ticker):
        return False
    if any(token in name for token in EXCLUDED_NAME_TOKENS):
        return False
    if any(token in sector for token in EXCLUDED_SECTOR_TOKENS):
        return False
    return True


def _first_sentence(text: str, max_len: int = 220) -> str:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return ""
    parts = re.split(r"(?<=[.!?. !?])\s+", clean)
    first = (parts[0] or "").strip()
    if len(first) > max_len:
        return first[:max_len].rstrip(" ,;:") + "..."
    return first


def _build_company_intro(sector: str, industry: str, summary: str) -> str:
    prefix_bits = []
    if sector:
        prefix_bits.append(f"Text: {sector}")
    if industry:
        prefix_bits.append(f"Text: {industry}")
    prefix = "; ".join(prefix_bits)
    sentence = _first_sentence(summary)
    if prefix and sentence:
        return f"{prefix}. {sentence}"
    return prefix or sentence


def _profile_strength(profile: Optional[Dict[str, Any]]) -> int:
    data = dict(profile or {})
    summary = str(data.get("summary") or "").strip()
    intro = str(data.get("company_intro") or "").strip()
    sector = str(data.get("sector") or "").strip()
    industry = str(data.get("industry") or "").strip()
    score = len(summary) + len(intro)
    if sector:
        score += 20
    if industry:
        score += 20
    return score


def _merge_company_profile(cached: Optional[Dict[str, Any]], fetched: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cached_data = dict(cached or {})
    fetched_data = dict(fetched or {})

    cached_score = _profile_strength(cached_data)
    fetched_score = _profile_strength(fetched_data)
    prefer_cached = cached_score > fetched_score

    sector = str((fetched_data if not prefer_cached else cached_data).get("sector") or "").strip()
    if not sector:
        sector = str(cached_data.get("sector") or fetched_data.get("sector") or "").strip()

    industry = str((fetched_data if not prefer_cached else cached_data).get("industry") or "").strip()
    if not industry:
        industry = str(cached_data.get("industry") or fetched_data.get("industry") or "").strip()

    summary = str((cached_data if prefer_cached else fetched_data).get("summary") or "").strip()
    if not summary:
        summary = str(fetched_data.get("summary") or cached_data.get("summary") or "").strip()

    company_intro = str((cached_data if prefer_cached else fetched_data).get("company_intro") or "").strip()
    if not company_intro:
        company_intro = _build_company_intro(sector, industry, summary)

    merged = {
        "fetched_at": str(fetched_data.get("fetched_at") or cached_data.get("fetched_at") or _now_iso()).strip(),
        "source": str((cached_data if prefer_cached else fetched_data).get("source") or "").strip()
            or str(cached_data.get("source") or fetched_data.get("source") or "").strip(),
        "sector": sector,
        "industry": industry,
        "summary": summary,
        "company_intro": company_intro,
    }
    return merged


def _default_strategy_payload() -> Dict[str, Any]:
    return {"filters": {}, "matched": 0, "items": []}


def _default_momentum_preset_payload() -> Dict[str, Any]:
    return {"label": "", "description": "", "filters": {}, "matched": 0, "items": []}


def _default_momentum_presets() -> Dict[str, Dict[str, Any]]:
    return {
        MOMENTUM_PRESET_CLASSIC: {
            "label": "5Text3Text 2x",
            "description": "Text5TextTradeText3Text 2 Text",
            "filters": {
                "daily_gain_5d_gt": 10.0,
                "avg_volume_5d_vs_3m_gte": 2.0,
            },
            "matched": 0,
            "items": [],
        },
        MOMENTUM_PRESET_GAIN_20D_BREAKOUT: {
            "label": "20Text30%",
            "description": "Text20TextTradeText30%",
            "filters": {
                "daily_gain_5d_gt": 10.0,
                "gain_20d_gt": 30.0,
            },
            "matched": 0,
            "items": [],
        },
        MOMENTUM_PRESET_MA200_EXTENSION: {
            "label": "TextMA200 50%",
            "description": "TextMA200 Text50%",
            "filters": {
                "daily_gain_5d_gt": 10.0,
                "distance_above_200ma_pct_gt": 50.0,
            },
            "matched": 0,
            "items": [],
        },
    }


def _normalize_momentum_presets(raw_presets: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    base = _default_momentum_presets()
    incoming = dict(raw_presets or {})
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, default_value in base.items():
        incoming_value = dict(incoming.get(key) or {})
        merged = {
            **_default_momentum_preset_payload(),
            **incoming_value,
            "label": default_value.get("label", ""),
            "description": default_value.get("description", ""),
            "filters": dict(default_value.get("filters") or {}),
        }
        merged["items"] = list(merged.get("items") or [])
        merged["matched"] = int(merged.get("matched") or len(merged["items"]))
        normalized[key] = merged
    return normalized


def _default_cache_payload() -> Dict[str, Any]:
    return {
        "source": "",
        "path": "",
        "cached_at": "",
        "recovered": False,
        "is_stale": False,
    }


def build_default_us_screener_payload() -> Dict[str, Any]:
    return {
        "success": False,
        "as_of_market_date": "",
        "generated_at": "",
        "cache": _default_cache_payload(),
        "stats": {
            "universe_total": 0,
            "after_market_cap": 0,
            "history_attempted": 0,
            "history_succeeded": 0,
            "history_failed": 0,
        },
        "warnings": [],
        "strategies": {
            STRATEGY_MOMENTUM_SPIKE: {
                "filters": {
                    "market_cap_min": MARKET_CAP_MIN,
                    "daily_gain_5d_gt": 10.0,
                    "history_period": HISTORY_PERIOD,
                },
                "presets": _default_momentum_presets(),
                "matched": 0,
                "items": [],
            },
            STRATEGY_POST_52W_LOW_REVERSAL: {
                "filters": {
                    "market_cap_min": MARKET_CAP_MIN,
                    "new_low_window_days": REVERSAL_RECENT_TRADING_DAYS,
                    "lookback_days": REVERSAL_LOOKBACK_TRADING_DAYS,
                    "distance_above_200ma_gte": REVERSAL_DISTANCE_ABOVE_200MA_PCT,
                    "history_period": HISTORY_PERIOD,
                },
                "matched": 0,
                "items": [],
            },
        },
    }


def normalize_us_screener_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    default = build_default_us_screener_payload()
    raw = dict(payload or {})
    normalized = dict(default)
    normalized.update(raw)
    cache = dict(default["cache"])
    cache.update(raw.get("cache") or {})
    normalized["cache"] = cache
    stats = dict(default["stats"])
    stats.update(raw.get("stats") or {})
    normalized["stats"] = stats

    default_strategies = default["strategies"]
    strategies = {
        key: {**_default_strategy_payload(), **dict(default_strategies.get(key) or {}), **dict((raw.get("strategies") or {}).get(key) or {})}
        for key in default_strategies
    }

    # Backward compatibility: old payload stored one single strategy at top-level.
    old_items = list(raw.get("items") or [])
    old_filters = dict(raw.get("filters") or {})
    momentum_strategy = dict(strategies.get(STRATEGY_MOMENTUM_SPIKE) or {})
    momentum_items = list(momentum_strategy.get("items") or [])
    momentum_presets = _normalize_momentum_presets(momentum_strategy.get("presets"))
    if old_items and not momentum_items:
        strategies[STRATEGY_MOMENTUM_SPIKE] = {
            "filters": {**default_strategies[STRATEGY_MOMENTUM_SPIKE]["filters"], **old_filters},
            "matched": len(old_items),
            "items": old_items,
            "presets": _normalize_momentum_presets(
                {
                    MOMENTUM_PRESET_CLASSIC: {
                        "items": old_items,
                        "matched": len(old_items),
                    }
                }
            ),
        }
    else:
        momentum_strategy["presets"] = momentum_presets
        strategies[STRATEGY_MOMENTUM_SPIKE] = momentum_strategy

    for key, value in strategies.items():
        value["filters"] = dict(value.get("filters") or {})
        value["items"] = list(value.get("items") or [])
        value["matched"] = int(value.get("matched") or len(value["items"]))
        if key == STRATEGY_MOMENTUM_SPIKE:
            value["presets"] = _normalize_momentum_presets(value.get("presets"))
        strategies[key] = value

    normalized["strategies"] = strategies
    normalized.pop("filters", None)
    normalized.pop("items", None)
    return normalized


class USScreenerService:
    def __init__(
        self,
        storage: Any,
        *,
        universe_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        history_fetcher: Optional[Callable[[List[str]], Dict[str, pd.DataFrame]]] = None,
        profile_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.storage = storage
        self.universe_fetcher = universe_fetcher or self._fetch_universe_from_akshare
        self.sleep_fn = sleep_fn
        self._alpaca_client: Optional[AlpacaUSMarketDataClient] = None
        self._history_cache: Optional[SQLiteOHLCVCache] = None
        if history_fetcher is not None:
            self.history_fetcher = history_fetcher
            self.market_data_provider = "custom"
        else:
            self.history_fetcher = self._build_default_history_fetcher()
        if profile_fetcher is not None:
            self.profile_fetcher = profile_fetcher
            self.market_data_provider = "custom"
        else:
            self.profile_fetcher = self._build_default_profile_fetcher()

    def _build_default_history_fetcher(self) -> Callable[[List[str]], Dict[str, pd.DataFrame]]:
        client = self._build_alpaca_client()
        if client is not None:
            self._alpaca_client = client
            self.market_data_provider = "alpaca"
            db_path = Path(getattr(self.storage, "base_dir", Path.home())) / "market_history_cache.sqlite3"
            self._history_cache = SQLiteOHLCVCache(SQLiteOHLCVCacheConfig(db_path=db_path), logger_=logger)
            return self._fetch_history_from_alpaca_cache
        self.market_data_provider = "yfinance"
        return self._fetch_history_from_yfinance

    def _build_default_profile_fetcher(self) -> Callable[[str], Dict[str, Any]]:
        client = self._alpaca_client or self._build_alpaca_client()
        if client is not None and self.market_data_provider == "alpaca":
            self._alpaca_client = client
            return client.fetch_company_profile
        return self._fetch_profile_from_yfinance

    def _fetch_history_from_alpaca_cache(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        if self._alpaca_client is None or self._history_cache is None:
            return {}
        return self._history_cache.fetch_history_frames(
            tickers,
            history_loader=self._alpaca_history_loader,
            source="alpaca_cache",
        )

    def _alpaca_history_loader(self, tickers: List[str], *, start_date, end_date) -> Dict[str, pd.DataFrame]:
        if self._alpaca_client is None:
            return {}
        return self._alpaca_client.fetch_daily_history_window(tickers, start_at=start_date, end_at=end_date)

    def _build_alpaca_client(self) -> Optional[AlpacaUSMarketDataClient]:
        api_key = str(getattr(self.storage, "get_alpaca_api_key", lambda: "")() or "").strip()
        api_secret = str(getattr(self.storage, "get_alpaca_api_secret", lambda: "")() or "").strip()
        if not api_key or not api_secret:
            return None
        return AlpacaUSMarketDataClient(
            api_key=api_key,
            api_secret=api_secret,
            trading_base_url=str(
                getattr(
                    self.storage,
                    "get_alpaca_trading_base_url",
                    lambda: "https://paper-api.alpaca.markets/v2",
                )()
            ),
            market_data_base_url=str(
                getattr(
                    self.storage,
                    "get_alpaca_market_data_base_url",
                    lambda: "https://data.alpaca.markets/v2",
                )()
            ),
            stock_feed=str(getattr(self.storage, "get_alpaca_stock_feed", lambda: "iex")() or "iex"),
            request_timeout=YFINANCE_DOWNLOAD_TIMEOUT_SECONDS,
        )

    def run(
        self,
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        resume: bool = False,
    ) -> Dict[str, Any]:
        if resume:
            return self._resume_partial_run(progress_callback=progress_callback)
        return self._start_full_run(progress_callback=progress_callback)

    def _start_full_run(
        self,
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        self.storage.clear_us_screener_partial()
        snapshot = self._load_recent_universe_snapshot()
        if snapshot:
            filtered_universe = [dict(item) for item in snapshot.get("items") or [] if isinstance(item, dict)]
            universe_total = int(snapshot.get("universe_total") or len(filtered_universe))
            if progress_callback:
                progress_callback(
                    {
                        "step": "reuse_universe",
                        "message": f"Text, TextRefresh {len(filtered_universe)} TextStock",
                    }
                )
        else:
            if progress_callback:
                progress_callback({"step": "fetch_universe", "message": "Text"})

            universe_rows = self.universe_fetcher()
            universe: List[Dict[str, Any]] = []
            for row in universe_rows or []:
                normalized = _normalize_universe_row(row)
                if normalized:
                    universe.append(normalized)
            if not universe:
                raise RuntimeError("Text universe")

            filtered_universe = [
                item
                for item in universe
                if item["market_cap"] > MARKET_CAP_MIN and _is_supported_common_stock(item)
            ]
            if not filtered_universe:
                raise RuntimeError("Text")
            universe_total = len(universe)
            self._save_universe_snapshot(filtered_universe, universe_total=universe_total)

        stats = {
            "universe_total": universe_total,
            "after_market_cap": len(filtered_universe),
            "history_attempted": len(filtered_universe),
            "history_succeeded": 0,
            "history_failed": 0,
        }
        batches = self._build_batches(filtered_universe)
        partial_state = self._initialize_partial_state(filtered_universe, stats, batches)
        self.storage.save_us_screener_partial(partial_state)
        return self._process_batches(partial_state, batches, progress_callback=progress_callback)

    def _load_recent_universe_snapshot(self) -> Optional[Dict[str, Any]]:
        payload = self.storage.get_us_screener_universe_snapshot()
        if not isinstance(payload, dict):
            return None
        items = [dict(item) for item in payload.get("items") or [] if isinstance(item, dict)]
        if not items:
            return None
        updated_at = str(payload.get("updated_at") or "").strip()
        if not updated_at:
            return None
        try:
            snapshot_dt = datetime.fromisoformat(updated_at)
        except ValueError:
            return None
        if datetime.now() - snapshot_dt > timedelta(hours=UNIVERSE_SNAPSHOT_TTL_HOURS):
            return None
        return {
            "items": items,
            "updated_at": updated_at,
            "universe_total": int(payload.get("universe_total") or len(items)),
        }

    def _save_universe_snapshot(self, filtered_universe: List[Dict[str, Any]], *, universe_total: int) -> None:
        payload = {
            "updated_at": _now_iso(),
            "universe_total": int(universe_total or len(filtered_universe)),
            "after_market_cap": len(filtered_universe),
            "items": [
                {
                    "ticker": item.get("ticker"),
                    "stock_name": item.get("stock_name"),
                    "market_cap": item.get("market_cap"),
                    "sector": item.get("sector"),
                    "industry": item.get("industry"),
                }
                for item in filtered_universe
                if item.get("ticker")
            ],
        }
        self.storage.save_us_screener_universe_snapshot(payload)

    def _resume_partial_run(
        self,
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        partial = self.storage.get_us_screener_partial()
        pending = partial.get("pending_batches") if isinstance(partial, dict) else None
        if not pending:
            raise RuntimeError("CurrentText")
        batches = [list(batch) for batch in pending if batch]
        partial.setdefault("run_id", str(uuid4()))
        partial["state"] = "running"
        partial["updated_at"] = _now_iso()
        partial.setdefault("warnings", [])
        partial.setdefault("failed_batches", [])
        partial.setdefault("failed_tickers", [])
        strategies = partial.get("strategies") or self._default_strategy_container()
        partial["strategies"] = strategies
        if not partial.get("stats"):
            partial["stats"] = {
                "universe_total": len(partial.get("universe") or {}),
                "after_market_cap": len(partial.get("universe") or {}),
                "history_attempted": len(partial.get("universe") or {}),
                "history_succeeded": 0,
                "history_failed": 0,
            }
        total_batches = int(partial.get("total_batches") or 0)
        if total_batches <= 0:
            partial["total_batches"] = len(batches)
        self.storage.save_us_screener_partial(partial)
        return self._process_batches(partial, batches, progress_callback=progress_callback)

    def _process_batches(
        self,
        partial_state: Dict[str, Any],
        batches: List[List[str]],
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        stats = dict(partial_state.get("stats") or {})
        stats["history_succeeded"] = 0
        stats["history_failed"] = 0
        warnings = list(partial_state.get("warnings") or [])
        strategies = partial_state.get("strategies") or self._default_strategy_container()
        partial_state["failed_batches"] = []
        partial_state["failed_tickers"] = []
        partial_state["last_error"] = ""
        for key, value in strategies.items():
            value["items"] = list(value.get("items") or [])
            value.setdefault("filters", build_default_us_screener_payload()["strategies"][key]["filters"])
            if key == STRATEGY_MOMENTUM_SPIKE:
                value["presets"] = _normalize_momentum_presets(value.get("presets"))
        universe_map = dict(partial_state.get("universe") or {})
        total_batches = int(partial_state.get("total_batches") or len(batches))
        completed_batches = int(partial_state.get("completed_batches") or (total_batches - len(partial_state.get("pending_batches") or [])))
        latest_market_date = partial_state.get("as_of_market_date") or None

        for offset, batch in enumerate(batches):
            batch = [ticker for ticker in batch if ticker]
            if not batch:
                continue
            batch_number = completed_batches + offset + 1
            remaining = [list(b) for b in batches[offset:]]
            partial_state["pending_batches"] = remaining
            partial_state["current_batch_size"] = len(batch)
            partial_state["state"] = "running"
            partial_state["updated_at"] = _now_iso()
            self.storage.save_us_screener_partial(partial_state)
            self._emit_progress(progress_callback, partial_state, step="fetch_history", message=f"Text {batch_number}/{total_batches} TextHistoryText")

            history_map, used_fallback = self._fetch_history_resilient(batch)
            if used_fallback:
                provider_label = "Alpaca" if self.market_data_provider == "alpaca" else "Yahoo Finance"
                self._append_warning_once(warnings, f"{provider_label} TextResultText, TextAutoText")

            success_any = False
            for ticker in batch:
                history = history_map.get(ticker)
                if history is None or history.empty:
                    stats["history_failed"] = int(stats.get("history_failed") or 0) + 1
                    failed_tickers = list(partial_state.get("failed_tickers") or [])
                    failed_tickers.append(ticker)
                    partial_state["failed_tickers"] = list(dict.fromkeys(failed_tickers))
                    continue
                stock_meta = self._lookup_stock_meta(universe_map, ticker)
                try:
                    latest_history_date = pd.Timestamp(history.index[-1]).normalize().strftime("%Y-%m-%d")
                    latest_market_date = max(
                        filter(None, [latest_market_date, latest_history_date]),
                        default=latest_history_date,
                    )

                    momentum_item = self._build_momentum_spike_item(history, stock_meta)
                    if momentum_item:
                        self._assign_momentum_item(strategies[STRATEGY_MOMENTUM_SPIKE], momentum_item)

                    reversal_item = self._build_post_52w_low_reversal_item(history, stock_meta)
                    if reversal_item:
                        strategies[STRATEGY_POST_52W_LOW_REVERSAL]["items"].append(reversal_item)
                except Exception as exc:
                    logger.warning("process ticker failed for %s: %s", ticker, exc)
                    stats["history_failed"] = int(stats.get("history_failed") or 0) + 1
                    failed_tickers = list(partial_state.get("failed_tickers") or [])
                    failed_tickers.append(ticker)
                    partial_state["failed_tickers"] = list(dict.fromkeys(failed_tickers))
                    self._append_warning_once(warnings, f"{ticker} TextFailed, Text")
                    continue

                success_any = True
                stats["history_succeeded"] = int(stats.get("history_succeeded") or 0) + 1

            if not success_any:
                remaining = [list(b) for b in batches[offset + 1:]]
                partial_state["pending_batches"] = [list(batch)] + remaining
                failed_batches = list(partial_state.get("failed_batches") or [])
                failed_batches.append(list(batch))
                partial_state["failed_batches"] = failed_batches
                partial_state["last_error"] = f"Text {batch_number} HistoryText"
                partial_state["stats"] = stats
                partial_state["warnings"] = warnings
                partial_state["state"] = "partial"
                partial_state["updated_at"] = _now_iso()
                self.storage.save_us_screener_partial(partial_state)
                self._emit_progress(
                    progress_callback,
                    partial_state,
                    step="warning",
                    message=partial_state["last_error"],
                )
                continue

            partial_state["completed_batches"] = batch_number
            partial_state["stats"] = stats
            partial_state["warnings"] = warnings
            partial_state["as_of_market_date"] = latest_market_date or partial_state.get("as_of_market_date") or ""
            partial_state["pending_batches"] = [list(b) for b in batches[offset + 1:]]
            partial_state["updated_at"] = _now_iso()
            self.storage.save_us_screener_partial(partial_state)

        failed_batches = [list(batch) for batch in partial_state.get("failed_batches") or [] if batch]
        if failed_batches:
            partial_state["pending_batches"] = [list(b) for b in failed_batches]
        else:
            partial_state["pending_batches"] = []

        if int(stats.get("history_succeeded") or 0) <= 0:
            partial_state["state"] = "partial"
            partial_state["last_error"] = "HistoryTextResult"
            partial_state["updated_at"] = _now_iso()
            self.storage.save_us_screener_partial(partial_state)
            raise RuntimeError(partial_state["last_error"])

        if int(stats.get("history_failed") or 0) > 0:
            self._append_warning_once(warnings, f"Text {stats['history_failed']} TextStockHistoryTextFailed")

        self._sort_strategy_items(strategies)
        combined_items = list(strategies[STRATEGY_MOMENTUM_SPIKE]["items"]) + list(
            strategies[STRATEGY_POST_52W_LOW_REVERSAL]["items"]
        )
        for preset in (strategies[STRATEGY_MOMENTUM_SPIKE].get("presets") or {}).values():
            combined_items.extend(list(preset.get("items") or []))
        self._emit_progress(progress_callback, partial_state, step="enrich_profiles", message="TextStockText")
        self._enrich_company_profiles(combined_items)

        for key, entry in strategies.items():
            entry["matched"] = len(entry.get("items") or [])
            if key == STRATEGY_MOMENTUM_SPIKE:
                for preset in (entry.get("presets") or {}).values():
                    preset["matched"] = len(preset.get("items") or [])

        partial_state["warnings"] = warnings
        partial_state["stats"] = stats
        partial_state["current_batch_size"] = 0
        partial_state["as_of_market_date"] = latest_market_date or datetime.now().strftime("%Y-%m-%d")

        has_pending_failures = len(partial_state.get("pending_batches") or []) > 0
        if has_pending_failures:
            pending_count = len(partial_state["pending_batches"])
            self._append_warning_once(
                warnings,
                f"TextFailed: Text {pending_count} Text",
            )
            if not str(partial_state.get("last_error") or "").strip():
                partial_state["last_error"] = f"Text {pending_count} Text"
        else:
            partial_state["failed_batches"] = []
            partial_state["failed_tickers"] = []
            partial_state["last_error"] = ""

        now_iso = _now_iso()
        partial_state["generated_at"] = now_iso
        partial_state["updated_at"] = now_iso
        partial_state["state"] = "success" if not has_pending_failures else "partial"
        partial_state["success"] = not has_pending_failures
        self.storage.save_us_screener_partial(partial_state)

        final_message = (
            "Text" if not has_pending_failures else f"TextRefresh, Text {len(partial_state['pending_batches'])} Text"
        )

        payload = normalize_us_screener_payload(
            {
                "success": not has_pending_failures,
                "as_of_market_date": partial_state["as_of_market_date"],
                "generated_at": partial_state["generated_at"],
                "stats": stats,
                "warnings": warnings,
                "strategies": {
                    STRATEGY_MOMENTUM_SPIKE: strategies[STRATEGY_MOMENTUM_SPIKE],
                    STRATEGY_POST_52W_LOW_REVERSAL: strategies[STRATEGY_POST_52W_LOW_REVERSAL],
                },
            }
        )
        if not has_pending_failures:
            self.storage.clear_us_screener_partial()
            self._emit_progress(progress_callback, partial_state, step="done", message=final_message)
        else:
            self._emit_progress(progress_callback, partial_state, step="partial", message=final_message)
        return payload

    def _initialize_partial_state(self, filtered_universe: List[Dict[str, Any]], stats: Dict[str, Any], batches: List[List[str]]) -> Dict[str, Any]:
        run_id = str(uuid4())
        now = _now_iso()
        universe_map = {item["ticker"]: item for item in filtered_universe}
        strategies = self._default_strategy_container()
        return {
            "run_id": run_id,
            "state": "running",
            "started_at": now,
            "updated_at": now,
            "success": False,
            "as_of_market_date": "",
            "generated_at": "",
            "stats": dict(stats),
            "warnings": [],
            "strategies": strategies,
            "cache": _default_cache_payload(),
            "completed_batches": 0,
            "total_batches": len(batches),
            "pending_batches": [list(batch) for batch in batches],
            "failed_batches": [],
            "failed_tickers": [],
            "universe": universe_map,
        }

    def _build_batches(self, filtered_universe: List[Dict[str, Any]]) -> List[List[str]]:
        batches: List[List[str]] = []
        for start in range(0, len(filtered_universe), BATCH_SIZE):
            batch = [item["ticker"] for item in filtered_universe[start:start + BATCH_SIZE]]
            if batch:
                batches.append(batch)
        return batches or [[]]

    def _emit_progress(self, progress_callback: Optional[Callable[[Dict[str, Any]], None]], partial_state: Dict[str, Any], *, step: str, message: str) -> None:
        if not progress_callback:
            return
        progress = {
            "total_batches": int(partial_state.get("total_batches") or 0),
            "completed_batches": int(partial_state.get("completed_batches") or 0),
            "current_batch_size": int(partial_state.get("current_batch_size") or 0),
            "pending_batches": len(partial_state.get("pending_batches") or []),
        }
        summary = self._partial_summary(partial_state)
        payload = {
            "state": partial_state.get("state", "running"),
            "step": step,
            "message": message,
            "progress": progress,
            "partial_summary": summary,
        }
        progress_callback(payload)

    def _partial_summary(self, partial_state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": partial_state.get("run_id"),
            "state": partial_state.get("state"),
            "completed_batches": int(partial_state.get("completed_batches") or 0),
            "total_batches": int(partial_state.get("total_batches") or 0),
            "pending_batches": len(partial_state.get("pending_batches") or []),
            "failed_batches": len(partial_state.get("failed_batches") or []),
            "updated_at": partial_state.get("updated_at"),
        }

    def _default_strategy_container(self) -> Dict[str, Dict[str, Any]]:
        base = build_default_us_screener_payload()["strategies"]
        container: Dict[str, Dict[str, Any]] = {}
        for key, value in base.items():
            entry = {
                "filters": dict(value.get("filters") or {}),
                "matched": 0,
                "items": [],
            }
            if key == STRATEGY_MOMENTUM_SPIKE:
                entry["presets"] = _normalize_momentum_presets(value.get("presets"))
            container[key] = entry
        return container

    def _append_warning_once(self, warnings: List[str], text: str) -> None:
        stripped = str(text or "").strip()
        if not stripped:
            return
        if stripped in warnings:
            return
        warnings.append(stripped)

    def _lookup_stock_meta(self, universe_map: Dict[str, Dict[str, Any]], ticker: str) -> Dict[str, Any]:
        fallback = {"ticker": ticker, "stock_name": ticker, "market_cap": 0}
        data = universe_map.get(ticker)
        if not isinstance(data, dict):
            return fallback
        merged = dict(data)
        merged.setdefault("ticker", ticker)
        merged.setdefault("stock_name", ticker)
        merged.setdefault("market_cap", 0)
        return merged

    def _fetch_universe_from_akshare(self) -> List[Dict[str, Any]]:
        last_error: Optional[Exception] = None
        try:
            return self._fetch_universe_from_nasdaq()
        except Exception as exc:
            last_error = exc
            logger.warning("fetch nasdaq universe failed, fallback to akshare: %s", exc)

        import akshare as ak

        for attempt in range(3):
            try:
                df = ak.stock_us_spot_em()
                if df is None or df.empty:
                    raise RuntimeError("Text")
                return df.to_dict("records")
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    self.sleep_fn(1.5 * (attempt + 1))
        raise RuntimeError(f"Text universe Failed: {last_error}") from last_error

    def _fetch_universe_from_nasdaq(self) -> List[Dict[str, Any]]:
        headers = {
            "user-agent": "Mozilla/5.0",
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.nasdaq.com",
            "referer": "https://www.nasdaq.com/market-activity/stocks/screener",
        }
        response = requests.get(
            "https://api.nasdaq.com/api/screener/stocks",
            params={
                "tableonly": "true",
                "limit": "25",
                "offset": "0",
                "download": "true",
            },
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        rows = (((payload or {}).get("data") or {}).get("rows") or [])
        if not rows:
            raise RuntimeError("NASDAQ screener Text")
        return [
            {
                "ticker": row.get("symbol"),
                "stock_name": row.get("name"),
                "market_cap": row.get("marketCap"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
            }
            for row in rows
        ]

    def _fetch_history_with_retry(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        last_error: Optional[Exception] = None
        use_provider_aliases = self.market_data_provider == "alpaca"
        requested_symbols = [
            _to_provider_symbol(ticker) if use_provider_aliases else str(ticker or "").strip().upper()
            for ticker in tickers
            if str(ticker or "").strip()
        ]
        provider_to_internal = {
            (
                _to_provider_symbol(ticker).upper()
                if use_provider_aliases
                else str(ticker or "").strip().upper()
            ): str(ticker or "").strip().upper()
            for ticker in tickers
            if str(ticker or "").strip()
        }
        for attempt in range(len(BATCH_BACKOFF_SECONDS) + 1):
            try:
                history_map = self.history_fetcher(requested_symbols)
                if history_map:
                    normalized_map: Dict[str, pd.DataFrame] = {}
                    for provider_symbol, frame in history_map.items():
                        internal_symbol = provider_to_internal.get(
                            str(provider_symbol or "").strip().upper(),
                            _from_provider_symbol(str(provider_symbol or "")),
                        )
                        if internal_symbol:
                            normalized_map[internal_symbol] = frame
                    return normalized_map
                if self.market_data_provider == "yfinance" and self._is_yfinance_globally_rate_limited():
                    raise RuntimeError("Yahoo Finance CurrentText, HistoryText")
                return {}
            except Exception as exc:
                last_error = exc
                if attempt < len(BATCH_BACKOFF_SECONDS):
                    self.sleep_fn(BATCH_BACKOFF_SECONDS[attempt])
                    continue
        logger.warning("history batch failed after retries: %s", last_error)
        return {}

    def _fetch_history_resilient(self, tickers: List[str]) -> Tuple[Dict[str, pd.DataFrame], bool]:
        """
        TextHistory, TextResult, TextAutoText. 
        Text (history_map, used_fallback). 
        """
        primary_result = self._fetch_history_with_retry(tickers)
        requested = [ticker for ticker in tickers if ticker]
        missing_tickers = [ticker for ticker in requested if ticker not in primary_result]
        if primary_result and not missing_tickers:
            return primary_result, False

        fallback: Dict[str, pd.DataFrame] = dict(primary_result or {})
        fallback_targets = missing_tickers if missing_tickers else requested
        worker_count = min(HISTORY_FALLBACK_WORKERS, max(1, len(fallback_targets)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._fetch_history_with_retry, [ticker]): ticker
                for ticker in fallback_targets
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    single_result = future.result() or {}
                except Exception as exc:
                    logger.warning("history fallback failed for %s: %s", ticker, exc)
                    continue
                history = single_result.get(ticker)
                if history is not None and not history.empty:
                    fallback[ticker] = history
        return fallback, True

    def _fetch_history_from_yfinance(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        if not tickers:
            return {}
        frame = yf.download(
            tickers=tickers,
            period=HISTORY_PERIOD,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=YFINANCE_DOWNLOAD_THREADS,
            group_by="ticker",
            timeout=YFINANCE_DOWNLOAD_TIMEOUT_SECONDS,
        )
        result: Dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            history = self._extract_history_frame(frame, ticker)
            if history is not None and not history.empty:
                result[ticker] = history
        return result

    def _is_yfinance_globally_rate_limited(self) -> bool:
        try:
            frame = yf.download(
                tickers=["AAPL"],
                period="1mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="ticker",
                timeout=YFINANCE_DOWNLOAD_TIMEOUT_SECONDS,
            )
            history = self._extract_history_frame(frame, "AAPL")
            return history is None or history.empty
        except Exception:
            return True

    def _extract_history_frame(self, frame: pd.DataFrame, ticker: str) -> Optional[pd.DataFrame]:
        if frame is None or frame.empty:
            return None
        try:
            if isinstance(frame.columns, pd.MultiIndex):
                level0 = frame.columns.get_level_values(0)
                level1 = frame.columns.get_level_values(1)
                if ticker in level0:
                    sub = frame[ticker].copy()
                elif ticker in level1:
                    sub = frame.xs(ticker, axis=1, level=1).copy()
                else:
                    return None
            else:
                sub = frame.copy()
            sub = sub.dropna(how="all")
            if sub.empty:
                return None
            sub.index = pd.to_datetime(sub.index)
            columns = {str(col).strip().lower(): col for col in sub.columns}
            close_col = columns.get("close")
            if close_col is None:
                return None
            cleaned = pd.DataFrame(index=sub.index)
            for source_name, target_name in (("open", "Open"), ("high", "High"), ("low", "Low"), ("volume", "Volume")):
                source_col = columns.get(source_name)
                if source_col is not None:
                    cleaned[target_name] = pd.to_numeric(sub[source_col], errors="coerce")
            cleaned["Close"] = pd.to_numeric(sub[close_col], errors="coerce")
            cleaned = cleaned.dropna(subset=["Close"])
            return cleaned.sort_index()
        except Exception:
            logger.exception("failed to extract history frame for %s", ticker)
            return None

    def _build_momentum_spike_item(self, history: pd.DataFrame, stock_meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metrics = self._compute_momentum_spike_metrics(history)
        if not metrics:
            return None
        if metrics["max_daily_gain_5d_pct"] <= 10.0:
            return None
        return {
            "ticker": stock_meta["ticker"],
            "stock_name": stock_meta["stock_name"],
            "market_cap": stock_meta["market_cap"],
            "latest_close": metrics["latest_close"],
            "latest_trade_date": metrics["latest_trade_date"],
            "trigger_trade_date": metrics["trigger_trade_date"],
            "max_daily_gain_5d_pct": metrics["max_daily_gain_5d_pct"],
            "gain_20d_pct": metrics["gain_20d_pct"],
            "gain_30d_pct": metrics["gain_30d_pct"],
            "avg_volume_5d": metrics["avg_volume_5d"],
            "avg_volume_3m": metrics["avg_volume_3m"],
            "avg_volume_5d_vs_3m": metrics["avg_volume_5d_vs_3m"],
            "ma200": metrics["ma200"],
            "distance_above_200ma_pct": metrics["distance_above_200ma_pct"],
            "sector": str(stock_meta.get("sector") or "").strip(),
            "industry": str(stock_meta.get("industry") or "").strip(),
            "company_intro": "",
        }

    def _build_post_52w_low_reversal_item(self, history: pd.DataFrame, stock_meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metrics = self._compute_post_52w_low_reversal_metrics(history)
        if not metrics:
            return None
        return {
            "ticker": stock_meta["ticker"],
            "stock_name": stock_meta["stock_name"],
            "market_cap": stock_meta["market_cap"],
            "latest_trade_date": metrics["latest_trade_date"],
            "latest_close": metrics["latest_close"],
            "new_low_trade_date": metrics["new_low_trade_date"],
            "new_low_close": metrics["new_low_close"],
            "ma50": metrics["ma50"],
            "ma100": metrics["ma100"],
            "ma200": metrics["ma200"],
            "distance_above_200ma_pct": metrics["distance_above_200ma_pct"],
            "rebound_from_new_low_pct": metrics["rebound_from_new_low_pct"],
            "days_since_new_low": metrics["days_since_new_low"],
            "sector": str(stock_meta.get("sector") or "").strip(),
            "industry": str(stock_meta.get("industry") or "").strip(),
            "company_intro": "",
        }

    def _compute_momentum_spike_metrics(self, history: pd.DataFrame) -> Optional[Dict[str, Any]]:
        close = pd.to_numeric(history["Close"], errors="coerce").dropna()
        if len(close) < 6:
            return None
        volume = pd.to_numeric(history.get("Volume"), errors="coerce") if "Volume" in history.columns else pd.Series(dtype="float64")
        latest_date = pd.Timestamp(close.index[-1]).normalize()
        recent_changes = close.pct_change().dropna().tail(5)
        if len(recent_changes) < 5:
            return None
        max_gain = float(recent_changes.max() * 100)
        trigger_date = pd.Timestamp(recent_changes.idxmax()).normalize().strftime("%Y-%m-%d")
        base_cutoff = latest_date - timedelta(days=30)
        base_close = close[close.index <= base_cutoff]
        if base_close.empty:
            return None
        base_price = float(base_close.iloc[-1])
        latest_close = float(close.iloc[-1])
        gain_30 = ((latest_close / base_price) - 1.0) * 100 if base_price else 0.0
        gain_20 = None
        if len(close) >= 21:
            base_20 = float(close.iloc[-21])
            gain_20 = ((latest_close / base_20) - 1.0) * 100 if base_20 else 0.0
        latest_ma200 = None
        distance_above_200ma_pct = None
        if len(close) >= 200:
            ma200_series = close.rolling(200, min_periods=200).mean()
            ma200_value = ma200_series.iloc[-1]
            if pd.notna(ma200_value):
                latest_ma200 = float(ma200_value)
                distance_above_200ma_pct = ((latest_close / latest_ma200) - 1.0) * 100 if latest_ma200 else 0.0
        avg_volume_5d = None
        avg_volume_3m = None
        avg_volume_5d_vs_3m = None
        if not volume.empty:
            volume = volume.dropna()
            if len(volume) >= 63:
                avg_volume_5d = _safe_float(volume.tail(5).mean())
                avg_volume_3m = _safe_float(volume.tail(63).mean())
                if avg_volume_5d is not None and avg_volume_3m not in (None, 0):
                    avg_volume_5d_vs_3m = avg_volume_5d / avg_volume_3m
        return {
            "latest_trade_date": latest_date.strftime("%Y-%m-%d"),
            "latest_close": round(latest_close, 2),
            "trigger_trade_date": trigger_date,
            "max_daily_gain_5d_pct": round(max_gain, 2),
            "gain_20d_pct": round(gain_20, 2) if gain_20 is not None else None,
            "gain_30d_pct": round(gain_30, 2),
            "avg_volume_5d": round(avg_volume_5d, 2) if avg_volume_5d is not None else None,
            "avg_volume_3m": round(avg_volume_3m, 2) if avg_volume_3m is not None else None,
            "avg_volume_5d_vs_3m": round(avg_volume_5d_vs_3m, 2) if avg_volume_5d_vs_3m is not None else None,
            "ma200": round(latest_ma200, 2) if latest_ma200 is not None else None,
            "distance_above_200ma_pct": round(distance_above_200ma_pct, 2) if distance_above_200ma_pct is not None else None,
        }

    def _compute_post_52w_low_reversal_metrics(self, history: pd.DataFrame) -> Optional[Dict[str, Any]]:
        close = pd.to_numeric(history["Close"], errors="coerce").dropna()
        if len(close) < REVERSAL_LOOKBACK_TRADING_DAYS:
            return None

        ma50 = close.rolling(50, min_periods=50).mean()
        ma100 = close.rolling(100, min_periods=100).mean()
        ma200 = close.rolling(200, min_periods=200).mean()
        rolling_252_min = close.rolling(REVERSAL_LOOKBACK_TRADING_DAYS, min_periods=REVERSAL_LOOKBACK_TRADING_DAYS).min()

        latest_ts = pd.Timestamp(close.index[-1]).normalize()
        latest_close = float(close.iloc[-1])
        latest_ma50 = _safe_float(ma50.iloc[-1])
        latest_ma100 = _safe_float(ma100.iloc[-1])
        latest_ma200 = _safe_float(ma200.iloc[-1])
        if latest_ma50 is None or latest_ma100 is None or latest_ma200 is None:
            return None
        if not (latest_close > latest_ma50 > latest_ma100 > latest_ma200):
            return None

        distance_above_200ma_pct = ((latest_close / latest_ma200) - 1.0) * 100 if latest_ma200 else 0.0
        if distance_above_200ma_pct < REVERSAL_DISTANCE_ABOVE_200MA_PCT:
            return None

        recent_flags = close.eq(rolling_252_min).tail(REVERSAL_RECENT_TRADING_DAYS)
        matched_dates = recent_flags[recent_flags].index
        if len(matched_dates) <= 0:
            return None

        new_low_match_ts = pd.Timestamp(matched_dates[-1])
        new_low_ts = new_low_match_ts.normalize()
        new_low_close = float(close.loc[new_low_match_ts])
        rebound_from_new_low_pct = ((latest_close / new_low_close) - 1.0) * 100 if new_low_close else 0.0

        return {
            "latest_trade_date": latest_ts.strftime("%Y-%m-%d"),
            "latest_close": round(latest_close, 2),
            "new_low_trade_date": new_low_ts.strftime("%Y-%m-%d"),
            "new_low_close": round(new_low_close, 2),
            "ma50": round(latest_ma50, 2),
            "ma100": round(latest_ma100, 2),
            "ma200": round(latest_ma200, 2),
            "distance_above_200ma_pct": round(distance_above_200ma_pct, 2),
            "rebound_from_new_low_pct": round(rebound_from_new_low_pct, 2),
            "days_since_new_low": max(0, int((latest_ts - new_low_ts).days)),
        }

    def _assign_momentum_item(self, momentum_strategy: Dict[str, Any], item: Dict[str, Any]) -> None:
        presets = momentum_strategy.get("presets") or {}
        if self._matches_momentum_preset(MOMENTUM_PRESET_CLASSIC, item):
            presets[MOMENTUM_PRESET_CLASSIC]["items"].append(item)
        if self._matches_momentum_preset(MOMENTUM_PRESET_GAIN_20D_BREAKOUT, item):
            presets[MOMENTUM_PRESET_GAIN_20D_BREAKOUT]["items"].append(item)
        if self._matches_momentum_preset(MOMENTUM_PRESET_MA200_EXTENSION, item):
            presets[MOMENTUM_PRESET_MA200_EXTENSION]["items"].append(item)
        momentum_strategy["items"] = list(presets.get(MOMENTUM_PRESET_CLASSIC, {}).get("items") or [])

    def _matches_momentum_preset(self, preset_id: str, item: Dict[str, Any]) -> bool:
        if float(item.get("max_daily_gain_5d_pct") or 0.0) <= 10.0:
            return False
        if preset_id == MOMENTUM_PRESET_CLASSIC:
            ratio = item.get("avg_volume_5d_vs_3m")
            return ratio is not None and float(ratio) >= 2.0
        if preset_id == MOMENTUM_PRESET_GAIN_20D_BREAKOUT:
            gain_20d = item.get("gain_20d_pct")
            return gain_20d is not None and float(gain_20d) > 30.0
        if preset_id == MOMENTUM_PRESET_MA200_EXTENSION:
            above_200 = item.get("distance_above_200ma_pct")
            return above_200 is not None and float(above_200) > 50.0
        return False

    def _sort_strategy_items(self, strategy_items: Dict[str, Dict[str, Any]]) -> None:
        momentum_items = strategy_items[STRATEGY_MOMENTUM_SPIKE]["items"]
        momentum_items.sort(
            key=lambda item: (
                -float(item.get("max_daily_gain_5d_pct") or 0.0),
                -float(item.get("market_cap") or 0.0),
                str(item.get("ticker") or ""),
            )
        )
        for preset in (strategy_items[STRATEGY_MOMENTUM_SPIKE].get("presets") or {}).values():
            preset["items"] = sorted(
                list(preset.get("items") or []),
                key=lambda item: (
                    -float(item.get("avg_volume_5d_vs_3m") or 0.0),
                    -float(item.get("max_daily_gain_5d_pct") or 0.0),
                    -float(item.get("gain_20d_pct") or 0.0),
                    -float(item.get("distance_above_200ma_pct") or 0.0),
                    -float(item.get("market_cap") or 0.0),
                    str(item.get("ticker") or ""),
                ),
            )
        strategy_items[STRATEGY_MOMENTUM_SPIKE]["items"] = list(
            (strategy_items[STRATEGY_MOMENTUM_SPIKE].get("presets") or {})
            .get(MOMENTUM_PRESET_CLASSIC, {})
            .get("items")
            or []
        )
        strategy_items[STRATEGY_POST_52W_LOW_REVERSAL]["items"].sort(
            key=lambda item: (
                -float(item.get("distance_above_200ma_pct") or 0.0),
                -float(item.get("rebound_from_new_low_pct") or 0.0),
                -float(item.get("market_cap") or 0.0),
                str(item.get("ticker") or ""),
            )
        )

    def _fetch_profile_from_yfinance(self, ticker: str) -> Dict[str, Any]:
        info = yf.Ticker(ticker).get_info()
        return {
            "source": "yfinance",
            "sector": str(info.get("sector") or "").strip(),
            "industry": str(info.get("industry") or "").strip(),
            "summary": str(info.get("longBusinessSummary") or "").strip(),
        }

    def _enrich_company_profiles(self, items: List[Dict[str, Any]]) -> None:
        cache = self.storage.get_us_screener_company_profiles()
        cache_updated = False
        now_ts = time.time()
        items_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
        profiles_by_ticker: Dict[str, Dict[str, Any]] = {}
        missing_tickers: List[str] = []

        for item in items:
            ticker = str(item.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            items_by_ticker.setdefault(ticker, []).append(item)
            cached = cache.get(ticker) if isinstance(cache, dict) else None
            profile = None
            if isinstance(cached, dict):
                fetched_at = str(cached.get("fetched_at") or "").strip()
                if fetched_at:
                    try:
                        fetched_ts = datetime.fromisoformat(fetched_at).timestamp()
                    except ValueError:
                        fetched_ts = 0.0
                    if now_ts - fetched_ts < PROFILE_CACHE_TTL_SECONDS:
                        profile = cached
            if profile is None:
                if ticker not in missing_tickers:
                    missing_tickers.append(ticker)
                continue
            profiles_by_ticker[ticker] = profile

        def fetch_profile(ticker: str) -> Tuple[str, Dict[str, Any], bool]:
            cached = cache.get(ticker) if isinstance(cache, dict) else None
            try:
                fetched = self.profile_fetcher(ticker) or {}
                profile = {
                    "fetched_at": _now_iso(),
                    "source": str(fetched.get("source") or "").strip(),
                    "sector": str(fetched.get("sector") or "").strip(),
                    "industry": str(fetched.get("industry") or "").strip(),
                    "summary": str(fetched.get("summary") or "").strip(),
                }
                profile["company_intro"] = _build_company_intro(
                    profile["sector"],
                    profile["industry"],
                    profile["summary"],
                )
                return ticker, _merge_company_profile(cached, profile), True
            except Exception as exc:
                logger.warning("fetch profile failed for %s: %s", ticker, exc)
                return ticker, cached if isinstance(cached, dict) else {}, False

        if missing_tickers:
            worker_count = min(PROFILE_FETCH_WORKERS, max(1, len(missing_tickers)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {executor.submit(fetch_profile, ticker): ticker for ticker in missing_tickers}
                for future in as_completed(futures):
                    ticker, profile, updated = future.result()
                    profiles_by_ticker[ticker] = profile
                    if updated:
                        cache[ticker] = profile
                        cache_updated = True

        for ticker, grouped_items in items_by_ticker.items():
            profile = profiles_by_ticker.get(ticker) or {}
            for item in grouped_items:
                item["sector"] = str((profile or {}).get("sector") or item.get("sector") or "").strip()
                item["industry"] = str((profile or {}).get("industry") or item.get("industry") or "").strip()
                item["company_intro"] = str((profile or {}).get("company_intro") or item.get("company_intro") or "").strip()

        if cache_updated:
            self.storage.save_us_screener_company_profiles(cache)
