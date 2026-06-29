from __future__ import annotations

from flask import Flask


def register_app_shell_module(app: Flask, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    app.add_url_rule("/", endpoint="index_page", view_func=auth_guard(service.index_page))
    app.add_url_rule("/__health", endpoint="health_check", view_func=service.health_check)
    app.add_url_rule("/__routes", endpoint="route_list", view_func=service.route_list)
    app.add_url_rule("/healthz", endpoint="healthz", view_func=service.healthz)
