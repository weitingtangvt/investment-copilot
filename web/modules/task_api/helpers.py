from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Response, jsonify


@dataclass
class TaskApiHelperDeps:
    task_status_messages: dict[str, str]


class TaskApiHelpers:
    def __init__(self, deps: TaskApiHelperDeps):
        self._deps = deps

    def task_status_message(self, status: str, *, error: str = "", explicit_message: str = "") -> str:
        message = str(explicit_message or "").strip()
        if message:
            return message
        clean_status = str(status or "").strip().lower()
        if clean_status == "failed" and str(error or "").strip():
            return str(error or "").strip()
        return self._deps.task_status_messages.get(clean_status, "TextStatusText")

    def task_payload(
        self,
        task: Optional[Dict[str, Any]],
        *,
        fallback_task_id: str = "",
        fallback_task_type: str = "",
        fallback_status: str = "",
        fallback_message: str = "",
        fallback_error: str = "",
    ) -> Dict[str, Any]:
        raw = dict(task or {})
        status = str(raw.get("status") or fallback_status or "").strip().lower()
        task_id = str(raw.get("task_id") or fallback_task_id).strip()
        task_type = str(raw.get("runner_name") or raw.get("task_type") or fallback_task_type).strip()
        error = str(raw.get("error") or fallback_error or "").strip()
        payload = raw.get("payload")
        metadata = raw.get("metadata")
        message = self.task_status_message(
            status,
            error=error,
            explicit_message=str(raw.get("message") or fallback_message or ""),
        )
        return {
            "task_id": task_id,
            "task_type": task_type,
            "status": status,
            "message": message,
            "payload": payload if isinstance(payload, dict) else {},
            "metadata": metadata if isinstance(metadata, dict) else {},
            "result": raw.get("result"),
            "error": error,
            "created_at": str(raw.get("created_at") or ""),
            "updated_at": str(raw.get("updated_at") or ""),
            "started_at": str(raw.get("started_at") or ""),
            "completed_at": str(raw.get("completed_at") or ""),
            "cancelled_at": str(raw.get("cancelled_at") or ""),
        }

    def task_error_response(
        self,
        status_code: int,
        *,
        task_id: str = "",
        task_type: str = "",
        status: str,
        message: str,
        error: str = "",
    ) -> Tuple[Response, int]:
        return (
            jsonify(
                self.task_payload(
                    None,
                    fallback_task_id=task_id,
                    fallback_task_type=task_type,
                    fallback_status=status,
                    fallback_message=message,
                    fallback_error=error,
                )
            ),
            status_code,
        )

    @staticmethod
    def split_csv_values(values: Iterable[Any]) -> List[str]:
        results: List[str] = []
        for value in values:
            for part in str(value or "").split(","):
                clean = part.strip().lower()
                if clean:
                    results.append(clean)
        return results


def build_task_api_helpers(deps: TaskApiHelperDeps) -> TaskApiHelpers:
    return TaskApiHelpers(deps)
