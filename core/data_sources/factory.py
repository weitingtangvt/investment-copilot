from __future__ import annotations

from .base import DataSourceRegistry
from .news import AKShareChineseNewsDataSource, GoogleRSSNewsDataSource, NewsAPIDataSource, TavilyNewsDataSource


def build_data_source_registry() -> DataSourceRegistry:
    registry = DataSourceRegistry()
    registry.register(AKShareChineseNewsDataSource())
    registry.register(GoogleRSSNewsDataSource())
    registry.register(NewsAPIDataSource())
    registry.register(TavilyNewsDataSource())
    return registry
