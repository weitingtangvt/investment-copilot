from __future__ import annotations

from flask import Blueprint

from .api import register_watchlist_api
from .pages import register_watchlist_pages


def create_watchlist_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("watchlist_module", __name__)
    register_watchlist_pages(bp, service, auth_guard=auth_guard)
    register_watchlist_api(bp, service, auth_guard=auth_guard)
    return bp
