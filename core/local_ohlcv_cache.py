from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_ticker(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _visible_window_start(range_name: str, end_dt: datetime) -> Optional[datetime]:
    value = str(range_name or "365d").strip().lower() or "365d"
    if value in {"365d", "1y", "12m"}:
        return end_dt - timedelta(days=365)
    if value == "6m":
        return end_dt - timedelta(days=183)
    if value == "3m":
        return end_dt - timedelta(days=92)
    if value == "18m":
        return end_dt - timedelta(days=548)
    if value == "ytd":
        return datetime(end_dt.year, 1, 1)
    return None


def _to_naive_timestamp(value: Any) -> Optional[pd.Timestamp]:
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts.tz_convert(None)


@dataclass
class SQLiteOHLCVCacheConfig:
    db_path: Path
    lookback_days: int = 600
    refresh_grace_days: int = 3
    overlap_days: int = 7


class SQLiteOHLCVCache:
    def __init__(self, config: SQLiteOHLCVCacheConfig, *, logger_: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.logger = logger_ or logger
        self._lock = threading.Lock()
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.config.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_ohlcv (
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (ticker, trade_date)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_ohlcv_ticker_date
                ON daily_ohlcv (ticker, trade_date)
                """
            )

    def _coverage(self, tickers: Iterable[str]) -> Dict[str, Dict[str, Optional[str]]]:
        normalized = [ticker for ticker in (_normalize_ticker(t) for t in tickers) if ticker]
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, MIN(trade_date) AS min_date, MAX(trade_date) AS max_date, COUNT(*) AS row_count
                FROM daily_ohlcv
                WHERE ticker IN ({placeholders})
                GROUP BY ticker
                """,
                normalized,
            ).fetchall()
        return {
            str(row["ticker"]).upper(): {
                "min_date": row["min_date"],
                "max_date": row["max_date"],
                "row_count": int(row["row_count"] or 0),
            }
            for row in rows
        }

    def upsert_history_frames(self, history_map: Dict[str, pd.DataFrame], *, source: str) -> None:
        if not history_map:
            return
        updated_at = _utc_now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            for ticker, frame in history_map.items():
                symbol = _normalize_ticker(ticker)
                if not symbol or frame is None or frame.empty:
                    continue
                clean = frame.copy()
                clean.index = pd.to_datetime(clean.index)
                rows = []
                for ts, row in clean.sort_index().iterrows():
                    date_text = pd.Timestamp(ts).normalize().strftime("%Y-%m-%d")
                    rows.append(
                        (
                            symbol,
                            date_text,
                            self._safe_number(row.get("Open")),
                            self._safe_number(row.get("High")),
                            self._safe_number(row.get("Low")),
                            self._safe_number(row.get("Close")),
                            self._safe_number(row.get("Volume")),
                            source,
                            updated_at,
                        )
                    )
                conn.executemany(
                    """
                    INSERT INTO daily_ohlcv
                        (ticker, trade_date, open, high, low, close, volume, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, trade_date) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    rows,
                )

    def fetch_history_frames(
        self,
        tickers: List[str],
        *,
        history_loader,
        source: str,
    ) -> Dict[str, pd.DataFrame]:
        normalized = [ticker for ticker in (_normalize_ticker(t) for t in tickers) if ticker]
        if not normalized:
            return {}
        today = _utc_now().date()
        full_start = today - timedelta(days=self.config.lookback_days)
        fresh_cutoff = today - timedelta(days=self.config.refresh_grace_days)
        coverage = self._coverage(normalized)
        missing: List[str] = []
        stale: List[str] = []
        stale_start_dates: List[datetime.date] = []

        for ticker in normalized:
            entry = coverage.get(ticker) or {}
            min_date = self._parse_date(entry.get("min_date"))
            max_date = self._parse_date(entry.get("max_date"))
            if min_date is None or max_date is None:
                missing.append(ticker)
                continue
            if min_date > full_start + timedelta(days=5):
                missing.append(ticker)
                continue
            if max_date < fresh_cutoff:
                stale.append(ticker)
                stale_start_dates.append(max_date - timedelta(days=self.config.overlap_days))

        if missing:
            self.logger.info("ohlcv cache cold fetch: %s tickers", len(missing))
            fetched = history_loader(missing, start_date=full_start, end_date=today)
            self.upsert_history_frames(fetched, source=source)

        if stale:
            stale_start = min(stale_start_dates) if stale_start_dates else full_start
            self.logger.info("ohlcv cache delta refresh: %s tickers from %s", len(stale), stale_start.isoformat())
            fetched = history_loader(stale, start_date=stale_start, end_date=today)
            self.upsert_history_frames(fetched, source=source)

        return self.load_history_frames(normalized, start_date=full_start)

    def load_history_frames(self, tickers: List[str], *, start_date) -> Dict[str, pd.DataFrame]:
        normalized = [ticker for ticker in (_normalize_ticker(t) for t in tickers) if ticker]
        if not normalized:
            return {}
        start_text = pd.Timestamp(start_date).normalize().strftime("%Y-%m-%d")
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, trade_date, open, high, low, close, volume
                FROM daily_ohlcv
                WHERE ticker IN ({placeholders}) AND trade_date >= ?
                ORDER BY ticker, trade_date
                """,
                [*normalized, start_text],
            ).fetchall()
        grouped: Dict[str, List[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["ticker"]).upper(), []).append(dict(row))

        result: Dict[str, pd.DataFrame] = {}
        for ticker, entries in grouped.items():
            frame = pd.DataFrame.from_records(entries)
            if frame.empty:
                continue
            frame["timestamp"] = pd.to_datetime(frame["trade_date"], errors="coerce")
            frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
            frame = frame.rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            result[ticker] = frame[["Open", "High", "Low", "Close", "Volume"]]
        return result

    def get_price_chart_series(self, ticker: str, *, history_loader, source: str, range_name: str = "365d") -> Dict[str, Any]:
        symbol = _normalize_ticker(ticker)
        result = {
            "success": False,
            "ticker": symbol,
            "range": range_name,
            "as_of_date": "",
            "series": {"candles": [], "ma50": [], "ma100": [], "ma200": []},
            "meta": {
                "latest_close": None,
                "change_1y_pct": None,
                "change_range_pct": None,
                "has_enough_history_for_ma200": False,
                "provider": source,
            },
            "error": "",
        }
        if not symbol:
            result["error"] = "TextSettingsStockTicker"
            return result
        end_dt = _utc_now().replace(tzinfo=None)
        range_start = _visible_window_start(str(range_name or "365d"), end_dt)
        if range_start is None:
            result["error"] = f"Text: {range_name}"
            return result
        history_map = self.fetch_history_frames([symbol], history_loader=history_loader, source=source)
        history = history_map.get(symbol)
        if history is None or history.empty:
            result["error"] = f"{symbol} No dataText"
            return result

        data = history.copy().dropna(subset=["Open", "High", "Low", "Close"])
        if data.empty:
            result["error"] = f"{symbol} Text"
            return result

        visible = data[data.index >= range_start]
        if visible.empty:
            visible = data.tail(min(len(data), 260)).copy()

        close_indexed = pd.Series(data["Close"].values, index=data.index.strftime("%Y-%m-%d"))
        ma50_map = {item["date"]: item["value"] for item in _calc_ma_series(close_indexed, 50)}
        ma100_map = {item["date"]: item["value"] for item in _calc_ma_series(close_indexed, 100)}
        ma200_map = {item["date"]: item["value"] for item in _calc_ma_series(close_indexed, 200)}

        candles: List[Dict[str, Any]] = []
        ma50: List[Dict[str, Any]] = []
        ma100: List[Dict[str, Any]] = []
        ma200: List[Dict[str, Any]] = []
        for ts, row in visible.iterrows():
            date_text = pd.Timestamp(ts).strftime("%Y-%m-%d")
            candles.append(
                {
                    "date": date_text,
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "volume": self._safe_number(row["Volume"]),
                }
            )
            ma50.append({"date": date_text, "value": ma50_map.get(date_text)})
            ma100.append({"date": date_text, "value": ma100_map.get(date_text)})
            ma200.append({"date": date_text, "value": ma200_map.get(date_text)})

        latest_close = float(visible.iloc[-1]["Close"])
        first_visible_close = float(visible.iloc[0]["Close"]) if len(visible) else latest_close
        change_range_pct = round((latest_close / first_visible_close - 1) * 100, 2) if first_visible_close > 0 else None
        one_year_visible = data[data.index >= (end_dt - timedelta(days=365))]
        first_1y_close = (
            float(one_year_visible.iloc[0]["Close"])
            if not one_year_visible.empty
            else first_visible_close
        )
        change_1y_pct = round((latest_close / first_1y_close - 1) * 100, 2) if first_1y_close > 0 else None

        result["success"] = True
        result["as_of_date"] = pd.Timestamp(visible.index[-1]).strftime("%Y-%m-%d")
        result["series"] = {"candles": candles, "ma50": ma50, "ma100": ma100, "ma200": ma200}
        result["meta"] = {
            "latest_close": round(latest_close, 4),
            "change_1y_pct": change_1y_pct,
            "change_range_pct": change_range_pct,
            "has_enough_history_for_ma200": len(data) >= 200,
            "provider": source,
        }
        return result

    @staticmethod
    def _safe_number(value: Any) -> Optional[float]:
        try:
            if value is None or pd.isna(value):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[datetime.date]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date()
        except ValueError:
            try:
                return datetime.strptime(str(value), "%Y-%m-%d").date()
            except ValueError:
                return None


def _calc_ma_series(close_indexed: pd.Series, window: int) -> List[Dict[str, Any]]:
    series = pd.to_numeric(close_indexed, errors="coerce").dropna()
    if series.empty:
        return []
    rolling = series.rolling(window, min_periods=window).mean()
    output: List[Dict[str, Any]] = []
    for idx, value in rolling.items():
        output.append(
            {
                "date": str(idx),
                "value": None if pd.isna(value) else round(float(value), 4),
            }
        )
    return output
