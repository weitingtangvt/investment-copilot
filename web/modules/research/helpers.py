from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from web.services.research_workflow_service import WorkflowStepResult


@dataclass
class ResearchStepHelperDeps:
    get_client: Callable[[], Any]
    get_env_collector: Callable[[], Any]
    get_research_engine: Callable[[], Any]
    get_storage_stock_name: Callable[[str], dict[str, Any]]
    get_runtime_meta: Callable[[], dict[str, Any]]


class ResearchStepHelpers:
    def __init__(self, deps: ResearchStepHelperDeps):
        self._deps = deps

    def collect_environment_step(
        self,
        stock_id: str,
        *,
        days: int = 7,
        force_refresh: bool = False,
        ai_enrich: Any = None,
    ) -> WorkflowStepResult:
        runtime = self._deps.get_client()
        collector = self._deps.get_env_collector()
        if not runtime or not collector:
            return WorkflowStepResult(status_code=400, payload={"error": "LLM is not configured"})

        result = collector.collect_news(
            stock_id=stock_id,
            stock_name=(self._deps.get_storage_stock_name(stock_id) or {}).get("stock_name", stock_id),
            time_range_days=days,
            force_refresh=force_refresh,
            ai_enrich=ai_enrich,
        )
        metadata = result.get("search_metadata") or {}
        runtime_meta = result.get("runtime_meta") or self._deps.get_runtime_meta()
        return WorkflowStepResult(
            status_code=200,
            payload={
                "stock_id": stock_id,
                "time_range": f"{days}d",
                "auto_collected": result.get("news", []),
                "user_uploaded": [],
                "search_metadata": metadata,
                "impact_cards": result.get("impact_cards", []),
                "runtime_meta": runtime_meta,
                "cache_hit": bool(metadata.get("cache_hit") or runtime_meta.get("cache_hit")),
                "degraded_reason": metadata.get("degraded_reason") or runtime_meta.get("degraded_reason", ""),
                "fallback_summary": result.get("fallback_summary", ""),
            },
        )

    def assess_impact_step(
        self,
        stock_id: str,
        *,
        time_range: str = "7d",
        auto_collected: list[dict[str, Any]] | None = None,
        user_uploaded: list[dict[str, Any]] | None = None,
        force_refresh: bool = False,
    ) -> WorkflowStepResult:
        collector = self._deps.get_env_collector()
        if collector is None:
            return WorkflowStepResult(status_code=400, payload={"error": "LLM is not configured"})
        return WorkflowStepResult(
            status_code=200,
            payload=collector.assess_impact(
                stock_id=stock_id,
                time_range=time_range,
                auto_collected=auto_collected or [],
                user_uploaded=user_uploaded or [],
                force_refresh=force_refresh,
            ),
        )

    def execute_research_step(
        self,
        stock_id: str,
        *,
        research_plan: dict[str, Any],
        environment_data: dict[str, Any],
        force_refresh: bool = False,
        staged_mode: bool = True,
        expand_full_report: bool = False,
    ) -> WorkflowStepResult:
        engine = self._deps.get_research_engine()
        if engine is None:
            return WorkflowStepResult(status_code=400, payload={"error": "LLM is not configured"})
        if not research_plan:
            return WorkflowStepResult(status_code=400, payload={"error": "research_plan is required"})
        result = engine.execute_research(
            stock_id=stock_id,
            research_plan=research_plan,
            environment_data=environment_data,
            force_refresh=force_refresh,
            staged_mode=staged_mode,
            expand_full_report=expand_full_report,
        )
        return WorkflowStepResult(status_code=200, payload=result)


def build_research_step_helpers(deps: ResearchStepHelperDeps) -> ResearchStepHelpers:
    return ResearchStepHelpers(deps)
