"""Application factory entrypoint."""

from __future__ import annotations

from flask import Flask


def create_app() -> Flask:
    from web.app import create_app as _create_app

    return _create_app()
