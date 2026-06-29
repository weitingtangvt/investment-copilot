from __future__ import annotations

from flask import Blueprint

from .api import register_factor_analysis_api


def create_factor_analysis_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("factor_analysis_module", __name__)
    register_factor_analysis_api(bp, service, auth_guard=auth_guard)
    return bp
