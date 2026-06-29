from __future__ import annotations

import json
import logging
import math
import os
import re
import hashlib
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for

# Allow running `python web/app.py` directly from project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = Path(__file__).resolve().parent
WEB_TEMPLATES_DIR = WEB_ROOT / "templates"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config_center.service import ConfigCenterService
from core.data_sources.alpaca_us_market import AlpacaUSMarketDataClient
from core.data_sources.factory import build_data_source_registry
from core.environment import EnvironmentCollector
from core.gpt53_client import GPT53Client
from core.ibkr_derived_portfolio import (
    IBKR_DELTA_BASELINE_WEEK_ID,
    IBKR_DELTA_PROJECTION_SOURCE,
    build_ibkr_derived_review_projection,
    build_ibkr_w25_baseline_delta_projection,
    canonical_ibkr_trade_ticker,
)
from core.local_ohlcv_cache import SQLiteOHLCVCache, SQLiteOHLCVCacheConfig
from core.portfolio_performance import (
    build_portfolio_ticker_aliases,
    calculate_weekly_portfolio_performance,
    resolve_portfolio_ticker_alias,
    week_end_date,
)
from core import ima_sync
from core.llm_cache import LLMCache
from core.llm_runtime import LLMRuntime
from core.preference_learner import PreferenceLearner
from core.research import ResearchEngine
from core.stock_commentary import get_stock_commentary
from core.storage import Storage
from core.us_screener import STRATEGY_MOMENTUM_SPIKE, STRATEGY_POST_52W_LOW_REVERSAL, USScreenerService
from core.weekly_review import WeeklyReviewManager, get_week_id
try:
    from utils.akshare_client import get_weekly_performance as ak_get_weekly_performance
    from utils.akshare_client import get_portfolio_returns as ak_get_portfolio_returns
    from utils.akshare_client import get_portfolio_and_weekly as ak_get_portfolio_and_weekly
    from utils.akshare_client import get_price_chart_series as ak_get_price_chart_series
    from utils.akshare_client import get_daily_ohlcv_frames as ak_get_daily_ohlcv_frames
except ImportError:
    ak_get_weekly_performance = None
    ak_get_portfolio_returns = None
    ak_get_portfolio_and_weekly = None
    ak_get_price_chart_series = None
    ak_get_daily_ohlcv_frames = None
from web.app_bootstrap import AppBootstrapDeps, register_application_modules
from web.app_services import AppServiceDeps, build_app_services
from web.modules.registry import register_feature_modules
from web.modules.task_api.helpers import TaskApiHelperDeps, build_task_api_helpers
from web.modules.us_screener.helpers import (
    USScreenerRuntimeHelperDeps,
    USScreenerScanRunnerDeps,
    build_us_screener_scan_task_runner,
    build_us_screener_runtime_helpers,
)
from web.modules.us_screener.service import (
    USScreenerModuleDeps,
    build_us_screener_module_service,
)
from web.modules.watchlist.helpers import (
    WatchlistAIJudgmentDeps,
    WatchlistPriceChartDeps,
    WatchlistPriceChartFetcher,
    WatchlistSnapshotDeps,
    build_generate_watch_candidate_ai_judgment,
    build_watchlist_snapshot_builder,
)
from web.modules.watchlist.performance import WatchlistPerformanceDeps, WatchlistPerformanceRefresher
from web.modules.filings.service import build_default_filings_service
from web.modules.weekly_review.helpers import (
    WeeklyReviewAnalysisDeps,
    WeeklyReviewExportDeps as WeeklyReviewHelperExportDeps,
    WeeklyReviewResolutionDeps,
    build_attach_weekly_review_analyses,
    build_normalize_weekly_review_payload,
    build_resolve_effective_weekly_review,
    has_weekly_review_content,
    build_weekly_review_snapshot_builder,
    build_weekly_reviews_export_response_builder_v2,
)
from web.modules.weekly_review.runtime import (
    WeeklyReviewApiDeps,
    WeeklyReviewExportDeps as WeeklyReviewRuntimeExportDeps,
    WeeklyReviewTaskRunnerDeps,
    WeeklyReviewTaskSubmitDeps,
    build_weekly_review_api_handlers,
    build_weekly_review_export_handlers,
    build_weekly_review_task_runners,
    build_weekly_review_task_submit_handlers,
)
from web.modules.watchlist.service import WatchlistModuleDeps, build_watchlist_module_service
from core.xueqiu_client import XueqiuClient
from web.modules.weekly_review.service import WeeklyReviewModuleDeps, build_weekly_review_module_service
from web.runtime.app_helpers import build_lazy_us_screener_service_getter, noop_task_runner, recent_week_ids
from web.runtime.common import (
    chat_with_retry,
    env_flag,
    format_zsxq_topic_time,
    ima_sync_key_watchlist,
    ima_sync_key_weekly_review,
    ima_sync_key_zsxq_daily,
    is_llm_failure_text,
    json_safe,
    markdown_from_zsxq_daily_snapshot,
    resolve_secret_key,
    safe_filename_part,
    safe_float,
    fmt_money,
    fmt_number,
    safe_int,
    seconds_until,
    serialize_ima_sync_status,
    to_change_pct,
    to_bool,
    write_markdown_snapshot,
    is_timeout_failure_text,
)
from web.runtime.app_data import AppDataDeps, build_app_data_helpers
from web.runtime.http import build_cors_after_request_handler, build_options_handler
from web.runtime.llm import RuntimeStateDeps, build_runtime_state_accessors
from web.runtime.tasks import TaskRuntimeDeps, build_task_runtime_accessors
from web.services.auth_service import AuthService
from web.services.background_task_manager import BackgroundTaskManager
from web.services.factor_analysis_service import FactorAnalysisService
from web.services.task_registry import WechatTaskRegistry

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


WATCHLIST_REVISIT_THRESHOLDS = {
    "weekly": 7.0,
    "monthly": 14.0,
    "since_added": 21.0,
}

WATCHLIST_PRICE_CHART_TTL_SECONDS = 600.0
WATCHLIST_PRICE_CHART_FAILURE_TTL_SECONDS = 20.0
WATCHLIST_PRICE_CHART_WAIT_SECONDS = 45.0
PORTFOLIO_KNOWN_EXTERNAL_CASH_FLOWS_HKD = [
    {"date": "2026-02-02", "amount_hkd": 200000.0, "source": "known_deposit"},
    {"date": "2026-05-02", "amount_hkd": 300000.0, "source": "known_deposit"},
    {"date": "2026-06-25", "amount_hkd": 250000.0, "source": "known_deposit"},
]

_portfolio_performance_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}


def _is_us_equity_ticker(ticker: Any) -> bool:
    text = str(ticker or "").strip().upper()
    if not text:
        return False
    if any(text.endswith(suffix) for suffix in (".HK", ".SH", ".SZ", ".SS", ".AS", ".DE", ".VI", ".T", ".KS", ".KQ")):
        return False
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?", text):
        return False
    return True


def _is_supported_akshare_history_ticker(ticker: Any) -> bool:
    text = str(ticker or "").strip().upper()
    if not text:
        return False
    if any(text.endswith(suffix) for suffix in (".HK", ".SH", ".SZ", ".SS", ".AS", ".DE", ".VI", ".T", ".KS", ".KQ")):
        return True
    return False


