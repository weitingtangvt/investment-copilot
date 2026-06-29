from __future__ import annotations

import threading

from flask import Flask

from web.modules.broker_import.blueprint import create_broker_import_module
from web.modules.llm_config.blueprint import create_llm_config_module
from web.modules.app_shell.register import register_app_shell_module
from web.modules.chart_lab.blueprint import create_chart_lab_module
from web.modules.decision_review.blueprint import create_decision_review_module
from web.modules.factor_analysis.blueprint import create_factor_analysis_module
from web.modules.preferences.blueprint import create_preferences_module
from web.modules.research.blueprint import create_research_module
from web.modules.shell_pages.register import register_shell_pages_module
from web.modules.stocks.blueprint import create_stocks_module
from web.modules.system_api.blueprint import create_system_api_module
from web.modules.task_api.blueprint import create_task_api_module
from web.modules.watchlist.blueprint import create_watchlist_module
from web.modules.us_screener.blueprint import create_us_screener_module
from web.modules.weekly_review.blueprint import create_weekly_review_module

_feature_modules_registered = False
_feature_modules_lock = threading.Lock()
_FEATURE_MODULES_EXTENSION_KEY = "investment_assistant_feature_modules_registered"


def register_feature_modules(
    app: Flask,
    *,
    llm_config_service=None,
    broker_import_service=None,
    app_shell_service=None,
    decision_review_service=None,
    factor_analysis_service=None,
    system_api_service=None,
    shell_pages_service=None,
    task_api_service=None,
    preferences_service=None,
    research_service=None,
    stocks_service=None,
    weekly_review_service=None,
    us_screener_service=None,
    watchlist_service=None,
    xueqiu_service=None,
    zsxq_service=None,
    auth_guard=None,
) -> None:
    """Register feature modules incrementally without changing runtime topology."""
    if app.extensions.get(_FEATURE_MODULES_EXTENSION_KEY):
        return
    with _feature_modules_lock:
        if app.extensions.get(_FEATURE_MODULES_EXTENSION_KEY):
            return
        if llm_config_service is not None:
            app.register_blueprint(
                create_llm_config_module(
                    llm_config_service,
                    auth_guard=auth_guard,
                )
            )
        if broker_import_service is not None:
            app.register_blueprint(
                create_broker_import_module(
                    broker_import_service,
                    auth_guard=auth_guard,
                )
            )
        if app_shell_service is not None:
            register_app_shell_module(
                app,
                app_shell_service,
                auth_guard=auth_guard,
            )
        app.register_blueprint(create_chart_lab_module(auth_guard=auth_guard))
        if decision_review_service is not None:
            app.register_blueprint(
                create_decision_review_module(
                    decision_review_service,
                    auth_guard=auth_guard,
                )
            )
        if factor_analysis_service is not None:
            app.register_blueprint(
                create_factor_analysis_module(
                    factor_analysis_service,
                    auth_guard=auth_guard,
                )
            )
        if system_api_service is not None:
            app.register_blueprint(
                create_system_api_module(
                    system_api_service,
                    auth_guard=auth_guard,
                )
            )
        if shell_pages_service is not None:
            register_shell_pages_module(
                app,
                shell_pages_service,
                auth_guard=auth_guard,
            )
        if task_api_service is not None:
            app.register_blueprint(
                create_task_api_module(
                    task_api_service,
                    auth_guard=auth_guard,
                )
            )
        if preferences_service is not None:
            app.register_blueprint(
                create_preferences_module(
                    preferences_service,
                    auth_guard=auth_guard,
                )
            )
        if research_service is not None:
            app.register_blueprint(
                create_research_module(
                    research_service,
                    auth_guard=auth_guard,
                )
            )
        if stocks_service is not None:
            app.register_blueprint(
                create_stocks_module(
                    stocks_service,
                    auth_guard=auth_guard,
                )
            )
        if weekly_review_service is not None:
            app.register_blueprint(
                create_weekly_review_module(
                    weekly_review_service,
                    auth_guard=auth_guard,
                )
            )
        if us_screener_service is not None:
            app.register_blueprint(
                create_us_screener_module(
                    us_screener_service,
                    auth_guard=auth_guard,
                )
            )
        if watchlist_service is not None:
            app.register_blueprint(
                create_watchlist_module(
                    watchlist_service,
                    auth_guard=auth_guard,
                )
            )
        app.extensions[_FEATURE_MODULES_EXTENSION_KEY] = True
