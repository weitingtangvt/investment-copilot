from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class XueqiuHelperDeps:
    get_storage: Any
    safe_int: Any
    get_task_manager: Any
    task_status_message: Any
    client_factory: Any


def build_xueqiu_module_helpers(deps: XueqiuHelperDeps) -> Any:
    return SimpleNamespace(
        sanitize_user=lambda user: user,
        build_client=lambda settings=None: deps.client_factory(),
        get_settings=lambda: {},
        save_settings=lambda settings: {"success": False, "error": "Not included in sanitized copy."},
        auth_status=lambda settings=None: {"authenticated": False},
        feed_meta=lambda *args, **kwargs: {},
        merge_posts=lambda *args, **kwargs: [],
        export_task_payload=lambda *args, **kwargs: {},
        session_task_payload=lambda *args, **kwargs: {},
        export_task_get=lambda *args, **kwargs: None,
        session_task_get=lambda *args, **kwargs: None,
    )
