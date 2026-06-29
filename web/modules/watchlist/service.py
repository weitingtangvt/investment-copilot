from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, render_template, request

from web.request_parsing import load_json_object


@dataclass
class WatchlistModuleService:
    watchlist_page: Callable[[], Any]
    get_watchlist: Callable[[], Any]
    add_watch_candidate: Callable[[], Any]
    update_watch_candidate: Callable[[str], Any]
    delete_watch_candidate: Callable[[str], Any]
    ack_watch_candidate_revisit: Callable[[str], Any]
    save_watch_candidate_weekly_note: Callable[[str], Any]
    watch_candidate_zsxq_matches: Callable[[str], Any]
    watch_candidate_filings: Callable[[str], Any]
    refresh_watch_candidate_filings: Callable[[str], Any]
    refresh_watch_candidate_performance: Callable[[str], Any]
    watch_candidate_price_chart: Callable[[str], Any]
    refresh_watchlist_all: Callable[[], Any]
    watch_candidate_ai_judgment: Callable[[str], Any]
    watchlist_ai_judgment_batch: Callable[[], Any]
    sync_watchlist_to_ima: Callable[[], Any]


@dataclass
class WatchlistModuleDeps:
    get_week_id: Callable[[], str]
    recent_week_ids: Callable[[], Any]
    get_storage: Callable[[], Any]
    serialize_ima_sync_status: Callable[[Any], Any]
    ima_sync_key_watchlist: Callable[[str], str]
    json_safe: Callable[[Any], Any]
    refresh_watch_candidate_perf: Callable[[Any], Any]
    fetch_price_chart: Callable[[Any, str], Any]
    to_bool: Callable[[Any, bool], bool]
    get_stock_commentary: Callable[..., Any]
    get_stock_filings: Callable[..., Any]
    generate_watch_candidate_ai_judgment: Callable[..., Any]
    build_watchlist_snapshot: Callable[[str], Any]
    sync_snapshot_to_ima: Callable[..., Any]
    logger: Any


