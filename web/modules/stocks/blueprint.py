from __future__ import annotations

from flask import Blueprint

from .api import register_stocks_api
from .pages import register_stocks_pages


def create_stocks_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("stocks_module", __name__)
    register_stocks_pages(bp, service, auth_guard=auth_guard)
    register_stocks_api(bp, service, auth_guard=auth_guard)
    return bp
