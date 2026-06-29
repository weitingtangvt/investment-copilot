from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class AppDataDeps:
    get_storage: Callable[[], Any]
    get_data_source_registry: Callable[[], Any]


@dataclass
class AppDataHelpers:
    build_news_source_diagnostics: Callable[[], List[Dict[str, Any]]]
    get_stocks_with_research_status: Callable[[], List[Dict[str, Any]]]


def build_app_data_helpers(deps: AppDataDeps) -> AppDataHelpers:
    def build_news_source_diagnostics() -> List[Dict[str, Any]]:
        storage = deps.get_storage()
        data_source_registry = deps.get_data_source_registry()
        strategy = storage.get_news_aggregation_strategy()
        use_ai_search = storage.get_news_use_ai_search()
        newsapi_key = bool(storage.get_newsapi_api_key())
        tavily_key = bool(storage.get_tavily_api_key())

        rows: List[Dict[str, Any]] = []
        source_specs = [
            {
                "source_name": "rss",
                "label": "Google RSS",
                "requires_key": False,
                "configured": True,
                "role": "external fallback",
            },
            {
                "source_name": "newsapi",
                "label": "News API",
                "requires_key": True,
                "configured": newsapi_key,
                "role": "aggregated source",
            },
            {
                "source_name": "tavily",
                "label": "Tavily",
                "requires_key": True,
                "configured": tavily_key,
                "role": "aggregated source",
            },
        ]

        for spec in source_specs:
            registered = bool(data_source_registry and data_source_registry.get(spec["source_name"]))
            configured = bool(spec["configured"])
            if not registered:
                status = "unregistered"
                warning = "source is not registered in runtime registry"
            elif spec["requires_key"] and not configured:
                status = "missing_config"
                warning = "missing API key"
            else:
                status = "ready"
                warning = ""

            rows.append(
                {
                    "source_name": spec["source_name"],
                    "label": spec["label"],
                    "status": status,
                    "registered": registered,
                    "configured": configured,
                    "requires_api_key": spec["requires_key"],
                    "role": spec["role"],
                    "warning": warning,
                    "strategy": strategy,
                    "use_ai_search": use_ai_search,
                }
            )
        return rows

    def get_stocks_with_research_status() -> List[Dict[str, Any]]:
        storage = deps.get_storage()
        items: List[Dict[str, Any]] = []
        for stock in storage.list_stocks():
            stock_id = str(stock.get("stock_id") or "").strip()
            if not stock_id:
                continue
            playbook = storage.get_stock_playbook(stock_id) or {}
            recent_research = storage.get_recent_research(stock_id, limit=1) if hasattr(storage, "get_recent_research") else []
            last_research = recent_research[0] if recent_research else None
            items.append(
                {
                    "stock_id": stock_id,
                    "stock_name": playbook.get("stock_name") or stock.get("stock_name") or stock_id,
                    "ticker": playbook.get("ticker") or stock.get("ticker") or "",
                    "summary": stock.get("summary") or playbook.get("core_thesis", {}).get("summary", ""),
                    "core_thesis": playbook.get("core_thesis") or {},
                    "last_research": last_research,
                    "updated_at": playbook.get("updated_at") or stock.get("updated_at") or "",
                }
            )
        return items

    return AppDataHelpers(
        build_news_source_diagnostics=build_news_source_diagnostics,
        get_stocks_with_research_status=get_stocks_with_research_status,
    )
