"""Aggregate news from multiple sources with optional data source registry."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from .data_sources.base import DataSourceRegistry

logger = logging.getLogger(__name__)


def _calculate_similarity(s1: str, s2: str) -> float:
    s1 = s1.lower().strip()
    s2 = s2.lower().strip()
    if s1 == s2:
        return 1.0
    set1 = set(s1.split())
    set2 = set(s2.split())
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def _deduplicate_news(news_list: List[Dict], priority_order: List[str]) -> List[Dict]:
    if not news_list:
        return []
    source_priority = {source: idx for idx, source in enumerate(priority_order)}
    unique: List[Dict] = []
    seen_titles: List[str] = []

    for news in sorted(news_list, key=lambda item: source_priority.get(item.get("source", ""), 999)):
        title = (news.get("title") or "").strip()
        if not title:
            continue
        is_duplicate = any(_calculate_similarity(title, seen_title) > 0.85 for seen_title in seen_titles)
        if not is_duplicate:
            seen_titles.append(title)
            unique.append(news)
    return unique


def _normalize_failed_dimension(source_name: str, error: str) -> Dict[str, str]:
    return {"source": source_name, "error": str(error or "").strip()}


def _wrap_source_failure(source_name: str, error: str) -> Dict:
    message = str(error or "").strip() or f"{source_name} returned no data"
    return {
        "news": [],
        "search_metadata": {
            "_is_metadata": True,
            "total_dimensions": 1,
            "successful_dimensions": 0,
            "failed_dimensions": [_normalize_failed_dimension(source_name, message)],
            "search_warnings": [message],
        },
    }


def _fetch_with_registry_or_fallback(
    source_name: str,
    data_source_registry: Optional[DataSourceRegistry],
    fallback: Callable[[], Dict],
    **kwargs,
) -> Dict:
    if data_source_registry is not None:
        source = data_source_registry.get(source_name)
        if source is not None:
            result = source.fetch(**kwargs)
            if isinstance(getattr(result, "data", None), dict):
                return result.data
            return _wrap_source_failure(source_name, getattr(result, "warning", "") or getattr(result, "error", ""))
    return fallback()


def _fetch_rss(
    stock_name: str,
    related_entities: List[str],
    time_range_days: int,
    playbook: Optional[Dict],
    data_source_registry: Optional[DataSourceRegistry],
) -> Dict:
    search_keywords = playbook.get("search_keywords", []) if playbook else []
    ticker = playbook.get("ticker", "").strip() if playbook else ""
    search_name = playbook.get("search_name", "").strip() if playbook else ""
    return _fetch_with_registry_or_fallback(
        "rss",
        data_source_registry,
        lambda: __import__("core.rss_news_client", fromlist=["collect_news_structured"]).collect_news_structured(
            stock_name=stock_name,
            ticker=ticker,
            search_keywords=search_keywords,
            related_entities=related_entities,
            time_range_days=time_range_days,
            search_name=search_name,
        ),
        stock_name=stock_name,
        ticker=ticker,
        search_keywords=search_keywords,
        related_entities=related_entities,
        time_range_days=time_range_days,
        search_name=search_name,
        playbook=playbook,
    )


def _fetch_akshare_chinese(
    stock_name: str,
    related_entities: List[str],
    time_range_days: int,
    playbook: Optional[Dict],
    data_source_registry: Optional[DataSourceRegistry],
) -> Dict:
    if data_source_registry is None:
        return _wrap_source_failure("akshare_chinese", "AKShare Chinese source is not registered")
    ticker = playbook.get("ticker", "").strip() if playbook else ""
    search_keywords = playbook.get("search_keywords", []) if playbook else []
    search_name = playbook.get("search_name", "").strip() if playbook else ""
    return _fetch_with_registry_or_fallback(
        "akshare_chinese",
        data_source_registry,
        lambda: _wrap_source_failure("akshare_chinese", "AKShare Chinese source is not registered"),
        stock_name=stock_name,
        ticker=ticker,
        search_keywords=search_keywords,
        related_entities=related_entities,
        time_range_days=time_range_days,
        search_name=search_name,
        playbook=playbook,
    )


def _fetch_newsapi(
    api_key: str,
    stock_name: str,
    related_entities: List[str],
    time_range_days: int,
    playbook: Optional[Dict],
    data_source_registry: Optional[DataSourceRegistry],
) -> Dict:
    return _fetch_with_registry_or_fallback(
        "newsapi",
        data_source_registry,
        lambda: __import__("core.newsapi_client", fromlist=["collect_news_structured"]).collect_news_structured(
            api_key=api_key,
            stock_name=stock_name,
            related_entities=related_entities,
            time_range_days=time_range_days,
            playbook=playbook,
        ),
        api_key=api_key,
        stock_name=stock_name,
        related_entities=related_entities,
        time_range_days=time_range_days,
        playbook=playbook,
    )


def _fetch_tavily(
    api_key: str,
    stock_name: str,
    related_entities: List[str],
    time_range_days: int,
    playbook: Optional[Dict],
    data_source_registry: Optional[DataSourceRegistry],
) -> Dict:
    return _fetch_with_registry_or_fallback(
        "tavily",
        data_source_registry,
        lambda: __import__("core.tavily_client", fromlist=["collect_news_structured"]).collect_news_structured(
            api_key=api_key,
            stock_name=stock_name,
            related_entities=related_entities,
            time_range_days=time_range_days,
            playbook=playbook,
        ),
        api_key=api_key,
        stock_name=stock_name,
        related_entities=related_entities,
        time_range_days=time_range_days,
        playbook=playbook,
    )


def aggregate_news_from_sources(
    storage,
    stock_name: str,
    related_entities: List[str],
    time_range_days: int = 7,
    playbook: Optional[Dict] = None,
    data_source_registry: Optional[DataSourceRegistry] = None,
) -> List[Dict]:
    tavily_key = storage.get_tavily_api_key()
    newsapi_key = storage.get_newsapi_api_key()
    strategy = storage.get_news_aggregation_strategy()

    all_news: List[Dict] = []
    all_warnings: List[str] = []
    successful_sources: List[str] = []
    failed_sources: List[str] = []
    source_priority = ["AKShare Chinese", "Google RSS", "News API", "Tavily", "LLM Search"]

    if strategy == "priority":
        try:
            result = _fetch_akshare_chinese(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=data_source_registry,
            )
            news = result.get("news", [])
            metadata = result.get("search_metadata", {})
            if news:
                all_warnings.extend(metadata.get("search_warnings", []))
                return [metadata] + news
            all_warnings.extend(metadata.get("search_warnings", []))
        except Exception as exc:
            logger.error("AKShare Chinese search failed: %s", exc)
            failed_sources.append(f"AKShare Chinese (error: {str(exc)[:50]})")

        try:
            result = _fetch_rss(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=data_source_registry,
            )
            news = result.get("news", [])
            metadata = result.get("search_metadata", {})
            if news:
                all_warnings.extend(metadata.get("search_warnings", []))
                return [metadata] + news
            all_warnings.extend(metadata.get("search_warnings", []))
            failed_sources.append("Google RSS (TextResult)")
        except Exception as exc:
            logger.error("Google RSS search failed: %s", exc)
            failed_sources.append(f"Google RSS (Error: {str(exc)[:50]})")

        if newsapi_key:
            try:
                result = _fetch_newsapi(
                    api_key=newsapi_key,
                    stock_name=stock_name,
                    related_entities=related_entities,
                    time_range_days=time_range_days,
                    playbook=playbook,
                    data_source_registry=data_source_registry,
                )
                news = result.get("news", [])
                metadata = result.get("search_metadata", {})
                if news:
                    all_warnings.extend(metadata.get("search_warnings", []))
                    return [metadata] + news
                all_warnings.extend(metadata.get("search_warnings", []))
                failed_sources.append("News API (TextResult)")
            except Exception as exc:
                logger.error("News API search failed: %s", exc)
                failed_sources.append(f"News API (Error: {str(exc)[:50]})")

        if tavily_key:
            try:
                result = _fetch_tavily(
                    api_key=tavily_key,
                    stock_name=stock_name,
                    related_entities=related_entities,
                    time_range_days=time_range_days,
                    playbook=playbook,
                    data_source_registry=data_source_registry,
                )
                news = result.get("news", [])
                metadata = result.get("search_metadata", {})
                if news:
                    all_warnings.extend(metadata.get("search_warnings", []))
                    return [metadata] + news
                all_warnings.extend(metadata.get("search_warnings", []))
                failed_sources.append("Tavily (TextResult)")
            except Exception as exc:
                logger.error("Tavily search failed: %s", exc)
                failed_sources.append(f"Tavily (Error: {str(exc)[:50]})")

        return [
            {
                "_is_metadata": True,
                "total_dimensions": 0,
                "successful_dimensions": 0,
                "failed_dimensions": [],
                "search_warnings": all_warnings
                + [
                    f"TextNewsTextFailed: {', '.join(failed_sources)}",
                    "Text Tavily API Key Text News API Key, Text AI Search. ",
                ],
            }
        ]

    def fetch_tavily() -> Optional[Dict]:
        if not tavily_key:
            return None
        try:
            return _fetch_tavily(
                api_key=tavily_key,
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=data_source_registry,
            )
        except Exception as exc:
            logger.error("Tavily search failed: %s", exc)
            return {"error": str(exc)}

    def fetch_newsapi() -> Optional[Dict]:
        if not newsapi_key:
            return None
        try:
            return _fetch_newsapi(
                api_key=newsapi_key,
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=data_source_registry,
            )
        except Exception as exc:
            logger.error("News API search failed: %s", exc)
            return {"error": str(exc)}

    def fetch_rss() -> Dict:
        try:
            return _fetch_rss(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=data_source_registry,
            )
        except Exception as exc:
            logger.error("Google RSS search failed: %s", exc)
            return {"error": str(exc)}

    def fetch_akshare_chinese() -> Dict:
        try:
            return _fetch_akshare_chinese(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=data_source_registry,
            )
        except Exception as exc:
            logger.error("AKShare Chinese search failed: %s", exc)
            return {"error": str(exc)}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_akshare_chinese): "AKShare Chinese",
            executor.submit(fetch_tavily): "Tavily",
            executor.submit(fetch_newsapi): "News API",
            executor.submit(fetch_rss): "Google RSS",
        }
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                result = future.result(timeout=30)
                if result is None:
                    continue
                if "error" in result:
                    failed_sources.append(f"{source_name} (Error: {result['error'][:50]})")
                    continue
                news = result.get("news", [])
                metadata = result.get("search_metadata", {})
                if news:
                    all_news.extend(news)
                    successful_sources.append(source_name)
                else:
                    failed_sources.append(f"{source_name} (TextResult)")
                all_warnings.extend(metadata.get("search_warnings", []))
            except Exception as exc:
                logger.error("%s execution failed: %s", source_name, exc)
                failed_sources.append(f"{source_name} (TextError)")

    unique_news = _deduplicate_news(all_news, source_priority)
    unique_news.sort(key=lambda item: item.get("date", ""), reverse=True)
    final_news = unique_news[:30]
    metadata = {
        "_is_metadata": True,
        "total_dimensions": len(successful_sources),
        "successful_dimensions": len(successful_sources),
        "failed_dimensions": [{"source": item} for item in failed_sources],
        "search_warnings": all_warnings
        + (
            [f"Text {len(successful_sources)} TextNewsText: {', '.join(successful_sources)}"]
            if successful_sources
            else [f"TextNewsTextFailed: {', '.join(failed_sources)}"]
        ),
    }
    return [metadata] + final_news
