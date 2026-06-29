from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf


@dataclass
class WatchlistPerformanceDeps:
    logger: Any
    get_storage: Callable[[], Any]
    thresholds: Dict[str, float]
    build_revisit_rule: Callable[[str, str, Optional[float], float], Optional[Dict[str, Any]]]
    to_change_pct: Callable[[Any], Optional[float]]
    get_portfolio_and_weekly: Optional[Callable[..., Dict[str, Any]]]
    get_weekly_performance: Optional[Callable[..., Dict[str, Any]]]
    get_portfolio_returns: Optional[Callable[..., Dict[str, Any]]]


class WatchlistPerformanceRefresher:
    def __init__(self, deps: WatchlistPerformanceDeps) -> None:
        self._deps = deps

    def normalize_watch_candidate_ticker(self, candidate: Dict[str, Any]) -> str:
        raw = str(candidate.get("ticker") or candidate.get("stock_id") or "").strip().upper()
        if not raw:
            return ""
        if raw.endswith((".HK", ".SH", ".SZ", ".SS", ".BJ", ".T", ".KS", ".KQ")):
            return raw
        if raw.startswith(("SH", "SZ", "BJ")) and len(raw) > 2 and raw[2:].isdigit():
            code = raw[2:]
            suffix = raw[:2]
            return f"{code}.{suffix}"
        if raw.startswith(("CN", "CH")) and len(raw) > 2 and raw[2:].isdigit():
            code = raw[2:]
            prefix = "SH" if code.startswith(("6", "9")) else "SZ"
            return f"{code}.{prefix}"
        if len(raw) == 6 and raw.isdigit():
            suffix = "SH" if raw.startswith(("5", "6", "7", "9")) else "SZ"
            return f"{raw}.{suffix}"
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 6:
            suffix = "SH" if digits.startswith(("5", "6", "7", "9")) else "SZ"
            return f"{digits}.{suffix}"
        return raw

    def build_watch_candidate_revisit_state(
        self,
        candidate: Dict[str, Any],
        performance_data: Optional[Dict[str, Any]],
        portfolio_returns: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        weekly_change_pct = self._deps.to_change_pct((performance_data or {}).get("change_pct"))
        monthly_change_pct = self._deps.to_change_pct((portfolio_returns or {}).get("return_1m"))
        since_added_change_pct = self._deps.to_change_pct((portfolio_returns or {}).get("return_since_buy"))
        current_price = self._deps.to_change_pct((portfolio_returns or {}).get("current_price"))
        baseline_price = self._deps.to_change_pct((portfolio_returns or {}).get("buy_date_price"))

        thresholds = dict(self._deps.thresholds)
        active_rules = []
        for window, label, change_pct in (
            ("weekly", "1Text", weekly_change_pct),
            ("monthly", "1Text", monthly_change_pct),
            ("since_added", "Text", since_added_change_pct),
        ):
            rule = self._deps.build_revisit_rule(window, label, change_pct, thresholds[window])
            if rule:
                active_rules.append(rule)
        active_rules.sort(key=lambda item: (item.get("priority", 9), item.get("label", "")))

        signature = "|".join(item.get("key") or "" for item in active_rules if item.get("key"))
        ack_signature = str(candidate.get("revisit_ack_signature") or "")
        acknowledged = bool(signature) and signature == ack_signature

        return {
            "metrics": {
                "weekly_change_pct": weekly_change_pct,
                "monthly_change_pct": monthly_change_pct,
                "since_added_change_pct": since_added_change_pct,
                "current_price": current_price,
                "baseline_price": baseline_price,
            },
            "thresholds": thresholds,
            "active_rules": active_rules,
            "signature": signature,
            "acknowledged": acknowledged,
        }

    def akshare_watch_refresh_available(self) -> bool:
        return any(
            (
                self._deps.get_portfolio_and_weekly,
                self._deps.get_weekly_performance,
                self._deps.get_portfolio_returns,
            )
        )

    def yfinance_symbol_for_watch(self, ticker: str) -> str:
        text = str(ticker or "").strip().upper()
        if not text:
            return ""
        if text.endswith(".SH"):
            return text[:-3] + ".SS"
        if text.endswith(".SS"):
            return text
        if text.endswith(".SZ"):
            return text
        if text.endswith(".BJ"):
            return text
        return text

    def download_watch_candidate_history(self, ticker: str, *, lookback_days: int = 540) -> Optional[pd.DataFrame]:
        symbol = self.yfinance_symbol_for_watch(ticker)
        if not symbol:
            return None
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        try:
            df = yf.download(
                symbol,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            self._deps.logger.warning("yfinance download failed for %s: %s", ticker, exc)
            return None
        if df is None or df.empty:
            return None
        df = df.reset_index()
        if "Date" in df.columns:
            df = df.rename(columns={"Date": "date"})
        else:
            first_col = df.columns[0]
            df = df.rename(columns={first_col: "date"})
        rename_map = {
            "Close": "close",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Volume": "volume",
        }
        df = df.rename(columns=rename_map)
        for col in ("close", "high", "low"):
            if col not in df.columns:
                return None
        if "volume" not in df.columns:
            df["volume"] = 0
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"].notna()].sort_values("date")
        return df

    @staticmethod
    def price_on_or_before_from_history(history: pd.DataFrame, target: datetime) -> Optional[float]:
        subset = history[history["date"] <= target]
        if subset.empty:
            return None
        return float(subset.iloc[-1]["close"])

    def build_weekly_perf_from_history(self, history: pd.DataFrame) -> Tuple[Dict[str, Any], str]:
        if history is None or history.empty:
            return {}, ""
        weekly = history.set_index("date")["close"].resample("W-FRI").last().dropna()
        if len(weekly) < 2:
            return {}, ""
        last_week_end = weekly.index[-2]
        this_week_end = weekly.index[-1]
        start_price = float(weekly.iloc[-2])
        end_price = float(weekly.iloc[-1])
        change = end_price - start_price
        change_pct = (change / start_price * 100) if start_price else 0.0
        mask = (history["date"] > last_week_end - pd.Timedelta(days=7)) & (history["date"] <= this_week_end)
        week_slice = history.loc[mask]
        if week_slice.empty:
            week_slice = history.tail(5)
        high = float(week_slice["high"].max())
        low = float(week_slice["low"].min())
        volume_avg = float(week_slice["volume"].mean()) if "volume" in week_slice else 0.0
        start_date = last_week_end.strftime("%Y-%m-%d")
        end_date = this_week_end.strftime("%Y-%m-%d")
        payload = {
            "start_price": round(start_price, 2),
            "end_price": round(end_price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "volume_avg": int(volume_avg) if not math.isnan(volume_avg) else 0,
            "start_date": start_date,
            "end_date": end_date,
        }
        summary = (
            f"Text: {'Text' if change >= 0 else 'Text'} {abs(change_pct):.2f}% "
            f"(Last Week {start_date} Text {start_price:.2f} → This Week {end_date} Text {end_price:.2f}), "
            f"Text {high:.2f}, Text {low:.2f}"
        )
        return payload, "".join(summary)

    @staticmethod
    def parse_watch_buy_date(value: Any) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            sanitized = text.replace("Z", "+00:00")
            return datetime.fromisoformat(sanitized)
        except ValueError:
            digits = re.sub(r"\D", "", text)
            if len(digits) >= 8:
                try:
                    return datetime(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
                except ValueError:
                    return None
        return None

    def build_portfolio_returns_from_history(self, history: pd.DataFrame, buy_date: Optional[str]) -> Dict[str, Any]:
        if history is None or history.empty:
            return {}
        current_price = float(history.iloc[-1]["close"])
        result: Dict[str, Any] = {
            "success": True,
            "current_price": round(current_price, 2),
            "buy_date_price": None,
            "return_since_buy": None,
            "return_1d": None,
            "return_1w": None,
            "return_1m": None,
            "return_1q": None,
            "ytd_return": None,
            "return_6m": None,
            "return_1y": None,
        }
        today = datetime.now()
        checkpoints = {
            "return_1d": today - timedelta(days=1),
            "return_1w": today - timedelta(days=7),
            "return_1m": today - timedelta(days=30),
            "return_1q": today - timedelta(days=92),
            "ytd_return": datetime(today.year, 1, 1),
            "return_6m": today - timedelta(days=185),
            "return_1y": today - timedelta(days=370),
        }
        for key, target in checkpoints.items():
            price = self.price_on_or_before_from_history(history, target)
            if price and price > 0:
                result[key] = round((current_price / price - 1) * 100, 2)
        buy_dt = self.parse_watch_buy_date(buy_date)
        if buy_dt:
            buy_price = self.price_on_or_before_from_history(history, buy_dt)
            if buy_price and buy_price > 0:
                result["buy_date_price"] = round(buy_price, 2)
                result["return_since_buy"] = round((current_price / buy_price - 1) * 100, 2)
        return result

    @staticmethod
    def fallback_summary_from_returns(portfolio_returns: Dict[str, Any]) -> str:
        if not portfolio_returns:
            return "Text yfinance RefreshMarket Data"
        current_price = portfolio_returns.get("current_price")
        since_buy = portfolio_returns.get("return_since_buy")
        if current_price is not None and since_buy is not None:
            return f"Text {current_price:.2f}, Text {since_buy:+.2f}%"
        if current_price is not None:
            return f"Text {current_price:.2f}"
        return "Text yfinance RefreshMarket Data"

    def refresh_watch_candidate_perf_via_yfinance(self, candidate: Dict[str, Any], ticker: str) -> Dict[str, Any]:
        stock_id = (candidate.get("stock_id") or "").strip()
        history = self.download_watch_candidate_history(ticker)
        if history is None or history.empty:
            return {
                "success": False,
                "stock_id": stock_id,
                "error": "Text yfinance Market Data",
            }
        performance_data, performance_summary = self.build_weekly_perf_from_history(history)
        portfolio_returns = self.build_portfolio_returns_from_history(history, candidate.get("watch_started_at"))
        revisit_state = self.build_watch_candidate_revisit_state(candidate, performance_data, portfolio_returns)
        summary_value = performance_summary or self.fallback_summary_from_returns(portfolio_returns)
        overall_success = bool(performance_data) or bool(portfolio_returns)
        if not overall_success:
            return {
                "success": False,
                "stock_id": stock_id,
                "error": "Market DataRefreshFailed",
            }
        updated = self._deps.get_storage().update_watch_candidate_performance(
            stock_id,
            performance_summary=summary_value,
            performance_data=performance_data,
            revisit_metrics=revisit_state.get("metrics"),
            revisit_thresholds=revisit_state.get("thresholds"),
            revisit_active_rules=revisit_state.get("active_rules"),
            revisit_signature=revisit_state.get("signature"),
        )
        return {
            "success": True,
            "stock_id": stock_id,
            "performance_summary": summary_value,
            "performance_data": performance_data,
            "revisit": revisit_state,
            "candidate": updated or candidate,
            "error": "",
        }

    def refresh(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        ticker = self.normalize_watch_candidate_ticker(candidate)
        stock_id = (candidate.get("stock_id") or "").strip()
        if not ticker:
            return {
                "success": False,
                "stock_id": stock_id,
                "error": "TextSettingsStockTicker",
            }
        if not self.akshare_watch_refresh_available():
            return self.refresh_watch_candidate_perf_via_yfinance(candidate, ticker)

        performance_summary = ""
        performance_data: Dict[str, Any] = {}
        portfolio_returns: Dict[str, Any] = {}
        errors: List[str] = []

        if self._deps.get_portfolio_and_weekly:
            try:
                combined = self._deps.get_portfolio_and_weekly(ticker, candidate.get("watch_started_at"))
                if combined.get("success"):
                    performance_data = combined.get("performance_data") or {}
                    performance_summary = combined.get("performance_summary") or ""
                    portfolio_returns = combined.get("portfolio_returns") or {}
                else:
                    err = str(combined.get("error") or "").strip()
                    if err:
                        errors.append(err)
            except Exception as exc:
                errors.append(str(exc))

        if not performance_data and self._deps.get_weekly_performance:
            try:
                result = self._deps.get_weekly_performance(ticker, 7)
            except Exception as exc:
                errors.append(str(exc))
            else:
                if result.get("success"):
                    performance_data = result.get("data") or {}
                    performance_summary = result.get("performance_summary") or performance_summary
                else:
                    err = str(result.get("error") or "").strip()
                    if err:
                        errors.append(err)

        if not portfolio_returns and self._deps.get_portfolio_returns:
            try:
                portfolio_result = self._deps.get_portfolio_returns(ticker, candidate.get("watch_started_at"))
            except Exception as exc:
                errors.append(str(exc))
            else:
                if portfolio_result.get("success"):
                    portfolio_returns = portfolio_result
                else:
                    err = str(portfolio_result.get("error") or "").strip()
                    if err:
                        errors.append(err)

        revisit_state = self.build_watch_candidate_revisit_state(candidate, performance_data, portfolio_returns)
        overall_success = bool(performance_data) or bool(portfolio_returns) or bool(revisit_state.get("active_rules"))
        if not overall_success:
            fallback = self.refresh_watch_candidate_perf_via_yfinance(candidate, ticker)
            if fallback.get("success"):
                return fallback

        summary_value = performance_summary if overall_success else (errors[0] if errors else "Market DataRefreshFailed")
        updated = self._deps.get_storage().update_watch_candidate_performance(
            stock_id,
            performance_summary=summary_value,
            performance_data=performance_data,
            revisit_metrics=revisit_state.get("metrics"),
            revisit_thresholds=revisit_state.get("thresholds"),
            revisit_active_rules=revisit_state.get("active_rules"),
            revisit_signature=revisit_state.get("signature"),
        )
        return {
            "success": overall_success,
            "stock_id": stock_id,
            "performance_summary": summary_value,
            "performance_data": performance_data or {},
            "revisit": revisit_state,
            "candidate": updated or candidate,
            "error": "" if overall_success else (errors[0] if errors else "Market DataRefreshFailed"),
        }
