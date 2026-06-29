from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


@dataclass
class ZsxqHelperDeps:
    get_config: Any
    get_base_dir: Any
    safe_filename_part: Any
    get_ima_export_path: Any
    get_ima_export_dir: Any


def build_zsxq_module_helpers(deps: ZsxqHelperDeps) -> Any:
    return SimpleNamespace(
        get_zsxq_client=lambda: None,
        get_zsxq_paths=lambda group_id=None: {},
        resolve_existing_zsxq_file=lambda *args, **kwargs: None,
        resolve_zsxq_daily_snapshot_path=lambda *args, **kwargs: Path(deps.get_base_dir()) / "community_disabled.md",
        find_existing_zsxq_daily_snapshot=lambda *args, **kwargs: None,
    )
