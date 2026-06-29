from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, request


@dataclass
class DecisionReviewService:
    index: Callable[[], Any]


@dataclass
class DecisionReviewModuleDeps:
    get_weekly_review_manager: Callable[[], Any]
    safe_int: Callable[[Any, int], int]
    json_safe: Callable[[Any], Any]
    logger: Any
    review_projection_provider: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None


class _ProjectedStorage:
    def __init__(self, storage: Any, provider: Callable[[str, dict[str, Any]], dict[str, Any] | None]) -> None:
        self._storage = storage
        self._provider = provider
        self._cache: dict[str, Any] = {}
        self._stock_list_cache: list[dict[str, Any]] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._storage, name)

    def get_weekly_review(self, week_id: str) -> Any:
        cache_key = str(week_id)
        if cache_key in self._cache:
            return self._cache[cache_key]
        review = self._storage.get_weekly_review(week_id)
        if not isinstance(review, dict):
            return review
        projected = self._provider(str(week_id), review)
        resolved = projected if isinstance(projected, dict) and projected.get("stocks") else review
        self._cache[cache_key] = resolved
        return resolved

    def get_weekly_review_with_portfolio_state(self, week_id: str, *args: Any, **kwargs: Any) -> Any:
        return self.get_weekly_review(week_id)

    def list_stocks(self) -> list[dict[str, Any]]:
        if self._stock_list_cache is None:
            self._stock_list_cache = list(self._storage.list_stocks() or [])
        return [dict(item) if isinstance(item, dict) else item for item in self._stock_list_cache]


def build_decision_review_module_service(deps: DecisionReviewModuleDeps) -> DecisionReviewService:
    def index():
        mgr = deps.get_weekly_review_manager()
        if mgr is None:
            return jsonify({"error": "LLM is not configured"}), 400
        limit = deps.safe_int(request.args.get("limit"), 10)
        stock_id = (request.args.get("stock_id") or "").strip() or None
        decision_type = (request.args.get("decision_type") or "").strip() or None
        window_mode = (request.args.get("window_mode") or "").strip() or None
        mark_horizon = (request.args.get("mark_horizon") or "").strip() or None
        original_storage = None
        try:
            if deps.review_projection_provider and hasattr(mgr, "storage"):
                original_storage = mgr.storage
                mgr.storage = _ProjectedStorage(original_storage, deps.review_projection_provider)
            payload = mgr.build_decision_review_index(
                limit=limit,
                stock_id=stock_id,
                decision_type=decision_type,
                window_mode=window_mode,
                mark_horizon=mark_horizon,
            )
        except Exception as exc:
            deps.logger.exception("Failed to build decision review index")
            return jsonify(
                deps.json_safe(
                    {
                        "error": str(exc),
                        "summary": {"event_count": 0},
                        "top_mistakes": [],
                        "by_stock": [],
                        "by_decision_type": [],
                        "by_redeployment_path": [],
                        "trim_follow_through": [],
                    }
                )
            ), 500
        finally:
            if original_storage is not None:
                mgr.storage = original_storage
        return jsonify(deps.json_safe(payload))

    return DecisionReviewService(index=index)
