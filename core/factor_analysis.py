"""US-only portfolio factor lab with multi-model comparison."""

from __future__ import annotations

import io
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm

logger = logging.getLogger(__name__)

_FF_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
_FF5_DAILY_URL = f"{_FF_BASE}/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
_MOM_DAILY_URL = f"{_FF_BASE}/F-F_Momentum_Factor_daily_CSV.zip"
_CACHE_DIR = os.path.join(os.path.expanduser("~"), "REDACTED", "factor_cache")

US_ONLY_MODEL_PACK = "us_equity_lab_v1"
BARRA_LITE_MODEL_PACK = "barra_lite_us_v1"

FACTOR_LABELS = {
    "Mkt-RF": {"zh": "Text Beta", "desc": "Text. "},
    "SMB": {"zh": "Text", "desc": "Text, Text. "},
    "HML": {"zh": "Text/Text", "desc": "Text, Text. "},
    "RMW": {"zh": "Text", "desc": "Text. "},
    "CMA": {"zh": "Text", "desc": "Text, Text. "},
    "Mom": {"zh": "Text", "desc": "Text, Text. "},
}

BARRA_LITE_FACTOR_LABELS = {
    "market": {"zh": "Market", "desc": "Portfolio sensitivity to the broad US equity tape."},
    "size": {"zh": "Size", "desc": "Positive values tilt toward larger market-cap holdings when market cap is available."},
    "value": {"zh": "Value", "desc": "Book-to-price or inverse price-to-book characteristic exposure."},
    "momentum": {"zh": "Momentum", "desc": "Recent trailing return characteristic exposure."},
    "quality": {"zh": "Quality", "desc": "ROE/profitability characteristic exposure when available."},
    "low_vol": {"zh": "Low Volatility", "desc": "Positive values tilt toward lower realized volatility."},
    "liquidity": {"zh": "Liquidity", "desc": "Dollar-volume characteristic exposure when available."},
    "theme_semis_hardware": {"zh": "Semis / Hardware", "desc": "Theme bucket exposure from holding classification."},
    "theme_software_ai": {"zh": "Software / AI", "desc": "Theme bucket exposure from holding classification."},
    "theme_industrials": {"zh": "Industrials", "desc": "Theme bucket exposure from holding classification."},
    "theme_materials_resources": {"zh": "Materials / Resources", "desc": "Theme bucket exposure from holding classification."},
    "theme_transport_logistics": {"zh": "Transport / Logistics", "desc": "Theme bucket exposure from holding classification."},
    "theme_rates_sensitive_growth": {"zh": "Rates-sensitive Growth", "desc": "Theme bucket exposure from holding classification."},
    "theme_defensive_other": {"zh": "Defensive / Other", "desc": "Residual theme bucket exposure."},
}

Q_FACTOR_LABELS = {
    "Mkt-RF": {"zh": "Text Beta", "desc": "Text. "},
    "ME": {"zh": "Text", "desc": "Text, Text. "},
    "IA": {"zh": "Text", "desc": "Text. "},
    "ROE": {"zh": "Text", "desc": "Text ROE / Text. "},
    "EG": {"zh": "Text/Text", "desc": "Text. "},
}

STYLE_OVERLAY_LABELS = {
    "QMJ": {"zh": "Text", "desc": "Text. "},
    "BAB": {"zh": "Text Beta / Text", "desc": "Text Beta, Text Beta. "},
    "AGG_LIQ": {"zh": "TextRisk", "desc": "TextRiskText. "},
    "MGMT": {"zh": "Text", "desc": "Text. "},
    "PERF": {"zh": "Text", "desc": "Text. "},
}

SECTOR_MACRO_SPECS = {
    "SOXX": {"zh": "Text", "desc": "Text", "bucket": "semis_hardware"},
    "IGV": {"zh": "Text", "desc": "Text / Text", "bucket": "software_ai"},
    "XLK": {"zh": "Text", "desc": "Text", "bucket": "software_ai"},
    "XLI": {"zh": "Text", "desc": "Text", "bucket": "industrials"},
    "XLB": {"zh": "Text", "desc": "Text", "bucket": "materials_resources"},
    "XLE": {"zh": "Text", "desc": "Text", "bucket": "materials_resources"},
    "IWM": {"zh": "Text", "desc": "Text 2000 Text", "bucket": "defensive_other"},
    "TLT": {"zh": "Text", "desc": "Text", "bucket": "rates_sensitive_growth"},
    "HYG": {"zh": "TextRisk", "desc": "TextReturnText", "bucket": "defensive_other"},
}

THEME_BUCKET_LABELS = {
    "semis_hardware": "Text / Text",
    "software_ai": "Text / AI Text",
    "industrials": "Text",
    "materials_resources": "Text / Text",
    "transport_logistics": "Text",
    "rates_sensitive_growth": "Text",
    "defensive_other": "Text / Text",
}

THEME_BUCKET_RULES = {
    "semis_hardware": {
        "keywords": [
            "NVDA", "AMD", "TSM", "ASML", "LRCX", "AMAT", "KLAC", "ON", "MU", "AVGO",
            "FORMFACTOR", "SAMPLE", "MACOM", "KEYSIGHT", "KEYS", "WDC", "SANDISK",
            "WESTERN DIGITAL", "SOXX", "SMH", "SEMICONDUCTOR", "CHIP", "Text", "Text",
        ],
        "rate_sensitivity": "high",
        "cycle_sensitivity": "medium",
    },
    "software_ai": {
        "keywords": [
            "MSFT", "ADBE", "CRM", "SNOW", "PLTR", "NOW", "NET", "DDOG", "MDB",
            "IGV", "XLK", "SOFTWARE", "SAAS", "AI", "CLOUD", "Text", "Text",
        ],
        "rate_sensitivity": "high",
        "cycle_sensitivity": "low",
    },
    "industrials": {
        "keywords": [
            "HON", "ROK", "EMR", "GE", "CAT", "SAMPLE", "PH", "DE", "XLI",
            "INDUSTRIAL", "AUTOMATION", "MANUFACTURING", "Text", "AutoText", "Text",
        ],
        "rate_sensitivity": "low",
        "cycle_sensitivity": "high",
    },
    "materials_resources": {
        "keywords": [
            "FCX", "AA", "XLB", "XLE", "CPER", "GLD", "SLV", "MATERIAL", "RESOURCE",
            "COPPER", "GOLD", "OIL", "Text", "Text", "Text", "Text", "Text",
        ],
        "rate_sensitivity": "low",
        "cycle_sensitivity": "high",
    },
    "transport_logistics": {
        "keywords": ["UPS", "FDX", "IYT", "LOGISTICS", "SHIPPING", "TRANSPORT", "Text", "Text", "Text"],
        "rate_sensitivity": "low",
        "cycle_sensitivity": "high",
    },
    "rates_sensitive_growth": {
        "keywords": ["TLT", "LONG DURATION", "GROWTH", "SaaS", "RATE SENSITIVE", "Text"],
        "rate_sensitivity": "high",
        "cycle_sensitivity": "low",
    },
}

