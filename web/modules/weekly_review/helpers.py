from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Response


@dataclass
class WeeklyReviewExportDeps:
    get_storage: Callable[[], Any]
    write_markdown_snapshot: Callable[[Path, str], Path]
    fmt_money: Callable[[Any], str]
    fmt_number: Callable[[Any], str]
    get_stock_commentary: Optional[Callable[..., Any]] = None


def has_weekly_review_content(review: Optional[Dict[str, Any]]) -> bool:
    if not review:
        return False
    market = review.get("market_context") or {}
    macro = review.get("macro_events") or {}
    if market.get("signals") or market.get("big_picture") or market.get("ai_summary") or market.get("watch_items"):
        return True
    if macro.get("top_events") or macro.get("events"):
        return True
    if review.get("factor_analysis"):
        return True
    for stock in (review.get("stocks") or {}).values():
        if stock.get("news") or stock.get("performance_summary") or stock.get("user_view"):
            return True
    return False


def build_normalize_weekly_review_payload(
    *,
    config_value: Callable[[str, Any], Any],
) -> Callable[[str, Optional[Dict[str, Any]]], Dict[str, Any]]:
    def normalize_weekly_review_payload(week_id: str, review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = dict(review or {})
        payload["week_id"] = str(payload.get("week_id") or week_id)
        payload["stocks"] = payload.get("stocks") or {}
        payload["market_context"] = payload.get("market_context") or {}
        payload["factor_analysis"] = payload.get("factor_analysis") or {}
        if payload.get("cash_balance") in ("", None):
            payload["cash_balance"] = None
        else:
            payload["cash_balance"] = float(payload.get("cash_balance"))
        payload["usd_to_hkd"] = float(payload.get("usd_to_hkd") or config_value("usd_to_hkd_rate", 7.8) or 7.8)
        payload["cny_to_hkd"] = float(payload.get("cny_to_hkd") or 1.07)
        payload["eur_to_hkd"] = float(payload.get("eur_to_hkd") or config_value("eur_to_hkd_rate", 8.4) or 8.4)
        payload["jpy_to_hkd"] = float(payload.get("jpy_to_hkd") or config_value("jpy_to_hkd_rate", 0.052) or 0.052)
        payload["krw_to_hkd"] = float(payload.get("krw_to_hkd") or config_value("krw_to_hkd_rate", 0.0056) or 0.0056)
        payload["trim_reallocation_analysis"] = payload.get("trim_reallocation_analysis") or {
            "summary": {},
            "stocks": [],
            "events": [],
        }
        payload["decision_attribution_analysis"] = payload.get("decision_attribution_analysis") or {
            "summary": {},
            "patterns": {},
            "stocks": [],
            "events": [],
        }
        return payload

    return normalize_weekly_review_payload


@dataclass
class WeeklyReviewAnalysisDeps:
    get_weekly_review_manager: Callable[[], Any]
    logger: Any
    review_projection_provider: Optional[Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]] = None


