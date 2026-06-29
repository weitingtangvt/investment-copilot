"""Thread-safe in-memory async task registry."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional


class TaskRegistry:
    def __init__(self, ttl_seconds: int = 3600, max_tasks: int = 500):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_tasks = max(50, int(max_tasks))
        self._tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired = [
            task_id
            for task_id, record in self._tasks.items()
            if (now - float(record.get("_updated_at", now))) > self.ttl_seconds
        ]
        for task_id in expired:
            self._tasks.pop(task_id, None)

        while len(self._tasks) > self.max_tasks:
            self._tasks.popitem(last=False)

    def set(self, task_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            record = dict(payload or {})
            record["_updated_at"] = time.time()
            self._tasks[task_id] = record
            self._tasks.move_to_end(task_id, last=True)
            self._cleanup_locked()

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._cleanup_locked()
            record = self._tasks.get(task_id)
            if not record:
                return None
            clean = dict(record)
            clean.pop("_updated_at", None)
            return clean


class WechatTaskRegistry(TaskRegistry):
    """Backward-compatible alias for existing call sites."""

    pass