def _build_local_ohlcv_cache_and_client(storage: Storage) -> tuple[Optional[SQLiteOHLCVCache], Optional[AlpacaUSMarketDataClient]]:
    api_key = str(storage.get_alpaca_api_key() or "").strip()
    api_secret = str(storage.get_alpaca_api_secret() or "").strip()
    if not api_key or not api_secret:
        return None, None
    client = AlpacaUSMarketDataClient(
        api_key=api_key,
        api_secret=api_secret,
        trading_base_url=storage.get_alpaca_trading_base_url(),
        market_data_base_url=storage.get_alpaca_market_data_base_url(),
        stock_feed=storage.get_alpaca_stock_feed(),
    )
    cache = SQLiteOHLCVCache(
        SQLiteOHLCVCacheConfig(db_path=storage.base_dir / "market_history_cache.sqlite3"),
        logger_=logger,
    )
    return cache, client

_safe_filename_part = safe_filename_part
_ima_sync_key_zsxq_daily = ima_sync_key_zsxq_daily
_ima_sync_key_weekly_review = ima_sync_key_weekly_review
_ima_sync_key_watchlist = ima_sync_key_watchlist
_serialize_ima_sync_status = serialize_ima_sync_status
_write_markdown_snapshot = write_markdown_snapshot
_format_zsxq_topic_time = format_zsxq_topic_time
_markdown_from_zsxq_daily_snapshot = markdown_from_zsxq_daily_snapshot
_env_flag = env_flag
_to_bool = to_bool
_safe_int = safe_int
_safe_float = safe_float
_json_safe = json_safe
_resolve_secret_key = lambda: resolve_secret_key(logger)
_is_llm_failure_text = is_llm_failure_text
_is_timeout_failure_text = is_timeout_failure_text
_chat_with_retry = lambda runtime_client, prompt, **kwargs: chat_with_retry(
    runtime_client,
    prompt,
    logger=logger,
    **kwargs,
)
_seconds_until = seconds_until

app = Flask(
    __name__,
    template_folder=str(WEB_TEMPLATES_DIR),
)
app.secret_key = _resolve_secret_key()
logger.info("Flask app initialized with template folder: %s", WEB_TEMPLATES_DIR)

storage = Storage()
config_center = ConfigCenterService(storage=storage)
data_source_registry = build_data_source_registry()
auth_service = AuthService(storage)
llm_cache = LLMCache(ttl_seconds=2 * 60 * 60)

client: Optional[LLMRuntime] = None
env_collector: Optional[EnvironmentCollector] = None
research_engine: Optional[ResearchEngine] = None
preference_learner: Optional[PreferenceLearner] = None
weekly_review_manager: Optional[WeeklyReviewManager] = None
us_screener_service: Optional[USScreenerService] = None

_runtime_state = build_runtime_state_accessors(
    RuntimeStateDeps(
        get_storage=lambda: storage,
        get_config_center=lambda: config_center,
        get_data_source_registry=lambda: data_source_registry,
        get_llm_cache=lambda: llm_cache,
        get_client_state=lambda: client,
        set_client_state=lambda value: globals().__setitem__("client", value),
        get_env_collector_state=lambda: env_collector,
        set_env_collector_state=lambda value: globals().__setitem__("env_collector", value),
        get_research_engine_state=lambda: research_engine,
        set_research_engine_state=lambda value: globals().__setitem__("research_engine", value),
        get_preference_learner_state=lambda: preference_learner,
        set_preference_learner_state=lambda value: globals().__setitem__("preference_learner", value),
        get_weekly_review_manager_state=lambda: weekly_review_manager,
        set_weekly_review_manager_state=lambda value: globals().__setitem__("weekly_review_manager", value),
        gpt53_client_factory=lambda *args, **kwargs: GPT53Client(*args, **kwargs),
        llm_runtime_factory=lambda **kwargs: LLMRuntime(**kwargs),
        environment_collector_factory=lambda *args, **kwargs: EnvironmentCollector(*args, **kwargs),
        research_engine_factory=lambda *args, **kwargs: ResearchEngine(*args, **kwargs),
        preference_learner_factory=lambda *args, **kwargs: PreferenceLearner(*args, **kwargs),
        weekly_review_manager_factory=lambda *args, **kwargs: WeeklyReviewManager(
            *args,
            history_frame_loader=_weekly_review_history_frame_loader,
            **kwargs,
        ),
    )
)

_app_data_helpers = build_app_data_helpers(
    AppDataDeps(
        get_storage=lambda: storage,
        get_data_source_registry=lambda: data_source_registry,
    )
)

_us_screener_job_thread: Optional[threading.Thread] = None
_us_screener_job_lock = threading.Lock()
US_SCREENER_AUTO_RESUME_DELAY_SECONDS = _safe_int(os.environ.get("US_SCREENER_AUTO_RESUME_DELAY_SECONDS"), 120)
_us_screener_retry_timer: Optional[threading.Timer] = None
_us_screener_retry_eta: Optional[datetime] = None

_wechat_tasks = WechatTaskRegistry(
    ttl_seconds=_safe_int(os.environ.get("WECHAT_TASK_TTL_SECONDS"), 7200),
    max_tasks=_safe_int(os.environ.get("WECHAT_TASK_MAX"), 500),
)

_local_ohlcv_cache, _local_ohlcv_client = _build_local_ohlcv_cache_and_client(storage)
_ibkr_projection_cache: dict[tuple[Any, ...], dict[str, Any]] = {}


def _path_mtime_token(path: Any) -> str:
    if not path:
        return "missing"
    try:
        p = Path(path)
        if not p.exists():
            return "missing"
        stat = p.stat()
        digest = ""
        if p.is_file() and stat.st_size <= 1_000_000:
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
            except Exception:
                digest = ""
        return f"{stat.st_mtime_ns}:{getattr(stat, 'st_ctime_ns', '')}:{stat.st_size}:{digest}"
    except Exception:
        return "unknown"


def _ibkr_projection_cache_token(year: int) -> str:
    return "|".join(
        [
            str(year),
            _path_mtime_token(getattr(storage, "weekly_reviews_path", None)),
            _path_mtime_token(getattr(storage, "broker_trade_ledger_path", None)),
            _path_mtime_token(getattr(storage, "ibkr_portfolio_baselines_path", None)),
            _path_mtime_token(getattr(getattr(_local_ohlcv_cache, "config", None), "db_path", None)),
        ]
    )


def _local_ohlcv_history_loader(tickers: list[str], *, start_date, end_date) -> dict[str, pd.DataFrame]:
    if _local_ohlcv_client is None:
        return {}
    return _local_ohlcv_client.fetch_daily_history_window(tickers, start_at=start_date, end_at=end_date)


def _akshare_ohlcv_history_loader(tickers: list[str], *, start_date, end_date) -> dict[str, pd.DataFrame]:
    if ak_get_daily_ohlcv_frames is None:
        return {}
    return ak_get_daily_ohlcv_frames(tickers, start_date=start_date, end_date=end_date)


