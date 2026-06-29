"""
News API Text - Text https://newsapi.org/docs Text /v2/everything TextSearchNews. 

TextRefreshNewsText, Text LLM TextSearch. Text API Key(config Text NEWSAPI_API_KEY). 
"""

import json
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

NEWSAPI_EVERYTHING_URL = "https://newsapi.org/v2/everything"


def _newsapi_request(api_key: str, params: Dict) -> Dict:
    """Text News API /v2/everything, Text JSON. """
    params["apiKey"] = api_key
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    url = f"{NEWSAPI_EVERYTHING_URL}?{qs}"
    req = Request(url, method="GET")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_news(
    api_key: str,
    q: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page_size: int = 20,
    language: Optional[str] = None,
    sort_by: str = "publishedAt",
) -> List[Dict]:
    """
    TextSearchNews. Text. 
    language=None Text(ResultText); Text 24 Text, to_date Text. 
    """
    params = {
        "q": q,
        "from": from_date,
        "to": to_date,
        "pageSize": min(page_size, 100),
        "sortBy": sort_by,
    }
    if language:
        params["language"] = language
    try:
        data = _newsapi_request(api_key, params)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
        raise RuntimeError(f"News API TextFailed: {e.code} {body[:200]}")
    except URLError as e:
        raise RuntimeError(f"TextError: {e.reason}")

    if data.get("status") != "ok":
        raise RuntimeError(data.get("message", "Unknown News API error"))

    articles = data.get("articles") or []
    out = []
    for a in articles:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        published = a.get("publishedAt") or ""
        if published and "T" in published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                date_str = published[:10] if len(published) >= 10 else ""
        else:
            date_str = published[:10] if published else ""
        out.append({
            "date": date_str,
            "title": title,
            "summary": (a.get("description") or "").strip() or (a.get("content") or "")[:200],
            "dimension": "",
            "relevance": "Text News API Search",
            "importance": "Text",
            "source": (a.get("source") or {}).get("name") or "News API",
            "url": a.get("url") or "",
        })
    return out


def collect_news_structured(
    api_key: str,
    stock_name: str,
    related_entities: List[str],
    time_range_days: int = 7,
    playbook: Optional[Dict] = None,
) -> Dict:
    """
    TextSearchNews, Text EnvironmentCollector.collect_news Text. 
    News API Text 24 Text, Text to_date Text. 
    """
    end_date = datetime.now()
    # Text 24 Text, TextDateTextResult
    to_date_dt = end_date - timedelta(days=1)
    start_date = end_date - timedelta(days=time_range_days)
    from_date = start_date.strftime("%Y-%m-%d")
    to_date = to_date_dt.strftime("%Y-%m-%d")

    thesis_keywords = []
    risk_keywords = []
    if playbook:
        core = playbook.get("core_thesis", {})
        if core.get("summary"):
            thesis_keywords.append(core.get("summary"))
        thesis_keywords.extend(core.get("key_points", [])[:3])
        risk_keywords = playbook.get("invalidation_triggers", [])[:3]

    dimensions = [
        {"dimension": "Text", "query": f"{stock_name} Text Text Filings Text Text"},
        {"dimension": "Text", "query": f"{stock_name} Text Text Text " + " ".join((related_entities or [])[:3])},
        {"dimension": "Text", "query": f"{stock_name} Text Text Text Text Text"},
        {"dimension": "Text", "query": f"{stock_name} Text Text Text Text Text"},
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

    all_news = []
    failed = []
    warnings = []

    for dim in dimensions:
        try:
            # TextResult; Text language=zh Text
            items = search_news(
                api_key,
                q=dim["query"],
                from_date=from_date,
                to_date=to_date,
                page_size=10,
                language=None,
            )
            for n in items:
                n["dimension"] = dim["dimension"]
                all_news.append(n)
        except Exception as e:
            failed.append({"dimension": dim["dimension"], "error": str(e)})
            warnings.append(f"Text{dim['dimension']}SearchFailed: {(str(e))[:60]}")

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
        warnings.append(
            "News API TextResult(TextSearchText, Text 24 Text). "
            "TextSettingsText News API Key Text AI Search. "
        )
    elif not news_list and warnings:
        warnings.append("TextSettingsText News API Key Text AI RefreshNews. ")

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