PACK_META = {
    "ff_core_pack": {
        "label": "FF Core",
        "family": "academic",
        "description": "FF5 + Text, Text. ",
    },
    "q_factor_pack": {
        "label": "Q-Factor",
        "family": "academic",
        "description": "Text, Text, Text. ",
    },
    "style_overlay_pack": {
        "label": "Style Overlay",
        "family": "overlay",
        "description": "QMJ / BAB / Liquidity / Mispricing Text. ",
    },
    "sector_macro_pack": {
        "label": "Text",
        "family": "proxy",
        "description": "Text, Text, Text, Text ETF Text. ",
    },
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_or_none(value: Any, digits: int = 3) -> Optional[float]:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _ensure_cache_dir() -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_path(name: str) -> str:
    return os.path.join(_CACHE_DIR, f"{name}.parquet")


def _cache_fresh(name: str, max_age_hours: int = 24) -> bool:
    path = _cache_path(name)
    if not os.path.exists(path):
        return False
    age = datetime.now().timestamp() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def _read_ff_csv_from_zip(url: str) -> pd.DataFrame:
    import requests
    import zipfile

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
        with zf.open(csv_name) as fh:
            raw = fh.read().decode("utf-8", errors="replace")

    lines = raw.strip().split("\n")
    start_idx = 0
    for i, line in enumerate(lines):
        parts = line.strip().split(",")
        if len(parts) >= 2 and parts[0].strip().isdigit() and len(parts[0].strip()) == 8:
            start_idx = i
            break

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        parts = lines[i].strip().split(",")
        if not parts[0].strip() or not parts[0].strip().isdigit():
            end_idx = i
            break

    header_line = lines[start_idx - 1] if start_idx > 0 else ""
    data_lines = lines[start_idx:end_idx]
    header_parts = [h.strip() for h in header_line.split(",")]
    if not header_parts[0] or header_parts[0].lower() in {"", "date"}:
        header_parts[0] = "date"

    df = pd.read_csv(io.StringIO("\n".join(data_lines)), header=None)
    if len(header_parts) == len(df.columns):
        df.columns = header_parts
    else:
        df.columns = ["date"] + [f"col_{i}" for i in range(1, len(df.columns))]

    date_col = df.columns[0]
    df[date_col] = df[date_col].astype(str).str.strip()
    df = df[df[date_col].str.len() == 8]
    df["date"] = pd.to_datetime(df[date_col], format="%Y%m%d")
    if date_col != "date":
        df = df.drop(columns=[date_col])
    df = df.set_index("date")

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0
    return df


def get_ff5_factors() -> pd.DataFrame:
    cache_name = "legacy_ff5_daily_us"
    _ensure_cache_dir()
    if _cache_fresh(cache_name):
        try:
            return pd.read_parquet(_cache_path(cache_name))
        except Exception:
            pass

    df = _read_ff_csv_from_zip(_FF5_DAILY_URL)
    col_map = {}
    for col in df.columns:
        normalized = col.strip().upper().replace(" ", "").replace("-", "")
        if normalized == "MKTRF":
            col_map[col] = "Mkt-RF"
        elif normalized in {"SMB", "HML", "RMW", "CMA", "RF"}:
            col_map[col] = normalized
    df = df.rename(columns=col_map)
    try:
        df.to_parquet(_cache_path(cache_name))
    except Exception as exc:
        logger.warning("cache legacy FF5 factors failed: %s", exc)
    return df


def get_momentum_factor() -> pd.DataFrame:
    cache_name = "legacy_mom_daily_us"
    _ensure_cache_dir()
    if _cache_fresh(cache_name):
        try:
            return pd.read_parquet(_cache_path(cache_name))
        except Exception:
            pass

    df = _read_ff_csv_from_zip(_MOM_DAILY_URL)
    cols = list(df.columns)
    if cols:
        df = df.rename(columns={cols[0]: "Mom"})[["Mom"]]

    try:
        df.to_parquet(_cache_path(cache_name))
    except Exception as exc:
        logger.warning("cache legacy momentum factors failed: %s", exc)
    return df


def _load_cached_frame(name: str, loader: Callable[[], pd.DataFrame], max_age_hours: int = 24) -> pd.DataFrame:
    _ensure_cache_dir()
    if _cache_fresh(name, max_age_hours=max_age_hours):
        try:
            return pd.read_parquet(_cache_path(name))
        except Exception:
            pass
    frame = loader()
    try:
        frame.to_parquet(_cache_path(name))
    except Exception as exc:
        logger.warning("cache write failed for %s: %s", name, exc)
    return frame


def _load_gfm_frame(name: str, builder: Callable[[], Any], max_age_hours: int = 24) -> pd.DataFrame:
    def _loader() -> pd.DataFrame:
        model = builder()
        model.load()
        df = model.to_pandas()
        df.index = pd.to_datetime(df.index)
        return df.sort_index()

    return _load_cached_frame(name, _loader, max_age_hours=max_age_hours)


def _load_ff_core_factor_data() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    status: Dict[str, Any] = {"provider": "", "success": False, "missing_factors": []}
    try:
        ff5 = _load_gfm_frame(
            "gfm_ff5_daily_us",
            lambda: __import__("getfactormodels").FamaFrenchFactors(frequency="d", model="5", region="us"),
        )
        carhart = _load_gfm_frame(
            "gfm_carhart_daily_us",
            lambda: __import__("getfactormodels").CarhartFactors(frequency="d", region="us"),
        )
        out = ff5[[c for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"] if c in ff5.columns]].copy()
        if "MOM" in carhart.columns:
            out["Mom"] = carhart["MOM"]
        elif "Mom" in carhart.columns:
            out["Mom"] = carhart["Mom"]
        status.update({"provider": "getfactormodels", "success": True})
        if "Mom" not in out.columns:
            status["missing_factors"] = ["Mom"]
        return out, status
    except Exception as exc:
        logger.warning("load ff core via getfactormodels failed: %s", exc)
        ff5 = get_ff5_factors()
        mom = get_momentum_factor()
        out = ff5[[c for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"] if c in ff5.columns]].copy()
        if not mom.empty and "Mom" in mom.columns:
            out = out.join(mom[["Mom"]], how="inner")
        status.update({"provider": "ken_french_fallback", "success": not out.empty, "fallback_reason": str(exc)})
        if "Mom" not in out.columns:
            status["missing_factors"] = ["Mom"]
        return out, status


def _load_q_factor_data() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    status: Dict[str, Any] = {"provider": "getfactormodels", "success": False, "missing_factors": []}
    try:
        df = _load_gfm_frame(
            "gfm_q_factors_daily_us",
            lambda: __import__("getfactormodels").QFactors(frequency="d"),
        )
        keep = [c for c in ["Mkt-RF", "ME", "IA", "ROE", "EG", "RF_Q"] if c in df.columns]
        out = df[keep].copy()
        status["success"] = True
        missing = [c for c in ["ME", "IA", "ROE", "EG"] if c not in out.columns]
        status["missing_factors"] = missing
        return out, status
    except Exception as exc:
        status["fallback_reason"] = str(exc)
        logger.warning("load q-factor data failed: %s", exc)
        return pd.DataFrame(), status


def _load_style_overlay_frames() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
    frames: Dict[str, pd.DataFrame] = {}
    statuses: Dict[str, Dict[str, Any]] = {}
    loaders: List[Tuple[str, Callable[[], Any], str]] = [
        ("QMJ", lambda: __import__("getfactormodels").QMJFactors(frequency="d"), "gfm_qmj_daily_us"),
        ("BAB", lambda: __import__("getfactormodels").BABFactors(frequency="d"), "gfm_bab_daily_us"),
        ("LIQ", lambda: __import__("getfactormodels").LiquidityFactors(frequency="m"), "gfm_liquidity_monthly_us"),
        ("MIS", lambda: __import__("getfactormodels").MispricingFactors(frequency="m"), "gfm_mispricing_monthly_us"),
    ]
    for key, builder, cache_name in loaders:
        try:
            frames[key] = _load_gfm_frame(cache_name, builder, max_age_hours=48)
            statuses[key] = {"provider": "getfactormodels", "success": True}
        except Exception as exc:
            logger.warning("load style overlay %s failed: %s", key, exc)
            frames[key] = pd.DataFrame()
            statuses[key] = {"provider": "getfactormodels", "success": False, "fallback_reason": str(exc)}
    return frames, statuses


def _get_stock_returns(ticker: str, days: int = 365) -> Optional[pd.Series]:
    from utils.akshare_client import _fetch_us_stock, _get_ak, _parse_date, _to_ak_code

    ak = _get_ak()
    if not ak:
        return None

    symbol, market = _to_ak_code(ticker)
    if not symbol or market != "us":
        return None

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 60)).strftime("%Y%m%d")
    try:
        df = _fetch_us_stock(ak, symbol, start_date, end_date)
    except Exception as exc:
        logger.warning("fetch US history failed for %s: %s", ticker, exc)
        return None

    if df is None or df.empty:
        return None

    date_col = "Date" if "Date" in df.columns else df.columns[0]
    close_col = "Text" if "Text" in df.columns else "close"
    if close_col not in df.columns:
        for col in df.columns:
            if "close" in col.lower() or "Text" in col:
                close_col = col
                break

    df = df.sort_values(date_col)
    df["_date"] = df[date_col].apply(_parse_date)
    df = df[df["_date"].notna()].copy()
    df = df.set_index("_date")
    df.index.name = "date"
    prices = pd.to_numeric(df[close_col], errors="coerce").dropna()
    if len(prices) < 25:
        return None

    returns = prices.pct_change().dropna()
    cutoff = datetime.now() - timedelta(days=days)
    return returns[returns.index >= cutoff]


def get_portfolio_returns(holdings: List[Dict[str, Any]], days: int = 365) -> Tuple[pd.DataFrame, Dict[str, str]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    returns_dict: Dict[str, pd.Series] = {}
    errors: Dict[str, str] = {}

    def fetch_one(holding: Dict[str, Any]) -> Tuple[str, str, Optional[pd.Series]]:
        ticker = holding.get("ticker") or holding.get("stock_id", "")
        return holding["stock_id"], ticker, _get_stock_returns(str(ticker), days=days)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_one, holding): holding for holding in holdings}
        for future in as_completed(futures):
            stock_id, ticker, ret = future.result()
            if ret is not None and len(ret) > 0:
                returns_dict[stock_id] = ret
            else:
                errors[stock_id] = f"Text {ticker} TextHistoryText. "

    if not returns_dict:
        return pd.DataFrame(), errors
    return pd.DataFrame(returns_dict).dropna(how="all"), errors


def _classify_holding(holding: Dict[str, Any]) -> Dict[str, str]:
    search_text = " ".join(str(holding.get(key) or "") for key in ("stock_id", "ticker", "stock_name")).upper()
    theme_bucket = "defensive_other"
    rate_sensitivity = "low"
    cycle_sensitivity = "low"
    for bucket, spec in THEME_BUCKET_RULES.items():
        if any(keyword.upper() in search_text for keyword in spec["keywords"]):
            theme_bucket = bucket
            rate_sensitivity = spec["rate_sensitivity"]
            cycle_sensitivity = spec["cycle_sensitivity"]
            break
    return {
        "theme_bucket": theme_bucket,
        "theme_label": THEME_BUCKET_LABELS.get(theme_bucket, "Text / Text"),
        "rate_sensitivity": rate_sensitivity,
        "cycle_sensitivity": cycle_sensitivity,
    }


def _enrich_holdings(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for holding in holdings:
        item = dict(holding)
        item.update(_classify_holding(item))
        enriched.append(item)
    return enriched


def _normalize_weights(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = sum(max(_safe_float(h.get("weight")), 0.0) for h in holdings)
    normalized: List[Dict[str, Any]] = []
    for holding in holdings:
        item = dict(holding)
        raw = max(_safe_float(item.get("weight")), 0.0)
        item["raw_weight"] = raw
        item["weight"] = raw / total if total > 0 else 0.0
        normalized.append(item)
    return normalized


def _monthly_returns(returns: pd.Series) -> pd.Series:
    if returns.empty:
        return returns
    price = (1.0 + returns).cumprod()
    monthly = price.resample("ME").last().pct_change().dropna()
    monthly.index.name = "date"
    return monthly


def _portfolio_returns_from_holdings(returns_df: pd.DataFrame, holdings: List[Dict[str, Any]]) -> pd.Series:
    weights = {h["stock_id"]: _safe_float(h.get("weight")) for h in holdings}
    cols = [c for c in returns_df.columns if c in weights]
    if not cols:
        return pd.Series(dtype=float)
    aligned = returns_df[cols].copy().dropna(how="all")
    if aligned.empty:
        return pd.Series(dtype=float)
    w = np.array([weights[c] for c in cols], dtype=float)
    if w.sum() <= 0:
        return pd.Series(dtype=float)
    w = w / w.sum()
    filled = aligned.fillna(0.0)
    portfolio = filled.mul(w, axis=1).sum(axis=1)
    portfolio.index.name = "date"
    return portfolio


def _positive_float_or_none(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _zscore_values(values: Dict[str, Optional[float]]) -> Dict[str, float]:
    valid = {key: float(value) for key, value in values.items() if value is not None and np.isfinite(float(value))}
    result = {key: 0.0 for key in values}
    if len(valid) < 2:
        return result
    series = pd.Series(valid, dtype=float)
    std = float(series.std(ddof=0))
    if std <= 0:
        return result
    scored = ((series - float(series.mean())) / std).clip(-3.0, 3.0)
    for key, value in scored.items():
        result[key] = float(value)
    return result


def _holding_characteristic_value(holding: Dict[str, Any], keys: Iterable[str], *, log_value: bool = False) -> Optional[float]:
    for key in keys:
        parsed = _positive_float_or_none(holding.get(key))
        if parsed is None:
            continue
        return float(np.log(parsed)) if log_value else parsed
    return None


def _holding_value_score(holding: Dict[str, Any]) -> Optional[float]:
    direct = _holding_characteristic_value(holding, ("book_to_price", "btp", "b_to_p", "value_score"))
    if direct is not None:
        return direct
    price_to_book = _positive_float_or_none(holding.get("price_to_book") or holding.get("pb"))
    if price_to_book:
        return 1.0 / price_to_book
    return None


def _normalize_barra_lite_category(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    chars = []
    last_was_sep = False
    for char in text.lower():
        if char.isalnum():
            chars.append(char)
            last_was_sep = False
        elif not last_was_sep:
            chars.append("_")
            last_was_sep = True
    return "".join(chars).strip("_")[:64]


def _barra_lite_category_label(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _barra_lite_sector_factor(value: Any) -> Optional[str]:
    normalized = _normalize_barra_lite_category(value)
    if not normalized:
        return None
    return f"sector_{normalized}"


def _barra_lite_factor_label(factor: str) -> Dict[str, str]:
    if factor in BARRA_LITE_FACTOR_LABELS:
        return BARRA_LITE_FACTOR_LABELS[factor]
    if factor.startswith("sector_"):
        label = factor.removeprefix("sector_").replace("_", " ").title()
        return {"zh": label, "desc": f"Sector dummy exposure for {label}."}
    return {"zh": factor, "desc": "Barra-lite derived factor."}


def _barra_lite_factor_labels(factors: Iterable[str]) -> Dict[str, Dict[str, str]]:
    labels = dict(BARRA_LITE_FACTOR_LABELS)
    for factor in factors:
        labels[factor] = _barra_lite_factor_label(factor)
    return labels


def _factor_mimicking_return(returns_df: pd.DataFrame, exposures: Dict[str, float]) -> pd.Series:
    cols = [col for col in returns_df.columns if col in exposures and abs(_safe_float(exposures.get(col))) > 1e-9]
    if not cols:
        return pd.Series(dtype=float)
    weights = np.array([_safe_float(exposures[col]) for col in cols], dtype=float)
    denom = float(np.sum(np.abs(weights)))
    if denom <= 0:
        return pd.Series(dtype=float)
    weights = weights / denom
    return returns_df[cols].fillna(0.0).mul(weights, axis=1).sum(axis=1)


def _compute_ewma_shrunk_covariance(
    factor_returns: pd.DataFrame,
    *,
    span: int = 63,
    annualization_factor: float = 252.0,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    clean = factor_returns.dropna(how="all").fillna(0.0)
    n_obs = int(len(clean))
    n_factors = int(len(clean.columns))
    if n_obs < 2 or n_factors == 0:
        return np.zeros((n_factors, n_factors), dtype=float), {
            "estimator": "ewma_shrinkage",
            "n_observations": n_obs,
            "n_factors": n_factors,
            "quality_score": 0.0,
            "quality": "Low",
            "span": span,
            "decay_lambda": 0.0,
            "shrinkage_intensity": 1.0,
            "annualization_factor": annualization_factor,
        }

    alpha = 2.0 / (span + 1.0)
    decay_lambda = 1.0 - alpha
    weights = np.array([decay_lambda ** i for i in range(n_obs - 1, -1, -1)], dtype=float)
    weights = weights / max(float(weights.sum()), 1e-12)

    values = clean.values.astype(float)
    center = np.average(values, axis=0, weights=weights)
    demeaned = values - center
    ewma_cov = (demeaned * weights[:, None]).T @ demeaned
    ewma_cov = np.nan_to_num(ewma_cov * annualization_factor, nan=0.0, posinf=0.0, neginf=0.0)

    target = np.diag(np.diag(ewma_cov))
    shrinkage_intensity = 0.15 if n_obs >= 126 else 0.35
    covariance = ((1.0 - shrinkage_intensity) * ewma_cov) + (shrinkage_intensity * target)
    covariance = (covariance + covariance.T) / 2.0

    obs_score = min(1.0, n_obs / max(float(n_factors * 30), 1.0))
    factor_penalty = max(0.5, min(1.0, 8.0 / max(float(n_factors), 1.0)))
    quality_score = round(max(0.0, min(1.0, obs_score * factor_penalty)), 3)
    quality = "High" if quality_score >= 0.75 else "Medium" if quality_score >= 0.45 else "Low"
    metadata = {
        "estimator": "ewma_shrinkage",
        "n_observations": n_obs,
        "n_factors": n_factors,
        "quality_score": quality_score,
        "quality": quality,
        "span": span,
        "decay_lambda": round(decay_lambda, 4),
        "shrinkage_intensity": round(shrinkage_intensity, 4),
        "annualization_factor": annualization_factor,
    }
    return covariance, metadata


def _barra_lite_residual_factor_names(factors: Iterable[str]) -> List[str]:
    return [
        factor for factor in factors
        if not factor.startswith("theme_") and not factor.startswith("sector_")
    ]


def _safe_sqrt_pct(value: Any) -> float:
    return round(np.sqrt(max(_safe_float(value), 0.0)) * 100, 2)


def _resolve_barra_lite_benchmark_weights(
    stock_exposures: List[Dict[str, Any]],
    benchmark_holdings: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, float], str]:
    benchmark_stock_ids = [str(row.get("stock_id") or "") for row in stock_exposures if row.get("stock_id")]
    if not benchmark_stock_ids:
        return {}, "equal_weight_active_holdings"

    alias_to_stock_id: Dict[str, str] = {}
    for row in stock_exposures:
        stock_id = str(row.get("stock_id") or "").strip()
        ticker = str(row.get("ticker") or stock_id).strip()
        if stock_id:
            alias_to_stock_id[stock_id.upper()] = stock_id
        if ticker:
            alias_to_stock_id[ticker.upper()] = stock_id

    custom_weights: Dict[str, float] = defaultdict(float)
    for row in benchmark_holdings or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("stock_id") or row.get("ticker") or "").strip().upper()
        stock_id = alias_to_stock_id.get(key)
        weight = _safe_float(row.get("weight"))
        if not stock_id or weight <= 0:
            continue
        custom_weights[stock_id] += weight

    total_custom = sum(custom_weights.values())
    if total_custom > 0:
        return {stock_id: weight / total_custom for stock_id, weight in custom_weights.items()}, "custom_benchmark_holdings"

    equal_weight = 1.0 / len(benchmark_stock_ids)
    return {stock_id: equal_weight for stock_id in benchmark_stock_ids}, "equal_weight_active_holdings"


def _barra_lite_factor_contributors(
    holdings: List[Dict[str, Any]],
    stock_exposures: List[Dict[str, Any]],
    factors: Iterable[str],
) -> Dict[str, List[Dict[str, Any]]]:
    exposure_by_stock = {row["stock_id"]: row.get("exposures") or {} for row in stock_exposures}
    contributors: Dict[str, List[Dict[str, Any]]] = {}
    for factor in factors:
        rows: List[Dict[str, Any]] = []
        for holding in holdings:
            stock_id = str(holding.get("stock_id") or "")
            exposure = _safe_float((exposure_by_stock.get(stock_id) or {}).get(factor))
            weight = _safe_float(holding.get("weight"))
            contribution = weight * exposure
            if abs(contribution) < 1e-6:
                continue
            rows.append(
                {
                    "stock_id": stock_id,
                    "ticker": holding.get("ticker") or stock_id,
                    "stock_name": holding.get("stock_name") or stock_id,
                    "weight": round(weight, 4),
                    "exposure": round(exposure, 4),
                    "contribution": round(contribution, 4),
                    "theme_bucket": holding.get("theme_bucket"),
                    "theme_label": holding.get("theme_label"),
                }
            )
        rows.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        if rows:
            contributors[factor] = rows[:5]
    return contributors


def _compute_barra_lite_exposures(
    holdings: List[Dict[str, Any]],
    returns_df: pd.DataFrame,
    factor_universe: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, float], Dict[str, List[str]]]:
    active_holdings = [holding for holding in holdings if holding.get("stock_id") in returns_df.columns]
    if not active_holdings:
        return [], {}, {"returns": [str(holding.get("stock_id") or "") for holding in holdings]}

    aligned_returns = returns_df[[holding["stock_id"] for holding in active_holdings]].dropna(how="all").fillna(0.0)
    market_series = aligned_returns.mean(axis=1)
    raw_by_stock: Dict[str, Dict[str, Optional[float]]] = {}
    missing: Dict[str, List[str]] = defaultdict(list)
    theme_factors = {f"theme_{bucket}" for bucket in THEME_BUCKET_LABELS}
    universe_group_by_stock: Dict[str, Dict[str, str]] = {}

    for item in factor_universe or []:
        ticker = str(item.get("stock_id") or item.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        universe_group_by_stock[ticker] = {
            "sector": _barra_lite_category_label(item.get("sector")),
            "industry": _barra_lite_category_label(item.get("industry")),
        }

    for holding in active_holdings:
        stock_id = holding["stock_id"]
        lookup_key = str(holding.get("stock_id") or holding.get("ticker") or "").strip().upper()
        ticker_key = str(holding.get("ticker") or holding.get("stock_id") or "").strip().upper()
        universe_group = universe_group_by_stock.get(lookup_key) or universe_group_by_stock.get(ticker_key) or {}
        stock_returns = aligned_returns[stock_id].dropna()
        beta = _run_single_factor_beta(stock_returns, market_series) or 1.0
        momentum_window = stock_returns.tail(min(len(stock_returns), 126))
        momentum = float((1.0 + momentum_window).prod() - 1.0) if len(momentum_window) >= 20 else None
        realized_vol = float(stock_returns.tail(min(len(stock_returns), 126)).std() * np.sqrt(252)) if len(stock_returns) >= 20 else None
        raw = {
            "market": beta,
            "size": _holding_characteristic_value(holding, ("market_cap", "float_market_cap", "cap"), log_value=True),
            "value": _holding_value_score(holding),
            "momentum": momentum,
            "quality": _holding_characteristic_value(holding, ("roe", "roa", "gross_margin", "quality_score")),
            "low_vol": -realized_vol if realized_vol is not None else None,
            "liquidity": _holding_characteristic_value(holding, ("dollar_volume", "avg_dollar_volume", "turnover_value"), log_value=True),
        }
        for factor, value in raw.items():
            if value is None:
                missing[factor].append(stock_id)
        theme_factor = f"theme_{holding.get('theme_bucket') or 'defensive_other'}"
        for factor in theme_factors:
            raw[factor] = 1.0 if factor == theme_factor else 0.0
        sector_label = _barra_lite_category_label(holding.get("sector") or universe_group.get("sector"))
        if not sector_label:
            sector_label = _barra_lite_category_label(holding.get("industry") or universe_group.get("industry"))
        sector_factor = _barra_lite_sector_factor(sector_label)
        if sector_factor:
            raw[sector_factor] = 1.0
        else:
            missing["sector"].append(stock_id)
        raw_by_stock[stock_id] = raw

    universe_raw_by_stock: Dict[str, Dict[str, Optional[float]]] = {}
    for item in factor_universe or []:
        ticker = str(item.get("stock_id") or item.get("ticker") or "").strip().upper()
        if not ticker or ticker in raw_by_stock:
            continue
        universe_raw_by_stock[ticker] = {
            "size": _holding_characteristic_value(item, ("market_cap", "float_market_cap", "cap"), log_value=True),
            "value": _holding_value_score(item),
            "quality": _holding_characteristic_value(item, ("roe", "roa", "gross_margin", "quality_score")),
            "liquidity": _holding_characteristic_value(item, ("dollar_volume", "avg_dollar_volume", "turnover_value"), log_value=True),
        }

    factor_names = sorted({factor for raw in raw_by_stock.values() for factor in raw})
    standardized_by_factor: Dict[str, Dict[str, float]] = {}
    for factor in factor_names:
        values = {stock_id: raw.get(factor) for stock_id, raw in raw_by_stock.items()}
        if factor == "market" or factor.startswith("theme_") or factor.startswith("sector_"):
            standardized_by_factor[factor] = {
                stock_id: round(_safe_float(value), 4)
                for stock_id, value in values.items()
            }
        else:
            universe_values = {
                stock_id: raw.get(factor)
                for stock_id, raw in universe_raw_by_stock.items()
                if factor in raw
            }
            if universe_values:
                values = {**values, **universe_values}
            standardized_by_factor[factor] = {
                stock_id: round(value, 4)
                for stock_id, value in _zscore_values(values).items()
                if stock_id in raw_by_stock
            }

    stock_rows: List[Dict[str, Any]] = []
    portfolio_exposure = {factor: 0.0 for factor in factor_names}
    holding_by_id = {holding["stock_id"]: holding for holding in active_holdings}
    for stock_id in raw_by_stock:
        holding = holding_by_id[stock_id]
        exposures = {factor: standardized_by_factor[factor].get(stock_id, 0.0) for factor in factor_names}
        weight = _safe_float(holding.get("weight"))
        for factor, exposure in exposures.items():
            portfolio_exposure[factor] += weight * exposure
        stock_rows.append(
            {
                "stock_id": stock_id,
                "ticker": holding.get("ticker") or stock_id,
                "stock_name": holding.get("stock_name") or stock_id,
                "weight": round(weight, 4),
                "theme_bucket": holding.get("theme_bucket"),
                "theme_label": holding.get("theme_label"),
                "weekly_change_pct": _round_or_none(holding.get("weekly_change_pct"), 3),
                "exposures": exposures,
            }
        )
    stock_rows.sort(key=lambda item: _safe_float(item.get("weight")), reverse=True)
    portfolio_exposure = {
        factor: round(value, 4)
        for factor, value in portfolio_exposure.items()
        if abs(value) >= 1e-6 or factor in {"market", "momentum", "low_vol"}
    }
    return stock_rows, portfolio_exposure, dict(missing)


def _compute_barra_lite_risk_model(
    holdings: List[Dict[str, Any]],
    returns_df: pd.DataFrame,
    stock_exposures: List[Dict[str, Any]],
    portfolio_exposure: Dict[str, float],
    benchmark_holdings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    portfolio_returns = _portfolio_returns_from_holdings(returns_df, holdings)
    if portfolio_returns.empty or len(portfolio_returns) < 30:
        return {
            "risk_decomposition": {"error": "insufficient_returns", "n_observations": len(portfolio_returns)},
            "factor_returns": {},
            "factor_risk_contributions": [],
        }

    stock_factor_map = {row["stock_id"]: row.get("exposures") or {} for row in stock_exposures}
    aligned_returns = returns_df[[row["stock_id"] for row in stock_exposures if row["stock_id"] in returns_df.columns]].dropna(how="all").fillna(0.0)
    factor_return_map: Dict[str, pd.Series] = {}
    for factor in portfolio_exposure:
        if factor == "market":
            factor_return_map[factor] = aligned_returns.mean(axis=1)
            continue
        exposures = {stock_id: (stock_factor_map.get(stock_id) or {}).get(factor, 0.0) for stock_id in aligned_returns.columns}
        series = _factor_mimicking_return(aligned_returns, exposures)
        if not series.empty and float(series.std()) > 0:
            factor_return_map[factor] = series

    if not factor_return_map:
        total_var = float(portfolio_returns.var() * 252)
        return {
            "risk_decomposition": {
                "total_risk_pct": round(np.sqrt(max(total_var, 0.0)) * 100, 2),
                "factor_risk_pct": 0.0,
                "specific_risk_pct": round(np.sqrt(max(total_var, 0.0)) * 100, 2),
                "factor_risk_share": 0.0,
                "specific_risk_share": 1.0,
            },
            "factor_returns": {},
            "factor_risk_contributions": [],
        }

    factor_returns = pd.DataFrame(factor_return_map).dropna(how="all").fillna(0.0)
    common_idx = portfolio_returns.index.intersection(factor_returns.index)
    portfolio_returns = portfolio_returns.loc[common_idx]
    factor_returns = factor_returns.loc[common_idx]
    factors = list(factor_returns.columns)
    covariance, covariance_model = _compute_ewma_shrunk_covariance(factor_returns)
    exposure_vec = np.array([_safe_float(portfolio_exposure.get(factor)) for factor in factors], dtype=float)
    total_var = max(float(portfolio_returns.var() * 252.0), 0.0)
    raw_factor_var = max(float(exposure_vec @ covariance @ exposure_vec.T), 0.0)
    marginal = covariance @ exposure_vec.T
    variance_contrib = exposure_vec * marginal

    weights = {holding["stock_id"]: _safe_float(holding.get("weight")) for holding in holdings}
    holding_meta = {holding["stock_id"]: holding for holding in holdings}
    residual_factors = _barra_lite_residual_factor_names(factors)
    aligned_stock_returns = aligned_returns.loc[common_idx]
    specific_rows = []
    for stock_id in aligned_stock_returns.columns:
        holding = holding_meta.get(stock_id) or {}
        stock_series = aligned_stock_returns[stock_id].fillna(0.0)
        fitted = pd.Series(0.0, index=common_idx)
        fitted_factor_count = 0
        stock_factor_exposures = stock_factor_map.get(stock_id) or {}
        for factor in residual_factors:
            exposure = _safe_float(stock_factor_exposures.get(factor))
            if abs(exposure) <= 1e-9 or factor not in factor_returns:
                continue
            fitted = fitted.add(factor_returns[factor].mul(exposure), fill_value=0.0)
            fitted_factor_count += 1
        residual = stock_series.subtract(fitted, fill_value=0.0)
        raw_var = max(float(stock_series.var() * 252.0), 0.0)
        residual_var = max(float(residual.var() * 252.0), 0.0)
        if raw_var > 0:
            residual_var = min(residual_var, raw_var)
        contribution = (weights.get(stock_id, 0.0) ** 2) * residual_var
        if contribution <= 0:
            continue
        specific_rows.append(
            {
                "stock_id": stock_id,
                "ticker": holding.get("ticker") or stock_id,
                "stock_name": holding.get("stock_name") or stock_id,
                "weight": round(weights.get(stock_id, 0.0), 4),
                "variance_contribution": round(contribution, 8),
                "variance_share": 0.0,
                "raw_variance": round(raw_var, 8),
                "residual_variance": round(residual_var, 8),
                "raw_volatility_pct": round(np.sqrt(max(raw_var, 0.0)) * 100, 2),
                "residual_volatility_pct": round(np.sqrt(max(residual_var, 0.0)) * 100, 2),
                "fitted_factor_count": fitted_factor_count,
            }
        )
    specific_rows.sort(key=lambda item: item["variance_contribution"], reverse=True)
    raw_specific_var = max(sum(_safe_float(item.get("variance_contribution")) for item in specific_rows), 0.0)
    explanatory_var = raw_factor_var + raw_specific_var
    if total_var > 0 and explanatory_var > total_var:
        scale = total_var / explanatory_var
    else:
        scale = 1.0
    factor_var = raw_factor_var * scale
    specific_var = raw_specific_var * scale
    factor_share = factor_var / total_var if total_var > 0 else 0.0
    specific_share = specific_var / total_var if total_var > 0 else 0.0
    scaled_variance_contrib = variance_contrib * scale
    scaled_specific_rows = []
    for row in specific_rows:
        scaled = dict(row)
        contribution = _safe_float(scaled.get("variance_contribution")) * scale
        scaled["variance_contribution"] = round(contribution, 8)
        scaled["variance_share"] = round(contribution / total_var, 4) if total_var > 0 else 0.0
        scaled_specific_rows.append(scaled)
    specific_rows = scaled_specific_rows
    specific_var = sum(_safe_float(item.get("variance_contribution")) for item in specific_rows)
    specific_share = specific_var / total_var if total_var > 0 else 0.0

    factor_rows = []
    for factor, contribution in zip(factors, scaled_variance_contrib):
        share = float(contribution) / total_var if total_var > 0 else 0.0
        factor_rows.append(
            {
                "factor": factor,
                "label": _barra_lite_factor_label(factor).get("zh") or factor,
                "exposure": round(_safe_float(portfolio_exposure.get(factor)), 4),
                "variance_contribution": round(float(contribution), 8),
                "variance_share": round(max(0.0, min(1.0, share)), 4),
            }
        )
    factor_rows.sort(key=lambda item: abs(_safe_float(item.get("variance_contribution"))), reverse=True)
    specific_model = {
        "estimator": "factor_residuals",
        "n_observations": int(len(common_idx)),
        "fitted_factors": residual_factors,
        "fitted_factor_count": len(residual_factors),
        "description": "Specific risk is estimated from holding return residuals after Barra-lite factor fitted returns.",
        "raw_specific_variance": round(raw_specific_var, 8),
        "variance_scale": round(scale, 6),
    }
    benchmark_weights, benchmark_model = _resolve_barra_lite_benchmark_weights(stock_exposures, benchmark_holdings)
    benchmark_weights = {stock_id: weight for stock_id, weight in benchmark_weights.items() if stock_id in aligned_stock_returns.columns}
    benchmark_stock_ids = list(benchmark_weights)
    if not benchmark_weights and len(aligned_stock_returns.columns) > 0:
        fallback_weight = 1.0 / len(aligned_stock_returns.columns)
        benchmark_weights = {stock_id: fallback_weight for stock_id in aligned_stock_returns.columns}
        benchmark_stock_ids = list(benchmark_weights)
        benchmark_model = "equal_weight_active_holdings"
    benchmark_exposure: Dict[str, float] = {}
    all_exposure_factors = sorted(set(portfolio_exposure) | {factor for exposure in stock_factor_map.values() for factor in exposure})
    for factor in all_exposure_factors:
        benchmark_exposure[factor] = round(
            sum(benchmark_weights.get(stock_id, 0.0) * _safe_float((stock_factor_map.get(stock_id) or {}).get(factor)) for stock_id in benchmark_stock_ids),
            4,
        )
    active_exposure = {
        factor: round(_safe_float(portfolio_exposure.get(factor)) - _safe_float(benchmark_exposure.get(factor)), 4)
        for factor in all_exposure_factors
        if abs(_safe_float(portfolio_exposure.get(factor)) - _safe_float(benchmark_exposure.get(factor))) >= 1e-6
    }
    active_vec = np.array([_safe_float(active_exposure.get(factor)) for factor in factors], dtype=float)
    raw_active_factor_var = max(float(active_vec @ covariance @ active_vec.T), 0.0) if len(active_vec) else 0.0
    active_marginal = covariance @ active_vec.T if len(active_vec) else np.array([])
    active_contrib = active_vec * active_marginal if len(active_vec) else np.array([])
    benchmark_returns = aligned_stock_returns.mul(pd.Series(benchmark_weights), axis=1).sum(axis=1) if benchmark_weights else pd.Series(dtype=float)
    active_returns = portfolio_returns.subtract(benchmark_returns, fill_value=0.0)
    tracking_var = max(float(active_returns.var() * 252.0), 0.0) if not active_returns.empty else 0.0
    active_factor_var = min(raw_active_factor_var, tracking_var) if tracking_var > 0 else raw_active_factor_var
    active_scale = (active_factor_var / raw_active_factor_var) if raw_active_factor_var > 0 else 1.0
    active_contrib = active_contrib * active_scale if len(active_contrib) else active_contrib
    active_factor_rows = []
    active_denom = tracking_var if tracking_var > 0 else max(float(np.sum(np.abs(active_contrib))), 1e-12)
    for factor, contribution in zip(factors, active_contrib):
        if abs(float(contribution)) < 1e-12 and abs(_safe_float(active_exposure.get(factor))) < 1e-9:
            continue
        active_factor_rows.append(
            {
                "factor": factor,
                "label": _barra_lite_factor_label(factor).get("zh") or factor,
                "active_exposure": round(_safe_float(active_exposure.get(factor)), 4),
                "variance_contribution": round(float(contribution), 8),
                "variance_share": round(float(contribution) / active_denom, 4),
            }
        )
    active_factor_rows.sort(key=lambda item: abs(_safe_float(item.get("variance_contribution"))), reverse=True)
    active_holding_contributors: Dict[str, List[Dict[str, Any]]] = {}
    top_active_exposures = []
    active_stock_ids = sorted(set(weights) | set(benchmark_weights))
    for factor, exposure in sorted(active_exposure.items(), key=lambda item: abs(_safe_float(item[1])), reverse=True):
        contributors = []
        for stock_id in active_stock_ids:
            stock_exposure = _safe_float((stock_factor_map.get(stock_id) or {}).get(factor))
            portfolio_weight = _safe_float(weights.get(stock_id))
            benchmark_weight = _safe_float(benchmark_weights.get(stock_id))
            active_weight = portfolio_weight - benchmark_weight
            contribution = active_weight * stock_exposure
            if abs(contribution) < 1e-9 and abs(active_weight) < 1e-9:
                continue
            holding = holding_meta.get(stock_id) or next((row for row in stock_exposures if row.get("stock_id") == stock_id), {})
            contributors.append(
                {
                    "stock_id": stock_id,
                    "ticker": holding.get("ticker") or stock_id,
                    "stock_name": holding.get("stock_name") or stock_id,
                    "portfolio_weight": round(portfolio_weight, 4),
                    "benchmark_weight": round(benchmark_weight, 4),
                    "active_weight": round(active_weight, 4),
                    "exposure": round(stock_exposure, 4),
                    "contribution": round(contribution, 4),
                }
            )
        contributors.sort(key=lambda item: abs(_safe_float(item.get("contribution"))), reverse=True)
        active_holding_contributors[factor] = contributors[:5]
        top_active_exposures.append(
            {
                "factor": factor,
                "label": _barra_lite_factor_label(factor).get("zh") or factor,
                "active_exposure": round(_safe_float(exposure), 4),
                "portfolio_exposure": round(_safe_float(portfolio_exposure.get(factor)), 4),
                "benchmark_exposure": round(_safe_float(benchmark_exposure.get(factor)), 4),
                "contributors": contributors[:3],
            }
        )
    def _active_exposure_display_priority(item: Dict[str, Any]) -> Tuple[int, float, float, str]:
        factor = str(item.get("factor") or "")
        if factor.startswith("sector_"):
            factor_type_priority = 3
        elif factor.startswith("theme_"):
            factor_type_priority = 2
        else:
            factor_type_priority = 1
        return (
            factor_type_priority,
            abs(_safe_float(item.get("active_exposure"))),
            max((_safe_float(row.get("contribution")) for row in item.get("contributors") or []), default=0.0),
            factor,
        )

    top_active_exposures.sort(
        key=_active_exposure_display_priority,
        reverse=True,
    )
    active_risk = {
        "benchmark_model": benchmark_model,
        "benchmark_weight_count": len(benchmark_stock_ids),
        "benchmark_weights": {stock_id: round(weight, 4) for stock_id, weight in benchmark_weights.items()},
        "benchmark_exposure": benchmark_exposure,
        "active_exposure": active_exposure,
        "holding_contributors": active_holding_contributors,
        "top_active_exposures": top_active_exposures[:8],
        "tracking_variance": round(tracking_var, 8),
        "tracking_error_pct": _safe_sqrt_pct(tracking_var),
        "active_factor_variance": round(active_factor_var, 8),
        "raw_active_factor_variance": round(raw_active_factor_var, 8),
        "active_factor_risk_pct": _safe_sqrt_pct(active_factor_var),
        "active_factor_risk_share": round(min(1.0, active_factor_var / tracking_var), 4) if tracking_var > 0 else 0.0,
        "factor_risk_contributions": active_factor_rows[:8],
        "description": "Benchmark is equal-weight across active holdings until explicit benchmark weights are available.",
    }

    return {
        "risk_decomposition": {
            "total_variance": round(total_var, 8),
            "factor_variance": round(factor_var, 8),
            "raw_factor_variance": round(raw_factor_var, 8),
            "specific_variance": round(specific_var, 8),
            "raw_specific_variance": round(raw_specific_var, 8),
            "unexplained_variance": round(max(total_var - factor_var - specific_var, 0.0), 8),
            "variance_scale": round(scale, 6),
            "total_risk_pct": round(np.sqrt(max(total_var, 0.0)) * 100, 2),
            "factor_risk_pct": round(np.sqrt(max(factor_var, 0.0)) * 100, 2),
            "specific_risk_pct": round(np.sqrt(max(specific_var, 0.0)) * 100, 2),
            "factor_risk_share": round(min(1.0, max(0.0, factor_share)), 4),
            "specific_risk_share": round(min(1.0, max(0.0, specific_share)), 4),
            "factor_risk_contributions": factor_rows,
            "specific_risk_contributions": specific_rows[:8],
            "specific_risk_model": specific_model,
            "covariance_model": covariance_model,
            "active_risk": active_risk,
        },
        "factor_returns": {
            factor: {
                "annualized_return": round(float(factor_returns[factor].mean() * 252.0), 4),
                "annualized_volatility": round(float(factor_returns[factor].std() * np.sqrt(252.0)), 4),
            }
            for factor in factors
        },
        "factor_risk_contributions": factor_rows,
    }


# ---------------------------------------------------------------------------
# Shared fast OLS kernel (numpy normal equations)
# ---------------------------------------------------------------------------

def _fast_ols_betas(y: np.ndarray, X: np.ndarray) -> Optional[np.ndarray]:
    """Compute OLS betas via (X'X)^-1 X'y. Returns None if singular."""
    try:
        XtX = X.T @ X
        Xty = X.T @ y
        return np.linalg.solve(XtX, Xty)
    except np.linalg.LinAlgError:
        return None


# ---------------------------------------------------------------------------
# Risk metrics engine
# ---------------------------------------------------------------------------

def _compute_risk_metrics(
    portfolio_returns: pd.Series,
    rf_series: Optional[pd.Series] = None,
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """VaR, CVaR, max drawdown, annualized volatility, Sharpe ratio."""
    if len(portfolio_returns) < 30:
        return {"error": "Text", "n_observations": len(portfolio_returns)}

    returns = portfolio_returns.dropna()
    n = len(returns)

    # VaR (historical simulation)
    var_pct = float(np.percentile(returns, (1 - confidence) * 100))
    var_95 = round(abs(var_pct) * 100, 2)

    # CVaR (expected shortfall)
    tail = returns[returns <= var_pct]
    cvar_95 = round(abs(float(tail.mean())) * 100, 2) if len(tail) > 0 else var_95

    # Max drawdown
    cum = (1 + returns).cumprod()
    running_max = cum.cummax()
    drawdowns = (cum - running_max) / running_max
    max_dd = round(abs(float(drawdowns.min())) * 100, 2)

    # Annualized volatility
    vol = round(float(returns.std()) * np.sqrt(252) * 100, 2)

    # Sharpe ratio
    if rf_series is not None:
        rf_aligned = rf_series.reindex(returns.index).fillna(0.0)
        excess = returns - rf_aligned
    else:
        excess = returns
    excess_mean = float(excess.mean())
    excess_std = float(excess.std())
    sharpe = round(excess_mean / excess_std * np.sqrt(252), 2) if excess_std > 0 else 0.0

    # Severity levels for frontend conditional coloring
    def _severity(value: float, green_lt: float, yellow_lt: float) -> str:
        if value < green_lt:
            return "green"
        elif value < yellow_lt:
            return "yellow"
        return "red"

    def _severity_sharpe(value: float) -> str:
        if value > 1.0:
            return "green"
        elif value >= 0.5:
            return "yellow"
        return "red"

    return {
        "var_95": var_95,
        "var_95_severity": _severity(var_95, 3, 5),
        "cvar_95": cvar_95,
        "cvar_95_severity": _severity(cvar_95, 5, 8),
        "max_drawdown": max_dd,
        "max_drawdown_severity": _severity(max_dd, 10, 20),
        "volatility": vol,
        "volatility_severity": _severity(vol, 15, 25),
        "sharpe": sharpe,
        "sharpe_severity": _severity_sharpe(sharpe),
        "n_observations": n,
    }


# ---------------------------------------------------------------------------
# Correlation matrix
# ---------------------------------------------------------------------------

def _compute_correlation_matrix(
    returns_df: pd.DataFrame,
    holdings: List[Dict[str, Any]],
    max_holdings: int = 15,
) -> Dict[str, Any]:
    """N×N correlation matrix for top holdings by weight."""
    sorted_h = sorted(holdings, key=lambda h: _safe_float(h.get("weight")), reverse=True)
    selected = [h["stock_id"] for h in sorted_h[:max_holdings] if h["stock_id"] in returns_df.columns]
    truncated = len(sorted_h) > max_holdings

    if len(selected) < 2:
        return {"matrix": [], "labels": selected, "truncated": False, "n_holdings": len(selected)}

    sub = returns_df[selected].dropna(how="all")
    try:
        corr = sub.corr()
    except Exception:
        # Degrade to diagonal
        corr = pd.DataFrame(np.eye(len(selected)), index=selected, columns=selected)
        logger.warning("Correlation computation failed, degraded to identity matrix.")

    matrix = []
    for row_label in selected:
        row = []
        for col_label in selected:
            row.append(round(float(corr.loc[row_label, col_label]), 4))
        matrix.append(row)

    return {
        "matrix": matrix,
        "labels": selected,
        "truncated": truncated,
        "n_holdings": len(sorted_h),
    }


# ---------------------------------------------------------------------------
# Stress testing
# ---------------------------------------------------------------------------

_STRESS_SCENARIOS = [
    {
        "scenario": "Text +100bp",
        "description": "Text100Text, TLTText8%",
        "factors": [("TLT", -0.08)],
    },
    {
        "scenario": "Text -20%",
        "description": "Text20%",
        "factors": [("Mkt-RF", -0.20)],
    },
    {
        "scenario": "Text",
        "description": "Text30%",
        "factors": [("SOXX", -0.30)],
    },
    {
        "scenario": "Text",
        "description": "TextReturnText10%, Text",
        "factors": [("HYG", -0.10)],
    },
    {
        "scenario": "Text",
        "description": "Text10%Text(TLTText4%)",
        "factors": [("Mkt-RF", -0.10), ("TLT", -0.04)],
    },
]


def _compute_stress_scenarios(
    primary_betas: Dict[str, float],
    sector_betas: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Compute portfolio loss under each stress scenario."""
    all_betas = {**sector_betas}
    if "Mkt-RF" in primary_betas:
        all_betas["Mkt-RF"] = primary_betas["Mkt-RF"]

    results = []
    for scenario in _STRESS_SCENARIOS:
        loss = 0.0
        missing = False
        for factor, shock in scenario["factors"]:
            beta = all_betas.get(factor)
            if beta is None:
                missing = True
                break
            loss += beta * shock
        if missing:
            results.append({
                "scenario": scenario["scenario"],
                "description": scenario["description"],
                "expected_loss_pct": None,
                "missing_factor": True,
            })
            continue
        loss_pct = round(loss * 100, 2)
        results.append({
            "scenario": scenario["scenario"],
            "description": scenario["description"],
            "expected_loss_pct": loss_pct,
            "missing_factor": False,
        })
    return results


# ---------------------------------------------------------------------------
# Rolling factor exposures
# ---------------------------------------------------------------------------

def _compute_rolling_exposures(
    portfolio_returns: pd.Series,
    factor_df: pd.DataFrame,
    factor_columns: List[str],
    rf_column: Optional[str],
    window: int = 63,
    step: int = 5,
) -> List[Dict[str, Any]]:
    """Rolling window regression using fast numpy OLS."""
    cols = [c for c in factor_columns if c in factor_df.columns]
    if not cols:
        return []

    common_idx = portfolio_returns.index.intersection(factor_df.index)
    if len(common_idx) < window:
        return []

    y_full = portfolio_returns.loc[common_idx].astype(float)
    if rf_column and rf_column in factor_df.columns:
        rf = factor_df.loc[common_idx, rf_column].astype(float).fillna(0.0)
        y_full = y_full - rf

    X_full = factor_df.loc[common_idx, cols].astype(float)
    dates = common_idx.sort_values()

    results = []
    for start in range(0, len(dates) - window + 1, step):
        end = start + window
        idx = dates[start:end]
        y_win = y_full.loc[idx].values
        X_win = X_full.loc[idx].values

        # NaN ratio check
        nan_ratio = np.isnan(y_win).sum() / len(y_win)
        if nan_ratio > 0.2:
            continue

        # Clean NaNs
        mask = ~(np.isnan(y_win) | np.isnan(X_win).any(axis=1))
        y_clean = y_win[mask]
        X_clean = X_win[mask]
        if len(y_clean) < window * 0.5:
            continue

        # Add constant
        X_c = np.column_stack([np.ones(len(X_clean)), X_clean])
        betas = _fast_ols_betas(y_clean, X_c)
        if betas is None:
            continue

        exposures = {}
        unreliable = False
        for i, col in enumerate(cols):
            b = float(betas[i + 1])  # skip constant
            if abs(b) >= 10:
                unreliable = True
            exposures[col] = round(b, 4)

        results.append({
            "date": str(idx[-1].date()) if hasattr(idx[-1], "date") else str(idx[-1]),
            "exposures": exposures,
            "unreliable": unreliable,
        })

    return results


# ---------------------------------------------------------------------------
# Marginal risk contribution
# ---------------------------------------------------------------------------

def _compute_marginal_risk(
    returns_df: pd.DataFrame,
    holdings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-holding marginal risk contribution."""
    stock_ids = [h["stock_id"] for h in holdings if h["stock_id"] in returns_df.columns]
    if len(stock_ids) < 1:
        return []

    weights_map = {h["stock_id"]: _safe_float(h.get("weight")) for h in holdings}
    w = np.array([weights_map.get(s, 0.0) for s in stock_ids], dtype=float)
    w_sum = w.sum()
    if w_sum <= 0:
        return []
    w = w / w_sum

    sub = returns_df[stock_ids].dropna(how="all").fillna(0.0)
    if len(sub) < 10:
        return []

    try:
        cov = np.cov(sub.values, rowvar=False)
        if cov.ndim == 0:
            cov = np.array([[float(cov)]])
    except np.linalg.LinAlgError:
        # Degrade to diagonal
        cov = np.diag(sub.var().values)
        logger.warning("Covariance singular, degraded to diagonal.")

    port_var = float(w @ cov @ w)
    port_std = np.sqrt(port_var) if port_var > 0 else 1e-10

    # MCR_i = (cov @ w)_i / port_std
    mcr = (cov @ w) / port_std
    risk_contrib = w * mcr  # weighted MCR
    total_rc = risk_contrib.sum()

    # Theme bucket lookup
    bucket_map = {}
    for h in holdings:
        classification = _classify_holding(h)
        bucket_map[h["stock_id"]] = classification["theme_bucket"]

    results = []
    for i, sid in enumerate(stock_ids):
        pct = round(float(risk_contrib[i] / total_rc * 100), 2) if total_rc != 0 else 0.0
        results.append({
            "stock_id": sid,
            "stock_name": next((h.get("stock_name", sid) for h in holdings if h["stock_id"] == sid), sid),
            "weight": round(float(w[i]) * 100, 2),
            "risk_contribution": round(float(risk_contrib[i]), 6),
            "pct_contribution": pct,
            "theme_bucket": bucket_map.get(sid, "defensive_other"),
        })

    results.sort(key=lambda x: abs(x["pct_contribution"]), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Weekly review risk summary
# ---------------------------------------------------------------------------

def _build_risk_summary_for_weekly(
    risk_metrics: Dict[str, Any],
    stress_results: List[Dict[str, Any]],
    drift_alerts: List[str],
) -> str:
    """Generate a concise risk summary text for weekly review integration."""
    if risk_metrics.get("error"):
        return ""

    parts = []
    var = risk_metrics.get("var_95")
    cvar = risk_metrics.get("cvar_95")
    dd = risk_metrics.get("max_drawdown")
    vol = risk_metrics.get("volatility")
    sharpe = risk_metrics.get("sharpe")

    if var is not None:
        parts.append(f"VaR(95%)={var}%")
    if cvar is not None:
        parts.append(f"CVaR={cvar}%")
    if dd is not None:
        parts.append(f"Text={dd}%")
    if vol is not None:
        parts.append(f"Text={vol}%")
    if sharpe is not None:
        parts.append(f"Sharpe={sharpe}")

    summary = "RiskText: " + ", ".join(parts) + ". " if parts else ""

    # Worst stress scenario
    valid_stress = [s for s in stress_results if s.get("expected_loss_pct") is not None]
    if valid_stress:
        worst = min(valid_stress, key=lambda s: s["expected_loss_pct"])
        if worst["expected_loss_pct"] < -5:
            summary += f" Text: {worst['scenario']}(Text{worst['expected_loss_pct']}%). "

    if drift_alerts:
        summary += " Text: " + "; ".join(drift_alerts[:3]) + ". "

    return summary.strip()


def _run_ols_regression(
    returns: pd.Series,
    factor_data: pd.DataFrame,
    factor_columns: List[str],
    rf_column: Optional[str],
    annualization: int,
) -> Dict[str, Any]:
    if returns.empty or factor_data.empty:
        return {"error": "Text. "}

    cols = [c for c in factor_columns if c in factor_data.columns]
    if not cols:
        return {"error": "Text. "}

    common_idx = returns.index.intersection(factor_data.index)
    if len(common_idx) < max(18, len(cols) * 4):
        return {"error": f"Text, Text {len(common_idx)} Text. ", "n_observations": len(common_idx)}

    y = returns.loc[common_idx].astype(float)
    if rf_column and rf_column in factor_data.columns:
        y = y - factor_data.loc[common_idx, rf_column].astype(float).fillna(0.0)

    X = factor_data.loc[common_idx, cols].astype(float)
    X = sm.add_constant(X, has_constant="add")
    y = y.reindex(X.index).dropna()
    X = X.reindex(y.index).dropna()
    if len(X) < max(18, len(cols) * 4):
        return {"error": f"Text, Text {len(X)} Text. ", "n_observations": len(X)}

    y = y.reindex(X.index)
    try:
        lags = max(1, min(5, len(X) // 20))
        fit = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    except Exception as exc:
        logger.warning("OLS with HAC failed, fallback to plain OLS: %s", exc)
        fit = sm.OLS(y, X).fit()

    params = fit.params.to_dict()
    tvalues = fit.tvalues.to_dict()
    pvalues = fit.pvalues.to_dict()
    betas = {col: round(float(params.get(col, 0.0)), 4) for col in cols}
    alpha_daily = float(params.get("const", 0.0))
    alpha = round(alpha_daily * annualization, 4)
    return {
        "alpha": alpha,
        "alpha_raw": round(alpha_daily, 6),
        "betas": betas,
        "r_squared": round(float(getattr(fit, "rsquared", 0.0)), 4),
        "adj_r_squared": round(float(getattr(fit, "rsquared_adj", 0.0)), 4),
        "t_stats": {("alpha" if k == "const" else k): round(float(v), 2) for k, v in tvalues.items()},
        "p_values": {("alpha" if k == "const" else k): round(float(v), 4) for k, v in pvalues.items()},
        "n_observations": int(len(X)),
        "covariance": "HAC",
    }


def _build_significance_summary(
    exposures: Dict[str, float],
    p_values: Dict[str, float],
    labels: Dict[str, Dict[str, str]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for factor, value in exposures.items():
        p_value = _safe_float(p_values.get(factor), 1.0)
        if p_value <= 0.05:
            confidence = "Text"
        elif p_value <= 0.15:
            confidence = "Text"
        else:
            confidence = "Text"
        rows.append(
            {
                "factor": factor,
                "label": (labels.get(factor) or {}).get("zh", factor),
                "value": round(_safe_float(value), 4),
                "p_value": round(p_value, 4),
                "confidence": confidence,
            }
        )
    rows.sort(key=lambda item: abs(item["value"]), reverse=True)
    return rows


def _window_stability(window_results: Dict[int, Dict[str, Any]]) -> Tuple[float, Dict[str, float]]:
    factor_values: Dict[str, List[float]] = defaultdict(list)
    factor_signs: Dict[str, List[int]] = defaultdict(list)
    for result in window_results.values():
        betas = (result or {}).get("betas") or {}
        for factor, value in betas.items():
            fvalue = _safe_float(value)
            factor_values[factor].append(fvalue)
            factor_signs[factor].append(1 if fvalue > 0 else -1 if fvalue < 0 else 0)

    if not factor_values:
        return 0.0, {}

    ranges: Dict[str, float] = {}
    factor_scores: List[float] = []
    for factor, values in factor_values.items():
        ranges[factor] = round(max(values) - min(values), 4)
        signs = factor_signs[factor]
        non_zero = [s for s in signs if s != 0]
        sign_consistency = 1.0 if len(set(non_zero)) <= 1 else 0.0
        dispersion = max(values) - min(values)
        magnitude = max(abs(v) for v in values) or 1.0
        dispersion_score = max(0.0, 1.0 - min(dispersion / max(magnitude, 0.1), 1.0))
        factor_scores.append(0.6 * sign_consistency + 0.4 * dispersion_score)
    return round(float(np.mean(factor_scores)), 3), ranges


def _build_factor_contributors(
    holdings: List[Dict[str, Any]],
    stock_map: Dict[str, Dict[str, Any]],
    factors: Iterable[str],
    *,
    use_proxy: bool,
) -> Dict[str, List[Dict[str, Any]]]:
    contributors: Dict[str, List[Dict[str, Any]]] = {}
    for factor in factors:
        rows: List[Dict[str, Any]] = []
        for holding in holdings:
            stock = stock_map.get(holding["stock_id"]) or {}
            beta_map = stock.get("proxy_betas") if use_proxy else stock.get("betas")
            beta = _safe_float((beta_map or {}).get(factor), 0.0)
            weight = _safe_float(holding.get("weight"), 0.0)
            contribution = beta * weight
            if abs(contribution) < 1e-6:
                continue
            rows.append(
                {
                    "stock_id": holding["stock_id"],
                    "ticker": holding.get("ticker") or holding["stock_id"],
                    "stock_name": holding.get("stock_name") or holding["stock_id"],
                    "weight": round(weight, 4),
                    "beta": round(beta, 4),
                    "contribution": round(contribution, 4),
                    "theme_bucket": holding.get("theme_bucket"),
                    "theme_label": holding.get("theme_label"),
                }
            )
        rows.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        if rows:
            contributors[factor] = rows[:5]
    return contributors


def _fit_pack(
    *,
    pack_key: str,
    holdings: List[Dict[str, Any]],
    returns_df: pd.DataFrame,
    factor_df: pd.DataFrame,
    factor_columns: List[str],
    rf_column: Optional[str],
    windows: List[int],
    annualization: int,
    labels: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    portfolio_returns = _portfolio_returns_from_holdings(returns_df, holdings)
    pack_result = {
        "label": PACK_META[pack_key]["label"],
        "family": PACK_META[pack_key]["family"],
        "description": PACK_META[pack_key]["description"],
        "portfolio_exposure": {},
        "stock_exposures": [],
        "window_results": {},
        "rolling_beta_range": {},
        "stability_score": 0.0,
        "significance_summary": [],
        "top_holding_contributors": {},
        "alpha": 0.0,
        "r_squared": 0.0,
        "adj_r_squared": 0.0,
        "n_observations": 0,
        "errors": {},
    }
    if portfolio_returns.empty or factor_df.empty:
        pack_result["error"] = "TextReturnText. "
        return pack_result

    sorted_windows = sorted(set(int(w) for w in windows if int(w) > 0))
    main_window = max(sorted_windows) if sorted_windows else 252

    for window in sorted_windows:
        res = _run_ols_regression(
            portfolio_returns.tail(window),
            factor_df,
            factor_columns=factor_columns,
            rf_column=rf_column,
            annualization=annualization,
        )
        pack_result["window_results"][window] = res

    main_result = pack_result["window_results"].get(main_window) or {}
    if main_result.get("error"):
        pack_result["error"] = main_result["error"]
        return pack_result

    pack_result["main_window"] = main_window
    pack_result["portfolio_exposure"] = main_result.get("betas") or {}
    pack_result["alpha"] = _safe_float(main_result.get("alpha"))
    pack_result["r_squared"] = _safe_float(main_result.get("r_squared"))
    pack_result["adj_r_squared"] = _safe_float(main_result.get("adj_r_squared"))
    pack_result["n_observations"] = int(main_result.get("n_observations") or 0)
    pack_result["t_stats"] = main_result.get("t_stats") or {}
    pack_result["p_values"] = main_result.get("p_values") or {}
    pack_result["significance_summary"] = _build_significance_summary(
        pack_result["portfolio_exposure"],
        pack_result["p_values"],
        labels,
    )

    stability_score, beta_ranges = _window_stability(pack_result["window_results"])
    pack_result["rolling_beta_range"] = beta_ranges
    pack_result["stability_score"] = stability_score

    stock_exposures: List[Dict[str, Any]] = []
    for holding in holdings:
        stock_id = holding["stock_id"]
        stock_returns = returns_df.get(stock_id)
        if stock_returns is None:
            pack_result["errors"][stock_id] = "TextReturnText. "
            continue
        res = _run_ols_regression(
            stock_returns.dropna().tail(main_window),
            factor_df,
            factor_columns=factor_columns,
            rf_column=rf_column,
            annualization=annualization,
        )
        if res.get("error"):
            pack_result["errors"][stock_id] = str(res["error"])
            continue
        row = {
            "stock_id": stock_id,
            "ticker": holding.get("ticker") or stock_id,
            "stock_name": holding.get("stock_name") or stock_id,
            "weight": round(_safe_float(holding.get("weight")), 4),
            "theme_bucket": holding.get("theme_bucket"),
            "theme_label": holding.get("theme_label"),
            "weekly_change_pct": _round_or_none(holding.get("weekly_change_pct"), 3),
            **res,
        }
        stock_exposures.append(row)
    stock_exposures.sort(key=lambda item: _safe_float(item.get("weight")), reverse=True)
    pack_result["stock_exposures"] = stock_exposures
    pack_result["top_holding_contributors"] = _build_factor_contributors(
        holdings,
        {row["stock_id"]: row for row in stock_exposures},
        pack_result["portfolio_exposure"].keys(),
        use_proxy=False,
    )
    return pack_result


def _run_single_factor_beta(asset_returns: pd.Series, factor_returns: pd.Series) -> Optional[float]:
    common_idx = asset_returns.index.intersection(factor_returns.index)
    if len(common_idx) < 20:
        return None
    x = factor_returns.loc[common_idx].astype(float).values
    y = asset_returns.loc[common_idx].astype(float).values
    var_x = np.var(x)
    if var_x <= 0:
        return None
    return float(np.cov(y, x, ddof=0)[0, 1] / var_x)


def _compute_sector_macro_exposures(
    holdings: List[Dict[str, Any]],
    returns_df: pd.DataFrame,
    days: int,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Dict[str, str]]:
    errors: Dict[str, str] = {}
    proxy_returns: Dict[str, pd.Series] = {}
    for ticker in SECTOR_MACRO_SPECS:
        series = _get_stock_returns(ticker, days=days)
        if series is None or series.empty:
            errors[ticker] = f"Text {ticker} TextMarket Data. "
            continue
        proxy_returns[ticker] = series
    if not proxy_returns:
        return {}, {}, errors

    proxy_df = pd.DataFrame(proxy_returns).dropna(how="all")
    portfolio_returns = _portfolio_returns_from_holdings(returns_df, holdings)
    portfolio_exposures: Dict[str, float] = {}
    details: Dict[str, Dict[str, Any]] = {}

    for factor, spec in SECTOR_MACRO_SPECS.items():
        if factor not in proxy_df.columns:
            continue
        beta = _run_single_factor_beta(portfolio_returns, proxy_df[factor].dropna())
        if beta is None:
            continue
        rows: List[Dict[str, Any]] = []
        for holding in holdings:
            stock_ret = returns_df.get(holding["stock_id"])
            if stock_ret is None:
                continue
            stock_beta = _run_single_factor_beta(stock_ret.dropna(), proxy_df[factor].dropna())
            if stock_beta is None:
                continue
            rows.append(
                {
                    "stock_id": holding["stock_id"],
                    "ticker": holding.get("ticker") or holding["stock_id"],
                    "stock_name": holding.get("stock_name") or holding["stock_id"],
                    "weight": round(_safe_float(holding.get("weight")), 4),
                    "beta": round(stock_beta, 4),
                    "contribution": round(stock_beta * _safe_float(holding.get("weight")), 4),
                }
            )
        rows.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        portfolio_exposures[factor] = round(beta, 4)
        details[factor] = {
            "factor": factor,
            "label": spec["zh"],
            "desc": spec["desc"],
            "beta": round(beta, 4),
            "top_holding_contributors": rows[:5],
        }
    return portfolio_exposures, details, errors


def _compute_style_overlays(
    holdings: List[Dict[str, Any]],
    returns_df: pd.DataFrame,
    overlay_frames: Dict[str, pd.DataFrame],
) -> Dict[str, Dict[str, Any]]:
    portfolio_returns = _portfolio_returns_from_holdings(returns_df, holdings)
    monthly_portfolio = _monthly_returns(portfolio_returns)
    overlays: Dict[str, Dict[str, Any]] = {}

    specs = [
        ("QMJ", overlay_frames.get("QMJ"), ["Mkt-RF", "QMJ"], "RF_AQR", 252, "QMJ"),
        ("BAB", overlay_frames.get("BAB"), ["Mkt-RF", "BAB"], "RF_AQR", 252, "BAB"),
        ("Liquidity", overlay_frames.get("LIQ"), ["AGG_LIQ"], None, 12, "AGG_LIQ"),
        ("Mispricing", overlay_frames.get("MIS"), ["Mkt-RF", "MGMT", "PERF"], "RF", 12, "MGMT"),
    ]
    for key, frame, columns, rf_col, annualization, headline_factor in specs:
        if frame is None or frame.empty:
            continue
        target_returns = portfolio_returns if annualization == 252 else monthly_portfolio
        result = _run_ols_regression(
            target_returns,
            frame,
            factor_columns=columns,
            rf_column=rf_col,
            annualization=annualization,
        )
        if result.get("error"):
            overlays[key] = {
                "label": PACK_META["style_overlay_pack"]["label"],
                "key": key,
                "error": result["error"],
            }
            continue
        betas = result.get("betas") or {}
        display_value = _safe_float(betas.get(headline_factor))
        label_map = STYLE_OVERLAY_LABELS.get(headline_factor) or {"zh": key, "desc": ""}
        overlays[key] = {
            "key": key,
            "label": label_map["zh"],
            "desc": label_map["desc"],
            "portfolio_exposure": betas,
            "headline_factor": headline_factor,
            "headline_value": round(display_value, 4),
            "alpha": _safe_float(result.get("alpha")),
            "r_squared": _safe_float(result.get("r_squared")),
            "n_observations": int(result.get("n_observations") or 0),
            "t_stats": result.get("t_stats") or {},
            "p_values": result.get("p_values") or {},
        }
    return overlays


def _build_bucket_exposures(
    holdings: List[Dict[str, Any]],
    stock_map: Dict[str, Dict[str, Any]],
    factor_order: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for holding in holdings:
        grouped[str(holding.get("theme_bucket") or "defensive_other")].append(holding)

    buckets: Dict[str, Dict[str, Any]] = {}
    for bucket, items in grouped.items():
        total_weight = sum(_safe_float(item.get("weight")) for item in items)
        weighted_return = 0.0
        academic_exposure: Dict[str, float] = {}
        for item in items:
            weighted_return += _safe_float(item.get("weekly_change_pct")) * _safe_float(item.get("weight"))
        if total_weight > 0:
            weighted_return = weighted_return / total_weight
        for factor in factor_order:
            used_weight = 0.0
            weighted_beta = 0.0
            for item in items:
                stock = stock_map.get(item["stock_id"]) or {}
                beta = _safe_float((stock.get("betas") or {}).get(factor))
                weight = _safe_float(item.get("weight"))
                if weight <= 0:
                    continue
                weighted_beta += beta * weight
                used_weight += weight
            if used_weight > 0:
                academic_exposure[factor] = round(weighted_beta / used_weight, 4)
        buckets[bucket] = {
            "bucket": bucket,
            "label": THEME_BUCKET_LABELS.get(bucket, bucket),
            "weight": round(total_weight, 4),
            "weekly_return": round(weighted_return, 3),
            "academic_exposure": academic_exposure,
        }
    return buckets


def _factor_bias_text(factor: str, value: float) -> str:
    if factor == "Mkt-RF":
        return "Text Beta" if value >= 0 else "Text Beta"
    if factor in {"SMB", "ME"}:
        return "Text" if value >= 0 else "Text"
    if factor == "HML":
        return "Text" if value >= 0 else "Text"
    if factor == "RMW":
        return "Text" if value >= 0 else "Text"
    if factor == "CMA":
        return "Text" if value >= 0 else "Text"
    if factor == "ROE":
        return "Text" if value >= 0 else "Text"
    if factor == "IA":
        return "Text" if value >= 0 else "Text"
    if factor == "EG":
        return "Text" if value >= 0 else "Text"
    if factor == "Mom":
        return "Text" if value >= 0 else "Text"
    return "Text" if value >= 0 else "Text"


def _primary_model_choice(model_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ff = model_results.get("ff_core_pack") or {}
    qf = model_results.get("q_factor_pack") or {}
    if ff.get("error") and not qf.get("error"):
        return {
            "key": "q_factor_pack",
            "label": PACK_META["q_factor_pack"]["label"],
            "reason": "FF Core Text, AutoText Q-Factor Text. ",
        }
    if qf.get("error"):
        return {
            "key": "ff_core_pack",
            "label": PACK_META["ff_core_pack"]["label"],
            "reason": "Q-Factor Text, Text FF Core Text. ",
        }
    ff_r2 = _safe_float(ff.get("r_squared"))
    q_r2 = _safe_float(qf.get("r_squared"))
    ff_stability = _safe_float(ff.get("stability_score"))
    q_stability = _safe_float(qf.get("stability_score"))
    if q_r2 > ff_r2 + 0.03 and q_stability >= ff_stability - 0.1:
        return {
            "key": "q_factor_pack",
            "label": PACK_META["q_factor_pack"]["label"],
            "reason": "Q-Factor Text, Text FF Core. ",
        }
    return {
        "key": "ff_core_pack",
        "label": PACK_META["ff_core_pack"]["label"],
        "reason": "FF Core TextCurrentText. ",
    }


def _build_model_comparison(model_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in ["ff_core_pack", "q_factor_pack"]:
        result = model_results.get(key) or {}
        rows.append(
            {
                "key": key,
                "label": PACK_META[key]["label"],
                "description": PACK_META[key]["description"],
                "r_squared": _safe_float(result.get("r_squared")),
                "adj_r_squared": _safe_float(result.get("adj_r_squared")),
                "stability_score": _safe_float(result.get("stability_score")),
                "alpha": _safe_float(result.get("alpha")),
                "n_observations": int(result.get("n_observations") or 0),
                "error": result.get("error"),
            }
        )
    rows.sort(key=lambda item: (item["error"] is not None, -item["r_squared"], -item["stability_score"]))
    for idx, row in enumerate(rows, start=1):
        row["fit_rank"] = idx
    return rows


def _build_portfolio_diagnosis(
    primary_key: str,
    primary_model: Dict[str, Any],
    sector_macro: Dict[str, float],
    bucket_exposures: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    diagnosis: List[Dict[str, Any]] = []
    primary_label_map = Q_FACTOR_LABELS if primary_key == "q_factor_pack" else FACTOR_LABELS
    sorted_factors = sorted(
        (primary_model.get("portfolio_exposure") or {}).items(),
        key=lambda item: abs(_safe_float(item[1])),
        reverse=True,
    )
    if sorted_factors:
        factor, value = sorted_factors[0]
        label = (primary_label_map.get(factor) or {}).get("zh", factor)
        diagnosis.append(
            {
                "type": "style",
                "factor": factor,
                "label": label,
                "value": round(_safe_float(value), 4),
                "summary": f"Text {label}, CurrentText {_factor_bias_text(factor, _safe_float(value))}. ",
                "description": f"{label} TextCurrentText. ",
            }
        )
    if len(sorted_factors) > 1:
        factor, value = sorted_factors[1]
        label = (primary_label_map.get(factor) or {}).get("zh", factor)
        diagnosis.append(
            {
                "type": "style",
                "factor": factor,
                "label": label,
                "value": round(_safe_float(value), 4),
                "summary": f"Text {label}, Text Beta, Text. ",
                "description": f"{label} Text {round(_safe_float(value), 4)}. ",
            }
        )
    sorted_proxies = sorted(sector_macro.items(), key=lambda item: abs(_safe_float(item[1])), reverse=True)
    if sorted_proxies:
        factor, value = sorted_proxies[0]
        diagnosis.append(
            {
                "type": "sector_macro",
                "factor": factor,
                "label": SECTOR_MACRO_SPECS[factor]["zh"],
                "value": round(_safe_float(value), 4),
                "summary": f"Text {SECTOR_MACRO_SPECS[factor]['zh']}, Text. ",
                "description": SECTOR_MACRO_SPECS[factor]["desc"],
            }
        )
    sorted_buckets = sorted(bucket_exposures.values(), key=lambda item: _safe_float(item.get("weight")), reverse=True)
    if sorted_buckets:
        bucket = sorted_buckets[0]
        diagnosis.append(
            {
                "type": "bucket",
                "factor": bucket["bucket"],
                "label": bucket["label"],
                "value": round(_safe_float(bucket.get("weight")) * 100, 1),
                "summary": f"Text {bucket['label']}, Text {round(_safe_float(bucket.get('weight')) * 100, 1)}%. ",
                "description": f"{bucket['label']} TextCurrentTextHoldingsText. ",
            }
        )
    return diagnosis


def _build_attribution_summary(
    primary_key: str,
    primary_model: Dict[str, Any],
    sector_macro: Dict[str, float],
    bucket_exposures: Dict[str, Dict[str, Any]],
    style_overlays: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    dominant_buckets = sorted(
        bucket_exposures.values(),
        key=lambda item: abs(_safe_float(item.get("weight")) * _safe_float(item.get("weekly_return"))),
        reverse=True,
    )[:3]
    proxy_focus = sorted(sector_macro.items(), key=lambda item: abs(_safe_float(item[1])), reverse=True)[:3]
    style_bias = sorted(
        (primary_model.get("portfolio_exposure") or {}).items(),
        key=lambda item: abs(_safe_float(item[1])),
        reverse=True,
    )[:3]

    lines: List[str] = []
    if dominant_buckets:
        bucket_text = "; ".join(
            f"{item['label']} Text {round(_safe_float(item.get('weight')) * 100, 1)}%, This WeekText {round(_safe_float(item.get('weekly_return')), 2)}%"
            for item in dominant_buckets
        )
        lines.append(f"Text {bucket_text} Text. ")
    if proxy_focus:
        proxy_text = ", ".join(f"{SECTOR_MACRO_SPECS[f]['zh']}({_safe_float(v):+.2f})" for f, v in proxy_focus)
        lines.append(f"Text {proxy_text}. ")
    if style_bias:
        label_map = Q_FACTOR_LABELS if primary_key == "q_factor_pack" else FACTOR_LABELS
        style_text = ", ".join(
            f"{(label_map.get(f) or {}).get('zh', f)}({_safe_float(v):+.2f})" for f, v in style_bias
        )
        lines.append(f"Text {style_text}. ")

    watch_items: List[str] = []
    if _safe_float(sector_macro.get("SOXX")) > 0.6:
        watch_items.append("Text, Text SOXX Text QQQ, Text. ")
    if _safe_float(sector_macro.get("TLT")) < -0.3:
        watch_items.append("Text, Text, Text. ")
    if _safe_float(sector_macro.get("HYG")) > 0.4:
        watch_items.append("TextRiskText, TextReturnTextRiskText. ")
    qmj = _safe_float((style_overlays.get("QMJ") or {}).get("headline_value"))
    if qmj > 0.2:
        watch_items.append("Text, Text Beta Text, Text. ")
    bab = _safe_float((style_overlays.get("BAB") or {}).get("headline_value"))
    if bab < -0.2:
        watch_items.append("Text Beta / Text, TextRiskText. ")

    return {
        "summary": " ".join(lines) if lines else "CurrentText, Text. ",
        "dominant_buckets": [
            {
                "bucket": item["bucket"],
                "label": item["label"],
                "weight": item["weight"],
                "weekly_return": item["weekly_return"],
            }
            for item in dominant_buckets
        ],
        "proxy_focus": [
            {"factor": factor, "label": SECTOR_MACRO_SPECS[factor]["zh"], "value": round(_safe_float(value), 4)}
            for factor, value in proxy_focus
        ],
        "style_bias": [
            {"factor": factor, "label": factor, "value": round(_safe_float(value), 4)}
            for factor, value in style_bias
        ],
        "watch_items": watch_items[:4],
    }


def _build_data_quality(
    requested_holdings: List[Dict[str, Any]],
    eligible_holdings: List[Dict[str, Any]],
    unsupported_holdings: List[Dict[str, Any]],
    stock_exposures: List[Dict[str, Any]],
    errors: Dict[str, str],
) -> Dict[str, Any]:
    low_r2 = [
        {
            "stock_id": stock["stock_id"],
            "stock_name": stock.get("stock_name", stock["stock_id"]),
            "r_squared": stock.get("r_squared", 0),
        }
        for stock in stock_exposures
        if _safe_float(stock.get("r_squared")) < 0.2
    ]
    observations = [_safe_float(stock.get("n_observations")) for stock in stock_exposures]
    return {
        "requested_holdings": len(requested_holdings),
        "eligible_holdings": len(eligible_holdings),
        "unsupported_holdings": len(unsupported_holdings),
        "analyzed_holdings": len(stock_exposures),
        "coverage_ratio": round(len(stock_exposures) / len(eligible_holdings), 3) if eligible_holdings else 0.0,
        "failed_holdings": len(errors),
        "avg_observations": round(float(np.mean(observations)), 1) if observations else 0.0,
        "low_r_squared_stocks": low_r2[:5],
    }


def _factor_quality_confidence(coverage_ratio: float) -> str:
    if coverage_ratio >= 0.8:
        return "high"
    if coverage_ratio >= 0.5:
        return "medium"
    return "low"


def _build_factor_data_quality(
    eligible_holdings: List[Dict[str, Any]],
    stock_exposures: List[Dict[str, Any]],
    factor_labels: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    eligible_count = len(eligible_holdings)
    factors = sorted(
        {
            factor
            for row in stock_exposures
            for factor, value in (row.get("exposures") or {}).items()
            if value is not None
        }
    )
    factor_rows: Dict[str, Dict[str, Any]] = {}
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    coverage_values: List[float] = []
    for factor in factors:
        covered = sum(1 for row in stock_exposures if factor in (row.get("exposures") or {}))
        coverage = round(covered / eligible_count, 3) if eligible_count else 0.0
        confidence = _factor_quality_confidence(coverage)
        confidence_counts[confidence] += 1
        coverage_values.append(coverage)
        label = factor_labels.get(factor) or {}
        if factor.startswith("sector_"):
            source = "sector"
        elif factor.startswith("theme_"):
            source = "theme"
        elif factor == "market":
            source = "price_returns"
        else:
            source = "characteristic"
        factor_rows[factor] = {
            "factor": factor,
            "label": label.get("zh") or label.get("en") or factor,
            "coverage_ratio": coverage,
            "covered_holdings": covered,
            "eligible_holdings": eligible_count,
            "confidence": confidence,
            "source": source,
        }

    return {
        "summary": {
            "factor_count": len(factor_rows),
            "high_confidence_count": confidence_counts["high"],
            "medium_confidence_count": confidence_counts["medium"],
            "low_confidence_count": confidence_counts["low"],
            "avg_coverage_ratio": round(float(np.mean(coverage_values)), 3) if coverage_values else 0.0,
        },
        "factors": factor_rows,
    }


def _build_risk_contribution_summary(
    factor_risk_contributions: List[Dict[str, Any]],
    marginal_risk: List[Dict[str, Any]],
) -> Dict[str, Any]:
    top_factor = next((row for row in factor_risk_contributions if row and row.get("factor")), {})
    top_holding_contributors = [
        row
        for row in sorted(
            marginal_risk or [],
            key=lambda item: abs(_safe_float(item.get("pct_contribution"))),
            reverse=True,
        )
        if row and row.get("stock_id")
    ][:5]
    return {
        "top_factor": top_factor,
        "factor_count": len([row for row in factor_risk_contributions if row and row.get("factor")]),
        "top_holding_contributors": top_holding_contributors,
    }


def _build_factor_risk_alerts(
    risk_contribution_summary: Dict[str, Any],
    factor_data_quality: Dict[str, Any],
    risk_decomposition: Dict[str, Any],
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    top_factor = risk_contribution_summary.get("top_factor") or {}
    if top_factor.get("factor"):
        share = _safe_float(top_factor.get("variance_share"))
        severity = "high" if share >= 0.4 else "medium" if share >= 0.2 else "low"
        label = top_factor.get("label") or top_factor.get("factor")
        alerts.append(
            {
                "type": "top_factor_risk",
                "severity": severity,
                "title": f"{label} is the top factor risk",
                "message": f"It explains about {share:.0%} of modeled factor variance. Watch catalysts that move this beta first.",
                "factor": top_factor.get("factor"),
                "value": round(share, 4),
            }
        )

    top_holding = (risk_contribution_summary.get("top_holding_contributors") or [{}])[0]
    if top_holding.get("stock_id"):
        pct = _safe_float(top_holding.get("pct_contribution"))
        severity = "high" if pct >= 35 else "medium" if pct >= 20 else "low"
        name = top_holding.get("ticker") or top_holding.get("stock_name") or top_holding.get("stock_id")
        alerts.append(
            {
                "type": "top_holding_risk",
                "severity": severity,
                "title": f"{name} is the largest holding risk",
                "message": f"It contributes about {pct:.1f}% of marginal portfolio risk. Drill into position sizing if this is unintended.",
                "stock_id": top_holding.get("stock_id"),
                "value": round(pct, 3),
            }
        )

    quality_summary = factor_data_quality.get("summary") or {}
    low_count = int(_safe_float(quality_summary.get("low_confidence_count")))
    if low_count > 0:
        alerts.append(
            {
                "type": "data_quality",
                "severity": "medium",
                "title": "Some factor signals have low confidence",
                "message": f"{low_count} factors have weak holding coverage. Treat those reads as directional, not precise.",
                "value": low_count,
            }
        )

    active_risk = risk_decomposition.get("active_risk") or {}
    active_rows = active_risk.get("factor_risk_contributions") or []
    if active_rows:
        top_active = active_rows[0]
        share = abs(_safe_float(top_active.get("variance_share")))
        label = top_active.get("label") or top_active.get("factor")
        alerts.append(
            {
                "type": "active_risk",
                "severity": "medium" if share >= 0.25 else "low",
                "title": f"{label} drives active risk",
                "message": "This is the biggest portfolio-versus-benchmark factor gap.",
                "factor": top_active.get("factor"),
                "value": round(share, 4),
            }
        )

    return alerts[:5]


def _build_confidence_summary(primary_model: Dict[str, Any], data_quality: Dict[str, Any]) -> Dict[str, Any]:
    r2 = _safe_float(primary_model.get("r_squared"))
    stability = _safe_float(primary_model.get("stability_score"))
    coverage = _safe_float(data_quality.get("coverage_ratio"))
    score = (0.45 * min(r2 / 0.5, 1.0)) + (0.35 * stability) + (0.2 * coverage)
    if score >= 0.72:
        level = "Text"
    elif score >= 0.45:
        level = "Text"
    else:
        level = "Text"
    return {
        "level": level,
        "score": round(score, 3),
        "summary": f"{level}: Text {r2:.2f}, Text {stability:.2f}, Text {coverage:.0%}. ",
    }


def _build_exposure_change(
    current_result: Dict[str, Any],
    previous_result: Optional[Dict[str, Any]],
    current_holdings: List[Dict[str, Any]],
    previous_holdings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not previous_result:
        return {"available": False, "message": "TextLast WeekHoldingsTextLast WeekTextAnalysisResult. ", "drift_alerts": []}

    current_primary = current_result.get("portfolio_exposure") or {}
    prev_primary = previous_result.get("portfolio_exposure") or {}
    current_sector = current_result.get("sector_macro_exposures") or current_result.get("proxy_factor_exposures") or {}
    prev_sector = previous_result.get("sector_macro_exposures") or previous_result.get("proxy_factor_exposures") or {}
    current_buckets = current_result.get("bucket_exposures") or {}
    prev_buckets = previous_result.get("bucket_exposures") or {}

    academic = {
        factor: round(_safe_float(current_primary.get(factor)) - _safe_float(prev_primary.get(factor)), 4)
        for factor in sorted(set(current_primary) | set(prev_primary))
    }
    sector_macro = {
        factor: round(_safe_float(current_sector.get(factor)) - _safe_float(prev_sector.get(factor)), 4)
        for factor in sorted(set(current_sector) | set(prev_sector))
    }

    buckets = {}
    for bucket in sorted(set(current_buckets) | set(prev_buckets)):
        buckets[bucket] = {
            "label": THEME_BUCKET_LABELS.get(bucket, bucket),
            "weight_change": round(
                _safe_float((current_buckets.get(bucket) or {}).get("weight"))
                - _safe_float((prev_buckets.get(bucket) or {}).get("weight")),
                4,
            ),
        }

    current_weights = {item["stock_id"]: _safe_float(item.get("weight")) for item in current_holdings}
    prev_weights = {item["stock_id"]: _safe_float(item.get("weight")) for item in previous_holdings}
    holding_changes = []
    for stock_id in sorted(set(current_weights) | set(prev_weights)):
        delta = round(current_weights.get(stock_id, 0.0) - prev_weights.get(stock_id, 0.0), 4)
        if abs(delta) < 1e-4:
            continue
        holding_changes.append({"stock_id": stock_id, "weight_change": delta})
    holding_changes.sort(key=lambda item: abs(item["weight_change"]), reverse=True)

    drift_alerts: List[str] = []
    if _safe_float(sector_macro.get("SOXX")) > 0.2 or _safe_float((buckets.get("semis_hardware") or {}).get("weight_change")) > 0.05:
        drift_alerts.append("Text")
    if _safe_float(sector_macro.get("TLT")) < -0.2 or _safe_float((buckets.get("rates_sensitive_growth") or {}).get("weight_change")) > 0.05:
        drift_alerts.append("Text")
    if _safe_float(sector_macro.get("XLI")) > 0.2 or _safe_float(sector_macro.get("XLB")) > 0.2:
        drift_alerts.append("Text / Text")
    if _safe_float(sector_macro.get("HYG")) > 0.2:
        drift_alerts.append("TextRiskText")

    return {
        "available": True,
        "academic": academic,
        "proxy": sector_macro,
        "buckets": buckets,
        "holdings": holding_changes[:8],
        "drift_alerts": drift_alerts,
    }


def _generate_warnings(
    primary_exposure: Dict[str, float],
    sector_macro_exposure: Dict[str, float],
    primary_key: str,
) -> List[str]:
    warnings: List[str] = []
    beta = _safe_float(primary_exposure.get("Mkt-RF"))
    if beta > 1.25:
        warnings.append(f"Text Beta Text ({beta:.2f}), Text, Text. ")
    elif beta < 0.65:
        warnings.append(f"Text Beta Text ({beta:.2f}), TextRiskText. ")

    growth_factor = "HML" if primary_key == "ff_core_pack" else "EG"
    growth_value = _safe_float(primary_exposure.get(growth_factor))
    if primary_key == "ff_core_pack" and growth_value < -0.35:
        warnings.append("Text, Text, Text. ")
    if primary_key == "q_factor_pack" and growth_value < -0.2:
        warnings.append("Text / Text, Text. ")

    if _safe_float(sector_macro_exposure.get("SOXX")) > 0.65:
        warnings.append("Text, Text. ")
    if _safe_float(sector_macro_exposure.get("TLT")) < -0.3:
        warnings.append("Text, Text. ")
    if _safe_float(sector_macro_exposure.get("HYG")) > 0.45:
        warnings.append("Text, TextRiskText. ")
    return warnings


def _rank_fit(model_results: Dict[str, Dict[str, Any]]) -> None:
    rows = [
        (key, _safe_float(value.get("r_squared")), _safe_float(value.get("stability_score")))
        for key, value in model_results.items()
        if key in {"ff_core_pack", "q_factor_pack"}
    ]
    rows.sort(key=lambda item: (-item[1], -item[2]))
    for idx, (key, _, _) in enumerate(rows, start=1):
        model_results[key]["fit_rank"] = idx


def _analyze_us_equity_lab(
    holdings: List[Dict[str, Any]],
    *,
    days: int = 365,
    windows: Optional[List[int]] = None,
    include_q_factors: bool = True,
    include_style_overlays: bool = True,
    include_sector_macro: bool = True,
) -> Dict[str, Any]:
    windows = windows or [63, 126, 252]
    requested_holdings = _enrich_holdings(holdings)
    eligible_holdings = [item for item in requested_holdings if str(item.get("market") or "").lower() == "us"]
    unsupported_holdings = [
        {
            "stock_id": item.get("stock_id"),
            "ticker": item.get("ticker") or item.get("stock_id"),
            "stock_name": item.get("stock_name") or item.get("stock_id"),
            "market": item.get("market"),
            "reason": "CurrentTextAnalysisText / Text ETF / ADR. ",
        }
        for item in requested_holdings
        if str(item.get("market") or "").lower() != "us"
    ]

    if not eligible_holdings:
        return {
            "error": "CurrentTextAnalysisTextHoldings. Text / Text ETF / ADR Holdings. ",
            "requested_holdings": requested_holdings,
            "eligible_holdings": [],
            "unsupported_holdings": unsupported_holdings,
            "model_pack": US_ONLY_MODEL_PACK,
        }

    eligible_holdings = _normalize_weights(eligible_holdings)
    returns_df, fetch_errors = get_portfolio_returns(eligible_holdings, days=max(days, 365))
    if returns_df.empty:
        return {
            "error": "TextHoldingsTextHistoryText, TextAnalysis. ",
            "requested_holdings": requested_holdings,
            "eligible_holdings": eligible_holdings,
            "unsupported_holdings": unsupported_holdings,
            "errors": fetch_errors,
            "model_pack": US_ONLY_MODEL_PACK,
        }

    factor_source_status: Dict[str, Any] = {}
    fallback_used: List[str] = []

    ff_core_df, ff_status = _load_ff_core_factor_data()
    factor_source_status["ff_core_pack"] = ff_status
    if ff_status.get("provider") != "getfactormodels":
        fallback_used.append("ff_core_pack")

    q_df, q_status = _load_q_factor_data()
    factor_source_status["q_factor_pack"] = q_status

    overlay_frames: Dict[str, pd.DataFrame] = {}
    if include_style_overlays:
        overlay_frames, overlay_status = _load_style_overlay_frames()
        factor_source_status["style_overlay_pack"] = overlay_status

    model_results: Dict[str, Dict[str, Any]] = {}
    model_results["ff_core_pack"] = _fit_pack(
        pack_key="ff_core_pack",
        holdings=eligible_holdings,
        returns_df=returns_df,
        factor_df=ff_core_df,
        factor_columns=["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"],
        rf_column="RF",
        windows=windows,
        annualization=252,
        labels=FACTOR_LABELS,
    )

    if include_q_factors:
        model_results["q_factor_pack"] = _fit_pack(
            pack_key="q_factor_pack",
            holdings=eligible_holdings,
            returns_df=returns_df,
            factor_df=q_df,
            factor_columns=["Mkt-RF", "ME", "IA", "ROE", "EG"],
            rf_column="RF_Q",
            windows=windows,
            annualization=252,
            labels=Q_FACTOR_LABELS,
        )
    else:
        model_results["q_factor_pack"] = {
            "label": PACK_META["q_factor_pack"]["label"],
            "family": PACK_META["q_factor_pack"]["family"],
            "description": PACK_META["q_factor_pack"]["description"],
            "error": "Text Q-Factor Analysis. ",
        }

    _rank_fit(model_results)
    primary_model = _primary_model_choice(model_results)
    primary_key = str(primary_model["key"])
    primary_result = model_results.get(primary_key) or {}

    sector_macro_exposures: Dict[str, float] = {}
    sector_macro_details: Dict[str, Dict[str, Any]] = {}
    sector_errors: Dict[str, str] = {}
    if include_sector_macro:
        sector_macro_exposures, sector_macro_details, sector_errors = _compute_sector_macro_exposures(
            eligible_holdings,
            returns_df,
            days=max(days, 365),
        )
    factor_source_status["sector_macro_pack"] = {
        "provider": "akshare_proxy_etf",
        "success": bool(sector_macro_exposures),
        "missing_factors": [factor for factor in SECTOR_MACRO_SPECS if factor not in sector_macro_exposures],
    }

    style_overlays = _compute_style_overlays(eligible_holdings, returns_df, overlay_frames) if include_style_overlays else {}
    if include_style_overlays and not style_overlays:
        fallback_used.append("style_overlay_pack")

    primary_stock_map = {row["stock_id"]: row for row in primary_result.get("stock_exposures") or []}
    bucket_exposures = _build_bucket_exposures(
        eligible_holdings,
        primary_stock_map,
        (primary_result.get("portfolio_exposure") or {}).keys(),
    )
    portfolio_diagnosis = _build_portfolio_diagnosis(primary_key, primary_result, sector_macro_exposures, bucket_exposures)
    attribution_summary = _build_attribution_summary(
        primary_key,
        primary_result,
        sector_macro_exposures,
        bucket_exposures,
        style_overlays,
    )

    holding_factor_contributors = {
        "academic_factors": primary_result.get("top_holding_contributors") or {},
        "proxy_factors": {factor: detail.get("top_holding_contributors") or [] for factor, detail in sector_macro_details.items()},
    }
    errors = dict(fetch_errors)
    errors.update(primary_result.get("errors") or {})
    errors.update(sector_errors)
    data_quality = _build_data_quality(
        requested_holdings,
        eligible_holdings,
        unsupported_holdings,
        primary_result.get("stock_exposures") or [],
        errors,
    )
    confidence_summary = _build_confidence_summary(primary_result, data_quality)
    model_comparison = _build_model_comparison(model_results)

    # --- Risk dashboard computations ---
    portfolio_returns = _portfolio_returns_from_holdings(returns_df, eligible_holdings)
    rf_series = None
    if not ff_core_df.empty and "RF" in ff_core_df.columns:
        rf_series = ff_core_df["RF"]

    risk_metrics = _compute_risk_metrics(portfolio_returns, rf_series=rf_series)
    correlation_matrix = _compute_correlation_matrix(returns_df, eligible_holdings)

    primary_betas = primary_result.get("portfolio_exposure") or {}
    stress_scenarios = _compute_stress_scenarios(primary_betas, sector_macro_exposures)

    # Rolling exposures use primary model's factor data
    if primary_key == "q_factor_pack" and not q_df.empty:
        rolling_factor_df = q_df
        rolling_factor_cols = ["Mkt-RF", "ME", "IA", "ROE", "EG"]
        rolling_rf_col = "RF_Q"
    else:
        rolling_factor_df = ff_core_df
        rolling_factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
        rolling_rf_col = "RF"
    rolling_exposures = _compute_rolling_exposures(
        portfolio_returns, rolling_factor_df, rolling_factor_cols, rolling_rf_col,
    )

    marginal_risk = _compute_marginal_risk(returns_df, eligible_holdings)

    risk_summary_text = _build_risk_summary_for_weekly(
        risk_metrics, stress_scenarios,
        [],  # drift_alerts populated later in analyze_portfolio_factors
    )

    return {
        "model_pack": US_ONLY_MODEL_PACK,
        "requested_holdings": requested_holdings,
        "eligible_holdings": eligible_holdings,
        "unsupported_holdings": unsupported_holdings,
        "portfolio_exposure": primary_result.get("portfolio_exposure") or {},
        "portfolio_alpha": round(_safe_float(primary_result.get("alpha")), 4),
        "portfolio_r_squared": round(_safe_float(primary_result.get("r_squared")), 4),
        "portfolio_adj_r_squared": round(_safe_float(primary_result.get("adj_r_squared")), 4),
        "stock_exposures": primary_result.get("stock_exposures") or [],
        "risk_decomposition": {},
        "warnings": _generate_warnings(primary_result.get("portfolio_exposure") or {}, sector_macro_exposures, primary_key),
        "errors": errors,
        "factor_labels": Q_FACTOR_LABELS if primary_key == "q_factor_pack" else FACTOR_LABELS,
        "proxy_factor_labels": SECTOR_MACRO_SPECS,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lookback_days": days,
        "portfolio_diagnosis": portfolio_diagnosis,
        "holding_factor_contributors": holding_factor_contributors,
        "bucket_exposures": bucket_exposures,
        "proxy_factor_exposures": sector_macro_exposures,
        "sector_macro_exposures": sector_macro_exposures,
        "sector_macro_details": sector_macro_details,
        "style_overlays": style_overlays,
        "attribution_summary": attribution_summary,
        "data_quality": data_quality,
        "model_results": model_results,
        "model_comparison": model_comparison,
        "primary_model": {
            **primary_model,
            "r_squared": round(_safe_float(primary_result.get("r_squared")), 4),
            "stability_score": round(_safe_float(primary_result.get("stability_score")), 3),
            "confidence": confidence_summary.get("level"),
        },
        "factor_source_status": factor_source_status,
        "source_coverage": {
            "ff_core_pack": not ff_core_df.empty,
            "q_factor_pack": not q_df.empty,
            "style_overlay_pack": bool(style_overlays),
            "sector_macro_pack": bool(sector_macro_exposures),
        },
        "fallback_used": fallback_used,
        "missing_factors": {
            key: value.get("missing_factors") or []
            for key, value in factor_source_status.items()
            if isinstance(value, dict)
        },
        "stability_summary": {
            "primary_model": round(_safe_float(primary_result.get("stability_score")), 3),
            "window_results": primary_result.get("window_results") or {},
        },
        "confidence_summary": confidence_summary,
        "risk_metrics": risk_metrics,
        "correlation_matrix": correlation_matrix,
        "stress_scenarios": stress_scenarios,
        "rolling_exposures": rolling_exposures,
        "marginal_risk": marginal_risk,
        "risk_summary_text": risk_summary_text,
    }


def _analyze_barra_lite_us(
    holdings: List[Dict[str, Any]],
    *,
    days: int = 365,
    factor_universe: Optional[List[Dict[str, Any]]] = None,
    benchmark_holdings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    requested_holdings = _enrich_holdings(holdings)
    eligible_holdings = [item for item in requested_holdings if str(item.get("market") or "").lower() == "us"]
    unsupported_holdings = [
        {
            "stock_id": item.get("stock_id"),
            "ticker": item.get("ticker") or item.get("stock_id"),
            "stock_name": item.get("stock_name") or item.get("stock_id"),
            "market": item.get("market"),
            "reason": "Barra-lite MVP currently covers US equities / US ETFs / ADRs only.",
        }
        for item in requested_holdings
        if str(item.get("market") or "").lower() != "us"
    ]

    if not eligible_holdings:
        return {
            "error": "No US holdings are available for Barra-lite factor analysis.",
            "requested_holdings": requested_holdings,
            "eligible_holdings": [],
            "unsupported_holdings": unsupported_holdings,
            "model_pack": BARRA_LITE_MODEL_PACK,
        }

    eligible_holdings = _normalize_weights(eligible_holdings)
    returns_df, fetch_errors = get_portfolio_returns(eligible_holdings, days=max(days, 365))
    if returns_df.empty:
        return {
            "error": "Unable to fetch US holding return history for Barra-lite factor analysis.",
            "requested_holdings": requested_holdings,
            "eligible_holdings": eligible_holdings,
            "unsupported_holdings": unsupported_holdings,
            "errors": fetch_errors,
            "model_pack": BARRA_LITE_MODEL_PACK,
        }

    stock_exposures, portfolio_exposure, missing_characteristics = _compute_barra_lite_exposures(
        eligible_holdings,
        returns_df,
        factor_universe=factor_universe,
    )
    if not stock_exposures:
        return {
            "error": "Unable to build stock-level exposures from available return history.",
            "requested_holdings": requested_holdings,
            "eligible_holdings": eligible_holdings,
            "unsupported_holdings": unsupported_holdings,
            "errors": fetch_errors,
            "model_pack": BARRA_LITE_MODEL_PACK,
        }

    risk_model = _compute_barra_lite_risk_model(
        eligible_holdings,
        returns_df,
        stock_exposures,
        portfolio_exposure,
        benchmark_holdings=benchmark_holdings,
    )
    contributors = _barra_lite_factor_contributors(
        eligible_holdings,
        stock_exposures,
        portfolio_exposure.keys(),
    )
    portfolio_returns = _portfolio_returns_from_holdings(returns_df, eligible_holdings)
    data_quality = _build_data_quality(
        requested_holdings,
        eligible_holdings,
        unsupported_holdings,
        stock_exposures,
        fetch_errors,
    )
    coverage = _safe_float(data_quality.get("coverage_ratio"))
    specific_share = _safe_float((risk_model.get("risk_decomposition") or {}).get("specific_risk_share"))
    confidence_score = round((0.65 * coverage) + (0.35 * max(0.0, 1.0 - min(specific_share, 1.0))), 3)
    confidence_level = "High" if confidence_score >= 0.72 else "Medium" if confidence_score >= 0.45 else "Low"

    top_factor = (risk_model.get("factor_risk_contributions") or [{}])[0]
    diagnosis = []
    if top_factor:
        diagnosis.append(
            {
                "type": "risk_driver",
                "summary": f"Top Barra-lite risk driver is {top_factor.get('label') or top_factor.get('factor')}.",
            }
        )
    factor_labels = _barra_lite_factor_labels(portfolio_exposure.keys())
    sector_factor_count = len([factor for factor in portfolio_exposure if factor.startswith("sector_")])
    covariance_model = (risk_model.get("risk_decomposition") or {}).get("covariance_model") or {}
    specific_risk_model = (risk_model.get("risk_decomposition") or {}).get("specific_risk_model") or {}
    active_risk_model = (risk_model.get("risk_decomposition") or {}).get("active_risk") or {}
    factor_risk_contributions = risk_model.get("factor_risk_contributions") or []
    marginal_risk = _compute_marginal_risk(returns_df, eligible_holdings)
    factor_data_quality = _build_factor_data_quality(eligible_holdings, stock_exposures, factor_labels)
    risk_contribution_summary = _build_risk_contribution_summary(factor_risk_contributions, marginal_risk)
    factor_risk_alerts = _build_factor_risk_alerts(
        risk_contribution_summary,
        factor_data_quality,
        risk_model.get("risk_decomposition") or {},
    )
    analysis_metadata = {
        "model_pack": BARRA_LITE_MODEL_PACK,
        "lookback_days": days,
        "benchmark_model": active_risk_model.get("benchmark_model") or "equal_weight_active_holdings",
        "covariance_estimator": covariance_model.get("estimator") or "",
        "covariance_quality": covariance_model.get("quality") or "",
        "specific_risk_estimator": specific_risk_model.get("estimator") or "",
        "factor_data_quality": factor_data_quality.get("summary") or {},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    return {
        "model_pack": BARRA_LITE_MODEL_PACK,
        "requested_holdings": requested_holdings,
        "eligible_holdings": eligible_holdings,
        "unsupported_holdings": unsupported_holdings,
        "portfolio_exposure": portfolio_exposure,
        "portfolio_alpha": 0.0,
        "portfolio_r_squared": 0.0,
        "portfolio_adj_r_squared": 0.0,
        "stock_exposures": stock_exposures,
        "risk_decomposition": risk_model.get("risk_decomposition") or {},
        "factor_risk_contributions": factor_risk_contributions,
        "factor_returns": risk_model.get("factor_returns") or {},
        "warnings": [],
        "errors": fetch_errors,
        "factor_labels": factor_labels,
        "proxy_factor_labels": {},
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lookback_days": days,
        "portfolio_diagnosis": diagnosis,
        "holding_factor_contributors": {"barra_lite_factors": contributors},
        "bucket_exposures": _build_bucket_exposures(eligible_holdings, {}, portfolio_exposure.keys()),
        "proxy_factor_exposures": {},
        "sector_macro_exposures": {},
        "sector_macro_details": {},
        "style_overlays": {},
        "attribution_summary": {
            "summary": "Barra-lite MVP uses characteristic exposures and factor-mimicking returns for risk decomposition.",
        },
        "data_quality": data_quality,
        "factor_data_quality": factor_data_quality,
        "risk_contribution_summary": risk_contribution_summary,
        "factor_risk_alerts": factor_risk_alerts,
        "analysis_metadata": analysis_metadata,
        "model_results": {
            BARRA_LITE_MODEL_PACK: {
                "label": "Barra-lite MVP",
                "family": "risk_model",
                "description": "Characteristic-style factor exposures with factor/specific risk decomposition.",
                "portfolio_exposure": portfolio_exposure,
                "stock_exposures": stock_exposures,
                "risk_decomposition": risk_model.get("risk_decomposition") or {},
            }
        },
        "model_comparison": [],
        "primary_model": {
            "key": BARRA_LITE_MODEL_PACK,
            "label": "Barra-lite MVP",
            "family": "risk_model",
            "reason": "Requested model pack.",
            "r_squared": 0.0,
            "stability_score": 0.0,
            "confidence": confidence_level,
        },
        "factor_source_status": {
            BARRA_LITE_MODEL_PACK: {
                "provider": "price_returns_and_optional_characteristics",
                "success": True,
                "missing_characteristics": missing_characteristics,
                "universe_count": len(factor_universe or []),
                "exposure_basis": "external_universe" if factor_universe else "portfolio_holdings",
                "sector_factor_count": sector_factor_count,
                "covariance_estimator": covariance_model.get("estimator") or "",
                "covariance_quality": covariance_model.get("quality") or "",
                "specific_risk_estimator": specific_risk_model.get("estimator") or "",
                "benchmark_model": active_risk_model.get("benchmark_model") or "",
            }
        },
        "source_coverage": {BARRA_LITE_MODEL_PACK: True},
        "fallback_used": [],
        "missing_factors": {BARRA_LITE_MODEL_PACK: []},
        "stability_summary": {"primary_model": 0.0, "window_results": {}},
        "confidence_summary": {
            "level": confidence_level,
            "score": confidence_score,
            "summary": f"{confidence_level}: coverage {coverage:.0%}, specific risk share {specific_share:.0%}.",
        },
        "risk_metrics": _compute_risk_metrics(portfolio_returns),
        "correlation_matrix": _compute_correlation_matrix(returns_df, eligible_holdings),
        "stress_scenarios": [],
        "rolling_exposures": [],
        "marginal_risk": marginal_risk,
        "risk_summary_text": "",
    }


def analyze_portfolio_factors(
    holdings: List[Dict[str, Any]],
    *,
    days: int = 365,
    previous_holdings: Optional[List[Dict[str, Any]]] = None,
    windows: Optional[List[int]] = None,
    us_only: bool = True,
    model_pack: str = US_ONLY_MODEL_PACK,
    include_q_factors: bool = True,
    include_style_overlays: bool = True,
    include_sector_macro: bool = True,
    factor_universe: Optional[List[Dict[str, Any]]] = None,
    benchmark_holdings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not us_only:
        logger.info("Ignoring non-US mode request; factor lab is intentionally US-only.")
    requested_pack = str(model_pack or US_ONLY_MODEL_PACK).strip() or US_ONLY_MODEL_PACK

    def run_model(model_holdings: List[Dict[str, Any]]) -> Dict[str, Any]:
        if requested_pack == BARRA_LITE_MODEL_PACK:
            return _analyze_barra_lite_us(
                model_holdings,
                days=days,
                factor_universe=factor_universe,
                benchmark_holdings=benchmark_holdings,
            )
        return _analyze_us_equity_lab(
            model_holdings,
            days=days,
            windows=windows,
            include_q_factors=include_q_factors,
            include_style_overlays=include_style_overlays,
            include_sector_macro=include_sector_macro,
        )

    result = run_model(holdings)
    result["us_only"] = True
    result["requested_model_pack"] = requested_pack

    previous_result = None
    previous_eligible_holdings: List[Dict[str, Any]] = []
    if previous_holdings:
        previous_result = run_model(previous_holdings)
        if previous_result.get("error"):
            previous_result = None
        else:
            previous_eligible_holdings = previous_result.get("eligible_holdings") or []

    result["exposure_change"] = _build_exposure_change(
        result,
        previous_result,
        result.get("eligible_holdings") or [],
        previous_eligible_holdings,
    )
    return result
