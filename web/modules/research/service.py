from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, request

from web.request_parsing import load_json_object
from web.services.domain_services import ResearchService
from web.services.research_workflow_service import BatchResearchWorkflowService


@dataclass
class ResearchModuleDeps:
    get_json: Callable[[], Any]
    safe_int: Callable[[Any, int], int]
    to_bool: Callable[[Any, bool], bool]
    json_safe: Callable[[Any], Any]
    get_client: Callable[[], Any]
    chat_with_retry: Callable[..., str]
    is_llm_failure_text: Callable[[Any], bool]
    get_runtime_meta: Callable[[], dict[str, Any]]
    get_storage: Callable[[], Any]
    collect_environment_step: Callable[..., Any]
    assess_impact_step: Callable[..., Any]
    execute_research_step: Callable[..., Any]


def build_research_module_service(deps: ResearchModuleDeps) -> ResearchService:
    def _json_object_body_or_error():
        return load_json_object(deps.get_json)

    def collect_environment(stock_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        step = deps.collect_environment_step(
            stock_id=stock_id,
            days=deps.safe_int(data.get("days"), 7),
            force_refresh=deps.to_bool(data.get("force_refresh"), False),
            ai_enrich=data.get("ai_enrich"),
        )
        return jsonify(deps.json_safe(step.payload)), step.status_code

    def assess_impact(stock_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        step = deps.assess_impact_step(
            stock_id=stock_id,
            time_range=str(data.get("time_range") or "7d"),
            auto_collected=data.get("auto_collected") or [],
            user_uploaded=data.get("user_uploaded") or [],
            force_refresh=deps.to_bool(data.get("force_refresh"), False),
        )
        return jsonify(deps.json_safe(step.payload)), step.status_code

    def adjust_plan(stock_id: str):
        runtime = deps.get_client()
        if not runtime:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        adjustment = str(data.get("adjustment_request") or "").strip()
        current_plan = data.get("research_plan") or {}
        if not adjustment:
            return jsonify({"error": "adjustment_request is required"}), 400
        prompt = f"Refine this research plan as JSON. stock={stock_id}\\nplan={current_plan}\\nadjustment={adjustment}"
        text = deps.chat_with_retry(runtime, prompt, retries=1, request_name="adjust_plan")
        return jsonify(
            {
                "stock_id": stock_id,
                "research_plan": current_plan,
                "assistant_note": str(text or "")[:500],
                "runtime_meta": deps.get_runtime_meta(),
            }
        ), 200

    def follow_up_research(stock_id: str):
        runtime = deps.get_client()
        if not runtime:
            return jsonify({"error": "LLM is not configured"}), 400
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        question = str(data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400
        prompt = f"stock={stock_id}\\ncontext={data.get('context') or {}}\\nquestion={question}"
        answer = deps.chat_with_retry(runtime, prompt, retries=1, request_name="follow_up")
        if deps.is_llm_failure_text(answer):
            return jsonify({"error": answer, "runtime_meta": deps.get_runtime_meta()}), 502
        return jsonify({"answer": answer, "runtime_meta": deps.get_runtime_meta()}), 200

    def execute_research(stock_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        step = deps.execute_research_step(
            stock_id=stock_id,
            research_plan=data.get("research_plan") or {},
            environment_data=data.get("environment_data") or {"time_range": "7d", "auto_collected": [], "user_uploaded": []},
            force_refresh=deps.to_bool(data.get("force_refresh"), False),
            staged_mode=deps.to_bool(data.get("staged_mode"), True),
            expand_full_report=deps.to_bool(data.get("expand_full_report"), False),
        )
        return jsonify(deps.json_safe(step.payload)), step.status_code

    def get_research_history(stock_id: str):
        return jsonify(deps.json_safe(deps.get_storage().get_research_history(stock_id)))

    def save_research_feedback(stock_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        record_id = str(data.get("record_id") or "").strip()
        if not record_id:
            return jsonify({"error": "record_id is required"}), 400
        ok = deps.get_storage().update_research_feedback(stock_id, record_id, data.get("feedback") or data)
        return jsonify({"success": bool(ok)}), 200

    def get_research_context(stock_id: str):
        limit = deps.safe_int(request.args.get("limit"), 3)
        return jsonify({"stock_id": stock_id, "context": deps.get_storage().get_research_context(stock_id, limit=limit)})

    def toggle_milestone(stock_id: str, record_id: str):
        return jsonify({"success": True, "is_milestone": bool(deps.get_storage().toggle_milestone(stock_id, record_id))})

    def scan_single_stock(stock_id: str):
        return collect_environment(stock_id)

    def batch_research_stock(stock_id: str):
        service = BatchResearchWorkflowService(
            collect_environment=deps.collect_environment_step,
            assess_impact=deps.assess_impact_step,
            execute_research=deps.execute_research_step,
        )
        result = service.run(stock_id)
        return jsonify(deps.json_safe(result.payload)), result.status_code

    return ResearchService(
        collect_environment=collect_environment,
        assess_impact=assess_impact,
        adjust_plan=adjust_plan,
        follow_up_research=follow_up_research,
        execute_research=execute_research,
        get_research_history=get_research_history,
        save_research_feedback=save_research_feedback,
        get_research_context=get_research_context,
        toggle_milestone=toggle_milestone,
        scan_single_stock=scan_single_stock,
        batch_research_stock=batch_research_stock,
    )
