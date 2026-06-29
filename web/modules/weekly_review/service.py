from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import Response, jsonify, render_template, request

from core.portfolio_quantstats import generate_quantstats_report
from web.request_parsing import load_json_object
from web.services.domain_services import WeeklyReviewService


@dataclass
class WeeklyReviewModuleService(WeeklyReviewService):
    weekly_review_page: Callable[[], Any]
    weekly_review_alias_redirect: Callable[[], Any]
    get_weekly_review_stock_zsxq_matches: Callable[[str, str], Any]
    get_weekly_review_stock_filings: Callable[[str, str], Any]
    refresh_weekly_review_stock_filings: Callable[[str, str], Any]
    submit_weekly_review_generate_task: Callable[[], Any]
    sync_weekly_review_to_ima: Callable[[str], Any]
    get_decision_logs: Callable[[str], Any]
    create_decision_log: Callable[[str], Any]
    update_decision_log: Callable[[str, str], Any]
    delete_decision_log: Callable[[str, str], Any]
    download_quantstats_report: Callable[[str], Any]


@dataclass
class WeeklyReviewModuleDeps:
    get_weekly_review_manager: Callable[[], Any]
    get_week_id: Callable[[], str]
    get_storage: Callable[[], Any]
    resolve_effective_weekly_review: Callable[[str | None], Any]
    attach_weekly_review_analyses: Callable[[str, Any], Any]
    normalize_weekly_review_payload: Callable[[str, Any], Any]
    has_weekly_review_content: Callable[[Any], bool]
    serialize_ima_sync_status: Callable[[Any], Any]
    ima_sync_key_weekly_review: Callable[[str], str]
    json_safe: Callable[[Any], Any]
    build_portfolio_performance: Callable[..., Any]
    get_stock_commentary: Callable[..., Any]
    get_stock_filings: Callable[..., Any]
    logger: Any


