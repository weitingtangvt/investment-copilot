"""Pure workflow orchestration for batch stock research."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


DEFAULT_RESEARCH_PLAN: Dict[str, Any] = {
    "research_objective": "Assess thesis",
    "research_modules": [],
    "timeline": "3-5 days",
    "hypothesis_to_test": [],
}


@dataclass(frozen=True)
class WorkflowStepResult:
    status_code: int
    payload: Dict[str, Any]

    @property
    def failed(self) -> bool:
        return self.status_code >= 400


class BatchResearchWorkflowService:
    """Compose collect/assess/execute dependencies without Flask request contexts."""

    def __init__(
        self,
        collect_environment: Callable[..., WorkflowStepResult],
        assess_impact: Callable[..., WorkflowStepResult],
        execute_research: Callable[..., WorkflowStepResult],
    ) -> None:
        self._collect_environment = collect_environment
        self._assess_impact = assess_impact
        self._execute_research = execute_research

    def run(
        self,
        stock_id: str,
        *,
        days: int = 7,
        force_refresh: bool = False,
        ai_enrich: Optional[Any] = None,
        user_uploaded: Optional[list[dict[str, Any]]] = None,
        staged_mode: bool = True,
        expand_full_report: bool = False,
    ) -> WorkflowStepResult:
        uploaded_items = list(user_uploaded or [])

        collect_result = self._collect_environment(
            stock_id,
            days=days,
            force_refresh=force_refresh,
            ai_enrich=ai_enrich,
        )
        if collect_result.failed:
            return collect_result

        collect_payload = collect_result.payload or {}
        assess_result = self._assess_impact(
            stock_id,
            time_range=str(collect_payload.get("time_range") or "7d"),
            auto_collected=list(collect_payload.get("auto_collected") or []),
            user_uploaded=uploaded_items,
            force_refresh=force_refresh,
        )
        if assess_result.failed:
            return assess_result

        assess_payload = assess_result.payload or {}
        plan = deepcopy(assess_payload.get("research_plan") or DEFAULT_RESEARCH_PLAN)
        environment_data = {
            "time_range": str(collect_payload.get("time_range") or "7d"),
            "auto_collected": list(collect_payload.get("auto_collected") or []),
            "user_uploaded": uploaded_items,
        }
        return self._execute_research(
            stock_id,
            research_plan=plan,
            environment_data=environment_data,
            force_refresh=force_refresh,
            staged_mode=staged_mode,
            expand_full_report=expand_full_report,
        )
