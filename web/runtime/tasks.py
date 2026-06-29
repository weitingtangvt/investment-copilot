from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass
class TaskRuntimeAccessors:
    patch_task_record: Callable[[Optional[Dict[str, Any]], Dict[str, Any]], None]
    create_background_task_manager: Callable[[Any], Any]
    get_task_manager: Callable[[], Any]
    set_wechat_task: Callable[[str, Dict[str, Any]], None]
    get_wechat_task: Callable[[str], Optional[Dict[str, Any]]]


@dataclass
class TaskRuntimeDeps:
    get_storage: Callable[[], Any]
    get_task_manager_state: Callable[[], Any]
    set_task_manager_state: Callable[[Any], None]
    task_manager_lock: Any
    background_task_manager_factory: Callable[..., Any]
    safe_int: Callable[[Any, int], int]
    logger: Any
    get_noop_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    get_weekly_review_generate_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    get_weekly_review_synthesize_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    get_weekly_review_chat_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    get_us_screener_scan_task_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    get_xueqiu_export_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    get_xueqiu_prepare_session_runner: Callable[[], Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]]]
    save_task_record: Callable[[str, Dict[str, Any]], None]
    wechat_set: Callable[[str, Dict[str, Any]], None]
    wechat_get: Callable[[str], Optional[Dict[str, Any]]]


def build_task_runtime_accessors(deps: TaskRuntimeDeps) -> TaskRuntimeAccessors:
    def patch_task_record(task: Optional[Dict[str, Any]], patch: Dict[str, Any]) -> None:
        clean_task_id = str((task or {}).get("task_id") or "").strip()
        if not clean_task_id:
            return
        deps.save_task_record(clean_task_id, dict(patch or {}))

    def create_background_task_manager(active_storage: Any) -> Any:
        os = __import__("os")
        manager = deps.background_task_manager_factory(
            storage=active_storage,
            max_workers=deps.safe_int(os.environ.get("BACKGROUND_TASK_MAX_WORKERS"), 4),
        )
        manager.register_runner("noop", deps.get_noop_runner())
        manager.register_runner("weekly_review_generate", deps.get_weekly_review_generate_runner())
        manager.register_runner("weekly_review_synthesize", deps.get_weekly_review_synthesize_runner())
        manager.register_runner("weekly_review_chat", deps.get_weekly_review_chat_runner())
        manager.register_runner("us_screener_scan", deps.get_us_screener_scan_task_runner())
        manager.register_runner("xueqiu_export", deps.get_xueqiu_export_runner())
        manager.register_runner("xueqiu_prepare_session", deps.get_xueqiu_prepare_session_runner())
        auto_start_flag = str(os.environ.get("BACKGROUND_TASK_AUTOSTART", "1")).strip().lower()
        is_pytest_runtime = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if auto_start_flag not in {"0", "false", "no"} and not is_pytest_runtime:
            manager.start()
        return manager

    def get_task_manager() -> Any:
        with deps.task_manager_lock:
            manager = deps.get_task_manager_state()
            storage = deps.get_storage()
            if manager is None or getattr(manager, "_storage", None) is not storage:
                if manager is not None:
                    try:
                        manager.shutdown(wait=False)
                    except Exception:
                        deps.logger.warning("failed to shutdown previous task manager", exc_info=True)
                manager = create_background_task_manager(storage)
                deps.set_task_manager_state(manager)
            return manager

    def set_wechat_task(task_id: str, payload: Dict[str, Any]) -> None:
        deps.wechat_set(task_id, payload)

    def get_wechat_task(task_id: str) -> Optional[Dict[str, Any]]:
        return deps.wechat_get(task_id)

    return TaskRuntimeAccessors(
        patch_task_record=patch_task_record,
        create_background_task_manager=create_background_task_manager,
        get_task_manager=get_task_manager,
        set_wechat_task=set_wechat_task,
        get_wechat_task=get_wechat_task,
    )
