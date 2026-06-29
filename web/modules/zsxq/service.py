from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class ZsxqModuleDeps:
    get_storage: Any
    get_zsxq_client: Any
    get_client: Any
    is_llm_failure_text: Any
    get_runtime_meta: Any
    render_template: Any
    get_json: Any
    safe_int: Any
    get_zsxq_paths: Any
    resolve_existing_zsxq_file: Any
    send_from_directory: Any
    load_zsxq_chat_context: Any
    json_safe: Any
    merge_topics_cache: Any
    write_markdown_snapshot: Any
    markdown_from_zsxq_daily_snapshot: Any
    resolve_zsxq_daily_snapshot_path: Any
    find_existing_zsxq_daily_snapshot: Any
    sync_snapshot_to_ima: Any
    ima_sync_key_zsxq_daily: Any
    now_factory: Any
    run_zsxq_auto_workflow: Any
    chat_with_retry: Any


def build_zsxq_module_service(deps: ZsxqModuleDeps) -> Any:
    return SimpleNamespace()