def build_weekly_review_module_service(
    deps: WeeklyReviewModuleDeps,
    *,
    submit_weekly_review_generate_task: Callable[[], Any],
    export_current_weekly_review_markdown: Callable[[], Any],
    export_all_weekly_reviews_markdown: Callable[[], Any],
    export_recent_weekly_reviews_markdown: Callable[[], Any],
    get_market_context: Callable[[], Any],
    refresh_market_context: Callable[[], Any],
    summarize_market_context: Callable[[], Any],
    refresh_macro_events: Callable[[], Any],
    refresh_stock_news: Callable[[str], Any],
    refresh_stock_performance: Callable[[str], Any],
    refresh_all_news: Callable[[], Any],
    refresh_all_news_and_scan: Callable[[], Any],
    refresh_portfolio_prices: Callable[[], Any],
    refresh_all_performance: Callable[[], Any],
    generate_news_summary: Callable[[str], Any],
    generate_weekly_stock_ai_summary: Callable[[str, str], Any],
    generate_weekly_stock_ai_summaries_batch: Callable[[str], Any],
    sync_weekly_review_to_ima: Callable[[str], Any],
    save_stock_weekly_view: Callable[[str], Any],
    save_weekly_portfolio: Callable[[], Any],
    save_rebalancing_ops: Callable[[], Any],
    apply_rebalancing: Callable[[], Any],
    get_portfolio_performance: Callable[[], Any],
    weekly_synthesize: Callable[[], Any],
    weekly_chat: Callable[[], Any],
) -> WeeklyReviewModuleService:
    def _json_object_body_or_error():
        return load_json_object()

    def _decision_log_manager_or_error():
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return None, (jsonify({"error": "LLM is not configured"}), 400)
        return mgr, None

    def _decision_log_for_week_or_error(week_id: str, log_id: str):
        row = deps.get_storage().get_decision_log(log_id)
        if row is None or str(row.get("week_id") or "").strip() != str(week_id or "").strip():
            return None, (jsonify({"error": "decision log not found"}), 404)
        return row, None

    def _find_week_stock(week_id: str, stock_id: str) -> dict[str, Any] | None:
        _, stocks, _, _ = deps.resolve_effective_weekly_review(week_id)
        resolved_stock_id = str(stock_id or "").strip()
        for stock in list(stocks or []):
            candidate_id = str(stock.get("stock_id") or "").strip()
            candidate_ticker = str(stock.get("ticker") or "").strip()
            if candidate_id.upper() == resolved_stock_id.upper() or candidate_ticker.upper() == resolved_stock_id.upper():
                return stock
        return None

    def _empty_zsxq_payload(stock: dict[str, Any], window: Any):
        return {
            "success": True,
            "stock_id": stock.get("stock_id") or "",
            "stock_name": stock.get("stock_name") or "",
            "ticker": stock.get("ticker") or "",
            "keywords_used": [],
            "match_count": 0,
            "header_match_count": 0,
            "body_match_count": 0,
            "source_group_count": 0,
            "items": [],
            "window": window or {},
        }

    def _empty_filings_payload(stock: dict[str, Any], window: Any, market: str):
        return {
            "success": True,
            "stock_id": stock.get("stock_id") or "",
            "stock_name": stock.get("stock_name") or "",
            "ticker": stock.get("ticker") or "",
            "market": market,
            "window": window or {},
            "counts": {"total": 0, "high_importance": 0},
            "items": [],
            "cache": {"hit": False, "updated_at": ""},
        }

    def weekly_review_page():
        requested_week_id = (request.args.get("week_id") or "").strip()
        week_id, stocks, review, history = deps.resolve_effective_weekly_review(requested_week_id)
        review["ima_sync_status"] = deps.serialize_ima_sync_status(
            deps.get_storage().get_ima_sync_record(deps.ima_sync_key_weekly_review(week_id))
        )
        if hasattr(deps.get_storage(), "build_weekly_review_data_health"):
            review["data_health"] = deps.get_storage().build_weekly_review_data_health(review, stock_list=stocks or [])
        return render_template(
            "weekly_review.html",
            week_id=week_id,
            review=review,
            stocks=stocks or [],
            week_options=history or [week_id],
        )

    def weekly_review_alias_redirect():
        return weekly_review_page()

    def get_weekly_review():
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        week_id = (request.args.get("week_id") or "").strip() or None
        resolved_week_id = week_id or deps.get_week_id()
        try:
            review = mgr.get_or_create_review(week_id=resolved_week_id) or {}
            review = deps.attach_weekly_review_analyses(resolved_week_id, review)
            payload = deps.normalize_weekly_review_payload(resolved_week_id, review)
            if not week_id and not deps.has_weekly_review_content(payload):
                resolved_week_id, _, payload, _ = deps.resolve_effective_weekly_review()
            payload["ima_sync_status"] = deps.serialize_ima_sync_status(
                deps.get_storage().get_ima_sync_record(deps.ima_sync_key_weekly_review(resolved_week_id))
            )
            if hasattr(deps.get_storage(), "build_weekly_review_data_health"):
                payload["data_health"] = deps.get_storage().build_weekly_review_data_health(
                    payload,
                    stock_list=deps.get_storage().list_stocks(),
                )
            return jsonify(deps.json_safe(payload))
        except Exception as exc:
            deps.logger.exception("Failed to load weekly review")
            return (
                jsonify(
                    deps.json_safe(
                        {
                            "error": str(exc),
                            "week_id": resolved_week_id,
                            "stocks": {},
                            "market_context": {},
                            "factor_analysis": {},
                            "trim_reallocation_analysis": {"summary": {}, "stocks": [], "events": []},
                            "decision_attribution_analysis": {"summary": {}, "patterns": {}, "stocks": [], "events": []},
                        }
                    )
                ),
                500,
            )

    def get_weekly_review_stock_zsxq_matches(week_id: str, stock_id: str):
        stock = _find_week_stock(week_id, stock_id)
        if not stock:
            return jsonify({"success": False, "error": "StockText"}), 404

        payload = deps.get_stock_commentary(
            deps.get_storage(),
            [
                {
                    "stock_id": stock.get("stock_id"),
                    "stock_name": stock.get("stock_name"),
                    "ticker": stock.get("ticker"),
                }
            ],
            week_id=week_id,
        ) or {}
        entry = ((payload.get("stocks") or {}).get(stock.get("stock_id"))) or {}
        result = {
            **_empty_zsxq_payload(stock, payload.get("window")),
            **entry,
            "success": True,
            "stock_id": stock.get("stock_id") or "",
            "stock_name": entry.get("stock_name") or stock.get("stock_name") or "",
            "ticker": entry.get("ticker") or stock.get("ticker") or "",
            "keywords_used": list(entry.get("keywords_used") or []),
            "match_count": int(entry.get("match_count") or len(entry.get("items") or []) or 0),
            "header_match_count": int(entry.get("header_match_count") or 0),
            "body_match_count": int(entry.get("body_match_count") or 0),
            "source_group_count": int(entry.get("source_group_count") or 0),
            "items": list(entry.get("items") or []),
            "window": payload.get("window") or {},
        }
        return jsonify(deps.json_safe(result))

    def get_weekly_review_stock_filings(week_id: str, stock_id: str):
        stock = _find_week_stock(week_id, stock_id)
        if not stock:
            return jsonify({"success": False, "error": "stock not found"}), 404
        payload = deps.get_stock_filings(
            stock_id=str(stock.get("stock_id") or "").strip(),
            stock_name=str(stock.get("stock_name") or "").strip(),
            ticker=str(stock.get("ticker") or "").strip(),
            week_id=week_id,
            force_refresh=False,
        ) or {}
        result = {
            **_empty_filings_payload(stock, payload.get("window"), payload.get("market") or "UNKNOWN"),
            **payload,
            "success": True,
        }
        return jsonify(deps.json_safe(result))

    def refresh_weekly_review_stock_filings(week_id: str, stock_id: str):
        stock = _find_week_stock(week_id, stock_id)
        if not stock:
            return jsonify({"success": False, "error": "stock not found"}), 404
        payload = deps.get_stock_filings(
            stock_id=str(stock.get("stock_id") or "").strip(),
            stock_name=str(stock.get("stock_name") or "").strip(),
            ticker=str(stock.get("ticker") or "").strip(),
            week_id=week_id,
            force_refresh=True,
        ) or {}
        result = {
            **_empty_filings_payload(stock, payload.get("window"), payload.get("market") or "UNKNOWN"),
            **payload,
            "success": True,
        }
        return jsonify(deps.json_safe(result))

    def get_portfolio_decision_memo(week_id: str):
        review = deps.get_storage().get_weekly_review(week_id) or {}
        memo = dict(review.get("portfolio_decision_memo") or {})
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "week_id": week_id,
                    "portfolio_decision_memo": memo,
                }
            )
        )

    def generate_portfolio_decision_memo(week_id: str):
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        result = mgr.generate_portfolio_decision_memo(
            week_id=week_id,
            user_judgment=str(data.get("user_judgment") or "").strip(),
            final_decision=str(data.get("final_decision") or "").strip(),
            user_feedback=str(data.get("user_feedback") or "").strip(),
        )
        status = 200 if result.get("success") else 502
        return jsonify(deps.json_safe(result)), status

    def save_portfolio_decision_memo_feedback(week_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        memo = deps.get_storage().update_weekly_portfolio_decision_memo(
            week_id,
            user_judgment=str(data.get("user_judgment") or "").strip(),
            final_decision=str(data.get("final_decision") or "").strip(),
            user_feedback=str(data.get("user_feedback") or "").strip(),
        )
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "week_id": week_id,
                    "portfolio_decision_memo": memo,
                }
            )
        )

    def get_decision_logs(week_id: str):
        mgr, error_response = _decision_log_manager_or_error()
        if error_response is not None:
            return error_response
        return jsonify(deps.json_safe(mgr.build_weekly_decision_log_context(week_id)))

    def create_decision_log(week_id: str):
        mgr, error_response = _decision_log_manager_or_error()
        if error_response is not None:
            return error_response
        payload, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        payload["week_id"] = week_id
        payload.setdefault("source_module", "weekly_review")
        row = deps.get_storage().create_decision_log(payload)
        context = mgr.build_weekly_decision_log_context(week_id)
        return jsonify(deps.json_safe({"log": row, "context": context}))

    def update_decision_log(week_id: str, log_id: str):
        mgr, error_response = _decision_log_manager_or_error()
        if error_response is not None:
            return error_response
        existing_row, lookup_error = _decision_log_for_week_or_error(week_id, log_id)
        if lookup_error is not None:
            return lookup_error
        patch, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        patch["week_id"] = str(existing_row.get("week_id") or week_id)
        row = deps.get_storage().update_decision_log(log_id, patch)
        if row is None:
            return jsonify({"error": "decision log not found"}), 404
        context = mgr.build_weekly_decision_log_context(week_id)
        return jsonify(deps.json_safe({"log": row, "context": context}))

    def delete_decision_log(week_id: str, log_id: str):
        mgr, error_response = _decision_log_manager_or_error()
        if error_response is not None:
            return error_response
        _, lookup_error = _decision_log_for_week_or_error(week_id, log_id)
        if lookup_error is not None:
            return lookup_error
        deleted = deps.get_storage().delete_decision_log(log_id)
        context = mgr.build_weekly_decision_log_context(week_id)
        return jsonify(deps.json_safe({"deleted": deleted, "context": context}))

    def get_portfolio_performance():
        week_id = (request.args.get("week_id") or "").strip() or deps.get_week_id()
        benchmark = (request.args.get("benchmark") or "QQQ").strip().upper() or "QQQ"
        mode = (request.args.get("mode") or "ytd").strip().lower() or "ytd"
        try:
            lookback_weeks = int(request.args.get("lookback_weeks") or 16)
        except (TypeError, ValueError):
            lookback_weeks = 16
        lookback_weeks = max(4, min(104, lookback_weeks))
        payload = deps.build_portfolio_performance(
            week_id=week_id,
            benchmark=benchmark,
            lookback_weeks=lookback_weeks,
            mode=mode,
        )
        return jsonify(deps.json_safe(payload))

    def download_quantstats_report(report_type: str):
        week_id = (request.args.get("week_id") or "").strip() or deps.get_week_id()
        benchmark = (request.args.get("benchmark") or "QQQ").strip().upper() or "QQQ"
        mode = (request.args.get("mode") or "ytd").strip().lower() or "ytd"
        try:
            lookback_weeks = int(request.args.get("lookback_weeks") or 52)
        except (TypeError, ValueError):
            lookback_weeks = 52
        lookback_weeks = max(4, min(104, lookback_weeks))
        payload = deps.build_portfolio_performance(
            week_id=week_id,
            benchmark=benchmark,
            lookback_weeks=lookback_weeks,
            mode=mode,
        )
        if not payload.get("success"):
            return jsonify(deps.json_safe({"success": False, "error": payload.get("error") or "portfolio_performance_unavailable"})), 422
        report = generate_quantstats_report(
            payload.get("series") or [],
            benchmark=payload.get("benchmark") or benchmark,
            report_type=report_type,
            analytics=payload.get("quant_analytics") or None,
        )
        if not report.get("success"):
            status = 503 if report.get("error") == "quantstats_not_installed" else 422
            return jsonify(deps.json_safe(report)), status
        extension = str(report.get("extension") or ("html" if report_type == "html" else "txt")).strip()
        file_name = f"portfolio-quantstats-{str(report_type or 'html').strip().lower()}-{benchmark}-{week_id}.{extension}"
        resp = Response(str(report.get("content") or ""), mimetype=str(report.get("mimetype") or "text/plain; charset=utf-8"))
        resp.headers["Content-Disposition"] = f'attachment; filename="{file_name}"'
        return resp

    return WeeklyReviewModuleService(
        get_weekly_review=get_weekly_review,
        export_current_weekly_review_markdown=export_current_weekly_review_markdown,
        export_all_weekly_reviews_markdown=export_all_weekly_reviews_markdown,
        export_recent_weekly_reviews_markdown=export_recent_weekly_reviews_markdown,
        get_market_context=get_market_context,
        refresh_market_context=refresh_market_context,
        summarize_market_context=summarize_market_context,
        refresh_macro_events=refresh_macro_events,
        refresh_stock_news=refresh_stock_news,
        refresh_stock_performance=refresh_stock_performance,
        refresh_all_news=refresh_all_news,
        refresh_all_news_and_scan=refresh_all_news_and_scan,
        refresh_portfolio_prices=refresh_portfolio_prices,
        refresh_all_performance=refresh_all_performance,
        generate_news_summary=generate_news_summary,
        generate_weekly_stock_ai_summary=generate_weekly_stock_ai_summary,
        generate_weekly_stock_ai_summaries_batch=generate_weekly_stock_ai_summaries_batch,
        save_stock_weekly_view=save_stock_weekly_view,
        save_weekly_portfolio=save_weekly_portfolio,
        save_rebalancing_ops=save_rebalancing_ops,
        apply_rebalancing=apply_rebalancing,
        get_portfolio_performance=get_portfolio_performance,
        download_quantstats_report=download_quantstats_report,
        weekly_synthesize=weekly_synthesize,
        weekly_chat=weekly_chat,
        weekly_review_page=weekly_review_page,
        weekly_review_alias_redirect=weekly_review_alias_redirect,
        get_weekly_review_stock_zsxq_matches=get_weekly_review_stock_zsxq_matches,
        get_weekly_review_stock_filings=get_weekly_review_stock_filings,
        refresh_weekly_review_stock_filings=refresh_weekly_review_stock_filings,
        submit_weekly_review_generate_task=submit_weekly_review_generate_task,
        sync_weekly_review_to_ima=sync_weekly_review_to_ima,
        get_portfolio_decision_memo=get_portfolio_decision_memo,
        generate_portfolio_decision_memo=generate_portfolio_decision_memo,
        save_portfolio_decision_memo_feedback=save_portfolio_decision_memo_feedback,
        get_decision_logs=get_decision_logs,
        create_decision_log=create_decision_log,
        update_decision_log=update_decision_log,
        delete_decision_log=delete_decision_log,
    )
