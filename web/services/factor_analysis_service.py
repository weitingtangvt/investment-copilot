"""Application service for factor analysis workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.storage import Storage
from core.weekly_review import get_week_id


@dataclass(frozen=True)
class FactorAnalysisResult:
    status_code: int
    payload: Dict[str, Any]


class FactorAnalysisService:
    """Build factor-analysis inputs from weekly review state."""

    def __init__(
        self,
        storage: Storage,
        analyze_portfolio_factors: Callable[..., Dict[str, Any]],
        week_id_resolver: Callable[[], str] = get_week_id,
        review_projection_provider: Optional[Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._storage = storage
        self._analyze_portfolio_factors = analyze_portfolio_factors
        self._week_id_resolver = week_id_resolver
        self._review_projection_provider = review_projection_provider

    def run(self, data: Dict[str, Any]) -> FactorAnalysisResult:
        days = self._safe_int(data.get("days"), 365)
        compare_prev_week = self._to_bool(data.get("compare_prev_week"), True)
        us_only = self._to_bool(data.get("us_only"), True)
        include_q_factors = self._to_bool(data.get("include_q_factors"), True)
        include_style_overlays = self._to_bool(data.get("include_style_overlays"), True)
        include_sector_macro = self._to_bool(data.get("include_sector_macro"), True)
        model_pack = (data.get("model_pack") or "").strip() or "us_equity_lab_v1"
        benchmark_holdings = self._normalize_benchmark_holdings(data.get("benchmark_holdings")) if model_pack == "barra_lite_us_v1" else None
        windows = self._normalize_windows(data.get("windows"))
        factor_universe = self._build_factor_universe() if model_pack == "barra_lite_us_v1" else None
        factor_universe_updated_at = self._factor_universe_updated_at() if model_pack == "barra_lite_us_v1" else ""

        week_id = (data.get("week_id") or "").strip() or self._week_id_resolver()
        stocks_list = self._storage.list_stocks()
        review = self._storage.get_or_create_weekly_review(week_id, stocks_list)
        review = self._project_review_if_available(week_id, review)
        holdings = self._build_holdings(review, stocks_list)

        if not holdings and week_id:
            prev_week_id = self._storage._prev_week_id(week_id)
            if prev_week_id:
                previous_review = self._storage.get_or_create_weekly_review(prev_week_id, stocks_list)
                previous_review = self._project_review_if_available(prev_week_id, previous_review)
                previous_holdings = self._build_holdings(previous_review, stocks_list)
                if previous_holdings:
                    review = previous_review
                    holdings = previous_holdings
                    week_id = prev_week_id

        if not holdings:
            return FactorAnalysisResult(
                status_code=400,
                payload={"error": "CurrentTextAnalysisTextHoldingsText, TextWeekly ReviewTextHoldingsTextCost. "},
            )

        previous_holdings = None
        previous_week_id = None
        if compare_prev_week:
            previous_week_id = self._storage._prev_week_id(week_id)
            if previous_week_id:
                previous_review = self._storage.get_weekly_review(previous_week_id)
                if previous_review:
                    previous_review = self._project_review_if_available(previous_week_id, previous_review)
                    previous_holdings = self._build_holdings(previous_review, stocks_list) or None

        result = self._analyze_portfolio_factors(
            holdings,
            days=days,
            previous_holdings=previous_holdings,
            windows=windows,
            us_only=us_only,
            model_pack=model_pack,
            include_q_factors=include_q_factors,
            include_style_overlays=include_style_overlays,
            include_sector_macro=include_sector_macro,
            factor_universe=factor_universe,
            benchmark_holdings=benchmark_holdings,
        )
        result["week_id"] = week_id
        result["compare_prev_week"] = compare_prev_week
        result["previous_week_id"] = previous_week_id
        result["windows"] = windows
        result["factor_input_lineage"] = self._build_factor_input_lineage(
            holdings=holdings,
            days=days,
            windows=windows,
            model_pack=model_pack,
            benchmark_holdings=benchmark_holdings,
            factor_universe=factor_universe,
            factor_universe_updated_at=factor_universe_updated_at,
        )
        previous_factor_analysis = None
        if previous_week_id:
            previous_saved_review = self._storage.get_weekly_review(previous_week_id) or {}
            previous_factor_analysis = previous_saved_review.get("factor_analysis") or None
        result["factor_risk_delta"] = self._build_factor_risk_delta(result, previous_factor_analysis, previous_week_id)
        self._storage.update_weekly_factor_analysis(week_id, result)
        return FactorAnalysisResult(status_code=200, payload=result)

    def _project_review_if_available(self, week_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        if not self._review_projection_provider:
            return review
        try:
            projected = self._review_projection_provider(week_id, review)
        except Exception:
            return review
        return projected if isinstance(projected, dict) and projected.get("stocks") else review

    def _build_factor_risk_delta(
        self,
        current: Dict[str, Any],
        previous: Optional[Dict[str, Any]],
        previous_week_id: Optional[str],
    ) -> Dict[str, Any]:
        if not previous:
            return {
                "available": False,
                "previous_week_id": previous_week_id,
                "message": "No saved previous-week factor analysis is available.",
                "top_changes": [],
                "alerts": [],
            }

        current_risk = self._factor_risk_map(current)
        previous_risk = self._factor_risk_map(previous)
        current_exposure = current.get("portfolio_exposure") or {}
        previous_exposure = previous.get("portfolio_exposure") or {}
        factor_labels = current.get("factor_labels") or previous.get("factor_labels") or {}

        changes = []
        for factor in sorted(set(current_risk) | set(previous_risk) | set(current_exposure) | set(previous_exposure)):
            risk_delta = round(self._safe_float(current_risk.get(factor)) - self._safe_float(previous_risk.get(factor)), 4)
            exposure_delta = round(self._safe_float(current_exposure.get(factor)) - self._safe_float(previous_exposure.get(factor)), 4)
            if abs(risk_delta) < 0.0001 and abs(exposure_delta) < 0.0001:
                continue
            label_obj = factor_labels.get(factor) or {}
            changes.append(
                {
                    "factor": factor,
                    "label": label_obj.get("zh") or label_obj.get("en") or factor,
                    "current_risk_share": round(self._safe_float(current_risk.get(factor)), 4),
                    "previous_risk_share": round(self._safe_float(previous_risk.get(factor)), 4),
                    "risk_share_delta": risk_delta,
                    "current_exposure": round(self._safe_float(current_exposure.get(factor)), 4),
                    "previous_exposure": round(self._safe_float(previous_exposure.get(factor)), 4),
                    "exposure_delta": exposure_delta,
                }
            )

        changes.sort(key=lambda row: (abs(row["risk_share_delta"]), abs(row["exposure_delta"])), reverse=True)
        quality_delta = self._factor_quality_delta(current, previous)
        alerts = self._factor_delta_alerts(changes, quality_delta)
        return {
            "available": True,
            "previous_week_id": previous_week_id,
            "top_changes": changes[:8],
            "quality_delta": quality_delta,
            "lineage_comparison": self._lineage_comparison(current.get("factor_input_lineage") or {}, previous.get("factor_input_lineage") or {}),
            "alerts": alerts,
        }

    def _build_factor_input_lineage(
        self,
        *,
        holdings: List[Dict[str, Any]],
        days: int,
        windows: List[int],
        model_pack: str,
        benchmark_holdings: Optional[List[Dict[str, Any]]],
        factor_universe: Optional[List[Dict[str, Any]]],
        factor_universe_updated_at: str,
    ) -> Dict[str, Any]:
        holdings_payload = [
            {
                "stock_id": row.get("stock_id"),
                "ticker": row.get("ticker"),
                "market": row.get("market"),
                "weight": round(self._safe_float(row.get("weight")), 8),
            }
            for row in sorted(holdings or [], key=lambda item: str(item.get("stock_id") or ""))
        ]
        model_config = {
            "model_pack": model_pack,
            "lookback_days": days,
            "windows": windows,
            "benchmark_holdings": benchmark_holdings or [],
        }
        universe_payload = {
            "updated_at": factor_universe_updated_at,
            "tickers": sorted(str(row.get("ticker") or row.get("stock_id") or "") for row in factor_universe or []),
        }
        holdings_signature = self._signature(holdings_payload)
        model_config_signature = self._signature(model_config)
        factor_universe_signature = self._signature(universe_payload)
        return {
            "input_signature": self._signature(
                {
                    "holdings": holdings_signature,
                    "model_config": model_config_signature,
                    "factor_universe": factor_universe_signature,
                }
            ),
            "holdings_signature": holdings_signature,
            "model_config_signature": model_config_signature,
            "factor_universe_signature": factor_universe_signature,
            "model_pack": model_pack,
            "lookback_days": days,
            "windows": windows,
            "benchmark_mode": "custom_benchmark_holdings" if benchmark_holdings else "equal_weight_active_holdings",
            "holding_count": len(holdings or []),
            "factor_universe_count": len(factor_universe or []),
            "factor_universe_updated_at": factor_universe_updated_at,
        }

    def _lineage_comparison(self, current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
        if not previous:
            return {
                "input_change_type": "unknown",
                "portfolio_changed": None,
                "model_inputs_changed": None,
                "factor_universe_changed": None,
            }
        portfolio_changed = current.get("holdings_signature") != previous.get("holdings_signature")
        model_config_changed = current.get("model_config_signature") != previous.get("model_config_signature")
        factor_universe_changed = current.get("factor_universe_signature") != previous.get("factor_universe_signature")
        model_inputs_changed = bool(model_config_changed or factor_universe_changed)
        if portfolio_changed and model_inputs_changed:
            change_type = "both_changed"
        elif portfolio_changed:
            change_type = "portfolio_changed"
        elif model_inputs_changed:
            change_type = "model_inputs_changed"
        else:
            change_type = "unchanged"
        return {
            "input_change_type": change_type,
            "portfolio_changed": portfolio_changed,
            "model_inputs_changed": model_inputs_changed,
            "model_config_changed": model_config_changed,
            "factor_universe_changed": factor_universe_changed,
        }

    def _signature(self, payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _factor_risk_map(self, payload: Dict[str, Any]) -> Dict[str, float]:
        rows = payload.get("factor_risk_contributions") or (payload.get("risk_decomposition") or {}).get("factor_risk_contributions") or []
        result: Dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("factor"):
                continue
            result[str(row["factor"])] = self._safe_float(row.get("variance_share"))
        return result

    def _factor_quality_delta(self, current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
        current_summary = (current.get("factor_data_quality") or {}).get("summary") or {}
        previous_summary = (previous.get("factor_data_quality") or {}).get("summary") or {}
        return {
            "avg_coverage_ratio_delta": round(
                self._safe_float(current_summary.get("avg_coverage_ratio")) - self._safe_float(previous_summary.get("avg_coverage_ratio")),
                4,
            ),
            "low_confidence_count_delta": int(self._safe_float(current_summary.get("low_confidence_count")) - self._safe_float(previous_summary.get("low_confidence_count"))),
        }

    def _factor_delta_alerts(self, changes: List[Dict[str, Any]], quality_delta: Dict[str, Any]) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        if changes:
            top = changes[0]
            direction = "rose" if top["risk_share_delta"] > 0 else "fell"
            alerts.append(
                {
                    "type": "risk_share_delta",
                    "severity": "medium" if abs(top["risk_share_delta"]) >= 0.1 else "low",
                    "title": f"{top['label']} risk share {direction}",
                    "message": f"Risk share changed by {top['risk_share_delta']:+.1%} versus the previous saved result.",
                    "factor": top["factor"],
                }
            )
        low_delta = int(quality_delta.get("low_confidence_count_delta") or 0)
        if low_delta:
            direction = "worsened" if low_delta > 0 else "improved"
            alerts.append(
                {
                    "type": "data_quality_delta",
                    "severity": "medium" if low_delta > 0 else "low",
                    "title": f"Factor data quality {direction}",
                    "message": f"Low-confidence factor count changed by {low_delta:+d}.",
                }
            )
        return alerts[:4]

    def _build_holdings(self, review_obj: Dict[str, Any], stocks_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        review_stocks = review_obj.get("stocks", {}) or {}
        usd_to_hkd = self._safe_float(review_obj.get("usd_to_hkd"), 7.8) or 7.8
        eur_to_hkd = self._safe_float(review_obj.get("eur_to_hkd"), 8.4) or 8.4
        cny_to_hkd = self._safe_float(review_obj.get("cny_to_hkd"), 1.07) or 1.07
        jpy_to_hkd = self._safe_float(review_obj.get("jpy_to_hkd"), 0.052) or 0.052
        krw_to_hkd = self._safe_float(review_obj.get("krw_to_hkd"), 0.0056) or 0.0056
        ticker_map: Dict[str, str] = {}
        stock_name_map: Dict[str, str] = {}
        playbook_map: Dict[str, Dict[str, Any]] = {}
        for stock in stocks_list:
            stock_id = stock["stock_id"]
            ticker_map[stock_id] = stock.get("ticker") or stock_id
            stock_name_map[stock_id] = stock.get("stock_name") or stock_id
            playbook = stock
            if hasattr(self._storage, "get_stock_playbook"):
                try:
                    playbook = self._storage.get_stock_playbook(stock_id) or stock
                except Exception:
                    playbook = stock
            playbook_map[stock_id] = playbook

        holdings: List[Dict[str, Any]] = []
        holding_values: Dict[str, float] = {}
        total_value = 0.0

        for stock_id, sdata in review_stocks.items():
            if not sdata:
                continue
            shares = self._safe_float(sdata.get("shares_held"))
            if shares <= 0:
                continue

            avg_cost = self._safe_float(sdata.get("avg_cost"))
            if avg_cost <= 0:
                continue

            perf = sdata.get("performance_data") or {}
            end_price = self._safe_float(perf.get("end_price"))
            start_price = self._safe_float(perf.get("start_price"))

            metrics = sdata.get("position_metrics") or {}
            ticker = metrics.get("ticker") or sdata.get("ticker") or ticker_map.get(stock_id) or stock_id
            ticker_upper = str(ticker).upper()
            is_hk = ticker_upper.endswith(".HK")
            is_a = any(ticker_upper.endswith(suffix) for suffix in (".SH", ".SZ", ".SS"))
            is_eu = any(ticker_upper.endswith(suffix) for suffix in (".AS", ".DE", ".VI"))
            is_jp = ticker_upper.endswith(".T")
            is_kr = any(ticker_upper.endswith(suffix) for suffix in (".KS", ".KQ"))
            is_us = not is_hk and not is_a and not is_eu and not is_jp and not is_kr

            if is_us:
                price_hkd = end_price * usd_to_hkd if end_price > 0 else 0.0
                fallback_cost = avg_cost * usd_to_hkd
                market = "us"
            elif is_eu:
                price_hkd = end_price * eur_to_hkd if end_price > 0 else 0.0
                fallback_cost = avg_cost * eur_to_hkd
                market = "eu"
            elif is_a:
                price_hkd = end_price * cny_to_hkd if end_price > 0 else 0.0
                fallback_cost = avg_cost * cny_to_hkd
                market = "a"
            elif is_jp:
                price_hkd = end_price * jpy_to_hkd if end_price > 0 else 0.0
                fallback_cost = avg_cost * jpy_to_hkd
                market = "jp"
            elif is_kr:
                price_hkd = end_price * krw_to_hkd if end_price > 0 else 0.0
                fallback_cost = avg_cost * krw_to_hkd
                market = "kr"
            else:
                price_hkd = end_price
                fallback_cost = avg_cost
                market = "hk"

            metric_value_hkd = self._safe_float(metrics.get("holding_value_hkd"))
            value = metric_value_hkd if metric_value_hkd > 0 else (shares * price_hkd if price_hkd > 0 else shares * fallback_cost)
            if value <= 0:
                continue

            weekly_change_pct = None
            if start_price > 0 and end_price > 0:
                weekly_change_pct = round((end_price / start_price - 1) * 100, 2)

            holding_values[stock_id] = value
            total_value += value
            holding = {
                "stock_id": stock_id,
                "ticker": ticker,
                "stock_name": sdata.get("stock_name") or stock_name_map.get(stock_id) or stock_id,
                "market": market,
                "weight": 0.0,
                "weekly_change_pct": weekly_change_pct,
            }
            playbook = playbook_map.get(stock_id) or {}
            for key in (
                "market_cap",
                "float_market_cap",
                "book_to_price",
                "price_to_book",
                "pb",
                "roe",
                "roa",
                "gross_margin",
                "dollar_volume",
                "avg_dollar_volume",
                "turnover_value",
                "sector",
                "industry",
            ):
                value = sdata.get(key, playbook.get(key))
                if value not in (None, ""):
                    holding[key] = value
            holdings.append(holding)

        if total_value > 0:
            for holding in holdings:
                holding["weight"] = holding_values.get(holding["stock_id"], 0.0) / total_value
        return holdings

    def _build_factor_universe(self) -> List[Dict[str, Any]]:
        if not hasattr(self._storage, "get_us_screener_universe_snapshot"):
            return []
        try:
            snapshot = self._storage.get_us_screener_universe_snapshot() or {}
        except Exception:
            return []
        rows = snapshot.get("items") or []
        if not isinstance(rows, list):
            return []

        universe: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or row.get("stock_id") or "").strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            item: Dict[str, Any] = {
                "stock_id": ticker,
                "ticker": ticker,
                "stock_name": str(row.get("stock_name") or row.get("name") or ticker).strip() or ticker,
                "market": "us",
            }
            for key in (
                "market_cap",
                "float_market_cap",
                "book_to_price",
                "price_to_book",
                "pb",
                "roe",
                "roa",
                "gross_margin",
                "dollar_volume",
                "avg_dollar_volume",
                "turnover_value",
                "sector",
                "industry",
            ):
                value = row.get(key)
                if value not in (None, ""):
                    item[key] = value
            universe.append(item)
        return universe

    def _factor_universe_updated_at(self) -> str:
        if not hasattr(self._storage, "get_us_screener_universe_snapshot"):
            return ""
        try:
            snapshot = self._storage.get_us_screener_universe_snapshot() or {}
        except Exception:
            return ""
        return str(snapshot.get("updated_at") or "")

    def _normalize_benchmark_holdings(self, raw_holdings: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(raw_holdings, list):
            return None

        normalized: List[Dict[str, Any]] = []
        for row in raw_holdings:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or row.get("stock_id") or "").strip().upper()
            if not ticker:
                continue
            weight = self._safe_float(row.get("weight"))
            if weight <= 0:
                continue
            normalized.append(
                {
                    "stock_id": ticker,
                    "ticker": ticker,
                    "weight": weight,
                }
            )
        return normalized or None

    @staticmethod
    def _normalize_windows(raw_windows: Any) -> List[int]:
        windows: List[int] = []
        if isinstance(raw_windows, list):
            for item in raw_windows:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    windows.append(value)
        return windows or [63, 126, 252]

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
