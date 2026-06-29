from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import feedparser
import requests

from .custom_news_sources import collect_almonty_custom_news

logger = logging.getLogger(__name__)

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False


class DeepSearchService:
    RESULT_CACHE_TTL_SECONDS = 6 * 60 * 60
    CONTENT_CACHE_TTL_SECONDS = 24 * 60 * 60
    MAX_SUPPLEMENTAL_QUERIES = 2
    MAX_FETCH_ARTICLES = 4
    MAX_FINAL_NEWS = 12
    PROMPT_VERSION = "weekly-news-deep-search-v1"
    GENERIC_NAME_TOKENS = {
        "inc",
        "corp",
        "corporation",
        "company",
        "co",
        "limited",
        "ltd",
        "holdings",
        "holding",
        "group",
        "plc",
        "sa",
        "nv",
        "ag",
        "industries",
        "industry",
        "technology",
        "technologies",
        "resources",
        "international",
        "energy",
        "pharma",
        "therapeutics",
    }

    def __init__(self, storage: Any, client: Any):
        self.storage = storage
        self.client = client
        self.base_dir = Path(getattr(storage, "base_dir", Path.home() / "REDACTED")) / "deep_search"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.result_cache_path = self.base_dir / "weekly_review_news_cache.json"
        self.content_cache_path = self.base_dir / "content_cache.json"
        self._lock = threading.Lock()

    def enhance_weekly_review_news(
        self,
        *,
        stock_id: str,
        stock_name: str,
        days: int,
        playbook: Optional[Dict[str, Any]],
        base_news: List[Dict[str, Any]],
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        normalized_base_news = self._normalize_news_items(base_news)
        signature = self._build_result_signature(
            stock_id=stock_id,
            stock_name=stock_name,
            days=days,
            playbook=playbook or {},
            base_news=normalized_base_news,
        )
        if not force_refresh:
            cached = self._get_cached_result(signature)
            if cached:
                cached = dict(cached)
                cached["cache_hit"] = True
                return cached

        supplemental_news, warnings = self._collect_supplemental_news(
            stock_name=stock_name,
            playbook=playbook or {},
            days=days,
        )
        merged_news = self._merge_news_lists(normalized_base_news, supplemental_news)
        filtered_news = self._filter_relevant_news(
            items=merged_news,
            stock_name=stock_name,
            playbook=playbook or {},
        )
        if filtered_news:
            merged_news = filtered_news
        fetch_targets = [item for item in merged_news if item.get("url")][: self.MAX_FETCH_ARTICLES]
        fetched_map = self._fetch_content_batch(fetch_targets, force_refresh=force_refresh)
        extracted_map = self._extract_articles(
            stock_name=stock_name,
            ticker=str((playbook or {}).get("ticker") or stock_id).strip(),
            items=fetch_targets,
            fetched_map=fetched_map,
        )
        final_news = self._apply_extractions(merged_news, extracted_map)
        deep_search_summary = self._build_deep_search_summary(final_news)
        fulltext_count = sum(1 for item in final_news if item.get("deep_search_enhanced"))
        result = {
            "news": final_news[: self.MAX_FINAL_NEWS],
            "deep_search_summary": deep_search_summary,
            "deep_search_meta": {
                "enabled": True,
                "signature": signature,
                "supplemental_hits": max(0, len(merged_news) - len(normalized_base_news)),
                "relevance_filtered": len(filtered_news) if filtered_news else len(merged_news),
                "fulltext_fetched": len([key for key, value in fetched_map.items() if value.get("success")]),
                "fulltext_enhanced": fulltext_count,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "search_warnings": warnings,
            "cache_hit": False,
        }
        self._set_cached_result(signature, result)
        return result

    def _normalize_news_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            summary = str(raw.get("summary") or "").strip()
            if not title and not summary:
                continue
            normalized.append(
                {
                    **raw,
                    "date": str(raw.get("date") or "").strip(),
                    "title": title,
                    "summary": summary,
                    "source": str(raw.get("source") or "").strip(),
                    "url": str(raw.get("url") or "").strip(),
                    "is_verifiable": bool(raw.get("is_verifiable", True)),
                    "deep_search_enhanced": bool(raw.get("deep_search_enhanced")),
                    "deep_search_key_facts": list(raw.get("deep_search_key_facts") or []),
                    "deep_search_evidence": list(raw.get("deep_search_evidence") or []),
                    "source_priority": int(raw.get("source_priority") or 0),
                }
            )
        return normalized

    def _collect_supplemental_news(
        self,
        *,
        stock_name: str,
        playbook: Dict[str, Any],
        days: int,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        queries = self._build_queries(stock_name, playbook)[: self.MAX_SUPPLEMENTAL_QUERIES]
        results: List[Dict[str, Any]] = []
        warnings: List[str] = []
        try:
            results.extend(collect_almonty_custom_news(stock_name=stock_name, playbook=playbook, days=days))
        except Exception as exc:
            warnings.append(f"custom source crawl failed: {str(exc)[:80]}")
        for query in queries:
            try:
                results.extend(self._search_google_news(query=query, days=days))
            except Exception as exc:
                warnings.append(f"Google News TextFailed: {str(exc)[:80]}")
            tavily_key = ""
            try:
                tavily_key = str(self.storage.get_tavily_api_key() or "").strip()
            except Exception:
                tavily_key = ""
            if tavily_key and TAVILY_AVAILABLE:
                try:
                    results.extend(self._search_tavily(query=query, days=days, api_key=tavily_key))
                except Exception as exc:
                    warnings.append(f"Tavily TextFailed: {str(exc)[:80]}")
        return self._dedupe_news(results), warnings

    def _build_queries(self, stock_name: str, playbook: Dict[str, Any]) -> List[str]:
        ticker = str(playbook.get("ticker") or "").strip()
        search_name = str(playbook.get("search_name") or "").strip() or stock_name
        keywords = [str(item).strip() for item in (playbook.get("search_keywords") or []) if str(item).strip()]
        queries = []
        queries.append(" ".join([part for part in [ticker, search_name] if part]).strip())
        if keywords:
            queries.append(" ".join([part for part in [search_name, " ".join(keywords[:2])] if part]).strip())
        queries.append(" ".join([part for part in [search_name, "earnings guidance outlook"] if part]).strip())
        unique: List[str] = []
        seen = set()
        for item in queries:
            clean = re.sub(r"\s+", " ", item).strip()
            if clean and clean not in seen:
                seen.add(clean)
                unique.append(clean)
        return unique

    def _search_google_news(self, *, query: str, days: int) -> List[Dict[str, Any]]:
        url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(url)
        cutoff = datetime.now() - timedelta(days=max(1, int(days)))
        items: List[Dict[str, Any]] = []
        for entry in feed.entries or []:
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            published = entry.get("published_parsed")
            date_str = ""
            if published:
                dt = datetime(*published[:6])
                if dt < cutoff:
                    continue
                date_str = dt.strftime("%Y-%m-%d")
            items.append(
                {
                    "date": date_str,
                    "title": title,
                    "summary": self._clean_html(entry.get("summary") or "")[:320],
                    "source": "Google News Deep Search",
                    "url": str(entry.get("link") or "").strip(),
                    "is_verifiable": True,
                    "is_synthetic": False,
                }
            )
            if len(items) >= 8:
                break
        return items

    def _search_tavily(self, *, query: str, days: int, api_key: str) -> List[Dict[str, Any]]:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            topic="news",
            days=max(1, int(days)),
            max_results=6,
            search_depth="basic",
        )
        items: List[Dict[str, Any]] = []
        for item in response.get("results", []) or []:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            published = str(item.get("published_date") or "").strip()
            date_str = published[:10] if published else ""
            items.append(
                {
                    "date": date_str,
                    "title": title,
                    "summary": str(item.get("content") or "").strip()[:320],
                    "source": "Tavily Deep Search",
                    "url": str(item.get("url") or "").strip(),
                    "is_verifiable": True,
                    "is_synthetic": False,
                }
            )
        return items

    def _merge_news_lists(self, base_news: List[Dict[str, Any]], supplemental: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        seen_titles = set()
        for item in list(base_news or []) + list(supplemental or []):
            key = self._news_key(item)
            title_key = self._title_key(item)
            if not key or key in seen or (title_key and title_key in seen_titles):
                continue
            seen.add(key)
            if title_key:
                seen_titles.add(title_key)
            merged.append(item)
        merged.sort(
            key=lambda item: (
                int(item.get("source_priority") or 0),
                item.get("date") or "",
                item.get("title") or "",
            ),
            reverse=True,
        )
        return merged

    def _filter_relevant_news(
        self,
        *,
        items: List[Dict[str, Any]],
        stock_name: str,
        playbook: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        stock_phrase = self._normalize_text(playbook.get("search_name") or stock_name)
        ticker = str(playbook.get("ticker") or "").strip().upper()
        keywords = [self._normalize_text(item) for item in (playbook.get("search_keywords") or []) if str(item).strip()]
        strong_tokens = [
            token
            for token in self._extract_tokens(stock_phrase or stock_name)
            if len(token) >= 4 and token not in self.GENERIC_NAME_TOKENS
        ]
        filtered: List[Dict[str, Any]] = []
        for item in items:
            haystack = self._normalize_text(" ".join([
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
            ]))
            if not haystack:
                continue
            if int(item.get("source_priority") or 0) >= 10:
                filtered.append(item)
                continue
            score = 0
            if stock_phrase and stock_phrase in haystack:
                score += 4
            if ticker and re.search(rf"\b{re.escape(ticker.lower())}\b", haystack):
                score += 3
            for token in strong_tokens:
                if token in haystack:
                    score += 2
            for keyword in keywords[:3]:
                if keyword and keyword in haystack:
                    score += 1
            if score >= 2:
                filtered.append(item)
        return filtered

    def _dedupe_news(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        seen = set()
        seen_titles = set()
        for item in items:
            key = self._news_key(item)
            title_key = self._title_key(item)
            if not key or key in seen or (title_key and title_key in seen_titles):
                continue
            seen.add(key)
            if title_key:
                seen_titles.add(title_key)
            result.append(item)
        result.sort(
            key=lambda item: (
                int(item.get("source_priority") or 0),
                item.get("date") or "",
                item.get("title") or "",
            ),
            reverse=True,
        )
        return result

    def _news_key(self, item: Dict[str, Any]) -> str:
        url = self._normalize_url(item.get("url") or "")
        if url:
            return f"url:{url}"
        title = re.sub(r"\s+", " ", str(item.get("title") or "").strip().lower())
        return f"title:{title[:180]}" if title else ""

    def _title_key(self, item: Dict[str, Any]) -> str:
        title = re.sub(r"\s+", " ", str(item.get("title") or "").strip().lower())
        return title[:180] if title else ""

    def _normalize_url(self, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""
        text = re.sub(r"#.*$", "", text)
        return text

    def _normalize_text(self, text: Any) -> str:
        value = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        return value

    def _extract_tokens(self, text: Any) -> List[str]:
        return [token for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", self._normalize_text(text)) if token]

    def _fetch_content_batch(self, items: List[Dict[str, Any]], *, force_refresh: bool) -> Dict[str, Dict[str, Any]]:
        outputs: Dict[str, Dict[str, Any]] = {}
        if not items:
            return outputs
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self._fetch_content, self._normalize_url(item.get("url") or ""), force_refresh): item
                for item in items
                if item.get("url")
            }
            for future in as_completed(futures):
                item = futures[future]
                key = self._normalize_url(item.get("url") or "")
                try:
                    outputs[key] = future.result()
                except Exception as exc:
                    outputs[key] = {"success": False, "error": str(exc)}
        return outputs

    def _fetch_content(self, url: str, force_refresh: bool) -> Dict[str, Any]:
        if not url:
            return {"success": False, "error": "missing url"}
        cache_key = self._hash_text(url)
        if not force_refresh:
            cached = self._get_cache_entry(self.content_cache_path, cache_key, self.CONTENT_CACHE_TTL_SECONDS)
            if cached:
                payload = dict(cached)
                payload["cache_hit"] = True
                return payload

        target = re.sub(r"^https?://", "", url)
        jina_url = f"https://r.jina.ai/http://{target}"
        response = requests.get(
            jina_url,
            timeout=15,
            headers={"Accept": "text/plain", "User-Agent": "investment-assistant/1.0"},
        )
        response.raise_for_status()
        content = str(response.text or "").strip()
        payload = {
            "success": bool(content),
            "content": content[:12000],
            "word_count": len(content.split()),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": False,
        }
        self._set_cache_entry(self.content_cache_path, cache_key, payload)
        return payload

    def _extract_articles(
        self,
        *,
        stock_name: str,
        ticker: str,
        items: List[Dict[str, Any]],
        fetched_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        articles = []
        for item in items:
            url = self._normalize_url(item.get("url") or "")
            fetched = fetched_map.get(url) or {}
            if not fetched.get("success") or not fetched.get("content"):
                continue
            articles.append(
                {
                    "url": url,
                    "title": str(item.get("title") or "").strip(),
                    "source": str(item.get("source") or "").strip(),
                    "content": str(fetched.get("content") or "").strip()[:3500],
                }
            )
        if not articles:
            return {}

        prompt = self._build_extraction_prompt(stock_name=stock_name, ticker=ticker, articles=articles)
        response = self._safe_chat(prompt)
        if not response:
            return {}
        parsed = self._extract_json_array(response)
        extracted: Dict[str, Dict[str, Any]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            url = self._normalize_url(item.get("url") or "")
            if not url:
                continue
            extracted[url] = {
                "refined_summary": str(item.get("refined_summary") or "").strip(),
                "key_facts": [str(v).strip() for v in (item.get("key_facts") or []) if str(v).strip()],
                "evidence": [str(v).strip() for v in (item.get("evidence") or []) if str(v).strip()],
                "incremental_value": str(item.get("incremental_value") or "").strip(),
            }
        return extracted

    def _build_extraction_prompt(self, *, stock_name: str, ticker: str, articles: List[Dict[str, Any]]) -> str:
        blocks = []
        for idx, article in enumerate(articles, start=1):
            blocks.append(
                "\n".join(
                    [
                        f"Text {idx}",
                        f"url: {article['url']}",
                        f"title: {article['title']}",
                        f"source: {article['source']}",
                        "content:",
                        article["content"],
                    ]
                )
            )
        return (
            f"TextResearchText. Text{stock_name} ({ticker or stock_name})TextNewsText, "
            "Text. Text JSON Text, Text markdown TickerText. \n"
            "Text: url, refined_summary, key_facts, evidence, incremental_value. \n"
            "Text: \n"
            "1. refined_summary Text 1-2 Text, Text. \n"
            "2. key_facts Text 3 Text, Text. \n"
            "3. evidence Text 2 Text, Text. \n"
            "4. incremental_value TextNewsText, TextText. \n"
            "5. Text, Text. \n\n"
            + "\n\n".join(blocks)
        )

    def _apply_extractions(
        self,
        items: List[Dict[str, Any]],
        extracted_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        enhanced: List[Dict[str, Any]] = []
        for raw in items:
            item = dict(raw)
            url = self._normalize_url(item.get("url") or "")
            extracted = extracted_map.get(url) or {}
            if extracted.get("refined_summary"):
                item["original_summary"] = item.get("summary") or ""
                item["summary"] = extracted["refined_summary"]
                item["deep_search_enhanced"] = True
                item["deep_search_key_facts"] = extracted.get("key_facts") or []
                item["deep_search_evidence"] = extracted.get("evidence") or []
                item["deep_search_incremental_value"] = extracted.get("incremental_value") or ""
            enhanced.append(item)
        return enhanced

    def _build_deep_search_summary(self, items: List[Dict[str, Any]]) -> str:
        highlights = []
        for item in items:
            if not item.get("deep_search_enhanced"):
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if title and summary:
                highlights.append(f"{title}: {summary}")
            elif summary:
                highlights.append(summary)
            if len(highlights) >= 2:
                break
        return "; ".join(highlights)

    def _safe_chat(self, prompt: str) -> str:
        try:
            import inspect

            sig = inspect.signature(self.client.chat)
            if "force_refresh" in sig.parameters:
                return str(self.client.chat(prompt, force_refresh=True) or "").strip()
            return str(self.client.chat(prompt) or "").strip()
        except Exception as exc:
            logger.warning("deep search chat failed: %s", exc)
            return ""

    def _extract_json_array(self, text: str) -> List[Any]:
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            pass
        match = re.search(r"\[(.*)\]", text, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []

    def _build_result_signature(
        self,
        *,
        stock_id: str,
        stock_name: str,
        days: int,
        playbook: Dict[str, Any],
        base_news: List[Dict[str, Any]],
    ) -> str:
        raw = {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "days": days,
            "ticker": playbook.get("ticker"),
            "search_name": playbook.get("search_name"),
            "search_keywords": playbook.get("search_keywords"),
            "titles": [
                {
                    "title": item.get("title"),
                    "url": self._normalize_url(item.get("url") or ""),
                    "date": item.get("date"),
                }
                for item in base_news[:12]
            ],
            "prompt_version": self.PROMPT_VERSION,
        }
        return self._hash_text(json.dumps(raw, ensure_ascii=False, sort_keys=True))

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()

    def _get_cached_result(self, signature: str) -> Optional[Dict[str, Any]]:
        return self._get_cache_entry(self.result_cache_path, signature, self.RESULT_CACHE_TTL_SECONDS)

    def _set_cached_result(self, signature: str, payload: Dict[str, Any]) -> None:
        self._set_cache_entry(self.result_cache_path, signature, payload)

    def _get_cache_entry(self, path: Path, key: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load_cache_file(path)
            items = data.get("items") or {}
            entry = items.get(key)
            if not isinstance(entry, dict):
                return None
            created_at = self._parse_datetime(entry.get("created_at"))
            if not created_at or (datetime.now() - created_at).total_seconds() > ttl_seconds:
                return None
            payload = entry.get("payload")
            return dict(payload) if isinstance(payload, dict) else None

    def _set_cache_entry(self, path: Path, key: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            data = self._load_cache_file(path)
            data.setdefault("items", {})
            data["items"][key] = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "payload": payload,
            }
            self._save_cache_file(path, data)

    def _load_cache_file(self, path: Path) -> Dict[str, Any]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("failed to load deep search cache: %s", path)
        return {"items": {}}

    def _save_cache_file(self, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _clean_html(self, text: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", str(text or ""))
        return re.sub(r"\s+", " ", clean).strip()
