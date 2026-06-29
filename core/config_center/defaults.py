from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "llm_provider": "gpt53",
    "gpt53_model": "gpt-5.2",
    "gpt53_base_url": "",
    "news_aggregation_strategy": "priority",
    "news_use_ai_search": True,
    "news_ai_enrich": True,
    "usd_to_hkd_rate": 7.8,
    "eur_to_hkd_rate": 8.4,
    "rsshub_url": "http://localhost:1200",
    "wewe_rss_url": "http://localhost:4000",
    "xueqiu_followed_users": [],
    "wechat_followed_accounts": [],
    "custom_models": [],
    "active_model": "gpt53",
}


def get_default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)
