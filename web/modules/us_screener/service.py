from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import jsonify, render_template, request

from core.stock_overview import build_stock_overview
from web.request_parsing import load_json_object
from web.services.tradingview_symbols import annotate_us_screener_payload


def _num(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _fmt_pct(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "--"
    return f"{number:+.2f}%"


def _candidate_theme(item: Dict[str, Any]) -> str:
    ticker = str(item.get("ticker") or item.get("stock_id") or "").upper()
    text = " ".join(
        str(item.get(key) or "")
        for key in ("stock_name", "sector", "industry", "company_intro")
    ).lower()
    ticker_groups = [
        ("China ADR", {"BABA", "JD", "PDD", "BIDU", "NTES", "BILI", "TME", "NIO", "XPEV", "LI", "YMM", "ZTO", "BEKE"}),
        ("AI Infra", {"NVDA", "SMCI", "DELL", "VRT", "ANET", "ARM", "AVGO", "MRVL", "MU", "AMD"}),
        ("Crypto-linked", {"COIN", "MSTR", "MARA", "RIOT", "CLSK", "IREN", "SAMPLE", "BITF"}),
        ("Nuclear/Power", {"CEG", "VST", "TLN", "GEV", "OKLO", "SMR", "NNE"}),
        ("Semis", {"TSM", "ASML", "LRCX", "AMAT", "KLAC", "ON", "QCOM", "INTC"}),
    ]
    for label, tickers in ticker_groups:
        if ticker in tickers:
            return label
    keyword_groups = [
        ("AI Infra", ("artificial intelligence", " ai ", "data center", "server", "gpu", "accelerator", "cloud infrastructure")),
        ("Biotech Squeeze", ("biotech", "biotechnology", "pharmaceutical", "clinical", "therapy", "therapeutics", "drug")),
        ("China ADR", ("china", "chinese", "beijing", "shanghai", "adr")),
        ("Nuclear/Power", ("nuclear", "uranium", "power generation", "electric utility", "grid", "energy infrastructure")),
        ("Crypto-linked", ("bitcoin", "crypto", "blockchain", "digital asset", "mining")),
        ("Semis", ("semiconductor", "chip", "wafer", "foundry", "memory")),
        ("Small-cap Momentum", ("small cap", "micro cap")),
    ]
    padded = f" {text} "
    for label, keywords in keyword_groups:
        if any(keyword in padded for keyword in keywords):
            return label
    for key in ("theme", "sector", "industry"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return "Unclassified"


def _missing_signal_hints(item: Dict[str, Any]) -> List[str]:
    hints: List[str] = []
    if _num(item.get("market_cap")) is None:
        hints.append("Text")
    if _num(item.get("latest_close")) is None:
        hints.append("Text")
    if _num(item.get("max_daily_gain_5d_pct")) is None and _num(item.get("rebound_from_new_low_pct")) is None:
        hints.append("Text")
    if _num(item.get("distance_above_200ma_pct")) is None:
        hints.append("Text200Text")
    if not str(item.get("company_intro") or "").strip():
        hints.append("Text")
    return hints[:4]


def _candidate_signal_tags(item: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    strategy = str(item.get("strategy") or "").strip()
    if strategy == "post_52w_low_reversal":
        tags.append("52w_low_reversal")
    else:
        tags.append("momentum_spike")
    if _num(item.get("max_daily_gain_5d_pct")) is not None:
        tags.append("price_momentum")
    if _num(item.get("rebound_from_new_low_pct")) is not None:
        tags.append("rebound")
    if _num(item.get("distance_above_200ma_pct")) is not None:
        tags.append("ma200_context")
    if item.get("sector"):
        tags.append(str(item.get("sector")).strip())
    dedup: List[str] = []
    for tag in tags:
        if tag and tag not in dedup:
            dedup.append(tag)
    return dedup[:6]


def _signal_reason_stack(item: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if item.get("trigger_trade_date"):
        reasons.append(f"TextDate {item.get('trigger_trade_date')}")
    if _num(item.get("max_daily_gain_5d_pct")) is not None:
        reasons.append(f"5Text {_fmt_pct(item.get('max_daily_gain_5d_pct'))}")
    if _num(item.get("avg_volume_5d_vs_3m")) is not None:
        reasons.append(f"5Text / 3Text {float(item.get('avg_volume_5d_vs_3m')):.2f}x")
    if _num(item.get("gain_30d_pct")) is not None:
        reasons.append(f"30Text {_fmt_pct(item.get('gain_30d_pct'))}")
    if _num(item.get("rebound_from_new_low_pct")) is not None:
        reasons.append(f"Text52Text {_fmt_pct(item.get('rebound_from_new_low_pct'))}")
    if _num(item.get("distance_above_200ma_pct")) is not None:
        reasons.append(f"Text200Text {_fmt_pct(item.get('distance_above_200ma_pct'))}")
    if item.get("days_since_new_low") not in (None, ""):
        reasons.append(f"Text52Text {item.get('days_since_new_low')} Text")
    theme = _candidate_theme(item)
    if theme != "Unclassified":
        reasons.append(f"Text/Text: {theme}")
    for hint in _missing_signal_hints(item):
        reasons.append(hint)
    return reasons[:6]


def _signal_strength(item: Dict[str, Any]) -> int:
    score = 35
    max_gain = _num(item.get("max_daily_gain_5d_pct"))
    gain_30d = _num(item.get("gain_30d_pct"))
    rebound = _num(item.get("rebound_from_new_low_pct"))
    above_200 = _num(item.get("distance_above_200ma_pct"))
    if max_gain is not None:
        score += min(28, max(0, max_gain * 1.1))
    if gain_30d is not None:
        score += min(16, max(-10, gain_30d / 2.5))
    if rebound is not None:
        score += min(24, max(0, rebound / 3.0))
    if above_200 is not None:
        score += min(14, max(-8, above_200 / 5.0))
    return max(0, min(99, int(round(score))))


def _research_priority(item: Dict[str, Any]) -> int:
    score = _signal_strength(item)
    market_cap = _num(item.get("market_cap"))
    gain_30d = _num(item.get("gain_30d_pct"))
    max_gain = _num(item.get("max_daily_gain_5d_pct"))
    above_200 = _num(item.get("distance_above_200ma_pct"))
    if market_cap is not None:
        if market_cap >= 2_000_000_000:
            score += 8
        elif market_cap < 300_000_000:
            score -= 14
    if gain_30d is not None and gain_30d > 80:
        score -= 10
    if max_gain is not None and max_gain > 35:
        score -= 8
    if above_200 is not None and above_200 > 90:
        score -= 8
    if not str(item.get("company_intro") or "").strip():
        score -= 4
    return max(0, min(99, int(round(score))))


def _priority_tier(score: int) -> Dict[str, str]:
    if score >= 82:
        return {"tier": "A", "label": "Must Review"}
    if score >= 68:
        return {"tier": "B", "label": "Worth a Look"}
    if score >= 50:
        return {"tier": "C", "label": "Weak Signal"}
    return {"tier": "D", "label": "Noise"}


def _idea_note(item: Dict[str, Any]) -> str:
    reasons = _signal_reason_stack(item)
    if not reasons:
        return "TextResult, Text, Text. "
    return f"{reasons[0]}, TextResearchTextTradeText. "


def _risk_note(item: Dict[str, Any]) -> str:
    flags: List[str] = []
    gain_30d = _num(item.get("gain_30d_pct"))
    max_gain = _num(item.get("max_daily_gain_5d_pct"))
    above_200 = _num(item.get("distance_above_200ma_pct"))
    market_cap = _num(item.get("market_cap"))
    if gain_30d is not None and gain_30d > 80:
        flags.append("Text")
    if max_gain is not None and max_gain > 35:
        flags.append("Text")
    if above_200 is not None and above_200 > 90:
        flags.append("Text")
    if market_cap is not None and market_cap < 300_000_000:
        flags.append("TextRisk")
    if not flags:
        return "TextRiskTextConfirm, TextResearchTextNews, Text. "
    return " / ".join(flags)


def enrich_idea_board_payload(payload: Dict[str, Any], research_queue: List[Dict[str, Any]]) -> Dict[str, Any]:
    output = dict(payload or {})
    strategies = output.get("strategies") if isinstance(output.get("strategies"), dict) else {}
    all_items: List[Dict[str, Any]] = []
    as_of_market_date = str(output.get("as_of_market_date") or "").strip()

    def signal_date_for_candidate(row: Dict[str, Any]) -> str:
        return str(
            row.get("signal_date")
            or as_of_market_date
            or row.get("trigger_trade_date")
            or row.get("new_low_trade_date")
            or row.get("latest_trade_date")
            or ""
        ).strip()

    def signal_key(row: Dict[str, Any], strategy_id: str | None = None) -> str:
        ticker = str(row.get("ticker") or row.get("stock_id") or "").strip().upper()
        strategy = str(row.get("strategy") or strategy_id or "").strip().lower()
        signal_date = signal_date_for_candidate(row)
        return "|".join([ticker, strategy, signal_date])

    queue_by_signal_key = {
        signal_key(item): item
        for item in research_queue or []
        if isinstance(item, dict) and str(item.get("ticker") or item.get("stock_id") or "").strip()
    }
    queue_without_date_by_ticker_strategy = {
        "|".join(
            [
                str(item.get("ticker") or item.get("stock_id") or "").strip().upper(),
                str(item.get("strategy") or "").strip().lower(),
            ]
        ): item
        for item in research_queue or []
        if isinstance(item, dict)
        and str(item.get("ticker") or item.get("stock_id") or "").strip()
        and not str(item.get("signal_date") or "").strip()
    }

    def enrich_item(item: Dict[str, Any], strategy_id: str) -> Dict[str, Any]:
        row = dict(item or {})
        row.setdefault("strategy", strategy_id)
        ticker = str(row.get("ticker") or row.get("stock_id") or "").upper()
        row_signal_date = signal_date_for_candidate(row)
        queue_item = queue_by_signal_key.get(signal_key(row, strategy_id))
        if queue_item is None:
            queue_item = queue_without_date_by_ticker_strategy.get(
                "|".join([ticker, str(row.get("strategy") or strategy_id or "").strip().lower()])
            )
        row["theme"] = _candidate_theme(row)
        row["signal_tags"] = _candidate_signal_tags(row)
        row["signal_reason_stack"] = _signal_reason_stack(row)
        row["missing_signal_hints"] = _missing_signal_hints(row)
        row["signal_strength"] = _signal_strength(row)
        row["research_priority"] = _research_priority(row)
        row["research_priority_tier"] = _priority_tier(row["research_priority"])
        row["idea_note"] = _idea_note(row)
        row["risk_note"] = _risk_note(row)
        row["signal_date"] = row_signal_date
        row["idea_status"] = str((queue_item or {}).get("status") or "").strip().lower()
        row["research_queue_id"] = str((queue_item or {}).get("id") or "").strip()
        row["dismiss_reason"] = str((queue_item or {}).get("dismiss_reason") or "").strip()
        all_items.append(row)
        return row

    for strategy_id, strategy in list(strategies.items()):
        if not isinstance(strategy, dict):
            continue
        strategy["items"] = [
            enrich_item(item, strategy_id)
            for item in list(strategy.get("items") or [])
            if isinstance(item, dict)
        ]
        presets = strategy.get("presets")
        if isinstance(presets, dict):
            for preset in presets.values():
                if isinstance(preset, dict):
                    preset["items"] = [
                        enrich_item(item, strategy_id)
                        for item in list(preset.get("items") or [])
                        if isinstance(item, dict)
                    ]

    themes: Dict[str, Dict[str, Any]] = {}
    seen_keys: set[str] = set()
    for item in all_items:
        key = str(item.get("ticker") or item.get("stock_id") or "")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        theme = _candidate_theme(item)
        bucket = themes.setdefault(
            theme,
            {
                "theme": theme,
                "matched": 0,
                "avg_move": 0.0,
                "representative_tickers": [],
                "dominant_signal": "",
                "_moves": [],
                "_signals": {},
            },
        )
        bucket["matched"] += 1
        move = _num(item.get("max_daily_gain_5d_pct"))
        if move is None:
            move = _num(item.get("rebound_from_new_low_pct"))
        if move is not None:
            bucket["_moves"].append(move)
        if len(bucket["representative_tickers"]) < 4:
            bucket["representative_tickers"].append(str(item.get("ticker") or item.get("stock_id") or "").upper())
        signal = str(item.get("strategy") or "")
        bucket["_signals"][signal] = bucket["_signals"].get(signal, 0) + 1

    theme_strip = []
    for bucket in themes.values():
        moves = bucket.pop("_moves", [])
        signals = bucket.pop("_signals", {})
        bucket["avg_move"] = round(sum(moves) / len(moves), 2) if moves else None
        bucket["dominant_signal"] = max(signals.items(), key=lambda pair: pair[1])[0] if signals else ""
        theme_strip.append(bucket)
    output["theme_strip"] = sorted(theme_strip, key=lambda row: (row.get("matched") or 0, row.get("avg_move") or 0), reverse=True)[:8]
    output["research_queue"] = list(research_queue or [])
    output["strategies"] = strategies
    return output


@dataclass
class USScreenerModuleService:
    us_screener_page: Callable[[], Any]
    latest: Callable[[], Any]
    status: Callable[[], Any]
    context: Callable[[], Any]
    stock_overview: Callable[[], Any]
    alerts: Callable[[], Any]
    create_alert: Callable[[], Any]
    delete_alert: Callable[[str], Any]
    research_queue: Callable[[], Any]
    create_research_queue_item: Callable[[], Any]
    update_research_queue_item: Callable[[str], Any]
    delete_research_queue_item: Callable[[str], Any]
    ai_brief: Callable[[], Any]
    run: Callable[[], Any]
    scan: Callable[[], Any]


@dataclass
class USScreenerModuleDeps:
    get_storage: Callable[[], Any]
    json_safe: Callable[[Any], Any]
    default_status_factory: Callable[[], Any]
    to_bool: Callable[[Any, bool], bool]
    safe_int: Callable[[Any, int], int]
    get_client: Callable[[], Any]
    chat_with_retry: Callable[..., Any]
    is_llm_failure_text: Callable[[Any], bool]
    strategy_momentum_spike: str
    strategy_post_52w_low_reversal: str
    get_partial_summary: Callable[[], Any]
    cancel_auto_retry: Callable[[], Any]
    start_thread: Callable[..., Any]
    get_job_thread: Callable[[], Any]
    job_lock: Any


def build_us_screener_module_service(
    deps: USScreenerModuleDeps,
    *,
    scan: Callable[[], Any],
) -> USScreenerModuleService:
    def _json_object_body_or_error():
        return load_json_object()

    def us_screener_page():
        return render_template("us_screener.html")

    def latest():
        storage = deps.get_storage()
        payload = storage.get_us_screener_latest()
        research_queue = storage.get_us_screener_research_queue()
        annotated = annotate_us_screener_payload(payload)
        return jsonify(deps.json_safe(enrich_idea_board_payload(annotated, research_queue)))

    def status():
        return jsonify(deps.json_safe(deps.default_status_factory()))

    def context():
        storage = deps.get_storage()
        watchlist = storage.get_watchlist() or {}
        watch_candidates = list(watchlist.get("candidates") or [])
        stock_pool = list(storage.list_stocks() or [])
        research_queue = storage.get_us_screener_research_queue()
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "watchlist": [
                        {
                            "stock_id": item.get("stock_id"),
                            "stock_name": item.get("stock_name"),
                            "ticker": item.get("ticker"),
                            "theme": item.get("theme"),
                            "industry": item.get("industry"),
                            "status": item.get("status"),
                        }
                        for item in watch_candidates
                        if item.get("stock_id")
                    ],
                    "stock_pool": [
                        {
                            "stock_id": str(item.get("stock_id") or "").strip().upper(),
                            "stock_name": item.get("stock_name"),
                            "ticker": str(item.get("ticker") or "").strip().upper(),
                            "sector": item.get("sector"),
                            "industry": item.get("industry"),
                        }
                        for item in stock_pool
                        if item.get("stock_id")
                    ],
                    "research_queue": research_queue,
                }
            )
        )

    def _latest_candidate_for_ticker(ticker: str) -> Dict[str, Any]:
        target = str(ticker or "").strip().upper()
        if not target:
            return {}
        payload = deps.get_storage().get_us_screener_latest()
        annotated = annotate_us_screener_payload(payload)
        strategies = annotated.get("strategies") if isinstance(annotated.get("strategies"), dict) else {}

        def candidate_key(row: Dict[str, Any]) -> str:
            return str(row.get("ticker") or row.get("stock_id") or "").strip().upper()

        def maybe_row(row: Any, strategy_id: str) -> Dict[str, Any]:
            if not isinstance(row, dict):
                return {}
            if candidate_key(row) != target and str(row.get("stock_id") or "").strip().upper() != target:
                return {}
            candidate = dict(row)
            candidate.setdefault("strategy", strategy_id)
            return candidate

        for strategy_id, strategy in strategies.items():
            if not isinstance(strategy, dict):
                continue
            for row in list(strategy.get("items") or []):
                matched = maybe_row(row, str(strategy_id))
                if matched:
                    return matched
            presets = strategy.get("presets")
            if isinstance(presets, dict):
                for preset in presets.values():
                    if not isinstance(preset, dict):
                        continue
                    for row in list(preset.get("items") or []):
                        matched = maybe_row(row, str(strategy_id))
                        if matched:
                            return matched
        return {}

    def stock_overview():
        ticker = str(request.args.get("ticker") or request.args.get("stock_id") or "").strip().upper()
        if not ticker:
            return jsonify({"success": False, "error": "missing_ticker", "message": "Text ticker"}), 400
        force = deps.to_bool(request.args.get("force"), False)
        storage = deps.get_storage()
        base_dir = Path(getattr(storage, "base_dir", Path.home() / "REDACTED"))
        candidate = _latest_candidate_for_ticker(ticker)
        payload = build_stock_overview(
            ticker,
            candidate=candidate,
            cache_dir=base_dir / "stock_overview_cache",
            force=force,
        )
        return jsonify(deps.json_safe(payload))

    def alerts():
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "alerts": deps.get_storage().get_us_screener_alerts(),
                }
            )
        )

    def create_alert():
        payload, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        try:
            alert = deps.get_storage().upsert_us_screener_alert(payload)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify(deps.json_safe({"success": True, "alert": alert}))

    def delete_alert(alert_id: str):
        deleted = deps.get_storage().delete_us_screener_alert(alert_id)
        if not deleted:
            return jsonify({"success": False, "error": "Alert not found"}), 404
        return jsonify({"success": True})

    def research_queue():
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "items": deps.get_storage().get_us_screener_research_queue(),
                }
            )
        )

    def create_research_queue_item():
        payload, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        try:
            item = deps.get_storage().upsert_us_screener_research_queue_item(payload)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify(deps.json_safe({"success": True, "item": item}))

    def update_research_queue_item(item_id: str):
        payload, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        item = deps.get_storage().update_us_screener_research_queue_item(item_id, payload)
        if item is None:
            return jsonify({"success": False, "error": "Research queue item not found"}), 404
        return jsonify(deps.json_safe({"success": True, "item": item}))

    def delete_research_queue_item(item_id: str):
        deleted = deps.get_storage().delete_us_screener_research_queue_item(item_id)
        if not deleted:
            return jsonify({"success": False, "error": "Research queue item not found"}), 404
        return jsonify({"success": True})

    def run():
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        resume_requested = deps.to_bool(data.get("resume"), False)
        with deps.job_lock:
            job_thread = deps.get_job_thread()
            if job_thread is not None and job_thread.is_alive():
                current_status = deps.get_storage().get_us_screener_job_status()
                payload = dict(current_status or {})
                payload["success"] = False
                payload["error"] = "Text"
                payload["partial_summary"] = deps.get_partial_summary()
                return jsonify(deps.json_safe(payload)), 409
            partial_state = deps.get_storage().get_us_screener_partial()
            pending_batches = 0
            pending_queue = partial_state.get("pending_batches") if isinstance(partial_state, dict) else None
            if resume_requested:
                pending_batches = len(pending_queue or [])
                if pending_batches <= 0:
                    return jsonify({"error": "CurrentText"}), 400
            partial_summary = deps.get_partial_summary() if resume_requested else None
            started_at = datetime.now().isoformat(timespec="seconds")
            progress_payload = {
                "total_batches": int((partial_summary or {}).get("total_batches") or 0),
                "completed_batches": int((partial_summary or {}).get("completed_batches") or 0),
                "current_batch_size": 0,
                "pending_batches": pending_batches,
            }
            message = "TextRefreshText" if resume_requested else "Text"
            if resume_requested and pending_batches:
                message = f"TextRefreshText {pending_batches} Text"
            deps.cancel_auto_retry()
            deps.get_storage().save_us_screener_job_status(
                {
                    "state": "running",
                    "started_at": started_at,
                    "finished_at": "",
                    "step": "resume_queued" if resume_requested else "queued",
                    "progress": progress_payload,
                    "message": message,
                    "last_error": "",
                    "partial_summary": partial_summary,
                }
            )
            deps.start_thread(resume=resume_requested)
        return jsonify({"success": True, "status": deps.json_safe(deps.get_storage().get_us_screener_job_status())})

    def ai_brief():
        payload, error_response = load_json_object(
            invalid_json_message="Text JSON",
            invalid_type_message="request body must be a JSON object",
        )
        if error_response is not None:
            return error_response

        force = deps.to_bool(payload.pop("force", False), False)

        def _opt_float(value: Any) -> Optional[float]:
            if value is None:
                return None
            if isinstance(value, str):
                cleaned = value.strip()
                if not cleaned:
                    return None
                cleaned = cleaned.replace(",", "").replace("%", "").replace("$", "")
                value = cleaned
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            if math.isnan(number) or math.isinf(number):
                return None
            return float(number)

        def _opt_int(value: Any) -> Optional[int]:
            maybe = _opt_float(value)
            if maybe is None:
                return None
            return int(round(maybe))

        def _normalize(stock_payload: Dict[str, Any]) -> Dict[str, Any]:
            data = stock_payload or {}
            stock_id = str(data.get("stock_id") or "").strip().upper()
            ticker = str(data.get("ticker") or stock_id).strip().upper()
            if not ticker:
                raise ValueError("TextStockTicker")
            stock_name = str(data.get("stock_name") or "").strip() or ticker
            strategy = str(data.get("strategy") or deps.strategy_momentum_spike).strip() or deps.strategy_momentum_spike

            normalized = {
                "stock_id": stock_id or ticker,
                "ticker": ticker,
                "stock_name": stock_name,
                "strategy": strategy,
                "sector": str(data.get("sector") or "").strip(),
                "industry": str(data.get("industry") or "").strip(),
                "company_intro": str(data.get("company_intro") or "").strip(),
                "market_cap": _opt_float(data.get("market_cap")),
                "latest_close": _opt_float(data.get("latest_close")),
                "trigger_trade_date": str(data.get("trigger_trade_date") or "").strip(),
                "gain_30d_pct": _opt_float(data.get("gain_30d_pct")),
                "max_daily_gain_5d_pct": _opt_float(data.get("max_daily_gain_5d_pct")),
                "avg_volume_5d": _opt_float(data.get("avg_volume_5d")),
                "avg_volume_3m": _opt_float(data.get("avg_volume_3m")),
                "avg_volume_5d_vs_3m": _opt_float(data.get("avg_volume_5d_vs_3m")),
                "new_low_trade_date": str(data.get("new_low_trade_date") or "").strip(),
                "rebound_from_new_low_pct": _opt_float(data.get("rebound_from_new_low_pct")),
                "distance_above_200ma_pct": _opt_float(data.get("distance_above_200ma_pct")),
                "days_since_new_low": _opt_int(data.get("days_since_new_low")),
                "ma50": _opt_float(data.get("ma50")),
                "ma100": _opt_float(data.get("ma100")),
                "ma200": _opt_float(data.get("ma200")),
            }

            profiles = deps.get_storage().get_us_screener_company_profiles()
            profile = None
            if isinstance(profiles, dict):
                profile = profiles.get(normalized["ticker"]) or profiles.get(normalized["stock_id"])
            if isinstance(profile, dict):
                normalized["sector"] = normalized["sector"] or str(profile.get("sector") or "").strip()
                normalized["industry"] = normalized["industry"] or str(profile.get("industry") or "").strip()
                normalized["company_intro"] = normalized["company_intro"] or str(profile.get("company_intro") or "").strip()
            return normalized

        def _collect_warnings(stock: Dict[str, Any]) -> List[str]:
            messages: List[str] = []
            if not str(stock.get("company_intro") or "").strip():
                messages.append("Text, AI Text. ")
            strategy = str(stock.get("strategy") or deps.strategy_momentum_spike)
            if strategy == deps.strategy_post_52w_low_reversal:
                if stock.get("rebound_from_new_low_pct") is None:
                    messages.append("Text, Text. ")
                if stock.get("distance_above_200ma_pct") is None:
                    messages.append("Text200Text, Text. ")
            else:
                if stock.get("max_daily_gain_5d_pct") is None:
                    messages.append("Text5Text, Text. ")
                if stock.get("avg_volume_5d_vs_3m") is None:
                    messages.append("Text, TextConfirm. ")
                if stock.get("gain_30d_pct") is None:
                    messages.append("Text30Text, Text. ")
            dedup: List[str] = []
            for item in messages:
                text = str(item or "").strip()
                if text and text not in dedup:
                    dedup.append(text)
            return dedup

        def _cache_key(stock: Dict[str, Any]) -> str:
            ticker = str(stock.get("ticker") or stock.get("stock_id") or "").strip().upper() or "UNKNOWN"
            strategy = str(stock.get("strategy") or deps.strategy_momentum_spike).strip().lower() or "default"
            return f"{strategy}|{ticker}"

        def _signature(stock: Dict[str, Any]) -> str:
            payload_data = {
                "v": 1,
                "ticker": stock.get("ticker"),
                "strategy": stock.get("strategy"),
                "sector": stock.get("sector"),
                "industry": stock.get("industry"),
                "company_intro": stock.get("company_intro"),
                "market_cap": stock.get("market_cap"),
                "latest_close": stock.get("latest_close"),
                "trigger_trade_date": stock.get("trigger_trade_date"),
                "gain_30d_pct": stock.get("gain_30d_pct"),
                "max_daily_gain_5d_pct": stock.get("max_daily_gain_5d_pct"),
                "avg_volume_5d": stock.get("avg_volume_5d"),
                "avg_volume_3m": stock.get("avg_volume_3m"),
                "avg_volume_5d_vs_3m": stock.get("avg_volume_5d_vs_3m"),
                "new_low_trade_date": stock.get("new_low_trade_date"),
                "rebound_from_new_low_pct": stock.get("rebound_from_new_low_pct"),
                "distance_above_200ma_pct": stock.get("distance_above_200ma_pct"),
                "days_since_new_low": stock.get("days_since_new_low"),
                "ma50": stock.get("ma50"),
                "ma100": stock.get("ma100"),
                "ma200": stock.get("ma200"),
            }
            blob = json.dumps(payload_data, ensure_ascii=False, sort_keys=True)
            return hashlib.sha256(blob.encode("utf-8")).hexdigest()

        def _build_prompt(stock: Dict[str, Any]) -> str:
            strategy_label = (
                "52Text"
                if stock.get("strategy") == deps.strategy_post_52w_low_reversal
                else "Text"
            )

            def format_market_cap(value: Optional[float]) -> str:
                if value is None or value <= 0:
                    return "unknown"
                if value >= 1_000_000_000_000:
                    return f"~${value / 1_000_000_000_000:.2f}T"
                if value >= 1_000_000_000:
                    return f"~${value / 1_000_000_000:.2f}B"
                if value >= 1_000_000:
                    return f"~${value / 1_000_000:.2f}M"
                return f"~${value:,.0f}"

            def fmt_price(value: Optional[float]) -> str:
                if value is None:
                    return "unknown"
                return f"${value:.2f}"

            def fmt_pct(value: Optional[float]) -> str:
                if value is None:
                    return "unknown"
                sign = "+" if value >= 0 else ""
                return f"{sign}{value:.2f}%"

            metrics: List[str] = []
            base_line: List[str] = []
            if stock.get("latest_close") is not None:
                base_line.append(f"latest close {fmt_price(stock['latest_close'])}")
            if stock.get("trigger_trade_date"):
                base_line.append(f"triggered on {stock['trigger_trade_date']}")
            if stock.get("gain_30d_pct") is not None:
                base_line.append(f"30d move {fmt_pct(stock['gain_30d_pct'])}")
            if stock.get("max_daily_gain_5d_pct") is not None:
                base_line.append(f"max 1d move in 5d {fmt_pct(stock['max_daily_gain_5d_pct'])}")
            if base_line:
                metrics.append(" · ".join(base_line))

            if stock.get("strategy") == deps.strategy_post_52w_low_reversal:
                reversal_bits: List[str] = []
                if stock.get("new_low_trade_date"):
                    reversal_bits.append(f"last 52w low on {stock['new_low_trade_date']}")
                if stock.get("rebound_from_new_low_pct") is not None:
                    reversal_bits.append(f"rebound {fmt_pct(stock['rebound_from_new_low_pct'])}")
                if stock.get("distance_above_200ma_pct") is not None:
                    reversal_bits.append(f"above 200dma {fmt_pct(stock['distance_above_200ma_pct'])}")
                if stock.get("days_since_new_low") is not None:
                    reversal_bits.append(f"{stock['days_since_new_low']} days since low")
                if reversal_bits:
                    metrics.append("reversal context: " + " · ".join(reversal_bits))
            else:
                if stock.get("max_daily_gain_5d_pct") is not None:
                    metrics.append(f"5d best day {fmt_pct(stock['max_daily_gain_5d_pct'])}")
                if stock.get("avg_volume_5d_vs_3m") is not None:
                    metrics.append(
                        f"5d avg volume vs 3m avg {float(stock['avg_volume_5d_vs_3m']):.2f}x"
                    )
                if stock.get("gain_30d_pct") is not None:
                    metrics.append(f"30d total move {fmt_pct(stock['gain_30d_pct'])}")

            if all(value is not None for value in (stock.get("ma50"), stock.get("ma100"), stock.get("ma200"))):
                metrics.append(
                    "moving averages: "
                    f"MA50 {fmt_price(stock['ma50'])} / MA100 {fmt_price(stock['ma100'])} / MA200 {fmt_price(stock['ma200'])}"
                )

            if not metrics:
                metrics.append("insufficient price/technical context")

            sector = stock.get("sector") or "unknown sector"
            industry = stock.get("industry") or ""
            industry_text = sector if not industry or industry == sector else f"{sector} / {industry}"
            intro = stock.get("company_intro") or "No dataText, Text. "

            metrics_block = "\n".join(f"- {line}" for line in metrics)
            return (
                "You are an equity research assistant. Use the structured data below plus your own pre-2025 knowledge "
                "to write a focused briefing in Simplified Chinese. Text, TextText, Text. \n\n"
                f"Company: {stock['stock_name']} ({stock['ticker']})\n"
                f"Strategy: {strategy_label}\n"
                f"Industry: {industry_text}\n"
                f"Market cap: {format_market_cap(stock.get('market_cap'))}\n"
                "Key metrics:\n"
                f"{metrics_block}\n"
                f"Company intro (may be empty): {intro}\n\n"
                "Text 4 Text(Text 2 Text, Text): \n"
                "[Text]Text, Text, Text; Text. \n"
                "[Text12Text]Text/Text/TradeText(Text, Text, Text), TextText. \n"
                "[TextAIText]Text/Text/AIText, Text; Text. \n"
                "[TextRiskText]Text/Text, Text 1-2 TextTradeTextRisk. "
            )

        try:
            stock = _normalize(payload)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        warnings = _collect_warnings(stock)
        cache_key = _cache_key(stock)
        signature = _signature(stock)
        cache_records = deps.get_storage().get_us_screener_ai_briefs()
        cached_entry = cache_records.get(cache_key) if isinstance(cache_records, dict) else None
        if (
            not force
            and isinstance(cached_entry, dict)
            and cached_entry.get("signature") == signature
            and str(cached_entry.get("brief") or "").strip()
        ):
            cached_payload = dict(cached_entry)
            cached_payload["cached"] = True
            cached_payload["success"] = True
            if not cached_payload.get("search_warnings"):
                cached_payload["search_warnings"] = warnings
            return jsonify(deps.json_safe(cached_payload))

        runtime = deps.get_client()
        if runtime is None:
            return jsonify(
                {
                    "success": False,
                    "error": "LLM Text",
                    "stock_id": stock["stock_id"],
                    "ticker": stock["ticker"],
                }
            ), 503

        prompt = _build_prompt(stock)
        raw_text = deps.chat_with_retry(
            runtime,
            prompt,
            retries=1,
            request_name=f"us_ai_brief_{stock['ticker']}",
            force_refresh=force,
        )
        normalized_brief = str(raw_text or "").strip()
        if deps.is_llm_failure_text(raw_text) or not normalized_brief:
            error_text = raw_text if deps.is_llm_failure_text(raw_text) else "LLM Text"
            return (
                jsonify(
                    deps.json_safe(
                        {
                            "success": False,
                            "error": error_text,
                            "stock_id": stock["stock_id"],
                            "ticker": stock["ticker"],
                            "strategy": stock["strategy"],
                            "search_warnings": warnings,
                        }
                    )
                ),
                502,
            )

        payload_data = {
            "success": True,
            "stock_id": stock["stock_id"],
            "stock_name": stock["stock_name"],
            "ticker": stock["ticker"],
            "strategy": stock["strategy"],
            "brief": normalized_brief,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "cached": False,
            "search_warnings": warnings,
            "signature": signature,
        }
        deps.get_storage().upsert_us_screener_ai_brief(cache_key, payload_data)
        return jsonify(deps.json_safe(payload_data))

    return USScreenerModuleService(
        us_screener_page=us_screener_page,
        latest=latest,
        status=status,
        context=context,
        stock_overview=stock_overview,
        alerts=alerts,
        create_alert=create_alert,
        delete_alert=delete_alert,
        research_queue=research_queue,
        create_research_queue_item=create_research_queue_item,
        update_research_queue_item=update_research_queue_item,
        delete_research_queue_item=delete_research_queue_item,
        ai_brief=ai_brief,
        run=run,
        scan=scan,
    )
