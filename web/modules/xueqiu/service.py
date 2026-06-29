from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class XueqiuModuleDeps:
    get_storage: Any
    render_template: Any
    safe_int: Any
    get_settings: Any
    save_settings: Any
    sanitize_user: Any
    auth_status: Any
    build_client: Any
    feed_meta: Any
    merge_posts: Any
    get_task_manager: Any
    export_task_payload: Any
    session_task_payload: Any
    export_task_get: Any
    session_task_get: Any
    send_from_directory: Any
    logger: Any


def build_xueqiu_module_service(deps: XueqiuModuleDeps) -> Any:
    return SimpleNamespace()
