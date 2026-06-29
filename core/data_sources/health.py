from __future__ import annotations

from .models import DataSourceHealthSnapshot, DataSourceResult


def build_health_snapshot(result: DataSourceResult) -> DataSourceHealthSnapshot:
    status = "ok" if result.success else "error"
    return DataSourceHealthSnapshot(
        source_name=result.source_name,
        status=status,
        error=result.error,
        warning=result.warning,
        latency_ms=result.latency_ms,
        checked_at=result.fetched_at,
        meta=dict(result.meta or {}),
    )
