from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .storage import Storage
from .weekly_review import get_week_id
from .zsxq_search import parse_zsxq_commentary_records, search_zsxq_records


HEADING_RE = re.compile(r"^###\s+\d+\.\s+(?P<author>.*?)\s+-\s+(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*$")
GROUP_ID_RE = re.compile(r"^(?P<group_id>\d+)_")
HTML_TAG_LINE_RE = re.compile(r"^<[^>]+>$")
NUMBERING_ONLY_RE = re.compile(r"^(?:\d+\s*[\.\)), :: ]?|[①②③④⑤⑥⑦⑧⑨⑩]+|[Text]+[, .．])$")
SHORT_AUTHOR_LIKE_RE = re.compile(r"^[A-Za-z\u4e00-\u9fff]{2,8}$")
PREVIEW_LIMIT = 280
ALLOWED_COMMENTARY_GROUP_IDS = {"28512858211281", "48418411254128", "48841224485848"}


@dataclass
class CommentaryWindow:
    week_id: str
    start_date: date
    end_date: date
    window_type: str = "iso_week"

    def to_dict(self) -> Dict[str, str]:
        return {
            "week_id": self.week_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "window_type": self.window_type,
        }


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_keyword(value: Any) -> str:
    return re.sub(r"\s+", " ", _normalize_text(value))


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    items: List[str] = []
    for raw in values:
        value = _normalize_keyword(raw)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(value)
    return items


def _parse_week_id(week_id: Optional[str]) -> CommentaryWindow:
    value = _normalize_text(week_id) or get_week_id()
    match = re.match(r"^(?P<year>\d{4})-W(?P<week>\d{2})$", value)
    if not match:
        value = get_week_id()
        match = re.match(r"^(?P<year>\d{4})-W(?P<week>\d{2})$", value)
    year = int(match.group("year"))
    week = int(match.group("week"))
    start = date.fromisocalendar(year, week, 1)
    end = start + timedelta(days=6)
    return CommentaryWindow(week_id=value, start_date=start, end_date=end, window_type="iso_week")


def _rolling_days_window(days: int = 7) -> CommentaryWindow:
    safe_days = max(1, int(days or 7))
    end = date.today()
    start = end - timedelta(days=safe_days - 1)
    return CommentaryWindow(
        week_id=f"rolling-{safe_days}d",
        start_date=start,
        end_date=end,
        window_type="rolling_days",
    )


def _preview_text(lines: List[str]) -> str:
    text = " ".join(_normalize_text(line) for line in lines if _normalize_text(line))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= PREVIEW_LIMIT:
        return text
    return text[: PREVIEW_LIMIT - 1].rstrip() + "..."


def _commentary_cache_paths(base_dir: Path) -> List[Path]:
    cache_dir = base_dir / "zsxq_cache"
    if not cache_dir.exists():
        return []
    allowed_files: List[Path] = []
    fallback_files: List[Path] = []
    for path in sorted(cache_dir.glob("*.md")):
        name = path.name.lower()
        group_id_match = GROUP_ID_RE.match(path.name)
        group_id = group_id_match.group("group_id") if group_id_match else ""
        if name == "all_dynamics.md":
            continue
        if not re.match(r"^\d+_", path.name):
            continue
        if not path.name.endswith("_Text.md"):
            continue
        if "_20" in name:
            continue
        target = allowed_files if (not ALLOWED_COMMENTARY_GROUP_IDS or group_id in ALLOWED_COMMENTARY_GROUP_IDS) else fallback_files
        target.append(path)
    return allowed_files or fallback_files


