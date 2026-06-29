from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ConfigStorageAdapter(Protocol):
    def get_config(self) -> dict[str, Any]:
        ...

    def save_config(self, config: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class ConfigSnapshot:
    config: dict[str, Any]
    version: int
    source: str
