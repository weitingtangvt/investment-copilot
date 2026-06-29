from __future__ import annotations

from flask import Blueprint

from .api import register_research_api


def create_research_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("research_module", __name__)
    register_research_api(bp, service, auth_guard=auth_guard)
    return bp