def build_watchlist_module_service(
    deps: WatchlistModuleDeps,
) -> WatchlistModuleService:
    def _json_object_body_or_error():
        return load_json_object()

    def _find_candidate(candidate_id: str):
        resolved_id = str(candidate_id or "").strip().upper()
        watchlist = deps.get_storage().get_watchlist()
        return next((item for item in watchlist.get("candidates", []) if item.get("stock_id") == resolved_id), None)

    def _empty_zsxq_payload(candidate: dict[str, Any], window: Any):
        return {
            "success": True,
            "stock_id": candidate.get("stock_id") or "",
            "stock_name": candidate.get("stock_name") or "",
            "ticker": candidate.get("ticker") or "",
            "keywords_used": [],
            "match_count": 0,
            "header_match_count": 0,
            "body_match_count": 0,
            "source_group_count": 0,
            "items": [],
            "window": window or {},
        }

    def _empty_filings_payload(candidate: dict[str, Any], window: Any, market: str):
        return {
            "success": True,
            "stock_id": candidate.get("stock_id") or "",
            "stock_name": candidate.get("stock_name") or "",
            "ticker": candidate.get("ticker") or "",
            "market": market,
            "window": window or {},
            "counts": {"total": 0, "high_importance": 0},
            "items": [],
            "cache": {"hit": False, "updated_at": ""},
        }

    def watchlist_page():
        week_id = deps.get_week_id()
        return render_template("watchlist.html", week_id=week_id, week_options=deps.recent_week_ids())

    def get_watchlist():
        week_id = str(request.args.get("week_id") or "").strip() or deps.get_week_id()
        payload = dict(deps.get_storage().get_watchlist())
        payload["ima_sync_status"] = deps.serialize_ima_sync_status(
            deps.get_storage().get_ima_sync_record(deps.ima_sync_key_watchlist(week_id))
        )
        return jsonify(deps.json_safe(payload))

    def add_watch_candidate():
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        candidate = deps.get_storage().upsert_watch_candidate(data)
        return jsonify({"success": True, "candidate": candidate})

    def update_watch_candidate(candidate_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        candidate = deps.get_storage().update_watch_candidate(candidate_id, data)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404
        return jsonify({"success": True, "candidate": candidate})

    def delete_watch_candidate(candidate_id: str):
        success = deps.get_storage().delete_watch_candidate(candidate_id)
        return jsonify({"success": success})

    def ack_watch_candidate_revisit(candidate_id: str):
        candidate = deps.get_storage().acknowledge_watch_candidate_revisit(candidate_id)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404
        return jsonify({"success": True, "candidate": candidate})

    def save_watch_candidate_weekly_note(candidate_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or "").strip() or deps.get_week_id()
        content = str(data.get("content") or "")
        candidate = deps.get_storage().save_watch_candidate_weekly_note(candidate_id, week_id, content)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404
        return jsonify({"success": True, "candidate": candidate, "week_id": week_id})

    def watch_candidate_zsxq_matches(candidate_id: str):
        candidate = _find_candidate(candidate_id)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404

        payload = deps.get_stock_commentary(
            deps.get_storage(),
            [
                {
                    "stock_id": candidate.get("stock_id"),
                    "stock_name": candidate.get("stock_name"),
                    "ticker": candidate.get("ticker"),
                }
            ],
            rolling_days=7,
        ) or {}
        entry = ((payload.get("stocks") or {}).get(candidate.get("stock_id"))) or {}
        result = {
            **_empty_zsxq_payload(candidate, payload.get("window")),
            **entry,
            "success": True,
            "stock_id": candidate.get("stock_id") or "",
            "stock_name": entry.get("stock_name") or candidate.get("stock_name") or "",
            "ticker": entry.get("ticker") or candidate.get("ticker") or "",
            "keywords_used": list(entry.get("keywords_used") or []),
            "match_count": int(entry.get("match_count") or len(entry.get("items") or []) or 0),
            "header_match_count": int(entry.get("header_match_count") or 0),
            "body_match_count": int(entry.get("body_match_count") or 0),
            "source_group_count": int(entry.get("source_group_count") or 0),
            "items": list(entry.get("items") or []),
            "window": payload.get("window") or {},
        }
        return jsonify(deps.json_safe(result))

    def watch_candidate_filings(candidate_id: str):
        candidate = _find_candidate(candidate_id)
        if not candidate:
            return jsonify({"success": False, "error": "candidate not found"}), 404
        payload = deps.get_stock_filings(
            stock_id=str(candidate.get("stock_id") or "").strip(),
            stock_name=str(candidate.get("stock_name") or "").strip(),
            ticker=str(candidate.get("ticker") or "").strip(),
            rolling_days=7,
            force_refresh=False,
        ) or {}
        result = {
            **_empty_filings_payload(candidate, payload.get("window"), payload.get("market") or "UNKNOWN"),
            **payload,
            "success": True,
        }
        return jsonify(deps.json_safe(result))

    def refresh_watch_candidate_filings(candidate_id: str):
        candidate = _find_candidate(candidate_id)
        if not candidate:
            return jsonify({"success": False, "error": "candidate not found"}), 404
        payload = deps.get_stock_filings(
            stock_id=str(candidate.get("stock_id") or "").strip(),
            stock_name=str(candidate.get("stock_name") or "").strip(),
            ticker=str(candidate.get("ticker") or "").strip(),
            rolling_days=7,
            force_refresh=True,
        ) or {}
        result = {
            **_empty_filings_payload(candidate, payload.get("window"), payload.get("market") or "UNKNOWN"),
            **payload,
            "success": True,
        }
        return jsonify(deps.json_safe(result))

    def refresh_watch_candidate_performance(candidate_id: str):
        candidate = _find_candidate(candidate_id)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404
        result = deps.refresh_watch_candidate_perf(candidate)
        status = 200 if result.get("success") else 500
        return jsonify(result), status

    def refresh_watchlist_all():
        watchlist = deps.get_storage().get_watchlist()
        results = []
        for candidate in watchlist.get("candidates", []):
            try:
                results.append(deps.refresh_watch_candidate_perf(candidate))
            except Exception as exc:
                deps.logger.exception("refresh watchlist candidate failed for %s", candidate.get("stock_id"))
                results.append(
                    {
                        "success": False,
                        "stock_id": candidate.get("stock_id"),
                        "error": str(exc),
                    }
                )
        return jsonify({"success": True, "results": results})

    def watch_candidate_price_chart(candidate_id: str):
        resolved_id = str(candidate_id or "").strip().upper()
        resolved_range = str(request.args.get("range") or "1y").strip().lower() or "1y"
        candidate = _find_candidate(resolved_id)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404
        payload = deps.fetch_price_chart(candidate, resolved_range)
        status = 200 if payload.get("success") else 502
        return jsonify(deps.json_safe(payload)), status

    def watch_candidate_ai_judgment(candidate_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        force = deps.to_bool(data.get("force"), False)
        candidate = _find_candidate(candidate_id)
        if not candidate:
            return jsonify({"success": False, "error": "TextStockText"}), 404

        commentary_payload = deps.get_stock_commentary(
            deps.get_storage(),
            [
                {
                    "stock_id": candidate.get("stock_id"),
                    "stock_name": candidate.get("stock_name"),
                    "ticker": candidate.get("ticker"),
                }
            ],
            rolling_days=7,
        )
        commentary_entry = ((commentary_payload or {}).get("stocks") or {}).get(candidate.get("stock_id")) or {}
        filings_entry = deps.get_stock_filings(
            stock_id=str(candidate.get("stock_id") or "").strip(),
            stock_name=str(candidate.get("stock_name") or "").strip(),
            ticker=str(candidate.get("ticker") or "").strip(),
            rolling_days=7,
            force_refresh=False,
        )
        result = deps.generate_watch_candidate_ai_judgment(
            candidate,
            commentary_entry=commentary_entry,
            filings_entry=filings_entry,
            force=force,
        )
        status = 200 if result.get("success") else 502
        return jsonify(deps.json_safe(result)), status

    def watchlist_ai_judgment_batch():
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        force = deps.to_bool(data.get("force"), False)
        watchlist = deps.get_storage().get_watchlist()
        candidates = list(watchlist.get("candidates") or [])
        records = [
            {
                "stock_id": item.get("stock_id"),
                "stock_name": item.get("stock_name"),
                "ticker": item.get("ticker"),
            }
            for item in candidates
            if item.get("stock_id")
        ]
        commentary_payload = (
            deps.get_stock_commentary(deps.get_storage(), records, rolling_days=7) if records else {"stocks": {}}
        )
        commentary_map = commentary_payload.get("stocks") or {}

        results = []
        for candidate in candidates:
            filings_entry = deps.get_stock_filings(
                stock_id=str(candidate.get("stock_id") or "").strip(),
                stock_name=str(candidate.get("stock_name") or "").strip(),
                ticker=str(candidate.get("ticker") or "").strip(),
                rolling_days=7,
                force_refresh=False,
            )
            results.append(
                deps.generate_watch_candidate_ai_judgment(
                    candidate,
                    commentary_entry=commentary_map.get(candidate.get("stock_id")) or {},
                    filings_entry=filings_entry,
                    force=force,
                )
            )

        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "results": results,
                    "generated": sum(1 for item in results if item.get("success") and not item.get("skipped")),
                    "skipped": sum(1 for item in results if item.get("success") and item.get("skipped")),
                    "failed": sum(1 for item in results if not item.get("success")),
                }
            )
        )

    def sync_watchlist_to_ima():
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        week_id = str(data.get("week_id") or "").strip() or deps.get_week_id()
        snapshot = deps.build_watchlist_snapshot(week_id)
        result = deps.sync_snapshot_to_ima(
            deps.get_storage(),
            snapshot_type="watchlist_snapshots",
            sync_key=deps.ima_sync_key_watchlist(week_id),
            local_file=snapshot["local_file"],
            title=snapshot["title"],
            force=deps.to_bool(data.get("force"), False),
        )
        return jsonify(
            deps.json_safe(
                {
                    "success": True,
                    "week_id": week_id,
                    "local_file": str(snapshot["local_file"]),
                    "ima_sync": result,
                }
            )
        )

    return WatchlistModuleService(
        watchlist_page=watchlist_page,
        get_watchlist=get_watchlist,
        add_watch_candidate=add_watch_candidate,
        update_watch_candidate=update_watch_candidate,
        delete_watch_candidate=delete_watch_candidate,
        ack_watch_candidate_revisit=ack_watch_candidate_revisit,
        save_watch_candidate_weekly_note=save_watch_candidate_weekly_note,
        watch_candidate_zsxq_matches=watch_candidate_zsxq_matches,
        watch_candidate_filings=watch_candidate_filings,
        refresh_watch_candidate_filings=refresh_watch_candidate_filings,
        refresh_watch_candidate_performance=refresh_watch_candidate_performance,
        watch_candidate_price_chart=watch_candidate_price_chart,
        refresh_watchlist_all=refresh_watchlist_all,
        watch_candidate_ai_judgment=watch_candidate_ai_judgment,
        watchlist_ai_judgment_batch=watchlist_ai_judgment_batch,
        sync_watchlist_to_ima=sync_watchlist_to_ima,
    )