def _weekly_review_history_frame_loader(tickers: list[str], *, lookback_days: int = 540) -> dict[str, pd.DataFrame]:
    if _local_ohlcv_cache is None:
        return {}
    symbols = sorted({str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()})
    if not symbols:
        return {}
    frames: dict[str, pd.DataFrame] = {}
    us_tickers = [ticker for ticker in symbols if _is_us_equity_ticker(ticker)]
    akshare_tickers = [ticker for ticker in symbols if ticker not in us_tickers and _is_supported_akshare_history_ticker(ticker)]
    try:
        if us_tickers and _local_ohlcv_client is not None:
            frames.update(
                _local_ohlcv_cache.fetch_history_frames(
                    us_tickers,
                    history_loader=_local_ohlcv_history_loader,
                    source="alpaca_cache",
                )
            )
        if akshare_tickers and ak_get_daily_ohlcv_frames is not None:
            frames.update(
                _local_ohlcv_cache.fetch_history_frames(
                    akshare_tickers,
                    history_loader=_akshare_ohlcv_history_loader,
                    source="akshare_cache",
                )
            )
        cache_start = datetime.now().date() - timedelta(days=max(int(lookback_days or 540), 30))
        missing = [ticker for ticker in symbols if ticker not in frames]
        if missing:
            frames.update(_local_ohlcv_cache.load_history_frames(missing, start_date=cache_start))
    except Exception as exc:
        logger.warning("weekly review stock performance local OHLCV load failed: %s", exc)
    return frames


def _portfolio_performance_cache_token() -> str:
    parts = [
        _path_mtime_token(getattr(storage, "weekly_reviews_path", None)),
        _path_mtime_token(getattr(storage, "broker_trade_ledger_path", None)),
        _path_mtime_token(getattr(storage, "ibkr_portfolio_baselines_path", None)),
        str(id(_local_ohlcv_cache)),
        str(id(_local_ohlcv_client)),
        str(id(_local_ohlcv_history_loader)),
        str(id(_akshare_ohlcv_history_loader)),
        str(id(ak_get_daily_ohlcv_frames)),
    ]
    return "|".join(parts)


def _is_after_ibkr_delta_baseline(week_id: Any) -> bool:
    target = week_end_date(str(week_id or ""))
    baseline = week_end_date(IBKR_DELTA_BASELINE_WEEK_ID)
    return bool(target is not None and baseline is not None and target > baseline)


def _portfolio_external_cash_flows_hkd() -> list[dict[str, Any]]:
    if hasattr(storage, "get_portfolio_external_cash_flows_hkd"):
        try:
            path = getattr(storage, "portfolio_external_cash_flows_path", None)
            if path is not None and Path(path).exists():
                return storage.get_portfolio_external_cash_flows_hkd()
            if hasattr(storage, "save_portfolio_external_cash_flows"):
                return storage.save_portfolio_external_cash_flows(PORTFOLIO_KNOWN_EXTERNAL_CASH_FLOWS_HKD)
        except Exception:
            logger.warning("Failed to load portfolio external cash flows; using in-memory defaults", exc_info=True)
    return [dict(item) for item in PORTFOLIO_KNOWN_EXTERNAL_CASH_FLOWS_HKD]


def _get_ibkr_delta_baseline() -> dict[str, Any]:
    if hasattr(storage, "get_ibkr_portfolio_baseline"):
        try:
            existing = storage.get_ibkr_portfolio_baseline(IBKR_DELTA_BASELINE_WEEK_ID)
            if existing.get("positions"):
                return existing
        except Exception:
            logger.warning("Failed to load IBKR W25 baseline snapshot", exc_info=True)
    return {}


