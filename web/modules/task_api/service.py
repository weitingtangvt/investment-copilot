from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from flask import jsonify

from web.request_parsing import load_json_object
from web.services.domain_services import TaskApiService


@dataclass
class TaskApiModuleDeps:
    get_json: Callable[[], Any]
    get_task_manager: Callable[[], Any]
    task_payload: Callable[..., Dict[str, Any]]
    task_error_response: Callable[..., Any]
    split_csv_values: Callable[[Any], list[str]]
    safe_int: Callable[[Any, int], int]
    to_bool: Callable[[Any, bool], bool]
    logger: Any


def build_task_api_module_service(deps: TaskApiModuleDeps) -> TaskApiService:
    def create_task():
        data, error_response = load_json_object(
            deps.get_json,
            invalid_json_message="Text JSON",
            invalid_type_message="Text JSON Text",
        )
        if error_response is not None:
            error_message = error_response[0].get_json().get("error") or "Text JSON Text"
            return deps.task_error_response(
                400,
                status="invalid_request",
                message=error_message,
            )

        task_type = str(data.get("task_type") or "").strip()
        if not task_type:
            return deps.task_error_response(
                400,
                status="invalid_request",
                message="task_type is required",
            )

        payload = data.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return deps.task_error_response(
                400,
                task_type=task_type,
                status="invalid_request",
                message="payload must be an object",
            )

        metadata = data.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            return deps.task_error_response(
                400,
                task_type=task_type,
                status="invalid_request",
                message="metadata must be an object",
            )

        requested_task_id = str(data.get("task_id") or "").strip() or None
        manager = deps.get_task_manager()
        try:
            created = manager.submit_task(
                task_type,
                dict(payload),
                task_id=requested_task_id,
                metadata=dict(metadata),
            )
        except KeyError:
            return deps.task_error_response(
                400,
                task_id=requested_task_id or "",
                task_type=task_type,
                status="invalid_request",
                message=f"unsupported task_type: {task_type}",
            )
        except ValueError as exc:
            return deps.task_error_response(
                400,
                task_id=requested_task_id or "",
                task_type=task_type,
                status="invalid_request",
                message=str(exc),
                error=str(exc),
            )
        except Exception as exc:
            if deps.logger is not None:
                deps.logger.exception("create task failed")
            return deps.task_error_response(
                500,
                task_id=requested_task_id or "",
                task_type=task_type,
                status="failed",
                message="TextFailed",
                error=str(exc),
            )
        return jsonify(deps.task_payload(created))

    def get_task(task_id: str):
        clean_task_id = str(task_id or "").strip()
        task = deps.get_task_manager().get_task(clean_task_id)
        if not task:
            return deps.task_error_response(
                404,
                task_id=clean_task_id,
                status="not_found",
                message="task not found",
            )
        return jsonify(deps.task_payload(task, fallback_task_id=clean_task_id))

    def list_tasks():
        from flask import request

        status_filters = deps.split_csv_values(request.args.getlist("status"))
        task_type_filter = str(request.args.get("task_type") or "").strip()
        limit = deps.safe_int(request.args.get("limit"), 100)
        limit = min(500, max(1, limit))

        items = deps.get_task_manager().list_tasks(
            statuses=status_filters or None,
            limit=limit,
        )
        if task_type_filter:
            items = [
                item
                for item in items
                if str(item.get("runner_name") or item.get("task_type") or "").strip() == task_type_filter
            ]
        serialized = [deps.task_payload(item) for item in items]
        return jsonify(
            {
                "tasks": serialized,
                "count": len(serialized),
                "status": ",".join(status_filters),
                "task_type": task_type_filter,
                "message": "ok",
            }
        )

    def cancel_task(task_id: str):
        clean_task_id = str(task_id or "").strip()
        task = deps.get_task_manager().cancel_task(clean_task_id)
        if not task:
            return deps.task_error_response(
                404,
                task_id=clean_task_id,
                status="not_found",
                message="task not found",
            )
        return jsonify(deps.task_payload(task, fallback_task_id=clean_task_id))

    def submit_us_screener_scan_task():
        data, error_response = load_json_object(
            deps.get_json,
            invalid_json_message="Text JSON",
            invalid_type_message="Text JSON Text",
        )
        if error_response is not None:
            error_message = error_response[0].get_json().get("error") or "Text JSON Text"
            return deps.task_error_response(
                400,
                task_type="us_screener_scan",
                status="invalid_request",
                message=error_message,
            )
        payload = dict(data)
        payload["resume"] = deps.to_bool(payload.get("resume"), False)
        try:
            created = deps.get_task_manager().submit_task("us_screener_scan", payload)
        except ValueError as exc:
            return deps.task_error_response(
                400,
                task_type="us_screener_scan",
                status="invalid_request",
                message=str(exc),
                error=str(exc),
            )
        except Exception as exc:
            if deps.logger is not None:
                deps.logger.exception("submit us_screener_scan task failed")
            return deps.task_error_response(
                500,
                task_type="us_screener_scan",
                status="failed",
                message="TextFailed",
                error=str(exc),
            )
        return jsonify(deps.task_payload(created, fallback_message="Text"))

    return TaskApiService(
        create_task=create_task,
        get_task=get_task,
        list_tasks=list_tasks,
        cancel_task=cancel_task,
        submit_us_screener_scan_task=submit_us_screener_scan_task,
    )
