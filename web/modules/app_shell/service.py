from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify

from web.services.domain_services import AppShellService


@dataclass
class AppShellModuleDeps:
    render_template: Callable[..., Any]
    get_stocks_with_research_status: Callable[[], list[dict[str, Any]]]
    get_storage: Callable[[], Any]
    resolve_effective_weekly_review: Callable[[], tuple[Any, Any, Any, Any]]
    get_app: Callable[[], Any]
    get_pid: Callable[[], int]
    get_cwd: Callable[[], str]
    project_root: str
    web_root: str


def build_app_shell_module_service(deps: AppShellModuleDeps) -> AppShellService:
    def index_page():
        homepage_stocks = deps.get_stocks_with_research_status()
        storage = deps.get_storage()
        portfolio = storage.get_portfolio_playbook()
        _, _, initial_weekly, _ = deps.resolve_effective_weekly_review()
        initial_prefs = storage.get_user_preferences() or {}
        return deps.render_template(
            "index.html",
            stocks=homepage_stocks,
            portfolio=portfolio,
            initial_weekly=initial_weekly,
            initial_prefs=initial_prefs,
        )

    def health_check():
        return jsonify({"ok": True, "service": "investment-assistant-web"})

    def route_list():
        app = deps.get_app()
        rules = sorted({rule.rule for rule in app.url_map.iter_rules()})
        return jsonify({"count": len(rules), "routes": rules})

    def healthz():
        app = deps.get_app()
        loader = getattr(app, "jinja_loader", None)
        searchpath = list(getattr(loader, "searchpath", None) or [])
        return jsonify(
            {
                "ok": True,
                "pid": deps.get_pid(),
                "cwd": deps.get_cwd(),
                "project_root": deps.project_root,
                "web_root": deps.web_root,
                "template_folder": str(app.template_folder or ""),
                "template_searchpath": searchpath,
            }
        )

    return AppShellService(
        index_page=index_page,
        health_check=health_check,
        route_list=route_list,
        healthz=healthz,
    )
