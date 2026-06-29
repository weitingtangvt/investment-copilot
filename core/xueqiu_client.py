"""Sanitized compatibility stub for removed market-feed integration."""

from __future__ import annotations

from typing import Any


class XueqiuClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("Market-feed integration is not included in the sanitized copy.")
