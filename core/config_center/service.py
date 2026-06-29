from __future__ import annotations

from copy import deepcopy
from typing import Any

from .defaults import get_default_config
from .models import ConfigSnapshot, ConfigStorageAdapter
from .validators import validate_config_patch, validate_full_config


class ConfigCenterService:
    def __init__(
        self,
        storage: ConfigStorageAdapter | None = None,
        initial_config: dict[str, Any] | None = None,
    ) -> None:
        self._storage = storage
        self._version = 0
        self._config = get_default_config()
        self._storage_raw_config: dict[str, Any] = {}

        if self._storage is not None:
            self._reload_from_storage(increment_version=False)
        if initial_config:
            self.update_config(initial_config, persist=False)

    def get_config(self) -> dict[str, Any]:
        return deepcopy(self._config)

    def get_value(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_snapshot(self) -> ConfigSnapshot:
        source = "storage_compatible" if self._storage is not None else "memory"
        return ConfigSnapshot(config=self.get_config(), version=self._version, source=source)

    def update_config(self, patch: dict[str, Any], persist: bool = True) -> ConfigSnapshot:
        normalized_patch = validate_config_patch(patch)
        merged = dict(self._config)
        merged.update(normalized_patch)
        self._config = validate_full_config(merged)
        self._version += 1
        if persist:
            self._persist()
        return self.get_snapshot()

    def reset_to_defaults(self, persist: bool = True) -> ConfigSnapshot:
        self._config = get_default_config()
        self._version += 1
        if persist:
            self._persist()
        return self.get_snapshot()

    def reload_from_storage(self) -> ConfigSnapshot:
        self._reload_from_storage(increment_version=True)
        return self.get_snapshot()

    def _reload_from_storage(self, increment_version: bool) -> None:
        if self._storage is None:
            return
        loaded: dict[str, Any] = {}
        try:
            raw = self._storage.get_config()
            if isinstance(raw, dict):
                loaded = raw
        except Exception:
            loaded = {}
        self._storage_raw_config = dict(loaded)
        allowed_keys = set(get_default_config().keys())
        managed_config = {key: value for key, value in loaded.items() if key in allowed_keys}
        self._config = validate_full_config(managed_config)
        if increment_version:
            self._version += 1

    def _persist(self) -> None:
        if self._storage is None:
            return
        merged = dict(self._storage_raw_config)
        merged.update(self._config)
        self._storage.save_config(merged)
        self._storage_raw_config = dict(merged)
