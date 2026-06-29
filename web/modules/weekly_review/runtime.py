from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from flask import Response, jsonify, request

from web.request_parsing import load_json_object


def _week_id_for_date_text(value: Any) -> Optional[str]:
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:10])
    except ValueError:
        digits = "".join(ch for ch in text if ch.isdigit())[:8]
        if len(digits) != 8:
            return None
        try:
            parsed = datetime.strptime(digits, "%Y%m%d")
        except ValueError:
            return None
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def _rebalancing_week_mismatch_error(week_id: str, ops: Any) -> Optional[Response]:
    if not isinstance(ops, list):
        return None
    selected_week = str(week_id or "").strip()
    if not selected_week:
        return None
    mismatches = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        op_week = _week_id_for_date_text(op.get("date"))
        if op_week and op_week != selected_week:
            mismatches.append(
                {
                    "index": index,
                    "stock_id": str(op.get("stock_id") or "").strip(),
                    "date": str(op.get("date") or "").strip(),
                    "actual_week_id": op_week,
                }
            )
    if not mismatches:
        return None
    first = mismatches[0]
    return jsonify(
        {
            "error": (
                f"TradesDate {first['date']} Text {first['actual_week_id']}, "
                f"TextSaveTextCurrentText {selected_week}. TextSave. "
            ),
            "mismatches": mismatches,
        }
    ), 400


@dataclass
class WeeklyReviewTaskRunners:
    generate: Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]
    synthesize: Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]
    chat: Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]


@dataclass
class WeeklyReviewTaskRunnerDeps:
    get_week_id: Callable[[], str]
    get_weekly_review_manager: Callable[[], Any]
    get_storage: Callable[[], Any]
    patch_task_record: Callable[[Optional[Dict[str, Any]], Dict[str, Any]], None]
    is_llm_failure_text: Callable[[Any], bool]
    get_runtime_meta: Callable[[], Dict[str, Any]]


@dataclass
class WeeklyReviewTaskSubmitHandlers:
    generate: Callable[[], Response]
    synthesize: Callable[[], Response]
    chat: Callable[[], Response]


@dataclass
class WeeklyReviewTaskSubmitDeps:
    get_week_id: Callable[[], str]
    get_task_manager: Callable[[], Any]
    task_payload: Callable[..., Dict[str, Any]]
    task_error_response: Callable[..., Any]
    logger: Any


@dataclass
class WeeklyReviewExportHandlers:
    export_current_markdown: Callable[[], Any]
    export_all_markdown: Callable[[], Any]
    export_recent_markdown: Callable[[], Any]
    sync_weekly_review_to_ima: Callable[[str], Response]


@dataclass
class WeeklyReviewExportDeps:
    get_storage: Callable[[], Any]
    safe_int: Callable[[Any, int], int]
    get_week_id: Callable[[], str]
    to_bool: Callable[[Any, bool], bool]
    build_weekly_review_snapshot: Callable[[str], Dict[str, Any]]
    build_weekly_reviews_export_response: Callable[[list[str], str], Any]
    sync_snapshot_to_ima: Callable[..., Dict[str, Any]]
    ima_sync_key_weekly_review: Callable[[str], str]
    json_safe: Callable[[Any], Any]


@dataclass
class WeeklyReviewApiHandlers:
    get_market_context: Callable[[], Response]
    refresh_market_context: Callable[[], Response]
    refresh_macro_events: Callable[[], Response]
    summarize_market_context: Callable[[], Response]
    refresh_stock_news: Callable[[str], Response]
    refresh_stock_performance: Callable[[str], Response]
    refresh_all_news: Callable[[], Response]
    refresh_all_news_and_scan: Callable[[], Response]
    refresh_portfolio_prices: Callable[[], Response]
    refresh_all_performance: Callable[[], Response]
    generate_news_summary: Callable[[str], Response]
    generate_weekly_stock_ai_summary: Callable[[str, str], Response]
    generate_weekly_stock_ai_summaries_batch: Callable[[str], Response]
    save_stock_weekly_view: Callable[[str], Response]
    save_weekly_portfolio: Callable[[], Response]
    save_rebalancing_ops: Callable[[], Response]
    apply_rebalancing: Callable[[], Response]
    get_portfolio_performance: Callable[[], Response]