def _build_preferred_ibkr_projection(
    reviews: list[dict[str, Any]],
    ledger: dict[str, Any],
    *,
    price_frames: dict[str, Any],
    external_cash_flows_hkd: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if any(_is_after_ibkr_delta_baseline(review.get("week_id")) for review in reviews):
        baseline = _get_ibkr_delta_baseline()
        if not baseline.get("positions"):
            return {
                "success": False,
                "error": "missing_ibkr_delta_baseline",
                "weeks": [],
                "reviews_by_week": {},
                "diagnostics": {
                    "source": "ibkr_w25_baseline_delta",
                    "baseline_week_id": IBKR_DELTA_BASELINE_WEEK_ID,
                    "silent_fallback_count": 0,
                },
            }
        try:
            projection = build_ibkr_w25_baseline_delta_projection(
                reviews,
                ledger,
                baseline=baseline,
                price_frames=price_frames,
                external_cash_flows_hkd=external_cash_flows_hkd or [],
            )
            if projection.get("success") and projection.get("reviews_by_week"):
                return projection
            return {
                **projection,
                "success": False,
                "error": projection.get("error") or "ibkr_delta_projection_failed",
                "diagnostics": {
                    **dict(projection.get("diagnostics") or {}),
                    "silent_fallback_count": 0,
                },
            }
        except Exception:
            logger.warning("IBKR W25 baseline-delta projection failed", exc_info=True)
            return {
                "success": False,
                "error": "ibkr_delta_projection_failed",
                "weeks": [],
                "reviews_by_week": {},
                "diagnostics": {
                    "source": "ibkr_w25_baseline_delta",
                    "baseline_week_id": IBKR_DELTA_BASELINE_WEEK_ID,
                    "silent_fallback_count": 0,
                },
            }
    return build_ibkr_derived_review_projection(
        reviews,
        ledger,
        price_frames=price_frames,
        external_cash_flows_hkd=external_cash_flows_hkd or [],
    )


def _portfolio_history_frames_loader(tickers: list[str], *, start_date=None, end_date=None) -> dict[str, pd.DataFrame]:
    symbols = sorted({str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()})
    if not symbols or _local_ohlcv_cache is None:
        return {}
    frames: dict[str, pd.DataFrame] = {}
    cache_start = start_date or (datetime.now().date() - timedelta(days=900))
    try:
        us_tickers = sorted(ticker for ticker in symbols if _is_us_equity_ticker(ticker))
        if us_tickers and _local_ohlcv_client is not None:
            frames.update(
                _local_ohlcv_cache.fetch_history_frames(
                    us_tickers,
                    history_loader=_local_ohlcv_history_loader,
                    source="alpaca_cache",
                )
            )
        akshare_tickers = sorted(
            ticker
            for ticker in symbols
            if ticker not in frames and _is_supported_akshare_history_ticker(ticker)
        )
        if akshare_tickers and ak_get_daily_ohlcv_frames is not None:
            frames.update(
                _local_ohlcv_cache.fetch_history_frames(
                    akshare_tickers,
                    history_loader=_akshare_ohlcv_history_loader,
                    source="akshare_cache",
                )
            )
        cached_only = sorted(ticker for ticker in symbols if ticker not in frames)
        if cached_only:
            frames.update(_local_ohlcv_cache.load_history_frames(cached_only, start_date=cache_start))
    except Exception as exc:
        logger.warning("portfolio history frame load failed: %s", exc)
    return frames


def _get_price_chart_series_with_local_cache(ticker: str, range: str = "365d") -> dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    local_source = ""
    local_loader = None
    if _local_ohlcv_cache is not None and _local_ohlcv_client is not None and _is_us_equity_ticker(symbol):
        local_source = "alpaca_cache"
        local_loader = _local_ohlcv_history_loader
    elif _local_ohlcv_cache is not None and ak_get_daily_ohlcv_frames is not None and _is_supported_akshare_history_ticker(symbol):
        local_source = "akshare_cache"
        local_loader = _akshare_ohlcv_history_loader

    if _local_ohlcv_cache is not None and local_loader is not None:
        try:
            local_payload = _local_ohlcv_cache.get_price_chart_series(
                symbol,
                history_loader=local_loader,
                source=local_source,
                range_name=range,
            )
            if local_payload.get("success") or ak_get_price_chart_series is None:
                return local_payload
            logger.warning(
                "local OHLCV price chart unavailable for %s: %s",
                symbol,
                local_payload.get("error") or "unknown error",
            )
        except Exception as exc:
            logger.warning("local OHLCV price chart failed for %s: %s", symbol, exc)
    if ak_get_price_chart_series is None:
        return {
            "success": False,
            "ticker": str(ticker or "").strip(),
            "range": str(range or "365d").strip().lower() or "365d",
            "as_of_date": "",
            "series": {"candles": [], "ma50": [], "ma100": [], "ma200": []},
            "meta": {
                "latest_close": None,
                "change_1y_pct": None,
                "has_enough_history_for_ma200": False,
                "provider": "unavailable",
            },
            "error": "Text",
        }
    return ak_get_price_chart_series(ticker, range=range)


def _build_weekly_portfolio_performance(
    *,
    week_id: str | None = None,
    benchmark: str = "QQQ",
    lookback_weeks: int = 16,
    mode: str = "ytd",
) -> dict[str, Any]:
    cache_key = (
        str(week_id or "").strip(),
        str(benchmark or "QQQ").strip().upper() or "QQQ",
        str(lookback_weeks or 16),
        str(mode or "ytd").strip().lower() or "ytd",
    )
    cache_token = _portfolio_performance_cache_token()
    cached = _portfolio_performance_cache.get(cache_key)
    if cached and cached.get("cache_token") == cache_token:
        return cached["payload"]

    history = sorted(
        list(storage.get_weekly_review_history(limit=10_000)),
        key=lambda wid: week_end_date(str(wid)) or pd.Timestamp.min,
    )
    if week_id and week_id in history:
        end_index = history.index(week_id)
    else:
        end_index = len(history) - 1
    if end_index < 0:
        return {"success": False, "error": "no_weekly_reviews", "series": [], "cash_flows": []}
    selected_mode = str(mode or "ytd").strip().lower()
    if selected_mode == "lookback":
        start_index = max(0, end_index - max(int(lookback_weeks or 16), 4) + 1)
    else:
        end_week_date = week_end_date(str(history[end_index]))
        if end_week_date is None:
            start_index = max(0, end_index - max(int(lookback_weeks or 16), 4) + 1)
        else:
            start_index = 0
            for idx in range(end_index, -1, -1):
                week_date = week_end_date(str(history[idx]))
                if week_date is None or week_date.year != end_week_date.year:
                    start_index = idx + 1
                    break
    selected_week_ids = []
    reviews = []
    for wid in history[start_index : end_index + 1]:
        existing_review = storage.get_weekly_review(wid) or {}
        if not existing_review.get("stocks") and existing_review.get("total_portfolio_value") is None and not existing_review.get("rebalancing_ops"):
            continue
        selected_week_ids.append(wid)
        reviews.append(existing_review)
    ticker_aliases = build_portfolio_ticker_aliases(reviews)
    tickers: set[str] = {resolve_portfolio_ticker_alias(benchmark or "QQQ", ticker_aliases) or "QQQ"}
    for review in reviews:
        for stock_id, payload in ((review.get("stocks") or {}) if isinstance(review, dict) else {}).items():
            if not isinstance(payload, dict):
                continue
            shares = _safe_float(payload.get("shares_held")) or 0.0
            if shares <= 0:
                continue
            ticker = resolve_portfolio_ticker_alias(payload.get("ticker") or stock_id, ticker_aliases)
            if ticker:
                tickers.add(ticker)
    try:
        ledger = storage.get_broker_trade_ledger(limit=None)
    except Exception:
        ledger = {}
    for trade in (ledger or {}).get("trades") or []:
        ticker = resolve_portfolio_ticker_alias(
            canonical_ibkr_trade_ticker(
                trade.get("ticker") or trade.get("stock_id") or trade.get("symbol"),
                trade.get("currency"),
            ),
            ticker_aliases,
        )
        if ticker:
            tickers.add(ticker)

    price_frames: dict[str, pd.DataFrame] = {}
    cache_start = datetime.now().date() - timedelta(days=900)
    if _local_ohlcv_cache is not None:
        try:
            us_tickers = sorted(ticker for ticker in tickers if _is_us_equity_ticker(ticker))
            if us_tickers and _local_ohlcv_client is not None:
                price_frames.update(
                    _local_ohlcv_cache.fetch_history_frames(
                        us_tickers,
                        history_loader=_local_ohlcv_history_loader,
                        source="alpaca_cache",
                    )
                )
            akshare_tickers = sorted(
                ticker
                for ticker in tickers
                if ticker not in price_frames and _is_supported_akshare_history_ticker(ticker)
            )
            if akshare_tickers and ak_get_daily_ohlcv_frames is not None:
                price_frames.update(
                    _local_ohlcv_cache.fetch_history_frames(
                        akshare_tickers,
                        history_loader=_akshare_ohlcv_history_loader,
                        source="akshare_cache",
                    )
                )
            cached_only = sorted(ticker for ticker in tickers if ticker not in price_frames)
            if cached_only:
                price_frames.update(_local_ohlcv_cache.load_history_frames(cached_only, start_date=cache_start))
        except Exception as exc:
            logger.warning("weekly portfolio performance OHLCV cache load failed: %s", exc)

    data_source = "weekly_review_holdings+local_ohlcv_cache"
    portfolio_external_cash_flows_hkd = _portfolio_external_cash_flows_hkd()
    performance_external_cash_flows_hkd = portfolio_external_cash_flows_hkd
    if (ledger or {}).get("trades"):
        try:
            projection = _build_preferred_ibkr_projection(
                reviews,
                ledger,
                price_frames=price_frames,
                external_cash_flows_hkd=portfolio_external_cash_flows_hkd,
            )
            if not projection.get("success") and projection.get("error"):
                return {
                    "success": False,
                    "error": projection.get("error"),
                    "series": [],
                    "cash_flows": [],
                    "data_source": projection.get("canonical_source") or "ibkr_derived_ledger",
                    "data_quality": projection.get("diagnostics") or {},
                    "diagnostics": projection.get("diagnostics") or {},
                }
            projected_reviews = [
                projection.get("reviews_by_week", {}).get(str(review.get("week_id") or "")) or review
                for review in reviews
            ]
            if projection.get("success") and projected_reviews:
                reviews = projected_reviews
                ticker_aliases = build_portfolio_ticker_aliases(reviews)
                data_source = f"{projection.get('canonical_source') or 'ibkr_derived_ledger'}+weekly_nav+local_ohlcv_cache"
                performance_external_cash_flows_hkd = (
                    portfolio_external_cash_flows_hkd
                    if projection.get("canonical_source") == IBKR_DELTA_PROJECTION_SOURCE
                    else []
                )
        except Exception as exc:
            logger.warning("IBKR-derived portfolio projection failed; falling back to weekly review holdings: %s", exc)

    payload = calculate_weekly_portfolio_performance(
        reviews,
        price_frames,
        benchmark=benchmark,
        external_cash_flows_hkd=performance_external_cash_flows_hkd,
    )
    payload.setdefault("week_ids", selected_week_ids)
    payload.setdefault("mode", "lookback" if selected_mode == "lookback" else "ytd")
    payload.setdefault("data_source", data_source)
    if payload.get("success"):
        _portfolio_performance_cache[cache_key] = {"cache_token": cache_token, "payload": payload}
    return payload


def _ibkr_projected_weekly_review(week_id: str, fallback_review: dict[str, Any] | None = None) -> dict[str, Any] | None:
    target_week_end = week_end_date(str(week_id or ""))
    if target_week_end is None:
        return fallback_review
    target_year = int(target_week_end.year)
    projection_mode = "w25_delta" if _is_after_ibkr_delta_baseline(week_id) else "full_ytd"
    cache_key = (target_year, projection_mode, _ibkr_projection_cache_token(target_year))
    cached_projection = _ibkr_projection_cache.get(cache_key)
    if isinstance(cached_projection, dict):
        return (cached_projection.get("reviews_by_week") or {}).get(str(week_id)) or fallback_review
    history = sorted(
        list(storage.get_weekly_review_history(limit=10_000)),
        key=lambda wid: week_end_date(str(wid)) or pd.Timestamp.min,
    )
    reviews = []
    tickers: set[str] = set()
    for wid in history:
        end_day = week_end_date(str(wid))
        if end_day is None or end_day.year != target_year:
            continue
        review = storage.get_weekly_review(wid) or {}
        if not review:
            continue
        reviews.append(review)
        for stock_id, payload in ((review.get("stocks") or {}) if isinstance(review, dict) else {}).items():
            if isinstance(payload, dict):
                ticker = str(payload.get("ticker") or stock_id or "").strip().upper()
                if ticker:
                    tickers.add(ticker)
    try:
        ledger = storage.get_broker_trade_ledger(limit=None)
    except Exception:
        ledger = {}
    if not reviews or not (ledger or {}).get("trades"):
        return fallback_review
    for trade in (ledger or {}).get("trades") or []:
        ticker = str(trade.get("ticker") or trade.get("stock_id") or trade.get("symbol") or "").strip().upper()
        if ticker:
            tickers.add(ticker)
    start_date = datetime(int(target_week_end.year), 1, 1).date() - timedelta(days=10)
    price_frames = _portfolio_history_frames_loader(sorted(tickers), start_date=start_date, end_date=datetime.now().date()) if tickers else {}
    projection = _build_preferred_ibkr_projection(
        reviews,
        ledger,
        price_frames=price_frames,
        external_cash_flows_hkd=_portfolio_external_cash_flows_hkd(),
    )
    if projection.get("success"):
        _ibkr_projection_cache.clear()
        _ibkr_projection_cache[cache_key] = projection
    return (projection.get("reviews_by_week") or {}).get(str(week_id)) or fallback_review

_watchlist_price_chart_fetcher = WatchlistPriceChartFetcher(
    WatchlistPriceChartDeps(
        to_change_pct=lambda value: to_change_pct(value),
        get_price_chart_series=_get_price_chart_series_with_local_cache,
        logger=logger,
        ttl_seconds=WATCHLIST_PRICE_CHART_TTL_SECONDS,
        failure_ttl_seconds=WATCHLIST_PRICE_CHART_FAILURE_TTL_SECONDS,
        wait_seconds=WATCHLIST_PRICE_CHART_WAIT_SECONDS,
    )
)
_fetch_price_chart = _watchlist_price_chart_fetcher.fetch
_generate_watch_candidate_ai_judgment = build_generate_watch_candidate_ai_judgment(
    WatchlistAIJudgmentDeps(
        get_client=lambda: get_client(),
        get_storage=lambda: storage,
        fmt_number=lambda value: fmt_number(value),
        chat_with_retry=lambda *args, **kwargs: _chat_with_retry(*args, **kwargs),
        is_llm_failure_text=lambda text: _is_llm_failure_text(text),
    )
)


def _call_watch_candidate_ai_judgment(candidate, *, commentary_entry=None, filings_entry=None, force=False):
    try:
        return _generate_watch_candidate_ai_judgment(
            candidate,
            commentary_entry=commentary_entry,
            filings_entry=filings_entry,
            force=force,
        )
    except TypeError as exc:
        if "filings_entry" not in str(exc):
            raise
        return _generate_watch_candidate_ai_judgment(
            candidate,
            commentary_entry=commentary_entry,
            force=force,
        )


_build_watchlist_snapshot = build_watchlist_snapshot_builder(
    WatchlistSnapshotDeps(
        get_storage=lambda: storage,
        write_markdown_snapshot=lambda path, content: _write_markdown_snapshot(path, content),
        to_change_pct=lambda value: to_change_pct(value),
    )
)
_watchlist_performance_refresher = WatchlistPerformanceRefresher(
    WatchlistPerformanceDeps(
        logger=logger,
        get_storage=lambda: storage,
        thresholds=WATCHLIST_REVISIT_THRESHOLDS,
        build_revisit_rule=lambda window, label, change_pct, threshold: build_revisit_rule(
            window,
            label,
            change_pct,
            threshold,
        ),
        to_change_pct=lambda value: to_change_pct(value),
        get_portfolio_and_weekly=ak_get_portfolio_and_weekly,
        get_weekly_performance=ak_get_weekly_performance,
        get_portfolio_returns=ak_get_portfolio_returns,
    )
)
_refresh_watch_candidate_perf = _watchlist_performance_refresher.refresh
_filings_service = build_default_filings_service(storage.base_dir)
_build_weekly_review_snapshot = build_weekly_review_snapshot_builder(
    WeeklyReviewHelperExportDeps(
        get_storage=lambda: storage,
        write_markdown_snapshot=lambda path, content: _write_markdown_snapshot(path, content),
        fmt_money=lambda value: fmt_money(value),
        fmt_number=lambda value: fmt_number(value),
        get_stock_commentary=lambda storage_obj, records, rolling_days=7, week_id=None: get_stock_commentary(
            storage_obj,
            records,
            rolling_days=rolling_days,
            week_id=week_id,
        ),
    )
)
_build_weekly_reviews_export_response = build_weekly_reviews_export_response_builder_v2(
    WeeklyReviewHelperExportDeps(
        get_storage=lambda: storage,
        write_markdown_snapshot=lambda path, content: _write_markdown_snapshot(path, content),
        fmt_money=lambda value: fmt_money(value),
        fmt_number=lambda value: fmt_number(value),
        get_stock_commentary=lambda storage_obj, records, rolling_days=7, week_id=None: get_stock_commentary(
            storage_obj,
            records,
            rolling_days=rolling_days,
            week_id=week_id,
        ),
    )
)
_has_weekly_review_content = has_weekly_review_content
_normalize_weekly_review_payload = build_normalize_weekly_review_payload(
    config_value=lambda key, default=None: _config_value(key, default),
)
_attach_weekly_review_analyses = build_attach_weekly_review_analyses(
    WeeklyReviewAnalysisDeps(
        get_weekly_review_manager=lambda: _get_weekly_review_manager(),
        logger=logger,
        review_projection_provider=lambda week_id, review: _ibkr_projected_weekly_review(week_id, review),
    )
)
_resolve_effective_weekly_review = build_resolve_effective_weekly_review(
    WeeklyReviewResolutionDeps(
        get_storage=lambda: storage,
        get_week_id=get_week_id,
        has_weekly_review_content=_has_weekly_review_content,
        attach_weekly_review_analyses=_attach_weekly_review_analyses,
        normalize_weekly_review_payload=_normalize_weekly_review_payload,
    )
)
_weekly_review_task_runners = build_weekly_review_task_runners(
    WeeklyReviewTaskRunnerDeps(
        get_week_id=get_week_id,
        get_weekly_review_manager=lambda: _get_weekly_review_manager(),
        get_storage=lambda: storage,
        patch_task_record=lambda task, patch: _patch_task_record(task, patch),
        is_llm_failure_text=lambda text: _is_llm_failure_text(text),
        get_runtime_meta=lambda: get_runtime_meta(),
    )
)
_weekly_review_generate_runner = _weekly_review_task_runners.generate
_weekly_review_synthesize_runner = _weekly_review_task_runners.synthesize
_weekly_review_chat_runner = _weekly_review_task_runners.chat
_weekly_review_task_submit_handlers = build_weekly_review_task_submit_handlers(
    WeeklyReviewTaskSubmitDeps(
        get_week_id=get_week_id,
        get_task_manager=lambda: _get_task_manager(),
        task_payload=lambda *args, **kwargs: _task_api_helpers.task_payload(*args, **kwargs),
        task_error_response=lambda *args, **kwargs: _task_api_helpers.task_error_response(*args, **kwargs),
        logger=logger,
    )
)
api_submit_weekly_review_generate_task = _weekly_review_task_submit_handlers.generate
api_weekly_synthesize = _weekly_review_task_submit_handlers.synthesize
api_weekly_chat = _weekly_review_task_submit_handlers.chat
_weekly_review_export_handlers = build_weekly_review_export_handlers(
    WeeklyReviewRuntimeExportDeps(
        get_storage=lambda: storage,
        safe_int=lambda value, default: _safe_int(value, default),
        get_week_id=get_week_id,
        to_bool=lambda value, default=False: _to_bool(value, default),
        build_weekly_review_snapshot=lambda week_id: _build_weekly_review_snapshot(week_id),
        build_weekly_reviews_export_response=lambda week_ids, prefix: _build_weekly_reviews_export_response(week_ids, prefix),
        sync_snapshot_to_ima=ima_sync.sync_snapshot_to_ima,
        ima_sync_key_weekly_review=_ima_sync_key_weekly_review,
        json_safe=lambda value: _json_safe(value),
    )
)
api_export_current_weekly_review_markdown = _weekly_review_export_handlers.export_current_markdown
api_export_all_weekly_reviews_markdown = _weekly_review_export_handlers.export_all_markdown
api_export_recent_weekly_reviews_markdown = _weekly_review_export_handlers.export_recent_markdown
api_sync_weekly_review_to_ima = _weekly_review_export_handlers.sync_weekly_review_to_ima
_weekly_review_api_handlers = build_weekly_review_api_handlers(
    WeeklyReviewApiDeps(
        get_weekly_review_manager=lambda: _get_weekly_review_manager(),
        get_week_id=get_week_id,
        get_storage=lambda: storage,
        get_stock_filings=lambda stock_id, stock_name, ticker, week_id=None, rolling_days=None, force_refresh=False: _filings_service.get_stock_filings(
            stock_id=stock_id,
            stock_name=stock_name,
            ticker=ticker,
            week_id=week_id,
            rolling_days=rolling_days,
            force_refresh=force_refresh,
        ),
        build_portfolio_performance=lambda **kwargs: _build_weekly_portfolio_performance(**kwargs),
        json_safe=lambda value: _json_safe(value),
        safe_int=lambda value, default: _safe_int(value, default),
        to_bool=lambda value, default=False: _to_bool(value, default),
        logger=logger,
    )
)
api_get_market_context = _weekly_review_api_handlers.get_market_context
api_refresh_market_context = _weekly_review_api_handlers.refresh_market_context
api_refresh_macro_events = _weekly_review_api_handlers.refresh_macro_events
api_summarize_market_context = _weekly_review_api_handlers.summarize_market_context
api_refresh_stock_news = _weekly_review_api_handlers.refresh_stock_news
api_refresh_stock_performance = _weekly_review_api_handlers.refresh_stock_performance
api_refresh_all_news = _weekly_review_api_handlers.refresh_all_news
api_refresh_all_news_and_scan = _weekly_review_api_handlers.refresh_all_news_and_scan
api_refresh_portfolio_prices = _weekly_review_api_handlers.refresh_portfolio_prices
api_refresh_all_performance = _weekly_review_api_handlers.refresh_all_performance
api_generate_news_summary = _weekly_review_api_handlers.generate_news_summary
api_generate_weekly_stock_ai_summary = _weekly_review_api_handlers.generate_weekly_stock_ai_summary
api_generate_weekly_stock_ai_summaries_batch = _weekly_review_api_handlers.generate_weekly_stock_ai_summaries_batch
api_save_stock_weekly_view = _weekly_review_api_handlers.save_stock_weekly_view
api_save_weekly_portfolio = _weekly_review_api_handlers.save_weekly_portfolio
api_save_rebalancing_ops = _weekly_review_api_handlers.save_rebalancing_ops
api_apply_rebalancing = _weekly_review_api_handlers.apply_rebalancing
api_get_portfolio_performance = _weekly_review_api_handlers.get_portfolio_performance

_task_manager_lock = threading.RLock()

TASK_STATUS_MESSAGES = {
    "queued": "Text",
    "running": "Text",
    "completed": "Text",
    "failed": "TextFailed",
    "cancelled": "TextCancel",
    "not_found": "Text",
    "invalid_request": "Text",
}
_task_api_helpers = build_task_api_helpers(
    TaskApiHelperDeps(
        task_status_messages=TASK_STATUS_MESSAGES,
    )
)

task_manager: Optional[BackgroundTaskManager] = None
_app_services = None


_task_runtime = build_task_runtime_accessors(
    TaskRuntimeDeps(
        get_storage=lambda: storage,
        get_task_manager_state=lambda: task_manager,
        set_task_manager_state=lambda value: globals().__setitem__("task_manager", value),
        task_manager_lock=_task_manager_lock,
        background_task_manager_factory=lambda **kwargs: BackgroundTaskManager(**kwargs),
        safe_int=_safe_int,
        logger=logger,
        get_noop_runner=lambda: noop_task_runner,
        get_weekly_review_generate_runner=lambda: _weekly_review_generate_runner,
        get_weekly_review_synthesize_runner=lambda: _weekly_review_synthesize_runner,
        get_weekly_review_chat_runner=lambda: _weekly_review_chat_runner,
        get_us_screener_scan_task_runner=lambda: _us_screener_scan_task_runner,
        get_xueqiu_export_runner=lambda: _app_services.xueqiu_task_runners.export,
        get_xueqiu_prepare_session_runner=lambda: _app_services.xueqiu_task_runners.prepare_session,
        save_task_record=lambda task_id, patch: storage.save_task_record(task_id, patch),
        wechat_set=lambda task_id, payload: _wechat_tasks.set(task_id, payload),
        wechat_get=lambda task_id: _wechat_tasks.get(task_id),
    )
)
_patch_task_record = _task_runtime.patch_task_record
_create_background_task_manager = _task_runtime.create_background_task_manager
_get_task_manager = _task_runtime.get_task_manager


requires_auth = auth_service.requires_auth


reset_llm_client = _runtime_state.reset_llm_client
_config_value = _runtime_state.config_value
_resolve_model_config = _runtime_state.resolve_model_config
get_client = _runtime_state.get_client
_get_env_collector = _runtime_state.get_env_collector
_get_research_engine = _runtime_state.get_research_engine
_get_weekly_review_manager = _runtime_state.get_weekly_review_manager


_get_us_screener_service = build_lazy_us_screener_service_getter(
    storage=storage,
    get_state=lambda: us_screener_service,
    set_state=lambda value: globals().__setitem__("us_screener_service", value),
    service_factory=lambda storage_obj: USScreenerService(storage_obj),
)


_us_screener_runtime_helpers = build_us_screener_runtime_helpers(
    USScreenerRuntimeHelperDeps(
        get_storage=lambda: storage,
        get_service=lambda: _get_us_screener_service(),
        get_job_thread=lambda: globals().get("_us_screener_job_thread"),
        set_job_thread=lambda value: globals().__setitem__("_us_screener_job_thread", value),
        get_retry_timer=lambda: globals().get("_us_screener_retry_timer"),
        set_retry_timer=lambda value: globals().__setitem__("_us_screener_retry_timer", value),
        get_retry_eta=lambda: globals().get("_us_screener_retry_eta"),
        set_retry_eta=lambda value: globals().__setitem__("_us_screener_retry_eta", value),
        job_lock=_us_screener_job_lock,
        now_factory=lambda: datetime.now(),
        seconds_until=lambda target: _seconds_until(target),
        auto_resume_delay_seconds=US_SCREENER_AUTO_RESUME_DELAY_SECONDS,
        logger=logger,
        run_job_callback=lambda resume=False: _us_screener_runtime_helpers.run_job(resume=resume),
    )
)
_start_us_screener_thread = _us_screener_runtime_helpers.start_thread
_cancel_us_screener_auto_retry = _us_screener_runtime_helpers.cancel_auto_retry
_schedule_us_screener_auto_retry = _us_screener_runtime_helpers.schedule_auto_retry
_default_us_screener_status = _us_screener_runtime_helpers.default_status
_save_us_screener_status = _us_screener_runtime_helpers.save_status
_get_us_screener_partial_summary = _us_screener_runtime_helpers.get_partial_summary
_run_us_screener_job = _us_screener_runtime_helpers.run_job
_us_screener_scan_task_runner = build_us_screener_scan_task_runner(
    USScreenerScanRunnerDeps(
        runtime_helpers=_us_screener_runtime_helpers,
        get_service=lambda: _get_us_screener_service(),
        get_storage=lambda: storage,
        patch_task_record=lambda task, patch: _patch_task_record(task, patch),
        safe_int=lambda value, default: _safe_int(value, default),
        to_bool=lambda value, default=False: _to_bool(value, default),
        get_job_thread=lambda: globals().get("_us_screener_job_thread"),
        set_job_thread=lambda value: globals().__setitem__("_us_screener_job_thread", value),
        job_lock=_us_screener_job_lock,
        current_thread_factory=lambda: threading.current_thread(),
        strategy_momentum_spike=STRATEGY_MOMENTUM_SPIKE,
        strategy_post_52w_low_reversal=STRATEGY_POST_52W_LOW_REVERSAL,
    )
)
get_runtime_meta = _runtime_state.get_runtime_meta
_build_news_source_diagnostics = _app_data_helpers.build_news_source_diagnostics
_get_stocks_with_research_status = _app_data_helpers.get_stocks_with_research_status

_build_xueqiu_client = XueqiuClient


_app_services = build_app_services(
    AppServiceDeps(
        app=app,
        storage=storage,
        get_storage=lambda: storage,
        config_center=config_center,
        auth_service=auth_service,
        logger=logger,
        project_root=str(PROJECT_ROOT),
        web_root=str(WEB_ROOT),
        get_client=get_client,
        reset_llm_client=reset_llm_client,
        config_value=_config_value,
        get_runtime_meta=get_runtime_meta,
        get_week_id=get_week_id,
        get_weekly_review_manager=_get_weekly_review_manager,
        get_env_collector=_get_env_collector,
        get_research_engine=_get_research_engine,
        get_stocks_with_research_status=_get_stocks_with_research_status,
        build_news_source_diagnostics=_build_news_source_diagnostics,
        resolve_effective_weekly_review=_resolve_effective_weekly_review,
        get_task_manager=_get_task_manager,
        patch_task_record=_patch_task_record,
        task_api_helpers=_task_api_helpers,
        safe_int=_safe_int,
        to_bool=_to_bool,
        json_safe=_json_safe,
        chat_with_retry=_chat_with_retry,
        is_llm_failure_text=_is_llm_failure_text,
        safe_filename_part=_safe_filename_part,
        write_markdown_snapshot=_write_markdown_snapshot,
        markdown_from_zsxq_daily_snapshot=_markdown_from_zsxq_daily_snapshot,
        xueqiu_client_factory=lambda **kwargs: _build_xueqiu_client(**kwargs),
        fetch_price_chart=_fetch_price_chart,
        load_price_frames=lambda tickers, start_date=None, end_date=None: _portfolio_history_frames_loader(
            tickers,
            start_date=start_date,
            end_date=end_date,
        ),
        external_cash_flows_hkd=_portfolio_external_cash_flows_hkd(),
        review_projection_provider=_ibkr_projected_weekly_review,
        now_factory=datetime.now,
        render_template=render_template,
        redirect=redirect,
        send_from_directory=send_from_directory,
        get_json=lambda: request.get_json(silent=True),
        factor_analysis_service_factory=lambda: FactorAnalysisService(
            storage=storage,
            analyze_portfolio_factors=__import__("core.factor_analysis", fromlist=["analyze_portfolio_factors"]).analyze_portfolio_factors,
            review_projection_provider=_ibkr_projected_weekly_review,
        ),
        ima_sync_key_zsxq_daily=_ima_sync_key_zsxq_daily,
        unauthorized_response=lambda: Response(
            "Authentication required to update auth settings.",
            401,
            {"WWW-Authenticate": 'Basic realm="Investment Assistant"'},
        ),
    )
)

if task_manager is None:
    task_manager = _create_background_task_manager(storage)

app.before_request(build_options_handler())
app.after_request(build_cors_after_request_handler())


def create_app() -> Flask:
    register_application_modules(
        AppBootstrapDeps(
            app=app,
            register_feature_modules_fn=register_feature_modules,
            auth_guard=requires_auth,
            llm_config_service=_app_services.llm_config_service,
            broker_import_service=_app_services.broker_import_service,
            app_shell_service=_app_services.app_shell_service,
            decision_review_service=_app_services.decision_review_service,
            factor_analysis_service=_app_services.factor_analysis_service,
            system_api_service=_app_services.system_api_service,
            shell_pages_service=_app_services.shell_pages_service,
            task_api_service=_app_services.task_api_service,
            preferences_service=_app_services.preferences_service,
            research_service=_app_services.research_service,
            stocks_service=_app_services.stocks_service,
            xueqiu_service=_app_services.xueqiu_service,
            zsxq_service=_app_services.zsxq_service,
            weekly_review_module_deps=WeeklyReviewModuleDeps(
                get_weekly_review_manager=_get_weekly_review_manager,
                get_week_id=get_week_id,
                get_storage=lambda: storage,
                resolve_effective_weekly_review=_resolve_effective_weekly_review,
                attach_weekly_review_analyses=_attach_weekly_review_analyses,
                normalize_weekly_review_payload=_normalize_weekly_review_payload,
                has_weekly_review_content=_has_weekly_review_content,
                serialize_ima_sync_status=_serialize_ima_sync_status,
                ima_sync_key_weekly_review=_ima_sync_key_weekly_review,
                json_safe=_json_safe,
                build_portfolio_performance=lambda **kwargs: _build_weekly_portfolio_performance(**kwargs),
                get_stock_commentary=lambda storage_obj, records, rolling_days=7, week_id=None: get_stock_commentary(
                    storage_obj,
                    records,
                    rolling_days=rolling_days,
                    week_id=week_id,
                ),
                get_stock_filings=lambda stock_id, stock_name, ticker, week_id=None, rolling_days=None, force_refresh=False: _filings_service.get_stock_filings(
                    stock_id=stock_id,
                    stock_name=stock_name,
                    ticker=ticker,
                    week_id=week_id,
                    rolling_days=rolling_days,
                    force_refresh=force_refresh,
                ),
                logger=logger,
            ),
            weekly_review_actions={
                "submit_weekly_review_generate_task": api_submit_weekly_review_generate_task,
                "export_current_weekly_review_markdown": api_export_current_weekly_review_markdown,
                "export_all_weekly_reviews_markdown": api_export_all_weekly_reviews_markdown,
                "export_recent_weekly_reviews_markdown": api_export_recent_weekly_reviews_markdown,
                "get_market_context": api_get_market_context,
                "refresh_market_context": api_refresh_market_context,
                "summarize_market_context": api_summarize_market_context,
                "refresh_macro_events": api_refresh_macro_events,
                "refresh_stock_news": api_refresh_stock_news,
                "refresh_stock_performance": api_refresh_stock_performance,
                "refresh_all_news": api_refresh_all_news,
                "refresh_all_news_and_scan": api_refresh_all_news_and_scan,
                "refresh_portfolio_prices": api_refresh_portfolio_prices,
                "refresh_all_performance": api_refresh_all_performance,
                "generate_news_summary": api_generate_news_summary,
                "generate_weekly_stock_ai_summary": api_generate_weekly_stock_ai_summary,
                "generate_weekly_stock_ai_summaries_batch": api_generate_weekly_stock_ai_summaries_batch,
                "sync_weekly_review_to_ima": api_sync_weekly_review_to_ima,
                "save_stock_weekly_view": api_save_stock_weekly_view,
                "save_weekly_portfolio": api_save_weekly_portfolio,
                "save_rebalancing_ops": api_save_rebalancing_ops,
                "apply_rebalancing": api_apply_rebalancing,
                "get_portfolio_performance": api_get_portfolio_performance,
                "weekly_synthesize": api_weekly_synthesize,
                "weekly_chat": api_weekly_chat,
            },
            us_screener_module_deps=USScreenerModuleDeps(
                get_storage=lambda: storage,
                json_safe=_json_safe,
                default_status_factory=_default_us_screener_status,
                to_bool=_to_bool,
                safe_int=_safe_int,
                get_client=get_client,
                chat_with_retry=_chat_with_retry,
                is_llm_failure_text=_is_llm_failure_text,
                strategy_momentum_spike=STRATEGY_MOMENTUM_SPIKE,
                strategy_post_52w_low_reversal=STRATEGY_POST_52W_LOW_REVERSAL,
                get_partial_summary=lambda: _get_us_screener_partial_summary(),
                cancel_auto_retry=lambda: _cancel_us_screener_auto_retry(),
                start_thread=lambda **kwargs: _start_us_screener_thread(**kwargs),
                get_job_thread=lambda: _us_screener_job_thread,
                job_lock=_us_screener_job_lock,
            ),
            us_screener_actions={
                "scan": _app_services.task_api_service.submit_us_screener_scan_task,
            },
            watchlist_module_deps=WatchlistModuleDeps(
                get_week_id=get_week_id,
                recent_week_ids=lambda count=8: recent_week_ids(get_week_id, count=count),
                get_storage=lambda: storage,
                serialize_ima_sync_status=_serialize_ima_sync_status,
                ima_sync_key_watchlist=_ima_sync_key_watchlist,
                json_safe=_json_safe,
                refresh_watch_candidate_perf=lambda candidate: _refresh_watch_candidate_perf(candidate),
                fetch_price_chart=lambda candidate, range_name: _fetch_price_chart(candidate, range_name=range_name),
                to_bool=_to_bool,
                get_stock_commentary=lambda storage_obj, records, rolling_days=7, week_id=None: get_stock_commentary(
                    storage_obj,
                    records,
                    rolling_days=rolling_days,
                    week_id=week_id,
                ),
                get_stock_filings=lambda stock_id, stock_name, ticker, week_id=None, rolling_days=None, force_refresh=False: _filings_service.get_stock_filings(
                    stock_id=stock_id,
                    stock_name=stock_name,
                    ticker=ticker,
                    week_id=week_id,
                    rolling_days=rolling_days,
                    force_refresh=force_refresh,
                ),
                generate_watch_candidate_ai_judgment=lambda candidate, commentary_entry=None, filings_entry=None, force=False: _call_watch_candidate_ai_judgment(
                    candidate,
                    commentary_entry=commentary_entry,
                    filings_entry=filings_entry,
                    force=force,
                ),
                build_watchlist_snapshot=_build_watchlist_snapshot,
                sync_snapshot_to_ima=ima_sync.sync_snapshot_to_ima,
                logger=logger,
            ),
        )
    )
    return app


create_app()


if __name__ == "__main__":
    app.run(
        host=(os.environ.get("FLASK_HOST") or "0.0.0.0").strip(),
        port=_safe_int(os.environ.get("FLASK_PORT"), 5000),
        debug=_env_flag("FLASK_DEBUG", False),
    )
