"""Shared LLM cache with normalized cache key and TTL policy."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


DEFAULT_TTL_SECONDS = 2 * 60 * 60


@dataclass
class CacheItem:
    value: Any
    expires_at: float


class LLMCache:
    """Thread-safe in-memory cache for LLM responses."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, max_entries: int = 800):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_entries = max(100, int(max_entries))
        self._lock = threading.Lock()
        self._store: Dict[str, CacheItem] = {}

    def _normalize_payload(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return " ".join(payload.split())
        try:
            packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return " ".join(packed.split())
        except Exception:
            return " ".join(str(payload).split())

    def _window_bucket(self, now: Optional[float] = None) -> int:
        ts = now if now is not None else time.time()
        return int(ts // self.ttl_seconds)

    def build_key(
        self,
        provider: str,
        model: str,
        method: str,
        payload: Any,
        time_window: Optional[int] = None,
    ) -> str:
        window = self._window_bucket() if time_window is None else int(time_window)
        normalized = self._normalize_payload(payload)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"{provider}|{model}|{method}|{digest}|{window}"

    def get(self, key: str) -> Tuple[bool, Optional[Any]]:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return False, None
            if item.expires_at <= now:
                self._store.pop(key, None)
                return False, None
            return True, copy.deepcopy(item.value)

    def set(self, key: str, value: Any) -> None:
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            if len(self._store) >= self.max_entries:
                self._evict_half()
            self._store[key] = CacheItem(value=copy.deepcopy(value), expires_at=expires_at)

    def _evict_half(self) -> None:
        if not self._store:
            return
        sorted_items = sorted(self._store.items(), key=lambda kv: kv[1].expires_at)
        cutoff = max(1, len(sorted_items) // 2)
        for key, _ in sorted_items[:cutoff]:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

