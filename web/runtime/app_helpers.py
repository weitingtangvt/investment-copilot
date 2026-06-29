from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable


def noop_task_runner(payload: dict[str, Any] | None, _task: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "payload": dict(payload or {})}


def build_lazy_us_screener_service_getter(
    *,
    storage: Any,
    get_state: Callable[[], Any],
    set_state: Callable[[Any], Any],
    service_factory: Callable[[Any], Any],
) -> Callable[[], Any]:
    def _getter() -> Any:
        service = get_state()
        if service is None or getattr(service, "storage", None) is not storage:
            service = service_factory(storage)
            set_state(service)
        return service

    return _getter


def recent_week_ids(get_week_id: Callable[[datetime | None], str], count: int = 8) -> list[str]:
    now = datetime.now()
    return [get_week_id(now - timedelta(weeks=i)) for i in range(max(1, count))]