def build_attach_weekly_review_analyses(
    deps: WeeklyReviewAnalysisDeps,
) -> Callable[[str, Optional[Dict[str, Any]]], Dict[str, Any]]:
    def attach_weekly_review_analysis(
        week_id: str,
        review: Optional[Dict[str, Any]],
        method_name: str,
        log_label: str,
    ) -> Dict[str, Any]:
        payload = dict(review or {})
        mgr = deps.get_weekly_review_manager()
        if mgr is not None and hasattr(mgr, method_name):
            try:
                payload = getattr(mgr, method_name)(week_id, payload)
            except Exception:
                deps.logger.exception("Failed to attach %s for week_id=%s", log_label, week_id)
        return payload

    def attach_trim_reallocation_analysis(week_id: str, review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return attach_weekly_review_analysis(
            week_id,
            review,
            method_name="attach_trim_reallocation_analysis",
            log_label="trim reallocation analysis",
        )

    def attach_decision_attribution_analysis(week_id: str, review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return attach_weekly_review_analysis(
            week_id,
            review,
            method_name="attach_decision_attribution_analysis",
            log_label="decision attribution analysis",
        )

    def attach_weekly_review_analyses(week_id: str, review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = dict(review or {})
        if deps.review_projection_provider:
            try:
                projected = deps.review_projection_provider(week_id, payload)
                if isinstance(projected, dict) and projected.get("stocks"):
                    payload = projected
            except Exception:
                deps.logger.exception("Failed to attach IBKR-derived projection for week_id=%s", week_id)
        payload = attach_trim_reallocation_analysis(week_id, payload)
        return attach_decision_attribution_analysis(week_id, payload)

    return attach_weekly_review_analyses


@dataclass
class WeeklyReviewResolutionDeps:
    get_storage: Callable[[], Any]
    get_week_id: Callable[[], str]
    has_weekly_review_content: Callable[[Optional[Dict[str, Any]]], bool]
    attach_weekly_review_analyses: Callable[[str, Optional[Dict[str, Any]]], Dict[str, Any]]
    normalize_weekly_review_payload: Callable[[str, Optional[Dict[str, Any]]], Dict[str, Any]]


def build_resolve_effective_weekly_review(
    deps: WeeklyReviewResolutionDeps,
) -> Callable[[Optional[str]], Tuple[str, List[Dict[str, Any]], Dict[str, Any], List[str]]]:
    def resolve_effective_weekly_review(
        requested_week_id: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], List[str]]:
        requested = str(requested_week_id or "").strip()
        week_id = requested or deps.get_week_id()
        storage = deps.get_storage()
        stocks = storage.list_stocks()
        review = storage.get_or_create_weekly_review(week_id, stocks)
        if not requested and not deps.has_weekly_review_content(review):
            history = storage.get_weekly_review_history(limit=24)
            for candidate in history:
                if candidate == week_id:
                    continue
                candidate_review = storage.get_or_create_weekly_review(candidate, stocks)
                if deps.has_weekly_review_content(candidate_review):
                    week_id = candidate
                    review = candidate_review
                    break
        history = storage.get_weekly_review_history(limit=24)
        if week_id not in history:
            history = [week_id] + history
        if hasattr(storage, "get_weekly_review_with_portfolio_state"):
            stateful_review = storage.get_weekly_review_with_portfolio_state(week_id, stock_list=stocks)
            if stateful_review:
                review = stateful_review
        review = deps.attach_weekly_review_analyses(week_id, review)
        return week_id, stocks or [], deps.normalize_weekly_review_payload(week_id, review), history or [week_id]

    return resolve_effective_weekly_review


def _format_markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text.replace("|", "\\|").replace("\n", " ")


def _format_export_value(value: Any, formatter: Callable[[Any], str]) -> str:
    if value in (None, ""):
        return ""
    try:
        return formatter(value) or ""
    except Exception:
        return str(value)


def _stock_records_from_review(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for stock_id, stock in (review.get("stocks") or {}).items():
        if not isinstance(stock, dict):
            continue
        resolved_id = str(stock.get("stock_id") or stock_id or "").strip()
        if not resolved_id:
            continue
        records.append(
            {
                "stock_id": resolved_id,
                "stock_name": stock.get("stock_name") or resolved_id,
                "ticker": stock.get("ticker") or resolved_id,
            }
        )
    return records


def _review_with_portfolio_state(storage: Any, week_id: str) -> Optional[Dict[str, Any]]:
    stocks = storage.list_stocks() if hasattr(storage, "list_stocks") else []
    if hasattr(storage, "get_weekly_review_with_portfolio_state"):
        review = storage.get_weekly_review_with_portfolio_state(week_id, stock_list=stocks)
        if review:
            return review
    review = storage.get_weekly_review(week_id) if hasattr(storage, "get_weekly_review") else None
    if review:
        return review
    if hasattr(storage, "get_or_create_weekly_review"):
        return storage.get_or_create_weekly_review(week_id, stocks)
    return None


def _commentary_payload_for_review(
    deps: WeeklyReviewExportDeps,
    storage: Any,
    week_id: str,
    review: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not callable(deps.get_stock_commentary):
        return None
    records = _stock_records_from_review(review)
    if not records:
        return None
    try:
        return deps.get_stock_commentary(storage, records, rolling_days=7, week_id=week_id) or {}
    except Exception as exc:
        return {"error": str(exc)}


def _commentary_entry_for_stock(
    commentary_payload: Optional[Dict[str, Any]],
    stock_id: str,
    stock: Dict[str, Any],
) -> Dict[str, Any]:
    if not commentary_payload:
        return {}
    stocks_payload = commentary_payload.get("stocks") or {}
    keys = [
        stock_id,
        stock.get("stock_id"),
        stock.get("ticker"),
        stock.get("stock_name"),
    ]
    for key in keys:
        if key and stocks_payload.get(key):
            entry = stocks_payload.get(key)
            return entry if isinstance(entry, dict) else {}
    return {}


def _append_full_holdings_table(
    lines: List[str],
    stocks: Dict[str, Any],
    *,
    fmt_money: Callable[[Any], str],
    fmt_number: Callable[[Any], str],
) -> None:
    lines.extend(["## Holdings and P&LText", ""])
    lines.append(
        "| Ticker | Text | Shares | Text | Text | Text | HoldingsText(HKD) | Weight | To Date% | YTD | 6M | 1Y | TextP&L(HKD) | This WeekP&L(HKD) | Contribution% |"
    )
    lines.append("| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for stock_id in sorted(stocks.keys()):
        stock = stocks.get(stock_id) or {}
        if not isinstance(stock, dict):
            continue
        metrics = stock.get("position_metrics") or {}
        performance = stock.get("performance_data") or {}
        returns = stock.get("portfolio_returns") or {}
        ticker = stock.get("ticker") or metrics.get("ticker") or stock_id
        row = [
            _format_markdown_cell(ticker),
            _format_markdown_cell(stock.get("stock_name") or stock_id),
            _format_export_value(stock.get("shares_held"), fmt_number),
            _format_markdown_cell(stock.get("buy_date") or ""),
            _format_export_value(stock.get("avg_cost"), fmt_number),
            _format_export_value(performance.get("end_price") or stock.get("current_price"), fmt_number),
            _format_export_value(metrics.get("holding_value_hkd"), fmt_money),
            _format_export_value(metrics.get("holding_pct"), fmt_number),
            _format_export_value(metrics.get("return_since_buy") or returns.get("return_since_buy"), fmt_number),
            _format_export_value(returns.get("ytd_return"), fmt_number),
            _format_export_value(returns.get("return_6m"), fmt_number),
            _format_export_value(returns.get("return_1y"), fmt_number),
            _format_export_value(metrics.get("unrealized_pnl_hkd"), fmt_money),
            _format_export_value(metrics.get("weekly_pnl_hkd"), fmt_money),
            _format_export_value(metrics.get("pnl_contribution"), fmt_number),
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")


def _append_stock_raw_commentary(
    lines: List[str],
    stock_id: str,
    stock: Dict[str, Any],
    commentary_payload: Optional[Dict[str, Any]],
) -> None:
    if not commentary_payload:
        return
    error = str(commentary_payload.get("error") or "").strip()
    if error:
        lines.extend(["", "#### This WeekText", "", f"_TextLoadFailed: {error}_"])
        return

    entry = _commentary_entry_for_stock(commentary_payload, stock_id, stock)
    items = entry.get("items") or []
    if not items:
        return

    lines.extend(["", "#### This WeekText", ""])
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            lines.extend([f"##### {index}. Text", "", str(item), ""])
            continue
        title = str(item.get("title") or item.get("headline") or item.get("header_lines", [""])[0] or f"Text {index}").strip()
        lines.extend([f"##### {index}. {title}", ""])
        source = str(item.get("source") or item.get("author") or item.get("source_file") or "").strip()
        date = str(item.get("date") or item.get("published_at") or item.get("created_at") or item.get("time") or "").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        keywords = item.get("matched_keywords") or item.get("keywords") or []
        if date:
            lines.append(f"- Date: {date}")
        if source:
            lines.append(f"- Text: {source}")
        if url:
            lines.append(f"- Text: {url}")
        if keywords:
            lines.append(f"- Text: {', '.join(str(keyword) for keyword in keywords)}")
        summary = str(item.get("summary") or item.get("description") or item.get("preview") or "").strip()
        if summary:
            lines.extend(["", summary])
        body = str(item.get("content") or item.get("body") or item.get("text") or "").strip()
        if body and body != summary:
            lines.extend(["", body])
        rendered_keys = {
            "title",
            "headline",
            "header_lines",
            "source",
            "author",
            "source_file",
            "date",
            "published_at",
            "created_at",
            "time",
            "url",
            "link",
            "matched_keywords",
            "keywords",
            "summary",
            "description",
            "preview",
            "content",
            "body",
            "text",
            "expanded",
        }
        remaining = {key: value for key, value in item.items() if key not in rendered_keys}
        if remaining:
            lines.extend(
                [
                    "",
                    "Text:",
                    "",
                    "```json",
                    json.dumps(remaining, ensure_ascii=False, indent=2, default=str),
                    "```",
                ]
            )
        lines.append("")


def markdown_from_weekly_review(
    week_id: str,
    review: Dict[str, Any],
    *,
    fmt_money: Callable[[Any], str],
    fmt_number: Callable[[Any], str],
    commentary_payload: Optional[Dict[str, Any]] = None,
) -> str:
    lines: List[str] = [f"# {week_id} Text", ""]

    total_value = fmt_money(review.get("total_portfolio_value"))
    if total_value:
        lines.append(f"- Text(HKD): {total_value}")
    if review.get("prices_refreshed_at"):
        lines.append(f"- TextRefreshText: {review.get('prices_refreshed_at')}")
    if review.get("updated_at"):
        lines.append(f"- Text: {review.get('updated_at')}")
    if len(lines) > 2:
        lines.append("")

    market_context = review.get("market_context") or {}
    if market_context:
        lines.extend(["## This WeekTextStatus", ""])
        big_picture = market_context.get("big_picture") or {}
        if big_picture:
            if big_picture.get("summary"):
                lines.extend(["### Big Picture", "", str(big_picture.get("summary")), ""])
            if big_picture.get("regime"):
                lines.append(f"- Regime: {big_picture.get('regime')}")
            for group in big_picture.get("groups") or []:
                lines.extend(["", f"### {group.get('label') or group.get('id')}", ""])
                for signal in (group.get("signals") or [])[:6]:
                    lines.append(
                        f"- **{signal.get('name') or signal.get('ticker')}**: "
                        f"{signal.get('change_pct')}% | {signal.get('read') or signal.get('proxy_note') or ''}"
                    )
            lines.append("")
        signals = market_context.get("signals") or []
        if signals:
            lines.extend(["### Text", ""])
            for signal in signals:
                if not isinstance(signal, dict) or not signal.get("success"):
                    continue
                tag_text = ", ".join(signal.get("tags") or [])
                line = f"- **{signal.get('name') or signal.get('ticker')}**: {signal.get('performance_summary') or ''}"
                if tag_text:
                    line += f" | Text: {tag_text}"
                lines.append(line)
            lines.append("")
        ai_summary = str(market_context.get("ai_summary") or "").strip()
        if ai_summary:
            lines.extend(["### AI Text", "", ai_summary, ""])
        watch_items = market_context.get("watch_items") or []
        if watch_items:
            lines.extend(["### Text", ""])
            for item in watch_items:
                lines.append(f"- {item}")
            lines.append("")

    factor_analysis = review.get("factor_analysis") or {}
    if factor_analysis:
        lines.extend(["## Text", ""])
        primary_model = factor_analysis.get("primary_model") or {}
        if primary_model:
            lines.extend(["### Text", ""])
            lines.append(
                f"- **{primary_model.get('label') or primary_model.get('key') or 'Text'}**"
                f": Text {fmt_number(primary_model.get('r_squared'))} | Text {fmt_number(primary_model.get('stability_score'))}"
            )
            reason = str(primary_model.get("reason") or "").strip()
            if reason:
                lines.append(f"- Text: {reason}")
            lines.append("")
        academic = factor_analysis.get("portfolio_exposure") or {}
        if academic:
            lines.extend(["### Academic Factor Exposure", ""])
            for factor, value in academic.items():
                label = ((factor_analysis.get("factor_labels") or {}).get(factor) or {}).get("zh") or factor
                lines.append(f"- **{label} ({factor})**: {fmt_number(value)}")
            lines.append("")
        proxy = factor_analysis.get("sector_macro_exposures") or factor_analysis.get("proxy_factor_exposures") or {}
        if proxy:
            lines.extend(["### Text", ""])
            for factor, value in proxy.items():
                label = ((factor_analysis.get("proxy_factor_labels") or {}).get(factor) or {}).get("zh") or factor
                lines.append(f"- **{label} ({factor})**: {fmt_number(value)}")
            lines.append("")
        overlays = factor_analysis.get("style_overlays") or {}
        if overlays:
            lines.extend(["### Style Overlay", ""])
            for key, item in overlays.items():
                if not isinstance(item, dict) or item.get("error"):
                    continue
                label = item.get("label") or key
                lines.append(f"- **{label}**: {fmt_number(item.get('headline_value'))}")
            lines.append("")
        diagnosis = factor_analysis.get("portfolio_diagnosis") or []
        if diagnosis:
            lines.extend(["### This WeekText", ""])
            for item in diagnosis:
                summary = str((item or {}).get("summary") or "").strip()
                if summary:
                    lines.append(f"- {summary}")
            lines.append("")
        exposure_change = factor_analysis.get("exposure_change") or {}
        if exposure_change.get("available"):
            lines.extend(["### TextLast WeekText", ""])
            for factor, value in (exposure_change.get("proxy") or {}).items():
                if not value:
                    continue
                lines.append(f"- {factor}: {fmt_number(value)}")
            for factor, meta in (exposure_change.get("buckets") or {}).items():
                delta = (meta or {}).get("weight_change")
                if not delta:
                    continue
                lines.append(f"- {(meta or {}).get('label') or factor}: {fmt_number(delta)}")
            for alert in exposure_change.get("drift_alerts") or []:
                lines.append(f"- Text: {alert}")
            lines.append("")
        attribution = factor_analysis.get("attribution_summary") or {}
        if attribution.get("watch_items"):
            lines.extend(["### TextRisk", ""])
            for item in attribution.get("watch_items") or []:
                lines.append(f"- {item}")
            lines.append("")
        unsupported = factor_analysis.get("unsupported_holdings") or []
        if unsupported:
            lines.extend(["### TextAnalysisTextHoldings", ""])
            for item in unsupported:
                lines.append(
                    f"- {(item.get('stock_name') or item.get('stock_id') or 'Text')} "
                    f"({item.get('ticker') or item.get('stock_id') or ''}): {item.get('reason') or 'Text'}"
                )
            lines.append("")

    stocks = review.get("stocks") or {}
    if stocks:
        _append_full_holdings_table(lines, stocks, fmt_money=fmt_money, fmt_number=fmt_number)
        lines.extend(["## HoldingsTextReview", ""])
        for stock_id in sorted(stocks.keys()):
            stock = stocks.get(stock_id) or {}
            stock_name = stock.get("stock_name") or stock_id
            lines.append(f"### {stock_name} ({stock_id})")

            meta_rows = []
            if stock.get("shares_held") not in (None, ""):
                meta_rows.append(f"- TextSharesText: {fmt_number(stock.get('shares_held'))}")
            if stock.get("avg_cost") not in (None, ""):
                meta_rows.append(f"- Text: {fmt_number(stock.get('avg_cost'))}")
            if stock.get("buy_date"):
                meta_rows.append(f"- BuyDate: {stock.get('buy_date')}")
            if stock.get("performance_summary"):
                meta_rows.append(f"- TextSummary: {stock.get('performance_summary')}")
            lines.extend(meta_rows)

            news = stock.get("news") or []
            if news:
                lines.extend(["", "#### News", ""])
                for item in news:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()
                    summary = str(item.get("summary") or "").strip()
                    date = str(item.get("date") or "").strip()
                    source = str(item.get("source") or "").strip()
                    header_bits = [part for part in [date, source] if part]
                    header = f" ({' | '.join(header_bits)})" if header_bits else ""
                    if title:
                        lines.append(f"- **{title}**{header}")
                    elif header_bits:
                        lines.append(f"- {' | '.join(header_bits)}")
                    if summary:
                        lines.append(f"  - {summary}")

            news_summary = str(stock.get("news_summary") or "").strip()
            if news_summary:
                lines.extend(["", "#### AI NewsText", "", news_summary])

            weekly_ai_summary = str(stock.get("broker_commentary_ai_summary") or "").strip()
            if weekly_ai_summary:
                lines.extend(["", "#### This Week AI Summary", "", weekly_ai_summary])

            _append_stock_raw_commentary(lines, stock_id, stock, commentary_payload)

            user_view = str(stock.get("user_view") or "").strip()
            if user_view:
                lines.extend(["", "#### Text", "", user_view])

            lines.append("")

    closed_positions = review.get("closed_positions") or []
    if closed_positions:
        lines.extend(["## Text", ""])
        for pos in closed_positions:
            if not isinstance(pos, dict):
                continue
            name = pos.get("stock_name") or pos.get("stock_id") or "Text"
            stock_id = pos.get("stock_id") or ""
            lines.append(f"### {name}{f' ({stock_id})' if stock_id else ''}")
            if pos.get("shares_sold") not in (None, ""):
                lines.append(f"- SellText: {fmt_number(pos.get('shares_sold'))}")
            if pos.get("sell_date"):
                lines.append(f"- SellDate: {pos.get('sell_date')}")
            if pos.get("sell_price") not in (None, ""):
                lines.append(f"- SellText: {fmt_number(pos.get('sell_price'))}")
            if pos.get("realized_pnl_hkd") not in (None, ""):
                lines.append(f"- TextP&L(HKD): {fmt_money(pos.get('realized_pnl_hkd'))}")
            elif pos.get("realized_pnl") not in (None, ""):
                lines.append(f"- TextP&L: {fmt_money(pos.get('realized_pnl'))}")
            lines.append("")

    rebalancing_ops = review.get("rebalancing_ops") or []
    if rebalancing_ops:
        lines.extend(["## TradesText", ""])
        for op in rebalancing_ops:
            if not isinstance(op, dict):
                continue
            op_type = str(op.get("op_type") or "").strip() or "Text"
            stock_id = str(op.get("stock_id") or "").strip()
            quantity = fmt_number(op.get("quantity"))
            price = fmt_number(op.get("price"))
            date = str(op.get("date") or "").strip()
            parts = [op_type]
            if stock_id:
                parts.append(stock_id)
            if quantity:
                parts.append(f"Text {quantity}")
            if price:
                parts.append(f"Text {price}")
            if date:
                parts.append(date)
            lines.append(f"- {' | '.join(parts)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_weekly_review_snapshot_builder(deps: WeeklyReviewExportDeps) -> Callable[[str], Dict[str, Any]]:
    def build_weekly_review_snapshot(week_id: str) -> Dict[str, Any]:
        storage = deps.get_storage()
        review = _review_with_portfolio_state(storage, week_id)
        commentary_payload = _commentary_payload_for_review(deps, storage, week_id, review or {})
        content = markdown_from_weekly_review(
            week_id,
            review or {},
            fmt_money=deps.fmt_money,
            fmt_number=deps.fmt_number,
            commentary_payload=commentary_payload,
        )
        filename = f"{week_id}_Weekly Review.md"
        path = storage.get_ima_export_path("weekly_reviews", filename)
        deps.write_markdown_snapshot(path, content)
        return {
            "week_id": week_id,
            "title": Path(filename).stem,
            "local_file": path,
            "content": content,
            "review": review or {},
        }

    return build_weekly_review_snapshot


def build_weekly_reviews_export_response_builder(deps: WeeklyReviewExportDeps) -> Callable[[Iterable[str], str], Response]:
    def build_weekly_reviews_export_response(week_ids: Iterable[str], filename_prefix: str) -> Response:
        parts: List[str] = []
        storage = deps.get_storage()
        for week_id in week_ids:
            review = _review_with_portfolio_state(storage, week_id)
            if not review:
                continue
            commentary_payload = _commentary_payload_for_review(deps, storage, week_id, review)
            parts.append(
                markdown_from_weekly_review(
                    week_id,
                    review,
                    fmt_money=deps.fmt_money,
                    fmt_number=deps.fmt_number,
                    commentary_payload=commentary_payload,
                )
            )

        if not parts:
            parts = ["# Text", "", "_No dataText. _", ""]

        content = "\n---\n\n".join(parts)
        filename = f"{filename_prefix}-{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        resp = Response(content, mimetype="text/markdown; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    return build_weekly_reviews_export_response


def build_weekly_reviews_export_response_builder_v2(deps: WeeklyReviewExportDeps) -> Callable[[Iterable[str], str], Response]:
    def build_weekly_reviews_export_response(week_ids: Iterable[str], filename_prefix: str) -> Response:
        snapshots: List[Tuple[str, str]] = []
        storage = deps.get_storage()
        for week_id in week_ids:
            review = _review_with_portfolio_state(storage, week_id)
            if not review:
                continue
            commentary_payload = _commentary_payload_for_review(deps, storage, week_id, review)
            snapshots.append(
                (
                    f"{week_id}_weekly-review.md",
                    markdown_from_weekly_review(
                        week_id,
                        review,
                        fmt_money=deps.fmt_money,
                        fmt_number=deps.fmt_number,
                        commentary_payload=commentary_payload,
                    ),
                )
            )

        if not snapshots:
            snapshots = [("weekly-review-empty.md", "# Weekly Reviews\n\n_No exportable weekly review content._\n")]

        if len(snapshots) == 1:
            file_name, content = snapshots[0]
            resp = Response(content, mimetype="text/markdown; charset=utf-8")
            resp.headers["Content-Disposition"] = f'attachment; filename="{file_name}"'
            return resp

        filename = f"{filename_prefix}-{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            for file_name, content in snapshots:
                archive.writestr(file_name, content)
        resp = Response(buffer.getvalue(), mimetype="application/zip")
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    return build_weekly_reviews_export_response
