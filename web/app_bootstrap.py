from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from web.modules.registry import register_feature_modules
from web.modules.us_screener.service import USScreenerModuleDeps, build_us_screener_module_service
from web.modules.watchlist.service import WatchlistModuleDeps, build_watchlist_module_service
from web.modules.weekly_review.service import WeeklyReviewModuleDeps, build_weekly_review_module_service


@dataclass
class AppBootstrapDeps:
    app: Any
    register_feature_modules_fn: Callable[..., Any]
    auth_guard: Callable[[Any], Any]
    llm_config_service: Any
    broker_import_service: Any
    app_shell_service: Any
    decision_review_service: Any
    factor_analysis_service: Any
    system_api_service: Any
    shell_pages_service: Any
    task_api_service: Any
    preferences_service: Any
    research_service: Any
    stocks_service: Any
    xueqiu_service: Any
    zsxq_service: Any
    weekly_review_module_deps: WeeklyReviewModuleDeps
    weekly_review_actions: dict[str, Any]
    us_screener_module_deps: USScreenerModuleDeps
    us_screener_actions: dict[str, Any]
    watchlist_module_deps: WatchlistModuleDeps


def register_application_modules(deps: AppBootstrapDeps) -> Any:
    deps.register_feature_modules_fn(
        deps.app,
        llm_config_service=deps.llm_config_service,
        broker_import_service=deps.broker_import_service,
        app_shell_service=deps.app_shell_service,
        decision_review_service=deps.decision_review_service,
        factor_analysis_service=deps.factor_analysis_service,
        system_api_service=deps.system_api_service,
        shell_pages_service=deps.shell_pages_service,
        task_api_service=deps.task_api_service,
        preferences_service=deps.preferences_service,
        research_service=deps.research_service,
        stocks_service=deps.stocks_service,
        weekly_review_service=build_weekly_review_module_service(
            deps.weekly_review_module_deps,
            **deps.weekly_review_actions,
        ),
        us_screener_service=build_us_screener_module_service(
            deps.us_screener_module_deps,
            scan=deps.us_screener_actions["scan"],
        ),
        watchlist_service=build_watchlist_module_service(deps.watchlist_module_deps),
        xueqiu_service=deps.xueqiu_service,
        zsxq_service=deps.zsxq_service,
        auth_guard=deps.auth_guard,
    )
    return deps.app
