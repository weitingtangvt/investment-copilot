from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class WatchlistSnapshotDeps:
    get_storage: Callable[[], Any]
    write_markdown_snapshot: Callable[[Path, str], Path]
    to_change_pct: Callable[[Any], Optional[float]]


def watch_candidate_revisit_lines(
    candidate: Dict[str, Any],
    *,
    to_change_pct: Callable[[Any], Optional[float]],
) -> List[str]:
    lines = []
    for rule in list(candidate.get("revisit_active_rules") or []):
        change_pct = to_change_pct(rule.get("change_pct"))
        if change_pct is None:
            continue
        label = str(rule.get("label") or rule.get("window") or "Text")
        lines.append(f"{label} {change_pct:+.1f}%")
    return lines


def watch_candidate_performance_line(
    candidate: Dict[str, Any],
    *,
    to_change_pct: Callable[[Any], Optional[float]],
) -> str:
    summary = str(candidate.get("performance_summary") or "").strip()
    if summary:
        return summary
    change_pct = to_change_pct((candidate.get("performance_data") or {}).get("change_pct"))
    if change_pct is None:
        return ""
    return f"Text1Text {change_pct:+.2f}%"


def markdown_from_watchlist_snapshot(
    week_id: str,
    watchlist: Dict[str, Any],
    *,
    to_change_pct: Callable[[Any], Optional[float]],
) -> str:
    candidates = list(watchlist.get("candidates") or [])
    theme_counter = Counter((item.get("theme") or "Text").strip() or "Text" for item in candidates)
    status_counter = Counter((item.get("status") or "WatchText").strip() or "WatchText" for item in candidates)
    revisit_pending = sum(
        1
        for item in candidates
        if list(item.get("revisit_active_rules") or [])
        and str(item.get("revisit_signature") or "") != str(item.get("revisit_ack_signature") or "")
    )

    lines: List[str] = [
        f"# {week_id} WatchText",
        "",
        f"- Text: {datetime.now().isoformat(timespec='seconds')}",
        f"- WatchText: {len(candidates)}",
        f"- Text: {len(theme_counter)}",
        f"- Text Revisit: {revisit_pending}",
        "",
        "## Text",
        "",
    ]

    if theme_counter:
        for name, count in sorted(theme_counter.items(), key=lambda item: item[0]):
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- No dataWatchText")
    lines.extend(["", "## StatusText", ""])
    if status_counter:
        for name, count in sorted(status_counter.items(), key=lambda item: item[0]):
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- No dataStatusText")
    lines.append("")

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        key = (candidate.get("theme") or "").strip() or "Text"
        grouped.setdefault(key, []).append(candidate)

    lines.extend(["## Text", ""])
    if not grouped:
        lines.extend(["_CurrentWatchText. _", ""])
        return "\n".join(lines).rstrip() + "\n"

    for theme in sorted(grouped.keys()):
        lines.extend([f"### {theme}", ""])
        items = sorted(
            grouped[theme],
            key=lambda item: str(item.get("stock_name") or item.get("stock_id") or ""),
        )
        for candidate in items:
            stock_name = str(candidate.get("stock_name") or candidate.get("stock_id") or "Text").strip()
            ticker = str(candidate.get("ticker") or candidate.get("stock_id") or "").strip()
            lines.append(f"#### {stock_name}{f' ({ticker})' if ticker else ''}")
            meta = [
                f"Text: {candidate.get('industry') or 'Text'}",
                f"Status: {candidate.get('status') or 'WatchText'}",
            ]
            if candidate.get("watch_started_at"):
                meta.append(f"TextDate: {candidate.get('watch_started_at')}")
            for item in meta:
                lines.append(f"- {item}")
            if str(candidate.get("profit_driver") or "").strip():
                lines.append(f"- Text: {candidate.get('profit_driver')}")
            if str(candidate.get("price_contains") or "").strip():
                lines.append(f"- CurrentText: {candidate.get('price_contains')}")
            if str(candidate.get("odds_assessment") or "").strip():
                lines.append(f"- Text: {candidate.get('odds_assessment')}")
            if str(candidate.get("watch_reason") or "").strip():
                lines.append(f"- Text: {candidate.get('watch_reason')}")
            if str(candidate.get("not_buy_reason") or "").strip():
                lines.append(f"- Text: {candidate.get('not_buy_reason')}")
            if str(candidate.get("weekly_note") or "").strip():
                lines.append(f"- This WeekText: {candidate.get('weekly_note')}")
            if str(candidate.get("ai_watch_judgment") or "").strip():
                lines.append(f"- AI Text: {candidate.get('ai_watch_judgment')}")
            performance_line = watch_candidate_performance_line(candidate, to_change_pct=to_change_pct)
            if performance_line:
                lines.append(f"- TextSummary: {performance_line}")
            revisit_lines = watch_candidate_revisit_lines(candidate, to_change_pct=to_change_pct)
            if revisit_lines:
                lines.append(f"- Revisit Text: {' / '.join(revisit_lines)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_watchlist_snapshot_builder(deps: WatchlistSnapshotDeps) -> Callable[[str], Dict[str, Any]]:
    def build_watchlist_snapshot(week_id: str) -> Dict[str, Any]:
        watchlist = deps.get_storage().get_watchlist()
        content = markdown_from_watchlist_snapshot(
            week_id,
            watchlist,
            to_change_pct=deps.to_change_pct,
        )
        filename = f"{week_id}_WatchText.md"
        path = deps.get_storage().get_ima_export_path("watchlist_snapshots", filename)
        deps.write_markdown_snapshot(path, content)
        return {
            "week_id": week_id,
            "title": Path(filename).stem,
            "local_file": path,
            "content": content,
            "watchlist": watchlist,
        }

    return build_watchlist_snapshot


@dataclass
class WatchlistPriceChartDeps:
    to_change_pct: Callable[[Any], Optional[float]]
    get_price_chart_series: Optional[Callable[..., Dict[str, Any]]]
    logger: Any
    ttl_seconds: float
    failure_ttl_seconds: float
    wait_seconds: float


class WatchlistPriceChartFetcher:
    def __init__(self, deps: WatchlistPriceChartDeps) -> None:
        self._deps = deps
        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._inflight: Dict[str, threading.Event] = {}

    def _empty_payload(
        self,
        *,
        stock_id: str,
        stock_name: str,
        ticker: str,
        range_name: str,
        error: str = "",
    ) -> Dict[str, Any]:
        return {
            "success": False,
            "stock_id": str(stock_id or "").strip(),
            "stock_name": str(stock_name or "").strip(),
            "ticker": str(ticker or "").strip(),
            "range": str(range_name or "1y").strip().lower() or "1y",
            "as_of_date": "",
            "series": {"candles": [], "ma50": [], "ma100": [], "ma200": []},
            "meta": {
                "latest_close": None,
                "change_1y_pct": None,
                "change_range_pct": None,
                "has_enough_history_for_ma200": False,
                "provider": "akshare",
            },
            "error": str(error or "").strip(),
        }

    def _serialize_payload(
        self,
        subject: Dict[str, Any],
        chart_result: Dict[str, Any],
        range_name: str,
    ) -> Dict[str, Any]:
        meta = dict((chart_result or {}).get("meta") or {})
        return {
            "success": bool(chart_result.get("success")),
            "stock_id": str(subject.get("stock_id") or "").strip(),
            "stock_name": str(subject.get("stock_name") or "").strip(),
            "ticker": str(subject.get("ticker") or subject.get("stock_id") or "").strip(),
            "range": str(range_name or "1y").strip().lower() or "1y",
            "as_of_date": str(chart_result.get("as_of_date") or "").strip(),
            "series": {
                "candles": list(((chart_result or {}).get("series") or {}).get("candles") or []),
                "ma50": list(((chart_result or {}).get("series") or {}).get("ma50") or []),
                "ma100": list(((chart_result or {}).get("series") or {}).get("ma100") or []),
                "ma200": list(((chart_result or {}).get("series") or {}).get("ma200") or []),
            },
            "meta": {
                "latest_close": self._deps.to_change_pct(meta.get("latest_close")),
                "change_1y_pct": self._deps.to_change_pct(meta.get("change_1y_pct")),
                "change_range_pct": self._deps.to_change_pct(meta.get("change_range_pct")),
                "has_enough_history_for_ma200": bool(meta.get("has_enough_history_for_ma200")),
                "provider": str(meta.get("provider") or "akshare").strip() or "akshare",
            },
            "error": str(chart_result.get("error") or "").strip(),
        }

    def fetch(self, subject: Dict[str, Any], range_name: str = "1y") -> Dict[str, Any]:
        stock_id = str(subject.get("stock_id") or "").strip()
        stock_name = str(subject.get("stock_name") or "").strip()
        ticker = str(subject.get("ticker") or subject.get("stock_id") or "").strip()
        resolved_range = str(range_name or "1y").strip().lower() or "1y"
        if not ticker:
            return self._empty_payload(
                stock_id=stock_id,
                stock_name=stock_name,
                ticker="",
                range_name=resolved_range,
                error="No dataTextMarket DataTicker",
            )

        if not self._deps.get_price_chart_series:
            return self._empty_payload(
                stock_id=stock_id,
                stock_name=stock_name,
                ticker=ticker,
                range_name=resolved_range,
                error="AKShare Text, Text: pip install akshare",
            )

        cache_key = f"{ticker.upper()}|{resolved_range}"
        now = time.monotonic()
        owner = False
        event: Optional[threading.Event] = None

        with self._lock:
            cache_entry = self._cache.get(cache_key)
            if cache_entry and float(cache_entry.get("expires_at") or 0) > now:
                return dict(cache_entry.get("payload") or {})

            event = self._inflight.get(cache_key)
            if event is None:
                event = threading.Event()
                self._inflight[cache_key] = event
                owner = True

        if not owner:
            assert event is not None
            event.wait(self._deps.wait_seconds)
            with self._lock:
                cache_entry = self._cache.get(cache_key)
                if cache_entry and float(cache_entry.get("expires_at") or 0) > time.monotonic():
                    return dict(cache_entry.get("payload") or {})
            return self._empty_payload(
                stock_id=stock_id,
                stock_name=stock_name,
                ticker=ticker,
                range_name=resolved_range,
                error="Text, Text",
            )

        try:
            payload = self._serialize_payload(
                subject,
                self._deps.get_price_chart_series(ticker, range=resolved_range),
                resolved_range,
            )
            ttl = self._deps.ttl_seconds if payload.get("success") else self._deps.failure_ttl_seconds
            with self._lock:
                self._cache[cache_key] = {
                    "payload": payload,
                    "expires_at": time.monotonic() + ttl,
                }
            return dict(payload)
        except Exception as exc:
            self._deps.logger.exception("fetch watchlist price chart failed for %s", ticker)
            payload = self._empty_payload(
                stock_id=stock_id,
                stock_name=stock_name,
                ticker=ticker,
                range_name=resolved_range,
                error=str(exc),
            )
            with self._lock:
                self._cache[cache_key] = {
                    "payload": payload,
                    "expires_at": time.monotonic() + self._deps.failure_ttl_seconds,
                }
            return payload
        finally:
            with self._lock:
                done_event = self._inflight.pop(cache_key, None)
            if done_event is not None:
                done_event.set()


@dataclass
class WatchlistAIJudgmentDeps:
    get_client: Callable[[], Any]
    get_storage: Callable[[], Any]
    fmt_number: Callable[[Any], str]
    chat_with_retry: Callable[..., Any]
    is_llm_failure_text: Callable[[Any], bool]


def watch_candidate_ai_signature(
    candidate: Dict[str, Any],
    commentary_entry: Optional[Dict[str, Any]] = None,
) -> str:
    metrics = dict((candidate or {}).get("revisit_metrics") or {})
    performance_data = dict((candidate or {}).get("performance_data") or {})
    commentary_items = []
    for item in list((commentary_entry or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        commentary_items.append(
            {
                "published_at": str(item.get("published_at") or "").strip(),
                "header_lines": [str(line).strip() for line in (item.get("header_lines") or []) if str(line).strip()],
                "matched_keywords": [str(line).strip() for line in (item.get("matched_keywords") or []) if str(line).strip()],
                "body": str(item.get("body") or "").strip(),
            }
        )
    payload = {
        "stock_id": str(candidate.get("stock_id") or "").strip(),
        "stock_name": str(candidate.get("stock_name") or "").strip(),
        "ticker": str(candidate.get("ticker") or "").strip(),
        "profit_driver": str(candidate.get("profit_driver") or "").strip(),
        "price_contains": str(candidate.get("price_contains") or "").strip(),
        "odds_assessment": str(candidate.get("odds_assessment") or "").strip(),
        "watch_reason": str(candidate.get("watch_reason") or "").strip(),
        "not_buy_reason": str(candidate.get("not_buy_reason") or "").strip(),
        "weekly_note": str(candidate.get("weekly_note") or "").strip(),
        "watch_started_at": str(candidate.get("watch_started_at") or "").strip(),
        "performance_summary": str(candidate.get("performance_summary") or "").strip(),
        "performance_data": performance_data,
        "revisit_metrics": metrics,
        "commentary_items": commentary_items,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_watch_candidate_ai_prompt(
    candidate: Dict[str, Any],
    commentary_entry: Optional[Dict[str, Any]] = None,
    *,
    fmt_number: Callable[[Any], str],
) -> str:
    metrics = dict((candidate or {}).get("revisit_metrics") or {})
    commentary_lines = []
    for item in list((commentary_entry or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        header = " / ".join([str(line).strip() for line in (item.get("header_lines") or []) if str(line).strip()][:2])
        body = str(item.get("body") or item.get("preview") or "").strip()
        matched = [str(word).strip() for word in (item.get("matched_keywords") or []) if str(word).strip()]
        suffix = f"; Text: {' / '.join(matched)}" if matched else ""
        commentary_lines.append(
            f"- Text: {str(item.get('published_at') or '').strip() or '--'}\n  Text: {header or '--'}\n  Text: {body or '--'}{suffix}"
        )

    profit_driver = str(candidate.get("profit_driver") or "").strip()
    price_contains = str(candidate.get("price_contains") or "").strip()
    odds_assessment = str(candidate.get("odds_assessment") or "").strip()
    watch_reason = str(candidate.get("watch_reason") or "").strip()
    not_buy_reason = str(candidate.get("not_buy_reason") or "").strip()
    weekly_note = str(candidate.get("weekly_note") or "").strip()
    performance_summary = str(candidate.get("performance_summary") or "").strip()
    price_compare = (
        f"Text: {fmt_number(metrics.get('baseline_price'))}; "
        f"Text: {fmt_number(metrics.get('current_price'))}; "
        f"Text: {fmt_number(metrics.get('since_added_change_pct'))}%"
    )

    return f"""TextWatchText. TextStockText, Text, Text, Text. 

Text: 
1. Text, Text, This WeekText. 
2. Text, Text. 
3. Text: 
[Text]Text: Text / Text / Text / Text
[Text]
[Text]
[Text]

## Stock
- Text: {candidate.get('stock_name') or candidate.get('stock_id')}
- Ticker: {candidate.get('ticker') or candidate.get('stock_id')}
- TextWatchDate: {candidate.get('watch_started_at') or 'Text'}

## Text
- Text: {profit_driver or 'No data'}
- CurrentText: {price_contains or 'No data'}
- Text: {odds_assessment or 'No data'}

## Text
- Text: {watch_reason or 'No data'}
- Text: {not_buy_reason or 'No data'}
- This WeekText: {weekly_note or 'No data'}

## Text
- This WeekText: {performance_summary or 'No data'}
- CurrentText: {price_compare}

## This WeekText
{chr(10).join(commentary_lines) if commentary_lines else '- Text 7 TextNo dataText'}
"""


def watch_candidate_ai_signature(
    candidate: Dict[str, Any],
    commentary_entry: Optional[Dict[str, Any]] = None,
    filings_entry: Optional[Dict[str, Any]] = None,
) -> str:
    metrics = dict((candidate or {}).get("revisit_metrics") or {})
    performance_data = dict((candidate or {}).get("performance_data") or {})
    commentary_items = []
    for item in list((commentary_entry or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        commentary_items.append(
            {
                "published_at": str(item.get("published_at") or "").strip(),
                "header_lines": [str(line).strip() for line in (item.get("header_lines") or []) if str(line).strip()],
                "matched_keywords": [str(line).strip() for line in (item.get("matched_keywords") or []) if str(line).strip()],
                "body": str(item.get("body") or item.get("preview") or "").strip(),
            }
        )
    filings_items = []
    for item in list((filings_entry or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        filings_items.append(
            {
                "filed_at": str(item.get("filed_at") or "").strip(),
                "doc_type": str(item.get("doc_type") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "importance": str(item.get("importance") or "").strip(),
            }
        )
    payload = {
        "stock_id": str(candidate.get("stock_id") or "").strip(),
        "stock_name": str(candidate.get("stock_name") or "").strip(),
        "ticker": str(candidate.get("ticker") or "").strip(),
        "profit_driver": str(candidate.get("profit_driver") or "").strip(),
        "price_contains": str(candidate.get("price_contains") or "").strip(),
        "odds_assessment": str(candidate.get("odds_assessment") or "").strip(),
        "watch_reason": str(candidate.get("watch_reason") or "").strip(),
        "not_buy_reason": str(candidate.get("not_buy_reason") or "").strip(),
        "weekly_note": str(candidate.get("weekly_note") or "").strip(),
        "watch_started_at": str(candidate.get("watch_started_at") or "").strip(),
        "performance_summary": str(candidate.get("performance_summary") or "").strip(),
        "performance_data": performance_data,
        "revisit_metrics": metrics,
        "commentary_items": commentary_items,
        "filings_items": filings_items,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_watch_candidate_ai_prompt(
    candidate: Dict[str, Any],
    commentary_entry: Optional[Dict[str, Any]] = None,
    filings_entry: Optional[Dict[str, Any]] = None,
    *,
    fmt_number: Callable[[Any], str],
) -> str:
    metrics = dict((candidate or {}).get("revisit_metrics") or {})
    commentary_lines = []
    for item in list((commentary_entry or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        header = " / ".join([str(line).strip() for line in (item.get("header_lines") or []) if str(line).strip()][:2])
        body = str(item.get("body") or item.get("preview") or "").strip()
        matched = [str(word).strip() for word in (item.get("matched_keywords") or []) if str(word).strip()]
        suffix = f"; Text: {' / '.join(matched)}" if matched else ""
        commentary_lines.append(
            f"- Text: {str(item.get('published_at') or '').strip() or '--'}\n  Text: {header or '--'}\n  Text: {body or '--'}{suffix}"
        )
    filings_lines = []
    for item in list((filings_entry or {}).get("items") or [])[:3]:
        if not isinstance(item, dict):
            continue
        filings_lines.append(
            f"- Text: {str(item.get('filed_at') or '').strip() or '--'}\n"
            f"  Text: {str(item.get('doc_type') or '').strip() or '--'}\n"
            f"  Filings: {str(item.get('title') or '').strip() or '--'}\n"
            f"  Summary: {str(item.get('summary') or '').strip() or '--'}\n"
            f"  Text: {str(item.get('importance') or '').strip() or 'low'}"
        )

    profit_driver = str(candidate.get("profit_driver") or "").strip()
    price_contains = str(candidate.get("price_contains") or "").strip()
    odds_assessment = str(candidate.get("odds_assessment") or "").strip()
    watch_reason = str(candidate.get("watch_reason") or "").strip()
    not_buy_reason = str(candidate.get("not_buy_reason") or "").strip()
    weekly_note = str(candidate.get("weekly_note") or "").strip()
    performance_summary = str(candidate.get("performance_summary") or "").strip()
    price_compare = (
        f"Text: {fmt_number(metrics.get('baseline_price'))}; "
        f"Text: {fmt_number(metrics.get('current_price'))}; "
        f"Text: {fmt_number(metrics.get('since_added_change_pct'))}%"
    )

    return f"""TextWatchText. TextStockText, Text, Text, Text. 
Text: 
1. Text, Text, This WeekText, TextFilingsText. 
2. Text, Text. 
3. Text: 
[Text]Text: Text / Text / Text / Text
[Text]
[Text]
[Text]

## Stock
- Text: {candidate.get('stock_name') or candidate.get('stock_id')}
- Ticker: {candidate.get('ticker') or candidate.get('stock_id')}
- TextWatchDate: {candidate.get('watch_started_at') or 'Text'}

## Text
- Text: {profit_driver or 'No data'}
- CurrentText: {price_contains or 'No data'}
- Text: {odds_assessment or 'No data'}

## Text
- Text: {watch_reason or 'No data'}
- Text: {not_buy_reason or 'No data'}
- This WeekText: {weekly_note or 'No data'}

## Text
- This WeekText: {performance_summary or 'No data'}
- CurrentText: {price_compare}

## This WeekText
{chr(10).join(commentary_lines) if commentary_lines else '- Text7TextNo dataText'}

## TextFilings
{chr(10).join(filings_lines) if filings_lines else '- Text7TextNo dataTextFilings'}
"""


def build_generate_watch_candidate_ai_judgment(
    deps: WatchlistAIJudgmentDeps,
) -> Callable[..., Dict[str, Any]]:
    def generate_watch_candidate_ai_judgment(
        candidate: Dict[str, Any],
        *,
        commentary_entry: Optional[Dict[str, Any]] = None,
        filings_entry: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        runtime = deps.get_client()
        stock_id = str(candidate.get("stock_id") or "").strip()
        if runtime is None:
            return {
                "success": False,
                "stock_id": stock_id,
                "error": "LLM is not configured",
                "candidate": candidate,
            }

        signature = watch_candidate_ai_signature(candidate, commentary_entry, filings_entry)
        existing_signature = str(candidate.get("ai_watch_judgment_signature") or "").strip()
        existing_judgment = str(candidate.get("ai_watch_judgment") or "").strip()
        if (not force) and existing_judgment and existing_signature == signature:
            return {
                "success": True,
                "stock_id": stock_id,
                "candidate": candidate,
                "judgment": existing_judgment,
                "skipped": True,
                "signature": signature,
            }

        prompt = build_watch_candidate_ai_prompt(
            candidate,
            commentary_entry,
            filings_entry,
            fmt_number=deps.fmt_number,
        )
        judgment = deps.chat_with_retry(
            runtime,
            prompt,
            retries=1,
            request_name=f"watchlist_ai_{stock_id}",
            force_refresh=force,
        )
        if deps.is_llm_failure_text(judgment):
            updated = deps.get_storage().update_watch_candidate_ai_judgment(
                stock_id,
                judgment=existing_judgment,
                signature=existing_signature,
                error=judgment,
            )
            return {
                "success": False,
                "stock_id": stock_id,
                "candidate": updated or candidate,
                "judgment": existing_judgment,
                "error": judgment,
                "signature": signature,
            }

        updated = deps.get_storage().update_watch_candidate_ai_judgment(
            stock_id,
            judgment=judgment,
            signature=signature,
            error="",
        )
        return {
            "success": True,
            "stock_id": stock_id,
            "candidate": updated or candidate,
            "judgment": judgment,
            "skipped": False,
            "signature": signature,
        }

    return generate_watch_candidate_ai_judgment