def normalize_keyword_list(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = re.split(r"[,, ;; , \n\r]+", value)
        return _dedupe_preserve_order(parts)
    if isinstance(value, (list, tuple, set)):
        return _dedupe_preserve_order(list(value))
    return []


def _registry_path(base_dir: Path) -> Path:
    return base_dir / "stock_commentary_keywords.json"


def _seed_registry(storage: Storage, path: Path) -> None:
    if path.exists():
        return

    payload: Dict[str, Dict[str, Any]] = {}

    for stock in storage.list_stocks():
        stock_id = _normalize_text(stock.get("stock_id"))
        if not stock_id:
            continue
        payload[stock_id] = {
            "stock_id": stock_id,
            "stock_name": _normalize_text(stock.get("stock_name")) or stock_id,
            "ticker": _normalize_text(stock.get("ticker")),
            "keywords": [],
        }

    watchlist = storage.get_watchlist()
    for candidate in watchlist.get("candidates", []):
        stock_id = _normalize_text(candidate.get("stock_id"))
        if not stock_id:
            continue
        payload.setdefault(
            stock_id,
            {
                "stock_id": stock_id,
                "stock_name": _normalize_text(candidate.get("stock_name")) or stock_id,
                "ticker": _normalize_text(candidate.get("ticker")),
                "keywords": [],
            },
        )
        if not payload[stock_id].get("stock_name"):
            payload[stock_id]["stock_name"] = _normalize_text(candidate.get("stock_name")) or stock_id
        if not payload[stock_id].get("ticker"):
            payload[stock_id]["ticker"] = _normalize_text(candidate.get("ticker"))

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_registry(storage: Storage) -> Dict[str, Dict[str, Any]]:
    path = _registry_path(storage.base_dir)
    _seed_registry(storage, path)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if isinstance(raw, list):
        entries = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            stock_id = _normalize_text(item.get("stock_id"))
            if stock_id:
                entries[stock_id] = item
        raw = entries

    if not isinstance(raw, dict):
        return {}

    registry: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        stock_id = _normalize_text((value or {}).get("stock_id") if isinstance(value, dict) else key) or _normalize_text(key)
        if not stock_id:
            continue
        if isinstance(value, dict):
            registry[stock_id] = {
                "stock_id": stock_id,
                "stock_name": _normalize_text(value.get("stock_name")),
                "ticker": _normalize_text(value.get("ticker")),
                "keywords": _dedupe_preserve_order(value.get("keywords") or []),
            }
        else:
            registry[stock_id] = {
                "stock_id": stock_id,
                "stock_name": "",
                "ticker": "",
                "keywords": [],
            }
    return registry


def _save_registry(storage: Storage, registry: Dict[str, Dict[str, Any]]) -> None:
    path = _registry_path(storage.base_dir)
    safe: Dict[str, Dict[str, Any]] = {}
    for key, value in (registry or {}).items():
        stock_id = _normalize_text((value or {}).get("stock_id") if isinstance(value, dict) else key) or _normalize_text(key)
        if not stock_id:
            continue
        safe[stock_id] = {
            "stock_id": stock_id,
            "stock_name": _normalize_text((value or {}).get("stock_name")),
            "ticker": _normalize_text((value or {}).get("ticker")),
            "keywords": _dedupe_preserve_order((value or {}).get("keywords") or []),
        }
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_registry_entries(
    entries: List[Dict[str, Any]],
    *,
    stock_id: str = "",
    ticker: str = "",
    stock_name: str = "",
) -> Dict[str, Any]:
    merged = {
        "stock_id": _normalize_text(stock_id),
        "stock_name": _normalize_text(stock_name),
        "ticker": _normalize_text(ticker),
        "keywords": [],
    }
    all_keywords: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_stock_id = _normalize_text(entry.get("stock_id"))
        entry_stock_name = _normalize_text(entry.get("stock_name"))
        entry_ticker = _normalize_text(entry.get("ticker"))
        if not merged["stock_id"]:
            merged["stock_id"] = entry_stock_id
        if not merged["ticker"]:
            merged["ticker"] = entry_ticker
        merged["stock_name"] = _prefer_stock_name(merged["stock_name"], entry_stock_name, merged["ticker"] or entry_ticker)
        all_keywords.extend(entry.get("keywords") or [])
    if not merged["stock_id"]:
        merged["stock_id"] = _normalize_text(stock_id) or _normalize_text(ticker)
    if not merged["stock_name"]:
        merged["stock_name"] = _normalize_text(stock_name) or merged["stock_id"]
    merged["keywords"] = _dedupe_preserve_order(all_keywords)
    return merged


def _matching_registry_keys(registry: Dict[str, Dict[str, Any]], stock_id: str, ticker: str = "") -> List[str]:
    stock_id_text = _normalize_text(stock_id)
    ticker_text = _normalize_text(ticker)
    exact_keys: List[str] = []
    for candidate_key in (stock_id_text, stock_id_text.upper(), ticker_text, ticker_text.upper()):
        if candidate_key and candidate_key in registry and candidate_key not in exact_keys:
            exact_keys.append(candidate_key)

    stock_fold = stock_id_text.casefold()
    ticker_fold = ticker_text.casefold()
    matched_keys = list(exact_keys)
    for key, value in registry.items():
        if key in matched_keys or not isinstance(value, dict):
            continue
        entry_stock_id = _normalize_text(value.get("stock_id"))
        entry_ticker = _normalize_text(value.get("ticker"))
        if (stock_fold and entry_stock_id.casefold() == stock_fold) or (ticker_fold and entry_ticker.casefold() == ticker_fold):
            matched_keys.append(key)
    return matched_keys


def _lookup_registry_entry(registry: Dict[str, Dict[str, Any]], stock_id: str, ticker: str = "", stock_name: str = "") -> Dict[str, Any]:
    matched_keys = _matching_registry_keys(registry, stock_id, ticker)
    if not matched_keys:
        return {}
    entries = [dict(registry[key] or {}) for key in matched_keys if key in registry]
    return _merge_registry_entries(entries, stock_id=stock_id, ticker=ticker, stock_name=stock_name)


def _build_keywords(record: Dict[str, Any], registry_entry: Optional[Dict[str, Any]]) -> List[str]:
    registry_entry = registry_entry or {}
    keywords = _dedupe_preserve_order(
        list(registry_entry.get("keywords") or [])
        + [
            registry_entry.get("stock_name"),
            registry_entry.get("stock_id"),
            registry_entry.get("ticker"),
            record.get("stock_name"),
            record.get("stock_id"),
            record.get("ticker"),
        ]
    )
    return keywords


ASCII_KEYWORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _keyword_in_text(keyword: str, haystack: str) -> bool:
    keyword_text = _normalize_keyword(keyword)
    haystack_text = _normalize_text(haystack)
    if not keyword_text or not haystack_text:
        return False

    if ASCII_KEYWORD_RE.fullmatch(keyword_text):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(keyword_text)}(?![A-Za-z0-9])", re.IGNORECASE)
        return bool(pattern.search(haystack_text))

    return keyword_text.casefold() in haystack_text.casefold()


