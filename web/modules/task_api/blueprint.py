from __future__ import annotations

from flask import Blueprint

from .api import register_task_api


def create_task_api_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("task_api_module", __name__)
    register_task_api(bp, service, auth_guard=auth_guard)
    return bp
