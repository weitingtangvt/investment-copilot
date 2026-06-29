"""RSS-based near real-time news collection fallback (Google News RSS)."""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List

import feedparser


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


def _clean_html(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _build_query(
    stock_name: str,
    ticker: str = "",
    search_keywords: List[str] = None,
    related_entities: List[str] = None,
    search_name: str = ""
) -> str:
    """Text Google News RSS Text(AND Text)"""
    # Text search_name(TextSearchText)
    name = search_name if search_name else stock_name

    # Text: ticker Text
    if ticker:
        base = f"{ticker} {name}"
    else:
        base = name

    # TextSearchText(Text/Text)
    if search_keywords:
        keywords = " ".join(search_keywords[:2])
        return f"{base} {keywords}"

    # Text: Text/ticker
    return base


def collect_news_structured(
    stock_name: str,
    ticker: str = "",
    search_keywords: List[str] = None,
    related_entities: List[str] = None,
    time_range_days: int = 7,
    search_name: str = ""
) -> Dict:
    query = _build_query(stock_name, ticker, search_keywords, related_entities, search_name)
    params = urllib.parse.urlencode(
        {
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
    )
    url = f"{GOOGLE_NEWS_RSS_URL}?{params}"
    feed = feedparser.parse(url)

    cutoff = datetime.now() - timedelta(days=int(time_range_days))
    unique_titles = set()
    news_list: List[Dict] = []
    for entry in feed.entries or []:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        normalized_title = title.lower()[:120]
        if normalized_title in unique_titles:
            continue
        unique_titles.add(normalized_title)

        published = entry.get("published_parsed")
        date_str = ""
        is_fresh = True
        if published:
            dt = datetime(*published[:6])
            date_str = dt.strftime("%Y-%m-%d")
            is_fresh = dt >= cutoff
        if not is_fresh:
            continue

        summary = _clean_html(entry.get("summary", ""))
        source = ""
        src = entry.get("source") or {}
        if isinstance(src, dict):
            source = (src.get("title") or "").strip()
        source = source or "Google News RSS"

        news_list.append(
            {
                "date": date_str,
                "title": title,
                "summary": summary[:320],
                "source": source,
                "url": entry.get("link") or "",
                "dimension": "external_rss",
                "relevance": "Google News RSS",
                "importance": "Text",
                "is_verifiable": True,
                "is_synthetic": False,
            }
        )

        if len(news_list) >= 20:
            break

    warnings = []
    if not news_list:
        warnings.append("Google News RSS TextNews, Text force_refresh Text. ")

    return {
        "news": news_list,
        "search_metadata": {
            "_is_metadata": True,
            "total_dimensions": 1,
            "successful_dimensions": 1 if news_list else 0,
            "failed_dimensions": [],
            "search_warnings": warnings,
        },
    }

