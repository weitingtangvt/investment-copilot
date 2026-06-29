from __future__ import annotations

from flask import Blueprint

from .api import register_broker_import_api


def create_broker_import_module(service, auth_guard=None) -> Blueprint:
    bp = Blueprint("broker_import_module", __name__)
    register_broker_import_api(bp, service, auth_guard=auth_guard)
    return bp

