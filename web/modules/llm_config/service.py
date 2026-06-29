from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify

from web.request_parsing import load_json_object
from web.services.domain_services import LLMConfigService


@dataclass
class LLMConfigModuleDeps:
    get_storage: Callable[[], Any]
    get_client: Callable[[], Any]
    config_value: Callable[[str, Any], Any]
    build_news_source_diagnostics: Callable[[], list[dict[str, Any]]]
    get_json: Callable[[], dict[str, Any]]
    to_bool: Callable[[Any, bool], bool]
    reload_config_center: Callable[[], None]
    reset_llm_client: Callable[[], None]


def build_llm_config_module_service(deps: LLMConfigModuleDeps) -> LLMConfigService:
    def _json_object_body_or_error():
        return load_json_object(deps.get_json)

    def get_llm_config():
        storage = deps.get_storage()
        runtime = deps.get_client()
        caps = runtime.capabilities() if runtime and hasattr(runtime, "capabilities") else {
            "supports_web_search": True,
            "supports_streaming": False,
            "supports_structured_json": True,
        }
        raw_config = storage.get_config()
        return jsonify(
            {
                "provider": "gpt53",
                "has_gpt53_key": bool(storage.get_gpt53_api_key()),
                "gpt53_base_url": str(deps.config_value("gpt53_base_url", storage.get_gpt53_base_url() or "") or ""),
                "gpt53_model": str(deps.config_value("gpt53_model", storage.get_gpt53_model()) or storage.get_gpt53_model()),
                "runtime_capabilities": caps,
                "active_model": str(deps.config_value("active_model", storage.get_active_model()) or "gpt53"),
                "custom_models": [
                    {
                        "name": model.get("name", ""),
                        "base_url": model.get("base_url", ""),
                        "model": model.get("model", ""),
                        "has_api_key": bool(model.get("api_key")),
                    }
                    for model in (deps.config_value("custom_models", storage.get_custom_models()) or [])
                ],
                "has_newsapi_key": bool(storage.get_newsapi_api_key()),
                "has_tavily_key": bool(storage.get_tavily_api_key()),
                "news_aggregation_strategy": storage.get_news_aggregation_strategy(),
                "news_use_ai_search": storage.get_news_use_ai_search(),
                "news_ai_enrich": storage.get_news_ai_enrich(),
                "usd_to_hkd_rate": float(raw_config.get("usd_to_hkd_rate") or 7.8),
                "eur_to_hkd_rate": float(raw_config.get("eur_to_hkd_rate") or 8.4),
                "news_source_diagnostics": deps.build_news_source_diagnostics(),
            }
        )

    def save_llm_config():
        storage = deps.get_storage()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        if "gpt53_api_key" in data:
            storage.set_gpt53_api_key((data.get("gpt53_api_key") or "").strip())
        if "gpt53_base_url" in data:
            storage.set_gpt53_base_url((data.get("gpt53_base_url") or "").strip())
        if "gpt53_model" in data:
            storage.set_gpt53_model((data.get("gpt53_model") or "").strip())
        if "newsapi_api_key" in data:
            storage.set_newsapi_api_key((data.get("newsapi_api_key") or "").strip())
        if "tavily_api_key" in data:
            storage.set_tavily_api_key((data.get("tavily_api_key") or "").strip())
        if "news_aggregation_strategy" in data:
            storage.set_news_aggregation_strategy(str(data.get("news_aggregation_strategy") or "").strip() or "priority")
        if "news_use_ai_search" in data:
            storage.set_news_use_ai_search(deps.to_bool(data.get("news_use_ai_search"), True))
        if "news_ai_enrich" in data:
            storage.set_news_ai_enrich(deps.to_bool(data.get("news_ai_enrich"), True))
        if "usd_to_hkd_rate" in data:
            config = storage.get_config()
            try:
                config["usd_to_hkd_rate"] = float(data.get("usd_to_hkd_rate"))
            except (TypeError, ValueError):
                config["usd_to_hkd_rate"] = 7.8
            storage.save_config(config)
        if "eur_to_hkd_rate" in data:
            config = storage.get_config()
            try:
                config["eur_to_hkd_rate"] = float(data.get("eur_to_hkd_rate"))
            except (TypeError, ValueError):
                config["eur_to_hkd_rate"] = 8.4
            storage.save_config(config)
        if "active_model" in data:
            storage.set_active_model((data.get("active_model") or "").strip() or "gpt53")
        deps.reload_config_center()
        deps.reset_llm_client()
        return jsonify({"success": True})

    return LLMConfigService(
        get_llm_config=get_llm_config,
        save_llm_config=save_llm_config,
    )
