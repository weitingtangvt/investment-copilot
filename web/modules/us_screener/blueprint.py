from __future__ import annotations

from flask import Blueprint

from .api import register_us_screener_api
from .pages import register_us_screener_pages


def create_us_screener_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("us_screener_module", __name__)
    register_us_screener_pages(bp, service, auth_guard=auth_guard)
    register_us_screener_api(bp, service, auth_guard=auth_guard)
    return bp
