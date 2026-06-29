from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class XueqiuTaskRunnerDeps:
    sanitize_user: Any
    build_client: Any
    patch_task_record: Any
    safe_int: Any


def _noop(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"success": False, "error": "Market-feed integration is not included in the sanitized copy."}


def build_xueqiu_task_runners(deps: XueqiuTaskRunnerDeps) -> Any:
    return SimpleNamespace(export=_noop, prepare_session=_noop)
