from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def safe_filename_part(value: Any, default: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._")
    return text[:48] or default


def ima_sync_key_zsxq_daily(group_id: str, snapshot_date: str) -> str:
    return f"zsxq_daily:{str(group_id or '').strip()}:{str(snapshot_date or '').strip()}"


def ima_sync_key_weekly_review(week_id: str) -> str:
    return f"weekly_review:{str(week_id or '').strip()}"


def ima_sync_key_watchlist(week_id: str) -> str:
    return f"watchlist_snapshot:{str(week_id or '').strip()}"


def serialize_ima_sync_status(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    return {
        "success": bool(record.get("success")),
        "status": str(record.get("status") or "").strip(),
        "snapshot_type": str(record.get("snapshot_type") or "").strip(),
        "sync_key": str(record.get("sync_key") or "").strip(),
        "local_file": str(record.get("local_file") or "").strip(),
        "title": str(record.get("title") or "").strip(),
        "knowledge_base_id": str(record.get("knowledge_base_id") or "").strip(),
        "knowledge_base_name": str(record.get("knowledge_base_name") or "").strip(),
        "media_id": str(record.get("media_id") or "").strip(),
        "synced_at": str(record.get("synced_at") or "").strip(),
        "error": str(record.get("error") or "").strip(),
        "attempted": bool(record.get("attempted")),
        "retry_available": bool(record.get("retry_available")),
    }


def write_markdown_snapshot(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def format_zsxq_topic_time(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        if "T" in text:
            date_part, time_part = text.split("T", 1)
            time_part = time_part.split(".")[0].split("+")[0]
            hour_min = ":".join(time_part.split(":")[:2])
            return f"{date_part} {hour_min}"
    except Exception:
        return text
    return text


def markdown_from_zsxq_daily_snapshot(group_id: str, group_name: str, topics: List[Dict[str, Any]]) -> str:
    rows: List[Dict[str, str]] = []
    for topic in topics or []:
        talk = topic.get("talk") or {}
        question = topic.get("question") or {}
        source = talk or question
        owner = source.get("owner") or topic.get("owner") or {}
        rows.append(
            {
                "author": str(owner.get("name") or "Text").strip() or "Text",
                "time": format_zsxq_topic_time(topic.get("create_time") or ""),
                "text": str(source.get("text") or "").strip() or "Text",
            }
        )

    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    lines: List[str] = [
        f"# {snapshot_date} Text",
        "",
        f"- Text: {group_name or group_id}",
        f"- Text ID: {group_id}",
        f"- Text: {datetime.now().isoformat(timespec='seconds')}",
        f"- Text: {len(rows)}",
        "",
    ]
    if not rows:
        lines.extend(["_Text. _", ""])
        return "\n".join(lines).rstrip() + "\n"

    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row['author']}",
                "",
                f"- Text: {row['time'] or 'Text'}",
                "",
                row["text"],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def env_flag(name: str, default: bool = False) -> bool:
    return to_bool(os.environ.get(name), default)


def to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def json_safe(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        if isinstance(obj, dict):
            return {str(k): json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [json_safe(v) for v in obj]
        if hasattr(obj, "isoformat"):
            try:
                return obj.isoformat()
            except Exception:
                pass
        return str(obj)


def resolve_secret_key(logger: logging.Logger) -> str:
    key = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if key:
        return key
    env_name = (os.environ.get("INVESTMENT_ASSISTANT_ENV") or "development").strip().lower()
    if env_name in {"dev", "development", "test"}:
        logger.warning("FLASK_SECRET_KEY is not set; using insecure development key.")
        return "dev-only-secret-key-change-me"
    raise RuntimeError("FLASK_SECRET_KEY is required in non-dev environments.")


def is_llm_failure_text(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    s = text.strip()
    return s.startswith("TextFailed") or s.lower().startswith("call failed")


def is_timeout_failure_text(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "request timed out",
            "timed out",
            "timeout",
            "error code: 504",
            "504",
            "gateway timeout",
            "Text",
        )
    )


def chat_with_retry(
    runtime_client: Any,
    prompt: str,
    *,
    retries: int = 2,
    request_name: str = "chat",
    force_refresh: bool = False,
    logger: Optional[logging.Logger] = None,
) -> str:
    last_text = ""
    for i in range(retries + 1):
        try:
            text = runtime_client.chat(prompt, force_refresh=force_refresh)
        except TypeError:
            text = runtime_client.chat(prompt)
        if not is_llm_failure_text(text):
            return text
        last_text = str(text)
        if i < retries and is_timeout_failure_text(last_text):
            if logger is not None:
                logger.warning("%s timed out, retry %s/%s", request_name, i + 1, retries)
            time.sleep(0.4 * (i + 1))
            continue
        break
    return last_text


def seconds_until(target: Optional[datetime]) -> int:
    if not target:
        return 0
    delta = target - datetime.now()
    return max(0, int(delta.total_seconds()))


def to_change_pct(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return round(parsed, 2)


def build_revisit_rule(window: str, label: str, change_pct: Optional[float], threshold: float) -> Optional[Dict[str, Any]]:
    if change_pct is None or abs(change_pct) < threshold:
        return None
    direction = "up" if change_pct >= 0 else "down"
    return {
        "key": f"{window}_{direction}",
        "window": window,
        "label": label,
        "direction": direction,
        "change_pct": round(change_pct, 2),
        "threshold": round(float(threshold), 2),
        "priority": {"since_added": 0, "monthly": 1, "weekly": 2}.get(window, 9),
    }


def fmt_money(value: Any) -> str:
    try:
        if value in (None, ""):
            return ""
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_number(value: Any) -> str:
    try:
        if value in (None, ""):
            return ""
        return f"{float(value):,.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)
