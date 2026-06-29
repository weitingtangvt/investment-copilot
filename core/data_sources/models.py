from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class DataSourceResult:
    success: bool
    data: Any = None
    error: str = ""
    warning: str = ""
    source_name: str = ""
    latency_ms: int = 0
    fetched_at: str = ""
    cache_hit: bool = False
    degraded: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataSourceHealthSnapshot:
    source_name: str
    status: str = "unknown"
    error: str = ""
    warning: str = ""
    latency_ms: int = 0
    checked_at: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
