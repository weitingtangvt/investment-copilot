from __future__ import annotations

from flask import Blueprint

from .api import register_system_api_routes


def create_system_api_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("system_api_module", __name__)
    register_system_api_routes(bp, service, auth_guard=auth_guard)
    return bp
