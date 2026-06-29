"""
Tavily API Text - Text Tavily Search API SearchNews. 

TextRefreshNewsText, Text, TextNewsText. 
Text API Key(config Text TAVILY_API_KEY). 
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False
    logger.warning("tavily-python Text, Tavily NewsSearchText")


def _search_dimension(
    client: "TavilyClient",
    dimension: str,
    query: str,
    time_range_days: int,
) -> List[Dict]:
    """SearchTextNews"""
    try:
        response = client.search(
            query=query,
            topic="news",
            days=time_range_days,
            max_results=10,
            search_depth="basic",  # TextSearch(1 credit)
        )

        results = response.get("results", [])
        news_items = []

        for item in results:
            # TextDate
            published_date = item.get("published_date", "")
            if published_date:
                try:
                    # Tavily Text "2026-03-08T10:30:00Z"
                    dt = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    date_str = published_date[:10] if len(published_date) >= 10 else ""
            else:
                date_str = datetime.now().strftime("%Y-%m-%d")

            # Text
            score = item.get("score", 0)
            relevance = f"Score: {score:.2f}" if score else "Text Tavily"

            news_items.append({
                "date": date_str,
                "title": item.get("title", "").strip(),
                "summary": item.get("content", "").strip()[:300],  # TextSummaryText
                "dimension": dimension,
                "relevance": relevance,
                "importance": "Text" if score > 0.8 else "Text" if score > 0.5 else "Text",
                "source": "Tavily",
                "url": item.get("url", ""),
                "is_verifiable": True,
                "is_synthetic": False,
            })

        return news_items

    except Exception as e:
        logger.error("Tavily SearchText%sFailed: %s", dimension, e)
        raise


def collect_news_structured(
    api_key: str,
    stock_name: str,
    related_entities: List[str],
    time_range_days: int = 7,
    playbook: Optional[Dict] = None,
) -> Dict:
    """
    TextSearchNews, Text EnvironmentCollector.collect_news Text. 
    Text ThreadPoolExecutor TextSearchText. 
    """
    if not TAVILY_AVAILABLE:
        return {
            "news": [],
            "search_metadata": {
                "_is_metadata": True,
                "total_dimensions": 0,
                "successful_dimensions": 0,
                "failed_dimensions": [],
                "search_warnings": ["tavily-python Text, Text: pip install tavily-python"],
            }
        }

    try:
        client = TavilyClient(api_key=api_key)
    except Exception as e:
        return {
            "news": [],
            "search_metadata": {
                "_is_metadata": True,
                "total_dimensions": 0,
                "successful_dimensions": 0,
                "failed_dimensions": [],
                "search_warnings": [f"Tavily TextFailed: {str(e)}"],
            }
        }

    # TextSearchText
    thesis_keywords = []
    risk_keywords = []
    if playbook:
        core = playbook.get("core_thesis", {})
        if core.get("summary"):
            thesis_keywords.append(core.get("summary"))
        thesis_keywords.extend(core.get("key_points", [])[:3])
        risk_keywords = playbook.get("invalidation_triggers", [])[:3]

    dimensions = [
        {"dimension": "Text", "query": f'"{stock_name}" (earnings OR revenue OR guidance OR announcement)'},
        {"dimension": "Text", "query": f'"{stock_name}" (competitors OR market share OR industry)'},
        {"dimension": "Text", "query": f'"{stock_name}" (product OR technology OR innovation)'},
        {"dimension": "Text", "query": f'"{stock_name}" (policy OR regulation)'},
    ]

    if thesis_keywords:
        dimensions.append({
            "dimension": "Text",
            "query": f"{stock_name} " + " ".join(thesis_keywords[:3]),
        })

    if risk_keywords:
        dimensions.append({
            "dimension": "RiskText",
            "query": f"{stock_name} " + " ".join(risk_keywords[:3]),
        })

    # TextSearch(Text 3 Text, Text)
    all_news = []
    failed = []
    warnings = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_dim = {
            executor.submit(
                _search_dimension,
                client,
                dim["dimension"],
                dim["query"],
                time_range_days,
            ): dim
            for dim in dimensions
        }

        for future in as_completed(future_to_dim):
            dim = future_to_dim[future]
            try:
                items = future.result(timeout=10)
                all_news.extend(items)
            except Exception as e:
                error_msg = str(e)
                failed.append({"dimension": dim["dimension"], "error": error_msg})
                warnings.append(f"Text{dim['dimension']}SearchFailed: {error_msg[:60]}")

    # Text(Text)
    seen = set()
    unique = []
    for n in all_news:
        key = (n.get("title") or "").lower().strip()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(n)

    # TextDateText, Text 20
    unique.sort(key=lambda x: x.get("date", ""), reverse=True)
    news_list = unique[:20]

    if not news_list and not warnings:
        warnings.append("Tavily API TextResult, TextNews. ")
    elif not news_list and warnings:
        warnings.append("TextSearchTextFailed, Text API Key Text. ")

    search_metadata = {
        "_is_metadata": True,
        "total_dimensions": len(dimensions),
        "successful_dimensions": len(dimensions) - len(failed),
        "failed_dimensions": failed,
        "search_warnings": warnings,
    }

    return {
        "news": news_list,
        "search_metadata": search_metadata,
    }
