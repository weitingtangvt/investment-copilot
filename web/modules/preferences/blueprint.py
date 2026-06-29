from __future__ import annotations

from flask import Blueprint

from .api import register_preferences_api
from .pages import register_preferences_pages


def create_preferences_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("preferences_module", __name__)
    register_preferences_pages(bp, service, auth_guard=auth_guard)
    register_preferences_api(bp, service, auth_guard=auth_guard)
    return bp
