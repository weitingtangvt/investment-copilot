from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional


@dataclass
class RuntimeStateAccessors:
    reset_llm_client: Callable[[], None]
    config_value: Callable[[str, Any], Any]
    resolve_model_config: Callable[[], Dict[str, str]]
    get_client: Callable[[], Any]
    get_env_collector: Callable[[], Any]
    get_research_engine: Callable[[], Any]
    get_weekly_review_manager: Callable[[], Any]
    get_runtime_meta: Callable[[], Dict[str, Any]]


@dataclass
class RuntimeStateDeps:
    get_storage: Callable[[], Any]
    get_config_center: Callable[[], Any]
    get_data_source_registry: Callable[[], Any]
    get_llm_cache: Callable[[], Any]
    get_client_state: Callable[[], Any]
    set_client_state: Callable[[Any], None]
    get_env_collector_state: Callable[[], Any]
    set_env_collector_state: Callable[[Any], None]
    get_research_engine_state: Callable[[], Any]
    set_research_engine_state: Callable[[Any], None]
    get_preference_learner_state: Callable[[], Any]
    set_preference_learner_state: Callable[[Any], None]
    get_weekly_review_manager_state: Callable[[], Any]
    set_weekly_review_manager_state: Callable[[Any], None]
    gpt53_client_factory: Callable[..., Any]
    llm_runtime_factory: Callable[..., Any]
    environment_collector_factory: Callable[..., Any]
    research_engine_factory: Callable[..., Any]
    preference_learner_factory: Callable[..., Any]
    weekly_review_manager_factory: Callable[..., Any]


def build_runtime_state_accessors(deps: RuntimeStateDeps) -> RuntimeStateAccessors:
    def reset_llm_client() -> None:
        deps.set_client_state(None)
        deps.set_env_collector_state(None)
        deps.set_research_engine_state(None)
        deps.set_preference_learner_state(None)
        deps.set_weekly_review_manager_state(None)

    def config_value(key: str, default: Any = None) -> Any:
        config_center = deps.get_config_center()
        if config_center is not None:
            return config_center.get_value(key, default)
        return default

    def resolve_model_config() -> Dict[str, str]:
        storage = deps.get_storage()
        active_model = str(config_value("active_model", storage.get_active_model()) or "gpt53").strip() or "gpt53"
        if active_model and active_model != "gpt53":
            custom_models = config_value("custom_models", storage.get_custom_models()) or []
            custom = next((item for item in custom_models if item.get("name") == active_model), None)
            if custom is None:
                custom = storage.get_custom_model(active_model)
            if custom and custom.get("api_key"):
                return {
                    "provider": f"custom:{active_model}",
                    "model": (custom.get("model") or "").strip(),
                    "base_url": (custom.get("base_url") or "").strip(),
                    "api_key": (custom.get("api_key") or "").strip(),
                }
        return {
            "provider": "gpt53",
            "model": str(config_value("gpt53_model", storage.get_gpt53_model()) or "").strip() or storage.get_gpt53_model(),
            "base_url": str(config_value("gpt53_base_url", storage.get_gpt53_base_url() or "") or "").strip(),
            "api_key": storage.get_gpt53_api_key() or "",
        }

    def get_client() -> Optional[Any]:
        client = deps.get_client_state()
        if client is not None:
            return client

        storage = deps.get_storage()
        cfg = resolve_model_config()
        if not cfg["api_key"]:
            return None

        raw_client = deps.gpt53_client_factory(cfg["api_key"], base_url=cfg["base_url"] or None, model=cfg["model"] or None)
        client = deps.llm_runtime_factory(
            provider=cfg["provider"],
            model=cfg["model"] or storage.get_gpt53_model(),
            base_url=cfg["base_url"],
            client=raw_client,
            cache=deps.get_llm_cache(),
        )
        deps.set_client_state(client)
        deps.set_env_collector_state(
            deps.environment_collector_factory(
                client,
                storage,
                data_source_registry=deps.get_data_source_registry(),
            )
        )
        deps.set_research_engine_state(deps.research_engine_factory(client, storage))
        deps.set_preference_learner_state(deps.preference_learner_factory(client, storage))
        deps.set_weekly_review_manager_state(deps.weekly_review_manager_factory(client, storage, deps.get_env_collector_state()))
        return client

    def get_env_collector() -> Optional[Any]:
        env_collector = deps.get_env_collector_state()
        if env_collector is None and get_client() is not None:
            deps.set_env_collector_state(
                deps.environment_collector_factory(
                    deps.get_client_state(),
                    deps.get_storage(),
                    data_source_registry=deps.get_data_source_registry(),
                )
            )
        return deps.get_env_collector_state()

    def get_research_engine() -> Optional[Any]:
        engine = deps.get_research_engine_state()
        if engine is None and get_client() is not None:
            deps.set_research_engine_state(deps.research_engine_factory(deps.get_client_state(), deps.get_storage()))
        return deps.get_research_engine_state()

    def get_weekly_review_manager() -> Optional[Any]:
        manager = deps.get_weekly_review_manager_state()
        if manager is None and get_client() is not None and get_env_collector() is not None:
            deps.set_weekly_review_manager_state(
                deps.weekly_review_manager_factory(
                    deps.get_client_state(),
                    deps.get_storage(),
                    deps.get_env_collector_state(),
                )
            )
        return deps.get_weekly_review_manager_state()

    def get_runtime_meta() -> Dict[str, Any]:
        runtime = deps.get_client_state() if deps.get_client_state() is not None else get_client()
        if runtime and hasattr(runtime, "get_runtime_meta"):
            return runtime.get_runtime_meta()
        storage = deps.get_storage()
        return {
            "provider": str(config_value("llm_provider", storage.get_llm_provider()) or "gpt53"),
            "model": str(config_value("gpt53_model", storage.get_gpt53_model()) or storage.get_gpt53_model()),
            "base_url": str(config_value("gpt53_base_url", storage.get_gpt53_base_url() or "") or ""),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "request_mode": "unknown",
            "degraded": False,
            "degraded_reason": "",
            "cache_hit": False,
            "runtime_seconds": 0.0,
        }

    return RuntimeStateAccessors(
        reset_llm_client=reset_llm_client,
        config_value=config_value,
        resolve_model_config=resolve_model_config,
        get_client=get_client,
        get_env_collector=get_env_collector,
        get_research_engine=get_research_engine,
        get_weekly_review_manager=get_weekly_review_manager,
        get_runtime_meta=get_runtime_meta,
    )
