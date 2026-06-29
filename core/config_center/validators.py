from __future__ import annotations

from typing import Any

from .defaults import get_default_config


class ConfigValidationError(ValueError):
    pass


_BOOL_KEYS = {"news_use_ai_search", "news_ai_enrich"}
_LIST_KEYS = {"xueqiu_followed_users", "wechat_followed_accounts", "custom_models"}
_STRING_KEYS = {
    "llm_provider",
    "gpt53_model",
    "gpt53_base_url",
    "news_aggregation_strategy",
    "rsshub_url",
    "wewe_rss_url",
    "active_model",
}
_NON_EMPTY_STRING_KEYS = _STRING_KEYS - {"gpt53_base_url"}
_AGGREGATION_STRATEGIES = {"priority", "merge"}


def validate_config_patch(patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ConfigValidationError("config patch must be a dict")

    allowed_keys = set(get_default_config().keys())
    normalized: dict[str, Any] = {}

    for key, value in patch.items():
        if key not in allowed_keys:
            raise ConfigValidationError(f"unknown config key: {key}")

        if key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigValidationError(f"{key} must be bool")
            normalized[key] = value
            continue

        if key in _LIST_KEYS:
            if not isinstance(value, list):
                raise ConfigValidationError(f"{key} must be list")
            normalized[key] = value
            continue

        if key in _STRING_KEYS:
            if not isinstance(value, str):
                raise ConfigValidationError(f"{key} must be string")
            cleaned = value.strip()
            if key in _NON_EMPTY_STRING_KEYS and not cleaned:
                raise ConfigValidationError(f"{key} cannot be empty")
            if key == "news_aggregation_strategy" and cleaned not in _AGGREGATION_STRATEGIES:
                raise ConfigValidationError("news_aggregation_strategy must be one of: priority, merge")
            normalized[key] = cleaned
            continue

        normalized[key] = value

    return normalized


def validate_full_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = get_default_config()
    merged.update(validate_config_patch(config))
    return merged
