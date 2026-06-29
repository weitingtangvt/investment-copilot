from __future__ import annotations

from flask import Blueprint

from .api import register_weekly_review_api
from .pages import register_weekly_review_pages


def create_weekly_review_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("weekly_review_module", __name__)
    register_weekly_review_pages(bp, service, auth_guard=auth_guard)
    register_weekly_review_api(bp, service, auth_guard=auth_guard)
    return bp
