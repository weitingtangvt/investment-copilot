from __future__ import annotations

from flask import Blueprint

from .api import register_decision_review_api


def create_decision_review_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("decision_review_module", __name__)
    register_decision_review_api(bp, service, auth_guard=auth_guard)
    return bp