def _canonical_pool_key(stock_id: str, ticker: str = "") -> str:
    ticker_text = _normalize_text(ticker).upper()
    if ticker_text:
        return f"ticker:{ticker_text}"
    return f"stock:{_normalize_text(stock_id).casefold()}"


def _prefer_stock_name(current_name: str, new_name: str, ticker: str = "") -> str:
    current_text = _normalize_text(current_name)
    new_text = _normalize_text(new_name)
    ticker_text = _normalize_text(ticker).upper()
    if not current_text:
        return new_text
    if not new_text:
        return current_text
    current_is_code = current_text.upper() == ticker_text and bool(ticker_text)
    new_is_code = new_text.upper() == ticker_text and bool(ticker_text)
    if current_is_code and not new_is_code:
        return new_text
    if len(new_text) > len(current_text) and not new_is_code:
        return new_text
    return current_text


def get_keyword_pool(storage: Storage) -> Dict[str, Any]:
    registry = _load_registry(storage)
    rows: Dict[str, Dict[str, Any]] = {}

    def merge_record(source: str, record: Dict[str, Any]) -> None:
        stock_id = _normalize_text(record.get("stock_id"))
        stock_name = _normalize_text(record.get("stock_name"))
        ticker = _normalize_text(record.get("ticker"))
        if not stock_id and not ticker:
            return

        registry_entry = _lookup_registry_entry(registry, stock_id, ticker, stock_name=stock_name)
        key = _canonical_pool_key(stock_id, ticker or _normalize_text(registry_entry.get("ticker")))
        row = rows.setdefault(
            key,
            {
                "pool_key": key,
                "stock_id": stock_id or _normalize_text(registry_entry.get("stock_id")) or ticker,
                "stock_name": stock_name or _normalize_text(registry_entry.get("stock_name")) or stock_id or ticker,
                "ticker": ticker or _normalize_text(registry_entry.get("ticker")),
                "sources": [],
                "keywords": _dedupe_preserve_order(registry_entry.get("keywords") or []),
                "updated_at": _normalize_text(record.get("updated_at")),
            },
        )
        row["stock_id"] = row["stock_id"] or stock_id or _normalize_text(registry_entry.get("stock_id")) or ticker
        row["ticker"] = row["ticker"] or ticker or _normalize_text(registry_entry.get("ticker"))
        row["stock_name"] = _prefer_stock_name(row.get("stock_name", ""), stock_name or _normalize_text(registry_entry.get("stock_name")), row.get("ticker", ""))
        if source not in row["sources"]:
            row["sources"].append(source)
        updated_at = _normalize_text(record.get("updated_at"))
        if updated_at and updated_at > _normalize_text(row.get("updated_at")):
            row["updated_at"] = updated_at

    for item in storage.list_stocks():
        merge_record("playbook", item)
    for item in storage.get_watchlist().get("candidates", []):
        merge_record("watchlist", item)
    for item in storage.list_weekly_review_stocks():
        merge_record("weekly_review", item)

    items = sorted(
        rows.values(),
        key=lambda item: (
            0 if item.get("keywords") else 1,
            _normalize_text(item.get("stock_name") or item.get("stock_id")).casefold(),
        ),
    )

    return {
        "success": True,
        "items": items,
        "counts": {
            "total": len(items),
            "with_keywords": sum(1 for item in items if item.get("keywords")),
            "without_keywords": sum(1 for item in items if not item.get("keywords")),
        },
    }


