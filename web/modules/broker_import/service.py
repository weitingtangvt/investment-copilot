from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from flask import jsonify, request

from core.ibkr_derived_portfolio import (
    IBKR_DELTA_BASELINE_WEEK_ID,
    build_ibkr_derived_review_projection,
    build_ibkr_w25_baseline_delta_projection,
    canonical_ibkr_trade_ticker,
)
from core.ibkr_trade_import import import_ibkr_csv_text
from core.portfolio_performance import build_portfolio_ticker_aliases
from web.services.domain_services import BrokerImportService


@dataclass
class BrokerImportModuleDeps:
    get_storage: Callable[[], Any]
    load_price_frames: Callable[..., dict[str, Any]] | None = None
    external_cash_flows_hkd: list[dict[str, Any]] | None = None


def _decode_upload(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def build_broker_import_module_service(deps: BrokerImportModuleDeps) -> BrokerImportService:
    def _week_end_date(week_id: Any) -> date | None:
        text = str(week_id or "").strip().upper()
        if "-W" not in text:
            return None
        try:
            year_text, week_text = text.split("-W", 1)
            return datetime.fromisocalendar(int(year_text), int(week_text), 5).date()
        except (TypeError, ValueError):
            return None

    def _weekly_review_aliases(storage: Any) -> dict[str, str]:
        try:
            data = storage._load_weekly_reviews_file() if hasattr(storage, "_load_weekly_reviews_file") else None
        except Exception:
            data = None
        weeks = (data or {}).get("weeks") if isinstance(data, dict) else {}
        reviews = [review for review in (weeks or {}).values() if isinstance(review, dict)]
        aliases = build_portfolio_ticker_aliases(reviews)
        for key, value in list(aliases.items()):
            if key.endswith(".HK"):
                aliases.setdefault(key[:-3], value)
        return aliases

    def _weekly_reviews_for_year(storage: Any, year: int | None) -> tuple[list[dict[str, Any]], int | None]:
        try:
            data = storage._load_weekly_reviews_file() if hasattr(storage, "_load_weekly_reviews_file") else None
        except Exception:
            data = None
        weeks = (data or {}).get("weeks") if isinstance(data, dict) else {}
        rows = []
        for week_id, review in (weeks or {}).items():
            if not isinstance(review, dict):
                continue
            end_day = _week_end_date(review.get("week_id") or week_id)
            if end_day is None:
                continue
            row = dict(review)
            row.setdefault("week_id", str(week_id))
            rows.append((end_day, row))
        if not rows:
            return [], year
        selected_year = int(year or max(day.year for day, _ in rows))
        selected = [row for day, row in sorted(rows, key=lambda item: item[0]) if day.year == selected_year]
        return selected, selected_year

    def _is_after_delta_baseline(week_id: Any) -> bool:
        target = _week_end_date(week_id)
        baseline = _week_end_date(IBKR_DELTA_BASELINE_WEEK_ID)
        return bool(target is not None and baseline is not None and target > baseline)

    def _get_delta_baseline(storage: Any) -> dict[str, Any]:
        if hasattr(storage, "get_ibkr_portfolio_baseline"):
            existing = storage.get_ibkr_portfolio_baseline(IBKR_DELTA_BASELINE_WEEK_ID)
            if existing.get("positions"):
                return existing
        return {}

    def _ticker_set_from_reviews_and_ledger(reviews: list[dict[str, Any]], ledger: dict[str, Any]) -> list[str]:
        tickers: set[str] = set()
        for review in reviews:
            for stock_id, payload in ((review.get("stocks") or {}) if isinstance(review, dict) else {}).items():
                if not isinstance(payload, dict):
                    continue
                ticker = str(payload.get("ticker") or stock_id or "").strip().upper()
                if ticker:
                    tickers.add(ticker)
        for trade in ledger.get("trades") or []:
            ticker = canonical_ibkr_trade_ticker(
                trade.get("ticker") or trade.get("stock_id") or trade.get("symbol"),
                trade.get("currency"),
            )
            if ticker:
                tickers.add(ticker)
        return sorted(tickers)

    def get_ibkr_trades():
        ledger = deps.get_storage().get_broker_trade_ledger(limit=100)
        return jsonify({"success": True, "ledger": ledger})

    def get_ibkr_derived_portfolio():
        raw_year = str(request.args.get("year") or "").strip()
        try:
            requested_year = int(raw_year) if raw_year else None
        except ValueError:
            return jsonify({"success": False, "error": "invalid_year", "weeks": []}), 400

        storage = deps.get_storage()
        reviews, selected_year = _weekly_reviews_for_year(storage, requested_year)
        if not reviews:
            return jsonify({"success": False, "error": "no_weekly_reviews", "weeks": []})

        ledger = storage.get_broker_trade_ledger(limit=None)
        tickers = _ticker_set_from_reviews_and_ledger(reviews, ledger)
        price_frames: dict[str, Any] = {}
        if deps.load_price_frames and tickers:
            start_date = date(int(selected_year or datetime.now().year), 1, 1) - timedelta(days=10)
            end_date = datetime.now().date()
            price_frames = deps.load_price_frames(tickers, start_date=start_date, end_date=end_date) or {}

        payload = {}
        requires_delta_baseline = any(_is_after_delta_baseline(review.get("week_id")) for review in reviews)
        if requires_delta_baseline:
            baseline = _get_delta_baseline(storage)
            if not baseline.get("positions"):
                return jsonify(
                    {
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
                )
            payload = build_ibkr_w25_baseline_delta_projection(
                reviews,
                ledger,
                baseline=baseline,
                price_frames=price_frames,
                external_cash_flows_hkd=deps.external_cash_flows_hkd or [],
            )
        else:
            payload = build_ibkr_derived_review_projection(
                reviews,
                ledger,
                price_frames=price_frames,
                external_cash_flows_hkd=deps.external_cash_flows_hkd or [],
            )
        payload.setdefault("year", selected_year)
        payload.setdefault("data_source", f"{payload.get('canonical_source') or 'ibkr_ledger'}+local_ohlcv_cache")
        return jsonify(payload)

    def import_ibkr_trades():
        uploads = list(request.files.getlist("files"))
        if not uploads and "file" in request.files:
            uploads = [request.files["file"]]
        if not uploads:
            return jsonify({"success": False, "error": "TextUpload IBKR CSV Text"}), 400
        if len(uploads) > 1:
            return jsonify(
                {
                    "success": False,
                    "error": "single_snapshot_file_required",
                    "message": "IBKR YTD snapshot import expects exactly one latest CSV file.",
                }
            ), 400

        totals = {
            "recognized_count": 0,
            "inserted_count": 0,
            "duplicate_count": 0,
            "error_count": 0,
        }
        imports = []
        errors = []
        storage = deps.get_storage()
        ticker_aliases = _weekly_review_aliases(storage)
        for upload in uploads:
            filename = str(upload.filename or "ibkr.csv").strip()
            raw = upload.read()
            if not raw:
                errors.append({"file": filename, "error": "Text"})
                continue
            result = import_ibkr_csv_text(
                storage,
                _decode_upload(raw),
                source_filename=filename,
                ticker_aliases=ticker_aliases,
            )
            imports.append(result.get("import"))
            for key in totals:
                totals[key] += int(result.get(key) or 0)
            for error in result.get("errors") or []:
                item = dict(error)
                item.setdefault("file", filename)
                errors.append(item)

        totals["error_count"] += len([item for item in errors if item.get("error") == "Text"])
        warnings = []
        if totals["recognized_count"] == 0 and not errors:
            warnings.append("CSV TextUpload, Text. TextConfirmText TradeDate/TradePrice Text IBKR Trades Text. ")
        ledger = storage.get_broker_trade_ledger(limit=100)
        return jsonify(
            {
                "success": True,
                "summary": totals,
                "imports": [item for item in imports if item],
                "errors": errors[:25],
                "warnings": warnings,
                "ledger": ledger,
            }
        )

    return BrokerImportService(
        get_ibkr_trades=get_ibkr_trades,
        import_ibkr_trades=import_ibkr_trades,
        get_ibkr_derived_portfolio=get_ibkr_derived_portfolio,
    )
