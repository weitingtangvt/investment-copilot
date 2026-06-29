from __future__ import annotations

from flask import Flask


def register_shell_pages_module(app: Flask, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    def _register(paths, endpoint, handler):
        view_func = auth_guard(handler)
        for path in paths:
            app.add_url_rule(path, endpoint=endpoint, view_func=view_func)

    _register(
        ["/decision_review", "/decision_review/"],
        "decision_review_page",
        service.decision_review_page,
    )
    _register(
        ["/portfolio-analysis", "/portfolio-analysis/"],
        "portfolio_analysis_page",
        service.portfolio_analysis_page,
    )
    _register(
        ["/portfolio_analysis", "/portfolio_analysis/"],
        "portfolio_analysis_page_legacy",
        service.portfolio_analysis_page_legacy,
    )
    _register(
        ["/portfolio-analytics", "/portfolio-analytics/"],
        "portfolio_analytics_page",
        service.portfolio_analytics_page,
    )
    _register(
        ["/research-history", "/research-history/", "/research_history", "/research_history/"],
        "research_history",
        service.research_history,
    )
    _register(
        ["/twitter", "/twitter/"],
        "twitter_page",
        service.twitter_page,
    )
    _register(
        ["/wechat", "/wechat/"],
        "wechat_page",
        service.wechat_page,
    )
    _register(
        ["/settings", "/settings/"],
        "settings_page",
        service.settings_page,
    )
    _register(
        ["/add-stock", "/add-stock/", "/add_stock", "/add_stock/"],
        "add_stock_page",
        service.add_stock_page,
    )
