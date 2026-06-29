from __future__ import annotations

from flask import Blueprint

from .api import register_llm_config_api


def create_llm_config_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("llm_config_bp", __name__)
    register_llm_config_api(bp, service, auth_guard=auth_guard)
    return bp
