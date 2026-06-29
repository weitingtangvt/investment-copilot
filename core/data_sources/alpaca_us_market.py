from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import requests


def _normalize_base_url(value: str, default: str) -> str:
    text = str(value or "").strip()
    return text.rstrip("/") if text else default.rstrip("/")


class _EndpointRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        *,
        sleep_fn: Callable[[float], None],
        monotonic_fn: Callable[[], float],
    ) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds or 0.0))
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self._lock = threading.Lock()
        self._last_request_at: Optional[float] = None

    def wait_turn(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = self.monotonic_fn()
            if self._last_request_at is None:
                self._last_request_at = now
                return
            remaining = self.min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleep_fn(remaining)
                now = self.monotonic_fn()
            self._last_request_at = now

    def mark_after_backoff(self) -> None:
        with self._lock:
            self._last_request_at = self.monotonic_fn()


class AlpacaUSMarketDataClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        trading_base_url: str = "https://paper-api.alpaca.markets/v2",
        market_data_base_url: str = "https://data.alpaca.markets/v2",
        stock_feed: str = "iex",
        request_timeout: int = 20,
        session: Optional[requests.sessions.Session] = None,
        min_market_data_interval_seconds: float = 0.35,
        min_trading_interval_seconds: float = 0.5,
        max_rate_limit_retries: int = 3,
        max_rate_limit_sleep_seconds: float = 30.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.api_secret = str(api_secret or "").strip()
        self.trading_base_url = _normalize_base_url(
            trading_base_url,
            "https://paper-api.alpaca.markets/v2",
        )
        self.market_data_base_url = _normalize_base_url(
            market_data_base_url,
            "https://data.alpaca.markets/v2",
        )
        self.stock_feed = str(stock_feed or "iex").strip() or "iex"
        self.request_timeout = int(request_timeout or 20)
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.time_fn = time_fn
        self.max_rate_limit_retries = max(0, int(max_rate_limit_retries or 0))
        self.max_rate_limit_sleep_seconds = max(0.0, float(max_rate_limit_sleep_seconds or 0.0))
        self._limiters = {
            "market_data": _EndpointRateLimiter(
                min_market_data_interval_seconds,
                sleep_fn=self.sleep_fn,
                monotonic_fn=self.monotonic_fn,
            ),
            "trading": _EndpointRateLimiter(
                min_trading_interval_seconds,
                sleep_fn=self.sleep_fn,
                monotonic_fn=self.monotonic_fn,
            ),
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json",
        }

    def fetch_daily_history(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=600)
        return self.fetch_daily_history_window(tickers, start_at=start_at.date(), end_at=end_at.date())

    def fetch_daily_history_window(
        self,
        tickers: List[str],
        *,
        start_at,
        end_at,
    ) -> Dict[str, pd.DataFrame]:
        normalized = [str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()]
        if not normalized:
            return {}
        if not isinstance(start_at, datetime):
            start_at = datetime.combine(start_at, datetime.min.time(), tzinfo=timezone.utc)
        elif start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        if not isinstance(end_at, datetime):
            end_at = datetime.combine(end_at, datetime.max.time(), tzinfo=timezone.utc)
        elif end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        params: Dict[str, Any] = {
            "symbols": ",".join(dict.fromkeys(normalized)),
            "timeframe": "1Day",
            "start": start_at.isoformat().replace("+00:00", "Z"),
            "end": end_at.isoformat().replace("+00:00", "Z"),
            "adjustment": "all",
            "feed": self.stock_feed,
            "limit": 10000,
        }
        rows_by_symbol: Dict[str, List[Dict[str, Any]]] = {}

        while True:
            payload = self._get_json(
                f"{self.market_data_base_url}/stocks/bars",
                endpoint_group="market_data",
                params=params,
            )
            for symbol, bars in (payload.get("bars") or {}).items():
                clean_symbol = str(symbol or "").strip().upper()
                if clean_symbol:
                    rows_by_symbol.setdefault(clean_symbol, []).extend(list(bars or []))
            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break
            params["page_token"] = next_page_token

        result: Dict[str, pd.DataFrame] = {}
        for symbol, bars in rows_by_symbol.items():
            frame = self._bars_to_frame(bars)
            if frame is not None and not frame.empty:
                result[symbol] = frame
        return result

    def fetch_company_profile(self, ticker: str) -> Dict[str, Any]:
        symbol = str(ticker or "").strip().upper()
        if not symbol:
            return {}
        payload = self._get_json(
            f"{self.trading_base_url}/assets/{symbol}",
            endpoint_group="trading",
        )
        name = str(payload.get("name") or symbol).strip()
        exchange = str(payload.get("exchange") or "").strip()
        summary = name
        if exchange:
            summary = f"{name}, Text {exchange} Text. "
        return {
            "source": "alpaca",
            "sector": "",
            "industry": "",
            "summary": summary,
        }

    def _get_json(
        self,
        url: str,
        *,
        endpoint_group: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        limiter = self._limiters[endpoint_group]
        rate_limit_attempts = 0
        while True:
            limiter.wait_turn()
            response = self.session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.request_timeout,
            )
            if int(getattr(response, "status_code", 200) or 200) != 429:
                response.raise_for_status()
                return response.json() or {}
            rate_limit_attempts += 1
            if rate_limit_attempts > self.max_rate_limit_retries:
                raise RuntimeError(f"Alpaca rate limit exceeded for {endpoint_group}")
            backoff_seconds = min(
                self._compute_retry_delay_seconds(response),
                self.max_rate_limit_sleep_seconds,
            )
            if backoff_seconds > 0:
                self.sleep_fn(backoff_seconds)
            limiter.mark_after_backoff()

    def _compute_retry_delay_seconds(self, response: Any) -> float:
        headers = getattr(response, "headers", {}) or {}
        raw_reset = str(headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset") or "").strip()
        if not raw_reset:
            return 1.0
        try:
            reset_at = float(raw_reset)
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, reset_at - float(self.time_fn()))

    def _bars_to_frame(self, bars: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
        records = []
        for bar in bars or []:
            open_ = pd.to_numeric(bar.get("o"), errors="coerce")
            high = pd.to_numeric(bar.get("h"), errors="coerce")
            low = pd.to_numeric(bar.get("l"), errors="coerce")
            close = pd.to_numeric(bar.get("c"), errors="coerce")
            volume = pd.to_numeric(bar.get("v"), errors="coerce")
            timestamp = pd.to_datetime(bar.get("t"), errors="coerce", utc=True)
            if pd.isna(close) or pd.isna(timestamp):
                continue
            records.append(
                {
                    "timestamp": timestamp.tz_convert(None),
                    "Open": None if pd.isna(open_) else float(open_),
                    "High": None if pd.isna(high) else float(high),
                    "Low": None if pd.isna(low) else float(low),
                    "Close": float(close),
                    "Volume": None if pd.isna(volume) else float(volume),
                }
            )
        if not records:
            return None
        frame = pd.DataFrame.from_records(records).set_index("timestamp").sort_index()
        frame.index = pd.DatetimeIndex(frame.index)
        return frame
