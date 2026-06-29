from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, request

from web.request_parsing import load_json_object
from web.services.domain_services import StocksService
from web.services.tradingview_symbols import resolve_tradingview_symbol


@dataclass
class StocksModuleDeps:
    get_storage: Callable[[], Any]
    render_template: Callable[..., Any]
    now_iso: Callable[[], str]
    get_stocks_with_research_status: Callable[[], list[dict[str, Any]]]
    fetch_price_chart: Callable[..., dict[str, Any]]
    json_safe: Callable[[Any], Any]
    get_stock_commentary: Callable[..., dict[str, Any]]
    get_keyword_pool: Callable[[Any], dict[str, Any]]
    save_keyword_entry: Callable[..., dict[str, Any]]
    normalize_keyword_list: Callable[[Any], list[str]]
    get_week_id: Callable[[], str]
    safe_int: Callable[[Any, int], int]


def build_stocks_module_service(deps: StocksModuleDeps) -> StocksService:
    def _json_object_body_or_error():
        return load_json_object()

    def stock_detail_page(stock_id: str):
        storage = deps.get_storage()
        playbook = storage.get_stock_playbook(stock_id) or {}
        history = storage.get_research_history(stock_id) if hasattr(storage, "get_research_history") else []
        ticker = str(playbook.get("ticker") or stock_id).strip()
        return deps.render_template(
            "stock_detail.html",
            playbook=playbook,
            stock_id=stock_id,
            history=history,
            tradingview_symbol=resolve_tradingview_symbol(ticker),
        )

    def stocks_page():
        return deps.render_template("stocks.html", stocks=deps.get_stocks_with_research_status())

    def get_stock(stock_id: str):
        return jsonify(deps.get_storage().get_stock_playbook(stock_id) or {})

    def save_stock(stock_id: str):
        storage = deps.get_storage()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        data["stock_id"] = stock_id
        data["updated_at"] = deps.now_iso()
        if not data.get("created_at"):
            existing = storage.get_stock_playbook(stock_id) or {}
            data["created_at"] = existing.get("created_at") or data["updated_at"]
        storage.save_stock_playbook(stock_id, data)
        return jsonify({"success": True})

    def update_stock_ticker(stock_id: str):
        storage = deps.get_storage()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        ticker = str(data.get("ticker") or "").strip()
        playbook = storage.get_stock_playbook(stock_id)
        if not playbook:
            return jsonify({"error": "Stock not found"}), 404
        playbook["ticker"] = ticker
        playbook["updated_at"] = deps.now_iso()
        storage.save_stock_playbook(stock_id, playbook)
        return jsonify({"success": True, "ticker": ticker})

    def delete_stock(stock_id: str):
        return jsonify({"success": deps.get_storage().delete_stock(stock_id)})

    def generic_price_chart():
        resolved_range = str(request.args.get("range") or "365d").strip().lower() or "365d"
        subject = {
            "stock_id": str(request.args.get("stock_id") or request.args.get("ticker") or "").strip(),
            "stock_name": str(request.args.get("stock_name") or "").strip(),
            "ticker": str(request.args.get("ticker") or request.args.get("stock_id") or "").strip(),
        }
        if not subject["ticker"]:
            return jsonify({"success": False, "error": "TextMarket DataTicker"}), 400
        payload = deps.fetch_price_chart(subject, range_name=resolved_range)
        status = 200 if payload.get("success") else 502
        return jsonify(deps.json_safe(payload)), status

    def stock_commentary():
        storage = deps.get_storage()
        context = str(request.args.get("context") or "watchlist").strip().lower()
        week_id = str(request.args.get("week_id") or "").strip() or None

        if context == "watchlist":
            watchlist = storage.get_watchlist()
            records = [
                {
                    "stock_id": item.get("stock_id"),
                    "stock_name": item.get("stock_name"),
                    "ticker": item.get("ticker"),
                }
                for item in watchlist.get("candidates", [])
                if item.get("stock_id")
            ]
            payload = deps.get_stock_commentary(storage, records, rolling_days=7)
            return jsonify(deps.json_safe(payload))

        if context == "weekly_review":
            records = [
                {
                    "stock_id": item.get("stock_id"),
                    "stock_name": item.get("stock_name"),
                    "ticker": item.get("ticker"),
                }
                for item in storage.list_stocks()
                if item.get("stock_id")
            ]
            payload = deps.get_stock_commentary(storage, records, week_id=week_id or deps.get_week_id())
            return jsonify(deps.json_safe(payload))

        return jsonify({"success": False, "error": "Unsupported commentary context"}), 400

    def stock_commentary_registry():
        return jsonify(deps.json_safe(deps.get_keyword_pool(deps.get_storage())))

    def save_stock_commentary_registry(stock_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        try:
            entry = deps.save_keyword_entry(
                deps.get_storage(),
                stock_id=stock_id,
                stock_name=str(data.get("stock_name") or "").strip(),
                ticker=str(data.get("ticker") or "").strip(),
                keywords=deps.normalize_keyword_list(data.get("keywords")),
            )
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify({"success": True, "entry": deps.json_safe(entry)})

    def playbook_page():
        return deps.render_template("playbook.html", stocks=deps.get_storage().list_stocks())

    def get_playbook(stock_id: str):
        pb = deps.get_storage().get_stock_playbook(stock_id)
        return jsonify(pb or {})

    def save_playbook(stock_id: str):
        storage = deps.get_storage()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        existing = storage.get_stock_playbook(stock_id) or {}
        existing["investment_framework"] = data.get("investment_framework", {})
        storage.save_stock_playbook(stock_id, existing)
        return jsonify({"success": True})

    return StocksService(
        stock_detail_page=stock_detail_page,
        stocks_page=stocks_page,
        get_stock=get_stock,
        save_stock=save_stock,
        update_stock_ticker=update_stock_ticker,
        delete_stock=delete_stock,
        generic_price_chart=generic_price_chart,
        stock_commentary=stock_commentary,
        stock_commentary_registry=stock_commentary_registry,
        save_stock_commentary_registry=save_stock_commentary_registry,
        playbook_page=playbook_page,
        get_playbook=get_playbook,
        save_playbook=save_playbook,
    )
