from __future__ import annotations

from typing import Any, Callable

from flask import Response, request


def build_options_handler() -> Callable[[], Response | None]:
    def handle_options() -> Response | None:
        if request.method == "OPTIONS":
            resp = Response("", status=200)
            resp.headers["Allow"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin") or "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Max-Age"] = "86400"
            return resp
        return None

    return handle_options


def build_cors_after_request_handler() -> Callable[[Any], Any]:
    def add_cors_headers(response: Any) -> Any:
        origin = request.headers.get("Origin")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
        return response

    return add_cors_headers
