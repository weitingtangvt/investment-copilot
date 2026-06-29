from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from web.services.domain_services import ShellPagesService


@dataclass
class ShellPagesModuleDeps:
    render_template: Callable[[str], Any]
    redirect: Callable[[str, int], Any]


def build_shell_pages_module_service(deps: ShellPagesModuleDeps) -> ShellPagesService:
    return ShellPagesService(
        decision_review_page=lambda: deps.render_template("decision_review.html"),
        portfolio_analysis_page=lambda: deps.render_template("portfolio_analysis.html"),
        portfolio_analysis_page_legacy=lambda: deps.redirect("/portfolio-analysis", 302),
        portfolio_analytics_page=lambda: deps.render_template("portfolio_analytics.html"),
        research_history=lambda: deps.render_template("research_history.html"),
        twitter_page=lambda: deps.render_template("twitter.html"),
        wechat_page=lambda: deps.render_template("wechat.html"),
        settings_page=lambda: deps.render_template("settings.html"),
        add_stock_page=lambda: deps.render_template("add_stock.html"),
    )
