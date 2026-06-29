"""Optional stock overview enrichment for US screener candidates."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


DEFAULT_TTL_HOURS = 24
_FMP_KEY_ENV_NAMES = ("FINANCIAL_MODELING_PREP_KEY", "FMP_API_KEY")


class StockOverviewProvider(Protocol):
    def fetch(self, ticker: str) -> dict[str, Any]:
        """Return raw fundamentals/quote data for one ticker."""


class StockOverviewUnavailable(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class FinanceToolkitStockOverviewProvider:
    """Lazy FinanceToolkit/FMP adapter.

    FinanceToolkit is intentionally optional. Import and API-key checks happen at
    request time so the web app can start and the screener can work without this
    enrichment dependency.
    """

    api_key: str | None = None

    def _resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        for name in _FMP_KEY_ENV_NAMES:
            value = os.environ.get(name)
            if value:
                return value
        return ""

    def dependency_status(self) -> dict[str, str]:
        if not self._resolved_api_key():
            return {
                "name": "FinanceToolkit",
                "source": "Financial Modeling Prep",
                "status": "missing_api_key",
                "detail": "Set FINANCIAL_MODELING_PREP_KEY or FMP_API_KEY.",
            }
        if importlib.util.find_spec("financetoolkit") is None:
            return {
                "name": "FinanceToolkit",
                "source": "Financial Modeling Prep",
                "status": "available",
                "detail": "FMP API key is configured; using direct FMP endpoints because FinanceToolkit is not installed.",
            }
        return {
            "name": "FinanceToolkit",
            "source": "Financial Modeling Prep",
            "status": "available",
            "detail": "FinanceToolkit/FMP enrichment is configured.",
        }

    def fetch(self, ticker: str) -> dict[str, Any]:
        dependency = self.dependency_status()
        if dependency["status"] != "available":
            raise StockOverviewUnavailable(dependency["status"], dependency["detail"])

        if importlib.util.find_spec("financetoolkit") is None:
            return self._fetch_fmp_direct(ticker)

        from financetoolkit import Toolkit  # type: ignore

        toolkit = Toolkit([ticker], api_key=self._resolved_api_key())
        profile = _call_first_available(
            [
                (toolkit, "get_profile"),
                (toolkit, "get_company_profile"),
                (getattr(toolkit, "companies", None), "get_profile"),
                (getattr(toolkit, "companies", None), "get_company_profile"),
            ]
        )
        ratios = _call_first_available(
            [
                (toolkit, "get_ratios"),
                (getattr(toolkit, "ratios", None), "collect_all_ratios"),
                (getattr(toolkit, "ratios", None), "get_profitability_ratios"),
                (getattr(toolkit, "ratios", None), "get_solvency_ratios"),
                (getattr(toolkit, "ratios", None), "get_valuation_ratios"),
            ]
        )
        quote = _call_first_available(
            [
                (toolkit, "get_quote"),
                (toolkit, "get_historical_data"),
                (getattr(toolkit, "technicals", None), "get_quote"),
            ]
        )
        payload = {
            "profile": _json_like(profile),
            "ratios": _json_like(ratios),
            "quote": _json_like(quote),
            "source": "finance_toolkit_fmp",
        }
        if not any(payload.get(key) for key in ("profile", "ratios", "quote")):
            return self._fetch_fmp_direct(ticker)
        return payload

    def _fetch_fmp_direct(self, ticker: str) -> dict[str, Any]:
        api_key = self._resolved_api_key()
        if not api_key:
            raise StockOverviewUnavailable("missing_api_key", "Set FINANCIAL_MODELING_PREP_KEY or FMP_API_KEY.")

        profile = _fmp_get_json("profile", {"symbol": ticker, "apikey": api_key})
        ratios = _fmp_first(
            _fmp_get_json_optional(
                "ratios",
                {"symbol": ticker, "period": "annual", "limit": "1", "apikey": api_key},
            )
        )
        quote = _fmp_first(_fmp_get_json_optional("quote", {"symbol": ticker, "apikey": api_key}))
        key_metrics = _fmp_first(
            _fmp_get_json_optional(
                "key-metrics",
                {"symbol": ticker, "period": "annual", "limit": "1", "apikey": api_key},
            )
        )
        profile_row = _fmp_first(profile)
        ratio_payload = _merge_dicts(ratios, key_metrics)
        payload = {
            "profile": _json_like(profile_row),
            "ratios": _json_like(ratio_payload),
            "quote": _json_like(quote),
            "source": "fmp_direct",
        }
        if not any(payload.get(key) for key in ("profile", "ratios", "quote")):
            raise StockOverviewUnavailable("fetch_failed", "FMP returned no profile, ratio, or quote data.")
        return payload


def build_stock_overview(
    ticker: str,
    *,
    candidate: dict[str, Any] | None = None,
    provider: StockOverviewProvider | None = None,
    cache_dir: str | Path | None = None,
    force: bool = False,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> dict[str, Any]:
    normalized_ticker = _normalize_ticker(ticker)
    candidate_data = dict(candidate or {})
    now = datetime.now()
    warnings: list[str] = []

    if not normalized_ticker:
        return {
            "success": False,
            "ticker": "",
            "source": "unavailable",
            "modules": [],
            "warnings": ["Missing ticker."],
            "data_dependencies": _dependencies("missing", "missing"),
            "as_of": now.isoformat(timespec="seconds"),
            "cache": {"hit": False, "path": "", "ttl_hours": ttl_hours},
        }

    cache_path = _cache_path(cache_dir, normalized_ticker)
    provider_payload: dict[str, Any] = {}
    cache_hit = False
    finance_status = "not_requested"
    finance_detail = ""

    if not force:
        cached = _load_cache(cache_path, ttl_hours=ttl_hours, now=now)
        if cached:
            provider_payload = dict(cached.get("provider_payload") or {})
            cache_hit = bool(provider_payload)
            if cache_hit:
                finance_status = "cached"
                finance_detail = "Loaded FinanceToolkit/FMP data from local cache."

    if not provider_payload:
        provider_to_use = provider
        if provider_to_use is None:
            default_provider = FinanceToolkitStockOverviewProvider()
            dependency = default_provider.dependency_status()
            finance_status = dependency["status"]
            finance_detail = dependency["detail"]
            if finance_status == "available":
                provider_to_use = default_provider
        else:
            finance_status = "available"
            finance_detail = "Injected stock overview provider."

        if provider_to_use is not None:
            try:
                provider_payload = dict(provider_to_use.fetch(normalized_ticker) or {})
                finance_status = "available"
                finance_detail = "Fetched FinanceToolkit/FMP enrichment data."
                _save_cache(cache_path, normalized_ticker, provider_payload, now)
            except StockOverviewUnavailable as exc:
                finance_status = exc.status
                finance_detail = exc.message
                stale = _load_cache(cache_path, ttl_hours=None, now=now)
                if stale:
                    provider_payload = dict(stale.get("provider_payload") or {})
                    cache_hit = bool(provider_payload)
                    if cache_hit:
                        finance_status = "stale_cache"
                        finance_detail = f"{exc.message}; using stale local cache."
                if not provider_payload:
                    warnings.append(exc.message)
            except Exception as exc:  # pragma: no cover - defensive boundary
                finance_status = "fetch_failed"
                finance_detail = str(exc) or "FinanceToolkit fetch failed."
                warnings.append(finance_detail)

    if not provider_payload:
        warnings.append("FinanceToolkit/FMP enrichment unavailable; using local screener fields only.")

    candidate_status = "available" if candidate_data else "missing"
    modules = _build_modules(candidate_data, provider_payload)
    source = _resolve_source(provider_payload, candidate_data)
    success = source != "unavailable"

    return {
        "success": success,
        "ticker": normalized_ticker,
        "stock_name": str(candidate_data.get("stock_name") or _find_value(provider_payload, ["companyName", "company_name"]) or normalized_ticker),
        "source": source,
        "business_summary": _business_summary(candidate_data, provider_payload),
        "modules": modules,
        "warnings": _dedupe(warnings),
        "data_dependencies": _dependencies(finance_status, candidate_status, finance_detail=finance_detail),
        "as_of": now.isoformat(timespec="seconds"),
        "cache": {
            "hit": cache_hit,
            "path": str(cache_path),
            "ttl_hours": ttl_hours,
        },
    }


def _dependencies(finance_status: str, candidate_status: str, finance_detail: str = "") -> list[dict[str, str]]:
    finance_details = {
        "available": "FinanceToolkit/FMP data fetched for this request.",
        "cached": "FinanceToolkit/FMP data loaded from local cache.",
        "stale_cache": "FinanceToolkit/FMP fetch failed; stale cache was used.",
        "missing_api_key": "REDACTED FINANCIAL_MODELING_PREP_KEY or FMP_API_KEY.",
        "not_installed": "Install financetoolkit to enable fundamentals enrichment.",
        "fetch_failed": "FinanceToolkit/FMP fetch failed.",
        "not_requested": "FinanceToolkit/FMP was not requested.",
        "missing": "FinanceToolkit/FMP data is missing.",
    }
    candidate_details = {
        "available": "Momentum/risk fields came from the current US screener candidate payload.",
        "missing": "No matching US screener candidate was found for this ticker.",
    }
    return [
        {
            "name": "FinanceToolkit",
            "source": "Financial Modeling Prep",
            "status": finance_status,
            "detail": finance_detail or finance_details.get(finance_status, finance_status),
        },
        {
            "name": "US screener candidate",
            "source": "local scan payload",
            "status": candidate_status,
            "detail": candidate_details.get(candidate_status, candidate_status),
        },
    ]


def _build_modules(candidate: dict[str, Any], provider_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _quality_module(provider_payload),
        _valuation_module(candidate, provider_payload),
        _momentum_module(candidate),
        _risk_module(candidate, provider_payload),
    ]


def _quality_module(provider_payload: dict[str, Any]) -> dict[str, Any]:
    roe = _find_number(provider_payload, ["return_on_equity", "returnOnEquity", "roe"])
    gross_margin = _find_number(provider_payload, ["gross_margin", "grossProfitMargin", "grossMargin"])
    operating_margin = _find_number(provider_payload, ["operating_margin", "operatingProfitMargin", "operatingMargin"])
    debt_to_equity = _find_number(provider_payload, ["debt_to_equity", "debtEquityRatio", "debtToEquity"])
    metrics = [
        _metric("ROE", roe, "ratio_pct", "FinanceToolkit / FMP"),
        _metric("Gross Margin", gross_margin, "ratio_pct", "FinanceToolkit / FMP"),
        _metric("Operating Margin", operating_margin, "ratio_pct", "FinanceToolkit / FMP"),
        _metric("Debt / Equity", debt_to_equity, "number", "FinanceToolkit / FMP"),
    ]
    score_parts = [
        _score_threshold(roe, [(0.25, 30), (0.15, 22), (0.08, 14), (0.0, 6)]),
        _score_threshold(gross_margin, [(0.55, 25), (0.35, 18), (0.20, 10), (0.0, 4)]),
        _score_threshold(operating_margin, [(0.25, 25), (0.15, 18), (0.05, 10), (0.0, 4)]),
        _score_threshold(debt_to_equity, [(0.4, 20), (0.8, 14), (1.5, 8), (999.0, 3)], lower_is_better=True),
    ]
    available = [part for part in score_parts if part is not None]
    return {
        "key": "quality",
        "label": "Quality",
        "score": _round_or_none(sum(available) * 100 / 100) if available else None,
        "metrics": metrics,
        "notes": _module_notes(metrics, "Quality needs FinanceToolkit/FMP fundamentals."),
    }


def _valuation_module(candidate: dict[str, Any], provider_payload: dict[str, Any]) -> dict[str, Any]:
    pe = _find_number(provider_payload, ["price_earnings_ratio", "priceEarningsRatio", "peRatio", "P/E"])
    ps = _find_number(provider_payload, ["price_to_sales_ratio", "priceToSalesRatio", "psRatio", "P/S"])
    market_cap = _first_number(
        _find_number(provider_payload, ["marketCap", "market_cap"]),
        _num(candidate.get("market_cap")),
    )
    latest_price = _first_number(
        _find_number(provider_payload, ["price", "latestPrice"]),
        _num(candidate.get("latest_close")),
    )
    metrics = [
        _metric("P/E", pe, "number", "FinanceToolkit / FMP"),
        _metric("P/S", ps, "number", "FinanceToolkit / FMP"),
        _metric("Market Cap", market_cap, "market_cap", "FinanceToolkit / FMP + local"),
        _metric("Price", latest_price, "price", "FinanceToolkit / FMP + local"),
    ]
    score_parts = [
        _score_threshold(pe, [(20, 38), (35, 28), (60, 18), (9999, 8)], lower_is_better=True),
        _score_threshold(ps, [(4, 32), (8, 24), (15, 14), (9999, 6)], lower_is_better=True),
        _score_threshold(market_cap, [(10_000_000_000, 20), (2_000_000_000, 15), (500_000_000, 9), (0, 4)]),
    ]
    available = [part for part in score_parts if part is not None]
    return {
        "key": "valuation",
        "label": "Valuation",
        "score": _round_or_none(sum(available) * 100 / 90) if available else None,
        "metrics": metrics,
        "notes": _module_notes(metrics, "Valuation needs FinanceToolkit/FMP ratios."),
    }


def _momentum_module(candidate: dict[str, Any]) -> dict[str, Any]:
    max_gain = _num(candidate.get("max_daily_gain_5d_pct"))
    gain_30d = _num(candidate.get("gain_30d_pct"))
    volume_surge = _num(candidate.get("avg_volume_5d_vs_3m"))
    above_200 = _num(candidate.get("distance_above_200ma_pct"))
    rebound = _num(candidate.get("rebound_from_new_low_pct"))
    metrics = [
        _metric("5D Max", max_gain, "pct_points", "US screener candidate"),
        _metric("30D Return", gain_30d, "pct_points", "US screener candidate"),
        _metric("5D / 3M Volume", volume_surge, "multiple", "US screener candidate"),
        _metric("Above 200MA", above_200, "pct_points", "US screener candidate"),
        _metric("Rebound", rebound, "pct_points", "US screener candidate"),
    ]
    score = 35
    if max_gain is not None:
        score += min(25, max(0, max_gain))
    if gain_30d is not None:
        score += min(16, max(-10, gain_30d / 2.5))
    if volume_surge is not None:
        score += min(14, max(0, (volume_surge - 1) * 8))
    if rebound is not None:
        score += min(12, max(0, rebound / 6))
    if above_200 is not None:
        score += min(10, max(-8, above_200 / 8))
    return {
        "key": "momentum",
        "label": "Momentum",
        "score": int(max(0, min(99, round(score)))) if any(metric["value"] is not None for metric in metrics) else None,
        "metrics": metrics,
        "notes": _module_notes(metrics, "Momentum needs current US screener candidate fields."),
    }


def _risk_module(candidate: dict[str, Any], provider_payload: dict[str, Any]) -> dict[str, Any]:
    beta = _find_number(provider_payload, ["beta"])
    debt_to_equity = _find_number(provider_payload, ["debt_to_equity", "debtEquityRatio", "debtToEquity"])
    gain_30d = _num(candidate.get("gain_30d_pct"))
    above_200 = _num(candidate.get("distance_above_200ma_pct"))
    market_cap = _first_number(_find_number(provider_payload, ["marketCap", "market_cap"]), _num(candidate.get("market_cap")))
    metrics = [
        _metric("Beta", beta, "number", "FinanceToolkit / FMP"),
        _metric("Debt / Equity", debt_to_equity, "number", "FinanceToolkit / FMP"),
        _metric("30D Return", gain_30d, "pct_points", "US screener candidate"),
        _metric("Above 200MA", above_200, "pct_points", "US screener candidate"),
        _metric("Market Cap", market_cap, "market_cap", "FinanceToolkit / FMP + local"),
    ]
    risk = 25
    if beta is not None:
        risk += max(0, (beta - 1.0) * 18)
    if debt_to_equity is not None:
        risk += max(0, (debt_to_equity - 0.8) * 12)
    if gain_30d is not None:
        risk += max(0, (gain_30d - 40) / 2)
    if above_200 is not None:
        risk += max(0, (above_200 - 50) / 3)
    if market_cap is not None and market_cap < 500_000_000:
        risk += 16
    return {
        "key": "risk",
        "label": "Risk",
        "score": int(max(0, min(99, round(risk)))) if any(metric["value"] is not None for metric in metrics) else None,
        "metrics": metrics,
        "notes": _risk_notes(beta, debt_to_equity, gain_30d, above_200, market_cap),
    }


def _risk_notes(
    beta: float | None,
    debt_to_equity: float | None,
    gain_30d: float | None,
    above_200: float | None,
    market_cap: float | None,
) -> list[str]:
    notes: list[str] = []
    if beta is not None and beta > 1.5:
        notes.append("High beta: expect larger market-driven moves.")
    if debt_to_equity is not None and debt_to_equity > 1.5:
        notes.append("Leverage is elevated versus common equity screens.")
    if gain_30d is not None and gain_30d > 60:
        notes.append("Short-term move is extended.")
    if above_200 is not None and above_200 > 80:
        notes.append("Price is far above the 200-day moving average.")
    if market_cap is not None and market_cap < 500_000_000:
        notes.append("Small-cap liquidity risk.")
    return notes or ["No severe risk flag from available fields."]


def _metric(label: str, value: Any, kind: str, source: str) -> dict[str, Any]:
    clean_value = _num(value)
    return {
        "label": label,
        "value": clean_value,
        "display": _format_value(clean_value, kind),
        "kind": kind,
        "source": source,
    }


def _module_notes(metrics: list[dict[str, Any]], fallback: str) -> list[str]:
    if any(metric.get("value") is not None for metric in metrics):
        return []
    return [fallback]


def _format_value(value: float | None, kind: str) -> str:
    if value is None:
        return "--"
    if kind == "ratio_pct":
        return f"{value * 100:.1f}%"
    if kind == "pct_points":
        return f"{value:+.2f}%"
    if kind == "multiple":
        return f"{value:.2f}x"
    if kind == "market_cap":
        return _format_market_cap(value)
    if kind == "price":
        return f"{value:,.2f}"
    return f"{value:,.2f}"


def _format_market_cap(value: float | None) -> str:
    if value is None:
        return "--"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    return f"{value:,.0f}"


def _score_threshold(
    value: float | None,
    thresholds: list[tuple[float, int]],
    *,
    lower_is_better: bool = False,
) -> int | None:
    if value is None:
        return None
    ordered = sorted(thresholds, key=lambda item: item[0], reverse=not lower_is_better)
    for threshold, score in ordered:
        if lower_is_better:
            if value <= threshold:
                return score
        elif value >= threshold:
            return score
    return thresholds[-1][1] if thresholds else None


def _round_or_none(value: float | None) -> int | None:
    if value is None:
        return None
    return int(max(0, min(99, round(value))))


def _resolve_source(provider_payload: dict[str, Any], candidate: dict[str, Any]) -> str:
    if provider_payload and candidate:
        return "finance_toolkit_fmp+candidate"
    if provider_payload:
        return "finance_toolkit_fmp"
    if candidate:
        return "candidate_only"
    return "unavailable"


def _business_summary(candidate: dict[str, Any], provider_payload: dict[str, Any]) -> dict[str, Any]:
    provider_text = _clean_text(
        _find_value(
            provider_payload,
            [
                "description",
                "companyDescription",
                "businessSummary",
                "longBusinessSummary",
                "profileDescription",
            ],
        )
    )
    if provider_text:
        return {
            "text": _truncate_sentence(provider_text, 420),
            "source": "FinanceToolkit / FMP",
        }

    candidate_intro = _clean_text(candidate.get("company_intro"))
    if candidate_intro:
        return {
            "text": _truncate_sentence(candidate_intro, 420),
            "source": "local scan payload",
        }

    sector = _clean_text(candidate.get("sector"))
    industry = _clean_text(candidate.get("industry"))
    name = _clean_text(candidate.get("stock_name") or candidate.get("ticker") or candidate.get("stock_id"))
    pieces = [item for item in [sector, industry] if item]
    if name and pieces:
        return {
            "text": f"{name} belongs to {' / '.join(pieces)}. A richer business description needs FMP Company Profile data.",
            "source": "local scan payload",
        }
    return {
        "text": "",
        "source": "",
    }


def _cache_path(cache_dir: str | Path | None, ticker: str) -> Path:
    base = Path(cache_dir) if cache_dir is not None else Path(os.path.expanduser("~/REDACTED")) / "stock_overview_cache"
    return base / f"{ticker.lower()}.json"


def _load_cache(cache_path: Path, *, ttl_hours: int | None, now: datetime) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if ttl_hours is None:
        return payload
    fetched_at = _parse_datetime(str(payload.get("fetched_at") or ""))
    if fetched_at is None:
        return {}
    if now - fetched_at > timedelta(hours=ttl_hours):
        return {}
    return payload


def _save_cache(cache_path: Path, ticker: str, provider_payload: dict[str, Any], now: datetime) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticker": ticker,
        "fetched_at": now.isoformat(timespec="seconds"),
        "provider_payload": _json_like(provider_payload),
    }
    with cache_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _first_number(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _call_first_available(targets: list[tuple[Any, str]]) -> Any:
    for obj, method_name in targets:
        if obj is None:
            continue
        method = getattr(obj, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
        except Exception:
            continue
        if result is not None:
            return result
    return {}


def _fmp_get_json(endpoint: str, params: dict[str, str]) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"https://financialmodelingprep.com/stable/{endpoint}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "investment-assistant/stock-overview"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise StockOverviewUnavailable("fetch_failed", f"FMP HTTP {exc.code} for {endpoint}") from exc
    except urllib.error.URLError as exc:
        raise StockOverviewUnavailable("fetch_failed", f"FMP request failed for {endpoint}: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StockOverviewUnavailable("fetch_failed", f"FMP returned invalid JSON for {endpoint}") from exc


def _fmp_get_json_optional(endpoint: str, params: dict[str, str]) -> Any:
    try:
        return _fmp_get_json(endpoint, params)
    except StockOverviewUnavailable:
        return {}


def _fmp_first(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return dict(value[0]) if value and isinstance(value[0], dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _merge_dicts(*values: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _json_like(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): _json_like(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_like(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return _json_like(value.to_dict())
        except Exception:
            return str(value)
    return str(value)


def _find_number(data: Any, aliases: list[str]) -> float | None:
    return _num(_find_value(data, aliases))


def _find_value(data: Any, aliases: list[str]) -> Any:
    targets = {_key_name(alias) for alias in aliases}
    return _find_value_inner(data, targets)


def _find_value_inner(data: Any, targets: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            if _key_name(key) in targets and not isinstance(value, (dict, list, tuple)):
                return value
        for value in data.values():
            nested = _find_value_inner(value, targets)
            if nested is not None:
                return nested
    elif isinstance(data, (list, tuple)):
        for item in data:
            nested = _find_value_inner(item, targets)
            if nested is not None:
                return nested
    return None


def _key_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _truncate_sentence(value: str, max_chars: int) -> str:
    text = _clean_text(value)
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rstrip()
    last_period = max(clipped.rfind("."), clipped.rfind(". "))
    if last_period >= 160:
        return clipped[: last_period + 1]
    return f"{clipped}..."


def _dedupe(items: list[str]) -> list[str]:
    output: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in output:
            output.append(text)
    return output