def save_keyword_entry(
    storage: Storage,
    stock_id: str,
    stock_name: str = "",
    ticker: str = "",
    keywords: Optional[List[str]] = None,
) -> Dict[str, Any]:
    registry = _load_registry(storage)
    entry = _lookup_registry_entry(registry, stock_id, ticker, stock_name=stock_name)
    preferred_key = _normalize_text(stock_id) or _normalize_text(entry.get("stock_id")) or _normalize_text(ticker)
    if not preferred_key:
        raise ValueError("stock_id or ticker is required")

    for alias_key in _matching_registry_keys(registry, stock_id, ticker):
        if alias_key != preferred_key:
            registry.pop(alias_key, None)

    registry[preferred_key] = {
        "stock_id": preferred_key,
        "stock_name": _normalize_text(stock_name) or _normalize_text(entry.get("stock_name")) or preferred_key,
        "ticker": _normalize_text(ticker) or _normalize_text(entry.get("ticker")),
        "keywords": _dedupe_preserve_order(keywords or []),
    }
    _save_registry(storage, registry)
    return registry[preferred_key]


def _is_noise_header_line(line: str, raw_index: int) -> bool:
    text = _normalize_text(line)
    if not text:
        return True
    if text == "Text":
        return True
    if HTML_TAG_LINE_RE.fullmatch(text):
        return True
    if NUMBERING_ONLY_RE.fullmatch(text):
        return True
    if raw_index <= 1 and text.endswith((": ", ":")) and len(text) <= 10:
        return True
    if raw_index == 0 and SHORT_AUTHOR_LIKE_RE.fullmatch(text):
        return True
    return False


def _extract_header_lines(body_lines: List[str]) -> List[str]:
    non_empty_body_lines = [_normalize_text(line) for line in body_lines if _normalize_text(line)]
    header_lines: List[str] = []
    for raw_index, line in enumerate(non_empty_body_lines):
        if _is_noise_header_line(line, raw_index):
            continue
        header_lines.append(line)
        if len(header_lines) >= 2:
            break
    if header_lines:
        return header_lines
    return non_empty_body_lines[:2]


