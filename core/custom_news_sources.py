"""Stock-specific news sources that are not covered well by generic aggregators."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _clean_html(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def _to_iso_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw[:10]


def _within_days(date_text: str, days: int) -> bool:
    try:
        day = datetime.strptime(_to_iso_date(date_text), "%Y-%m-%d")
    except ValueError:
        return False
    cutoff = datetime.now() - timedelta(days=max(1, int(days)))
    return day >= cutoff


def _request_json(url: str, *, timeout: int = 30) -> Any:
    response = requests.get(url, timeout=timeout, headers=DEFAULT_HEADERS)
    response.raise_for_status()
    return response.json()


def _make_news_item(
    *,
    date_text: str,
    title: str,
    summary: str,
    url: str,
    source: str,
    source_priority: int,
) -> Dict[str, Any]:
    return {
        "date": _to_iso_date(date_text),
        "title": str(title or "").strip(),
        "summary": str(summary or "").strip(),
        "source": source,
        "url": str(url or "").strip(),
        "is_verifiable": True,
        "is_synthetic": False,
        "source_priority": int(source_priority),
    }


def _fetch_ctia_tungsten_news(days: int, *, limit: int = 8) -> List[Dict[str, Any]]:
    category_id = 17  # tungsten-news
    payload = _request_json(
        f"https://www.ctia.com.cn/wp-json/wp/v2/posts?categories={category_id}&per_page={max(5, int(limit))}"
    )
    items: List[Dict[str, Any]] = []
    for row in payload or []:
        date_text = str(row.get("date") or "")
        if not _within_days(date_text, days):
            continue
        title = _clean_html(((row.get("title") or {}).get("rendered") if isinstance(row.get("title"), dict) else ""))
        summary = _clean_html(
            ((row.get("excerpt") or {}).get("rendered") if isinstance(row.get("excerpt"), dict) else "")
            or ((row.get("content") or {}).get("rendered") if isinstance(row.get("content"), dict) else "")
        )[:260]
        url = str(row.get("link") or "").strip()
        if not title or not url:
            continue
        items.append(
            _make_news_item(
                date_text=date_text,
                title=title,
                summary=summary,
                url=url,
                source="CTIA Tungsten News",
                source_priority=12,
            )
        )
    return items


def _fetch_almonty_press_releases(days: int, *, fallback_days: int = 30, limit: int = 6) -> List[Dict[str, Any]]:
    payload = _request_json(f"https://press.almonty.com/wp-json/wp/v2/posts?per_page={max(5, int(limit))}")
    recent: List[Dict[str, Any]] = []
    fallback: List[Dict[str, Any]] = []
    for row in payload or []:
        date_text = str(row.get("date") or "")
        title = _clean_html(((row.get("title") or {}).get("rendered") if isinstance(row.get("title"), dict) else ""))
        content_html = ((row.get("excerpt") or {}).get("rendered") if isinstance(row.get("excerpt"), dict) else "") or (
            (row.get("content") or {}).get("rendered") if isinstance(row.get("content"), dict) else ""
        )
        summary = _clean_html(content_html)[:320]
        url = str(row.get("link") or "").strip()
        if not title or not url:
            continue
        item = _make_news_item(
            date_text=date_text,
            title=title,
            summary=summary,
            url=url,
            source="Almonty Press Release",
            source_priority=20,
        )
        if _within_days(date_text, days):
            recent.append(item)
        elif _within_days(date_text, fallback_days):
            fallback.append(item)
    return recent or fallback[:3]


def collect_almonty_custom_news(stock_name: str, playbook: Optional[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    lowered_name = str(stock_name or "").lower()
    ticker = str((playbook or {}).get("ticker") or "").strip().upper()
    search_name = str((playbook or {}).get("search_name") or "").lower()
    if "almonty" not in lowered_name and "almonty" not in search_name and ticker not in {"ALM", "AII"}:
        return []

    collected: List[Dict[str, Any]] = []
    for fetcher in (_fetch_almonty_press_releases, _fetch_ctia_tungsten_news):
        try:
            collected.extend(fetcher(days))
        except Exception:
            continue

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in collected:
        key = (str(item.get("url") or "").strip(), str(item.get("title") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(
        key=lambda item: (
            int(item.get("source_priority") or 0),
            str(item.get("date") or ""),
            str(item.get("title") or ""),
        ),
        reverse=True,
    )
    return deduped