@dataclass
class WeeklyReviewApiDeps:
    get_weekly_review_manager: Callable[[], Any]
    get_week_id: Callable[[], str]
    get_storage: Callable[[], Any]
    get_stock_filings: Callable[..., Any]
    build_portfolio_performance: Callable[..., Any]
    json_safe: Callable[[Any], Any]
    safe_int: Callable[[Any, int], int]
    to_bool: Callable[[Any, bool], bool]
    logger: Any


def build_weekly_review_task_runners(deps: WeeklyReviewTaskRunnerDeps) -> WeeklyReviewTaskRunners:
    def generate(
        payload: Optional[Dict[str, Any]],
        task: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        request_payload = dict(payload or {})
        week_id = str(request_payload.get("week_id") or "").strip() or deps.get_week_id()
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            raise RuntimeError("LLM is not configured")

        deps.patch_task_record(
            task,
            {
                "message": f"Generating weekly review for {week_id}...",
                "week_id": week_id,
            },
        )

        review = mgr.get_or_create_review(week_id=week_id) or {}
        stocks_raw = review.get("stocks")
        if isinstance(stocks_raw, dict):
            stock_count = len(stocks_raw)
        elif isinstance(stocks_raw, list):
            stock_count = len(stocks_raw)
        else:
            stock_count = 0

        summary = {
            "stock_count": stock_count,
            "has_market_context": bool(review.get("market_context")),
            "has_factor_analysis": bool(review.get("factor_analysis")),
            "updated_at": str(review.get("updated_at") or ""),
            "prices_refreshed_at": str(review.get("prices_refreshed_at") or ""),
        }
        payload_result = {
            "success": True,
            "week_id": week_id,
            "summary": summary,
            "message": "Weekly review generated.",
        }
        deps.patch_task_record(task, payload_result)
        return payload_result

    def synthesize(
        payload: Optional[Dict[str, Any]],
        task: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        request_payload = dict(payload or {})
        week_id = str(request_payload.get("week_id") or "").strip() or deps.get_week_id()
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            raise RuntimeError("LLM is not configured")

        deps.patch_task_record(
            task,
            {
                "message": f"Generating weekly synthesis for {week_id}...",
                "week_id": week_id,
            },
        )

        text = mgr.synthesize_thesis_update(week_id=week_id)
        if deps.is_llm_failure_text(text):
            raise RuntimeError(str(text or "LLM Text"))

        review = deps.get_storage().get_weekly_review(week_id) or {}
        if not review and hasattr(mgr, "get_or_create_review"):
            try:
                review = mgr.get_or_create_review(week_id=week_id) or {}
            except Exception:
                review = {}
        payload_result = {
            "success": True,
            "week_id": week_id,
            "summary": str(text or "").strip(),
            "stocks": review.get("stocks") or {},
            "runtime_meta": deps.get_runtime_meta(),
            "message": "Weekly synthesis completed.",
        }
        deps.patch_task_record(task, payload_result)
        return payload_result

    def chat(
        payload: Optional[Dict[str, Any]],
        task: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        request_payload = dict(payload or {})
        week_id = str(request_payload.get("week_id") or "").strip() or deps.get_week_id()
        message = str(request_payload.get("message") or "").strip()
        if not message:
            raise RuntimeError("message is required")
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            raise RuntimeError("LLM is not configured")

        deps.patch_task_record(
            task,
            {
                "message": f"Generating weekly review chat reply for {week_id}...",
                "week_id": week_id,
            },
        )

        answer = mgr.chat_about_thesis(
            user_message=message,
            history=request_payload.get("history") or [],
            synthesis_result=request_payload.get("synthesis_result"),
            week_id=week_id,
        )
        if deps.is_llm_failure_text(answer):
            raise RuntimeError(str(answer or "LLM Text"))

        payload_result = {
            "success": True,
            "week_id": week_id,
            "answer": str(answer or "").strip(),
            "runtime_meta": deps.get_runtime_meta(),
            "message": "Weekly review chat reply completed.",
        }
        deps.patch_task_record(task, payload_result)
        return payload_result

    return WeeklyReviewTaskRunners(generate=generate, synthesize=synthesize, chat=chat)


def build_weekly_review_task_submit_handlers(deps: WeeklyReviewTaskSubmitDeps) -> WeeklyReviewTaskSubmitHandlers:
    def _json_object_body_or_error():
        return load_json_object(
            invalid_json_message="Text JSON",
            invalid_type_message="Text JSON Text",
        )

    def generate() -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            error_message = error_response[0].get_json().get("error") or "Text JSON Text"
            return deps.task_error_response(
                400,
                task_type="weekly_review_generate",
                status="invalid_request",
                message=error_message,
            )
        payload = dict(data)
        payload["week_id"] = str(payload.get("week_id") or "").strip() or deps.get_week_id()
        try:
            created = deps.get_task_manager().submit_task("weekly_review_generate", payload)
        except ValueError as exc:
            return deps.task_error_response(
                400,
                task_type="weekly_review_generate",
                status="invalid_request",
                message=str(exc),
                error=str(exc),
            )
        except Exception as exc:
            deps.logger.exception("submit weekly_review_generate task failed")
            return deps.task_error_response(
                500,
                task_type="weekly_review_generate",
                status="failed",
                message="TextReviewGenerateTextFailed",
                error=str(exc),
            )
        return jsonify(deps.task_payload(created, fallback_message="TextReviewGenerateText"))

    def synthesize() -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            error_message = error_response[0].get_json().get("error") or "Text JSON Text"
            return deps.task_error_response(
                400,
                task_type="weekly_review_synthesize",
                status="invalid_request",
                message=error_message,
            )
        payload = dict(data)
        payload["week_id"] = str(payload.get("week_id") or "").strip() or deps.get_week_id()
        try:
            created = deps.get_task_manager().submit_task("weekly_review_synthesize", payload)
        except ValueError as exc:
            return deps.task_error_response(
                400,
                task_type="weekly_review_synthesize",
                status="invalid_request",
                message=str(exc),
                error=str(exc),
            )
        except Exception as exc:
            deps.logger.exception("submit weekly_review_synthesize task failed")
            return deps.task_error_response(
                500,
                task_type="weekly_review_synthesize",
                status="failed",
                message="TextReviewTextFailed",
                error=str(exc),
            )
        return jsonify(deps.task_payload(created, fallback_message="TextReviewText"))

    def chat() -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            error_message = error_response[0].get_json().get("error") or "Text JSON Text"
            return deps.task_error_response(
                400,
                task_type="weekly_review_chat",
                status="invalid_request",
                message=error_message,
            )
        msg = str(data.get("message") or "").strip()
        if not msg:
            return deps.task_error_response(
                400,
                task_type="weekly_review_chat",
                status="invalid_request",
                message="message is required",
            )
        payload = dict(data)
        payload["message"] = msg
        payload["week_id"] = str(payload.get("week_id") or "").strip() or deps.get_week_id()
        if "synthesis_result" not in payload and "synthesis" in payload:
            payload["synthesis_result"] = payload.get("synthesis")
        try:
            created = deps.get_task_manager().submit_task("weekly_review_chat", payload)
        except ValueError as exc:
            return deps.task_error_response(
                400,
                task_type="weekly_review_chat",
                status="invalid_request",
                message=str(exc),
                error=str(exc),
            )
        except Exception as exc:
            deps.logger.exception("submit weekly_review_chat task failed")
            return deps.task_error_response(
                500,
                task_type="weekly_review_chat",
                status="failed",
                message="TextFailed",
                error=str(exc),
            )
        return jsonify(deps.task_payload(created, fallback_message="Text"))

    return WeeklyReviewTaskSubmitHandlers(generate=generate, synthesize=synthesize, chat=chat)


def build_weekly_review_export_handlers(deps: WeeklyReviewExportDeps) -> WeeklyReviewExportHandlers:
    def _json_object_body_or_error():
        return load_json_object()

    def export_current_markdown():
        week_id = str(request.args.get("week_id") or "").strip() or deps.get_week_id()
        return deps.build_weekly_reviews_export_response([week_id], "weekly-review")

    def export_all_markdown():
        week_ids = deps.get_storage().get_weekly_review_history(limit=10_000)
        return deps.build_weekly_reviews_export_response(sorted(week_ids), "weekly-reviews")

    def export_recent_markdown():
        limit = deps.safe_int(request.args.get("limit"), 3)
        if limit is None or limit <= 0:
            limit = 3
        limit = min(limit, 12)
        week_ids = deps.get_storage().get_weekly_review_history(limit=limit)
        prefix = f"weekly-reviews-latest-{len(week_ids) or limit}"
        return deps.build_weekly_reviews_export_response(sorted(week_ids), prefix)

    def sync_weekly_review_to_ima(week_id: str) -> Response:
        resolved_week_id = str(week_id or "").strip() or deps.get_week_id()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        snapshot = deps.build_weekly_review_snapshot(resolved_week_id)
        result = deps.sync_snapshot_to_ima(
            deps.get_storage(),
            snapshot_type="weekly_reviews",
            sync_key=deps.ima_sync_key_weekly_review(resolved_week_id),
            local_file=snapshot["local_file"],
            title=snapshot["title"],
            force=deps.to_bool(data.get("force"), False),
        )
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "week_id": resolved_week_id,
                    "local_file": str(snapshot["local_file"]),
                    "ima_sync": result,
                }
            )
        )

    return WeeklyReviewExportHandlers(
        export_current_markdown=export_current_markdown,
        export_all_markdown=export_all_markdown,
        export_recent_markdown=export_recent_markdown,
        sync_weekly_review_to_ima=sync_weekly_review_to_ima,
    )