def _parse_commentary_file(path: Path) -> List[Dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="ignore")

    records: List[Dict[str, Any]] = []
    current_lines: List[str] = []
    in_block = False
    block_index = 0
    group_id_match = GROUP_ID_RE.match(path.name)
    group_id = group_id_match.group("group_id") if group_id_match else ""

    def flush() -> None:
        nonlocal current_lines, in_block, block_index
        if not current_lines:
            return
        block_index += 1
        lines = [line.rstrip() for line in current_lines]
        heading = _normalize_text(lines[0]) if lines else ""
        heading_match = HEADING_RE.match(heading)
        published_at = None
        if heading_match:
            try:
                published_at = datetime.strptime(heading_match.group("timestamp"), "%Y-%m-%d %H:%M")
            except ValueError:
                published_at = None
        body_lines = lines[1:]
        non_empty_body_lines = [_normalize_text(line) for line in body_lines if _normalize_text(line)]
        header_lines = _extract_header_lines(body_lines)
        body_text = "\n".join(body_lines).strip()
        preview = _preview_text(non_empty_body_lines)
        records.append(
            {
                "id": f"{path.stem}-{block_index}",
                "source_file": path.name,
                "source_group_id": group_id,
                "published_at": published_at.isoformat(timespec="minutes") if published_at else "",
                "published_date": published_at.date().isoformat() if published_at else "",
                "heading": heading,
                "header_lines": header_lines,
                "body": body_text,
                "preview": preview,
                "_published_dt": published_at,
            }
        )
        current_lines = []
        in_block = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("### "):
            flush()
            current_lines = [line]
            in_block = True
            continue
        if not in_block:
            continue
        if _normalize_text(line) == "---":
            flush()
            continue
        current_lines.append(line)

    flush()
    return records


def _parse_commentary_records(base_dir: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in _commentary_cache_paths(base_dir):
        records.extend(_parse_commentary_file(path))
    records.sort(key=lambda item: item.get("_published_dt") or datetime.min, reverse=True)
    return records


def _matches_window(item: Dict[str, Any], window: CommentaryWindow) -> bool:
    published_dt = item.get("_published_dt")
    if not isinstance(published_dt, datetime):
        return False
    published_day = published_dt.date()
    return window.start_date <= published_day <= window.end_date


def _stock_result_template(record: Dict[str, Any], keywords: List[str]) -> Dict[str, Any]:
    return {
        "stock_id": _normalize_text(record.get("stock_id")),
        "stock_name": _normalize_text(record.get("stock_name")) or _normalize_text(record.get("stock_id")),
        "ticker": _normalize_text(record.get("ticker")),
        "keywords_used": keywords,
        "match_count": 0,
        "header_match_count": 0,
        "body_match_count": 0,
        "source_group_count": 0,
        "items": [],
    }


def get_stock_commentary(
    storage: Storage,
    stock_records: List[Dict[str, Any]],
    week_id: Optional[str] = None,
    rolling_days: Optional[int] = None,
) -> Dict[str, Any]:
    window = _rolling_days_window(rolling_days) if rolling_days else _parse_week_id(week_id)
    registry = _load_registry(storage)
    commentary_records = [item for item in parse_zsxq_commentary_records(storage.base_dir) if _matches_window(item, window)]

    result_by_stock: Dict[str, Dict[str, Any]] = {}

    for stock_record in stock_records:
        stock_id = _normalize_text(stock_record.get("stock_id"))
        if not stock_id:
            continue
        registry_entry = _lookup_registry_entry(
            registry,
            stock_id,
            _normalize_text(stock_record.get("ticker")),
            stock_name=_normalize_text(stock_record.get("stock_name")),
        )
        keywords = _build_keywords(stock_record, registry_entry)
        result = _stock_result_template(stock_record, keywords)
        result_by_stock[stock_id] = result
        if not keywords:
            continue

        items = search_zsxq_records(commentary_records, keywords, scope="all")
        result["items"] = items
        result["match_count"] = len(items)
        result["header_match_count"] = sum(1 for item in items if item.get("matched_in") in {"header", "both"})
        result["body_match_count"] = sum(1 for item in items if item.get("matched_in") in {"body", "both"})
        result["source_group_count"] = len({_normalize_text(item.get("source_group_id")) for item in items if _normalize_text(item.get("source_group_id"))})

    return {
        "success": True,
        "week_id": window.week_id,
        "window": window.to_dict(),
        "stocks": result_by_stock,
    }
