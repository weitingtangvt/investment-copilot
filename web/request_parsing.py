from __future__ import annotations

from typing import Any, Callable

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType


def json_object_error(
    message: str = "request body must be a JSON object",
    *,
    status: int = 400,
):
    return jsonify({"error": message}), status


def load_json_object(
    get_json: Callable[[], Any] | None = None,
    *,
    allow_empty: bool = True,
    invalid_json_message: str = "request body is not valid JSON",
    invalid_type_message: str = "request body must be a JSON object",
):
    try:
        raw_body = request.get_data(cache=True) or b""
    except Exception:
        raw_body = b""

    has_body = bool(raw_body.strip())

    if has_body:
        try:
            data = request.get_json(silent=False)
        except (BadRequest, UnsupportedMediaType):
            return None, json_object_error(invalid_json_message)
    else:
        try:
            data = get_json() if get_json is not None else None
        except (BadRequest, UnsupportedMediaType):
            return None, json_object_error(invalid_json_message)
        except Exception:
            return None, json_object_error(invalid_json_message)

    if data is None:
        if has_body:
            return None, json_object_error(invalid_type_message)
        return ({}, None) if allow_empty else (None, json_object_error(invalid_type_message))
    if not isinstance(data, dict):
        return None, json_object_error(invalid_type_message)
    return dict(data), None
