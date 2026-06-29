from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class LLMCapabilitySet:
    supports_web_search: bool = False
    supports_streaming: bool = False
    supports_structured_json: bool = True

    def as_dict(self) -> Dict[str, bool]:
        return {
            "supports_web_search": self.supports_web_search,
            "supports_streaming": self.supports_streaming,
            "supports_structured_json": self.supports_structured_json,
        }


@dataclass(frozen=True)
class LLMRequest:
    prompt: str
    system: str = ""
    max_tokens: int = 4096
    timeout_sec: float | None = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    degraded: bool = False
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
