"""Authentication service for Flask routes."""

from __future__ import annotations

import functools
import hashlib
from typing import Any, Callable, Dict

from flask import Response, request, session


class AuthService:
    def __init__(self, storage: Any):
        self.storage = storage

    def get_auth_config(self) -> Dict[str, Any]:
        config = self.storage.get_config()
        return {
            "enabled": config.get("auth_enabled", False),
            "password_hash": config.get("auth_password_hash", None),
        }

    def check_auth(self, password: str) -> bool:
        auth_config = self.get_auth_config()
        if not auth_config["enabled"]:
            return True
        if not auth_config["password_hash"]:
            return True
        input_hash = hashlib.sha256((password or "").encode()).hexdigest()
        return input_hash == auth_config["password_hash"]

    def requires_auth(self, func: Callable) -> Callable:
        @functools.wraps(func)
        def decorated(*args, **kwargs):
            auth_config = self.get_auth_config()
            if not auth_config["enabled"]:
                return func(*args, **kwargs)
            if session.get("authenticated"):
                return func(*args, **kwargs)
            auth = request.authorization
            if auth and self.check_auth(auth.password):
                session["authenticated"] = True
                return func(*args, **kwargs)
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Investment Assistant"'},
            )

        return decorated

    def can_setup_auth(self) -> bool:
        """Allow setup when auth is not initialized, or caller is already authenticated."""
        auth_config = self.get_auth_config()
        has_existing_password = bool(auth_config.get("password_hash"))
        if not has_existing_password:
            return True
        if session.get("authenticated"):
            return True
        auth = request.authorization
        if auth and self.check_auth(auth.password):
            session["authenticated"] = True
            return True
        return False

    def save_auth(self, password: str, enable: bool) -> Dict[str, Any]:
        config = self.storage.get_config()
        if password:
            config["auth_password_hash"] = hashlib.sha256(password.encode()).hexdigest()
        config["auth_enabled"] = bool(enable)
        self.storage.save_config(config)
        return config