def build_weekly_review_api_handlers(deps: WeeklyReviewApiDeps) -> WeeklyReviewApiHandlers:
    def _json_object_body_or_error():
        return load_json_object()

    def get_market_context() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        week_id = (request.args.get("week_id") or "").strip() or None
        return jsonify(deps.json_safe(mgr.get_market_context(week_id=week_id)))

    def refresh_market_context() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or request.args.get("week_id") or "").strip() or None
        days = deps.safe_int(data.get("days"), deps.safe_int(request.args.get("days"), 7))
        return jsonify(deps.json_safe(mgr.refresh_market_context(week_id=week_id, days=days)))

    def refresh_macro_events() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or request.args.get("week_id") or "").strip() or None
        days = deps.safe_int(data.get("days"), deps.safe_int(request.args.get("days"), 7))
        result = mgr.refresh_macro_events(week_id=week_id, days=days)
        if result.get("error"):
            return jsonify(deps.json_safe(result)), 400
        return jsonify(deps.json_safe(result))

    def summarize_market_context() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or request.args.get("week_id") or "").strip() or None
        try:
            result = mgr.summarize_market_context(week_id=week_id)
        except Exception as exc:
            deps.logger.exception("summarize_market_context failed for week_id=%s", week_id)
            return jsonify({"error": f"Failed to summarize market context: {exc}"}), 500
        if result.get("error"):
            return jsonify(deps.json_safe(result)), 400
        return jsonify(deps.json_safe(result))

    def refresh_stock_news(stock_id: str) -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        days = deps.safe_int(data.get("days"), deps.safe_int(request.args.get("days"), 7))
        force_refresh = deps.to_bool(data.get("force_refresh"), deps.to_bool(request.args.get("force"), False))
        return jsonify(deps.json_safe(mgr.refresh_stock_news(stock_id, days=days, force_refresh=force_refresh)))

    def refresh_stock_performance(stock_id: str) -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        days = deps.safe_int(data.get("days"), deps.safe_int(request.args.get("days"), 7))
        week_id = str(data.get("week_id") or request.args.get("week_id") or "").strip() or None
        return jsonify(deps.json_safe(mgr.refresh_stock_performance(stock_id, days=days, week_id=week_id)))

    def refresh_all_news() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        return jsonify(
            deps.json_safe(
                {
                    "results": mgr.refresh_all_news(
                        days=deps.safe_int(data.get("days"), 7),
                        force_refresh=deps.to_bool(data.get("force_refresh"), False),
                    )
                }
            )
        )

    def refresh_all_news_and_scan() -> Response:
        return refresh_all_news()

    def refresh_portfolio_prices() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or request.args.get("week_id") or "").strip() or None
        days = deps.safe_int(data.get("days"), deps.safe_int(request.args.get("days"), 7))
        force_refresh = deps.to_bool(data.get("force_refresh"), deps.to_bool(request.args.get("force"), False))
        max_age_minutes = deps.safe_int(
            data.get("max_age_minutes"),
            deps.safe_int(request.args.get("max_age_minutes"), 15),
        )
        try:
            result = mgr.refresh_portfolio_prices(
                week_id=week_id,
                days=days,
                force_refresh=force_refresh,
                max_age_minutes=max_age_minutes,
            )
            if isinstance(result, dict):
                payload = {
                    "success": True,
                    "week_id": week_id,
                    "results": result.get("results") or [],
                    "summary": result.get("summary") or {},
                    "data_health": result.get("data_health") or {},
                    "prices_refreshed_at": result.get("prices_refreshed_at") or "",
                }
            else:
                payload = {"success": True, "results": result or [], "week_id": week_id}
            return jsonify(deps.json_safe(payload))
        except Exception as exc:
            deps.logger.exception("refresh_portfolio_prices failed for week_id=%s days=%s", week_id, days)
            return jsonify({"error": f"RefreshHoldingsTextFailed: {exc}"}), 500

    def refresh_all_performance() -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        days = deps.safe_int(data.get("days"), deps.safe_int(request.args.get("days"), 7))
        return jsonify(deps.json_safe({"results": mgr.refresh_all_performance(days=days)}))

    def generate_news_summary(stock_id: str) -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or request.args.get("week_id") or "").strip() or None
        return jsonify(deps.json_safe(mgr.generate_news_summary(stock_id, week_id=week_id)))

    def generate_weekly_stock_ai_summary(stock_id: str, week_id: str) -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        resolved_week_id = str(week_id or "").strip() or deps.get_week_id()
        playbook = deps.get_storage().get_stock_playbook(stock_id) or {}
        filings_entry = deps.get_stock_filings(
            stock_id=stock_id,
            stock_name=str(playbook.get("stock_name") or stock_id).strip(),
            ticker=str(playbook.get("ticker") or stock_id).strip(),
            week_id=resolved_week_id,
            force_refresh=False,
        )
        result = mgr.generate_stock_weekly_ai_summary(
            stock_id=stock_id,
            week_id=resolved_week_id,
            force=deps.to_bool(data.get("force"), False),
            filings_entry=filings_entry,
        )
        status = 200 if result.get("success") else 502
        review = deps.get_storage().get_weekly_review(resolved_week_id) or {}
        result = dict(result)
        result["stock_data"] = ((review.get("stocks") or {}).get(stock_id) or {})
        return jsonify(deps.json_safe(result)), status

    def generate_weekly_stock_ai_summaries_batch(week_id: str) -> Response:
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        resolved_week_id = str(week_id or "").strip() or deps.get_week_id()
        review = deps.get_storage().get_weekly_review(resolved_week_id) or {}
        stocks = (review.get("stocks") or {}) if isinstance(review, dict) else {}
        filings_map: Dict[str, Dict[str, Any]] = {}
        for stock_id, stock_data in stocks.items():
            playbook = deps.get_storage().get_stock_playbook(stock_id) or {}
            filings_map[stock_id] = deps.get_stock_filings(
                stock_id=stock_id,
                stock_name=str((stock_data or {}).get("stock_name") or playbook.get("stock_name") or stock_id).strip(),
                ticker=str(playbook.get("ticker") or stock_id).strip(),
                week_id=resolved_week_id,
                force_refresh=False,
            )
        result = mgr.generate_all_stock_weekly_ai_summaries(
            week_id=resolved_week_id,
            force=deps.to_bool(data.get("force"), False),
            filings_map=filings_map,
        )
        review = deps.get_storage().get_weekly_review(resolved_week_id) or {}
        result = dict(result)
        result["stocks"] = review.get("stocks") or {}
        return jsonify(deps.json_safe(result))

    def save_stock_weekly_view(stock_id: str) -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or deps.get_week_id())
        user_view = str(data.get("user_view") or "")
        try:
            storage = deps.get_storage()
            playbook = storage.get_stock_playbook(stock_id)
            stock_name = playbook.get("stock_name", stock_id) if playbook else stock_id
            storage.update_stock_weekly_data(
                week_id=week_id,
                stock_id=stock_id,
                stock_name=stock_name,
                user_view=user_view,
            )
            if user_view.strip():
                storage.log_interaction(
                    {
                        "type": "weekly_view",
                        "stock_id": stock_id,
                        "stock_name": stock_name,
                        "week_id": week_id,
                        "user_view": user_view,
                    }
                )
            return jsonify({"success": True, "stock_id": stock_id, "week_id": week_id})
        except Exception as exc:
            deps.logger.exception("save_stock_weekly_view failed for stock_id=%s week_id=%s", stock_id, week_id)
            return jsonify({"error": f"SaveThis WeekTextFailed: {exc}"}), 500

    def save_weekly_portfolio() -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or deps.get_week_id())
        total_portfolio_value = data["total_portfolio_value"] if "total_portfolio_value" in data else None
        if "total_portfolio_value" in data and total_portfolio_value is None:
            total_portfolio_value = ""
        cash_balance = data["cash_balance"] if "cash_balance" in data else None
        if "cash_balance" in data and cash_balance is None:
            cash_balance = ""
        usd_to_hkd = data["usd_to_hkd"] if "usd_to_hkd" in data else None
        if "usd_to_hkd" in data and usd_to_hkd is None:
            usd_to_hkd = ""
        cny_to_hkd = data["cny_to_hkd"] if "cny_to_hkd" in data else None
        if "cny_to_hkd" in data and cny_to_hkd is None:
            cny_to_hkd = ""
        eur_to_hkd = data["eur_to_hkd"] if "eur_to_hkd" in data else None
        if "eur_to_hkd" in data and eur_to_hkd is None:
            eur_to_hkd = ""
        jpy_to_hkd = data["jpy_to_hkd"] if "jpy_to_hkd" in data else None
        if "jpy_to_hkd" in data and jpy_to_hkd is None:
            jpy_to_hkd = ""
        krw_to_hkd = data["krw_to_hkd"] if "krw_to_hkd" in data else None
        if "krw_to_hkd" in data and krw_to_hkd is None:
            krw_to_hkd = ""
        deps.get_storage().update_weekly_portfolio(
            week_id=week_id,
            holdings=data.get("holdings") if "holdings" in data else None,
            stock_names=data.get("stock_names") if "stock_names" in data else None,
            total_portfolio_value=total_portfolio_value,
            cash_balance=cash_balance,
            usd_to_hkd=usd_to_hkd,
            cny_to_hkd=cny_to_hkd,
            eur_to_hkd=eur_to_hkd,
            jpy_to_hkd=jpy_to_hkd,
            krw_to_hkd=krw_to_hkd,
            buy_dates=data.get("buy_dates") if "buy_dates" in data else None,
            closed_positions=data.get("closed_positions") if "closed_positions" in data else None,
            avg_costs=data.get("avg_costs") if "avg_costs" in data else None,
        )
        return jsonify({"success": True, "week_id": week_id})

    def save_rebalancing_ops() -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or deps.get_week_id())
        ops = data.get("ops") or []
        if not isinstance(ops, list):
            return jsonify({"error": "ops must be a list"}), 400
        mismatch_error = _rebalancing_week_mismatch_error(week_id, ops)
        if mismatch_error is not None:
            return mismatch_error
        if ops:
            return jsonify({"error": "non-empty rebalancing ops must be applied via /api/weekly-review/rebalancing/apply to keep holdings and sell review in sync"}), 409
        deps.get_storage().save_rebalancing_ops(week_id, ops)
        return jsonify({"success": True, "week_id": week_id, "ops_count": len(ops)})

    def apply_rebalancing() -> Response:
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or deps.get_week_id())
        ops = data.get("ops_override")
        if ops is None:
            ops = data.get("ops")
        base_holdings = data.get("base_holdings_override")
        if base_holdings is None:
            base_holdings = data.get("base_holdings")
        if ops is not None and not isinstance(ops, list):
            return jsonify({"error": "ops must be a list"}), 400
        mismatch_error = _rebalancing_week_mismatch_error(week_id, ops)
        if mismatch_error is not None:
            return mismatch_error
        result = deps.get_storage().apply_rebalancing_ops(
            week_id=week_id,
            stock_names=data.get("stock_names"),
            ops_override=ops,
            base_holdings_override=base_holdings,
            code_to_storage_key=data.get("code_to_storage_key"),
            display_codes=data.get("display_codes"),
            dry_run=deps.to_bool(data.get("dry_run"), False),
        )
        payload = {
            "success": True,
            "week_id": week_id,
            "result": result,
            "holdings": (result or {}).get("holdings") or {},
            "closed_positions": (result or {}).get("closed_positions") or [],
            "avg_costs": (result or {}).get("avg_costs") or {},
            "buy_dates": (result or {}).get("buy_dates") or {},
            "preview_summary": (result or {}).get("preview_summary") or {},
        }
        return jsonify(deps.json_safe(payload))

    def get_portfolio_performance() -> Response:
        week_id = str(request.args.get("week_id") or deps.get_week_id()).strip()
        benchmark = str(request.args.get("benchmark") or "QQQ").strip().upper() or "QQQ"
        mode = str(request.args.get("mode") or "ytd").strip().lower() or "ytd"
        lookback_weeks = deps.safe_int(request.args.get("lookback_weeks"), 16)
        lookback_weeks = max(4, min(104, lookback_weeks))
        try:
            payload = deps.build_portfolio_performance(
                week_id=week_id,
                benchmark=benchmark,
                lookback_weeks=lookback_weeks,
                mode=mode,
            )
            return jsonify(deps.json_safe(payload))
        except Exception as exc:
            deps.logger.exception("portfolio performance failed for week_id=%s benchmark=%s", week_id, benchmark)
            return jsonify({"success": False, "error": f"TextReturnTextFailed: {exc}"}), 500

    return WeeklyReviewApiHandlers(
        get_market_context=get_market_context,
        refresh_market_context=refresh_market_context,
        refresh_macro_events=refresh_macro_events,
        summarize_market_context=summarize_market_context,
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
    )
