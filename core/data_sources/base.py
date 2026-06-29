from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict


class BaseDataSource(ABC):
    source_name = "base"

    @abstractmethod
    def fetch(self, **kwargs):
        raise NotImplementedError


class DataSourceRegistry:
    def __init__(self):
        self._sources: Dict[str, BaseDataSource] = {}

    def register(self, source: BaseDataSource) -> None:
        self._sources[source.source_name] = source

    def get(self, source_name: str):
        return self._sources.get(str(source_name or "").strip())

    def all(self):
        return dict(self._sources)
