"""Environment collection and impact assessment."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .data_sources.base import DataSourceRegistry
from .prompt_utils import render_prompt
from .prompts.environment_v1 import IMPACT_ASSESSMENT_PROMPT_V1, NEWS_ENRICH_PROMPT_V1
from .storage import Storage


class EnvironmentCollector:
    """Collect market environment signals and evaluate impact."""

    def __init__(
        self,
        client: Any,
        storage: Storage,
        data_source_registry: Optional[DataSourceRegistry] = None,
    ):
        self.client = client
        self.storage = storage
        self.data_source_registry = data_source_registry

    def collect_news(
        self,
        stock_id: str,
        stock_name: str,
        time_range_days: int = 7,
        force_refresh: bool = False,
        ai_enrich: Optional[bool] = None,
    ) -> Dict[str, Any]:
        start_ts = time.time()
        playbook = self.storage.get_stock_playbook(stock_id)
        related_entities = (playbook.get("related_entities", []) if playbook else []) or []
        capabilities = self._get_capabilities()
        supports_web_search = bool(capabilities.get("supports_web_search", True))

        tavily_key = self.storage.get_tavily_api_key()
        newsapi_key = self.storage.get_newsapi_api_key()
        use_ai_search = self.storage.get_news_use_ai_search()
        ai_enrich_enabled = self.storage.get_news_ai_enrich() if ai_enrich is None else bool(ai_enrich)
        cache_key = self._build_news_result_cache_key(
            stock_id=stock_id,
            stock_name=stock_name,
            time_range_days=time_range_days,
            ai_enrich=ai_enrich_enabled,
            tavily_enabled=bool(tavily_key),
            newsapi_enabled=bool(newsapi_key),
            use_ai_search=bool(use_ai_search),
            supports_web_search=supports_web_search,
        )
        if not force_refresh:
            cached = self._get_cached_news_result(cache_key)
            if cached is not None:
                result = dict(cached)
                metadata = dict(result.get("search_metadata") or {})
                cached_at = float(metadata.get("cached_at_epoch") or time.time())
                metadata["cache_hit"] = True
                metadata["cache_age_seconds"] = max(0, int(time.time() - cached_at))
                result["search_metadata"] = metadata
                runtime_meta = dict(result.get("runtime_meta") or self._get_runtime_meta())
                runtime_meta["cache_hit"] = True
                result["runtime_meta"] = runtime_meta
                result["impact_cards"] = self._build_impact_cards(result.get("news", []))
                return result

        if tavily_key or newsapi_key:
            from .news_aggregator import aggregate_news_from_sources

            raw_result = aggregate_news_from_sources(
                storage=self.storage,
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                data_source_registry=self.data_source_registry,
            )
            search_source = "aggregated_sources"
        elif use_ai_search and supports_web_search:
            raw_result = self._call_search_news_structured(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
                force_refresh=force_refresh,
            )
            search_source = "llm"
        else:
            raw_result = self._collect_news_external(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
            )
            search_source = "external"

        search_metadata = None
        news_list: List[Dict[str, Any]] = []
        if isinstance(raw_result, dict):
            maybe_metadata = raw_result.get("search_metadata")
            if isinstance(maybe_metadata, dict):
                search_metadata = maybe_metadata
            for item in (raw_result.get("news") or []):
                if isinstance(item, dict):
                    news_list.append(item)
        else:
            for item in raw_result or []:
                if isinstance(item, dict) and item.get("_is_metadata"):
                    search_metadata = item
                elif isinstance(item, dict):
                    news_list.append(item)

        runtime_meta = self._get_runtime_meta()
        normalized_metadata = self._normalize_search_metadata(
            raw_metadata=search_metadata or {},
            runtime_seconds=round(time.time() - start_ts, 3),
            runtime_meta=runtime_meta,
        )
        normalized_metadata["search_source"] = (
            (search_metadata or {}).get("search_source") if isinstance(search_metadata, dict) else None
        ) or search_source

        verifiable_news = [n for n in news_list if bool(n.get("is_verifiable", True))]
        synthetic_news = [n for n in news_list if not bool(n.get("is_verifiable", True))]
        normalized_metadata["verifiable_news_count"] = len(verifiable_news)
        normalized_metadata["synthetic_news_count"] = len(synthetic_news)
        normalized_metadata["has_verifiable_news"] = bool(verifiable_news)
        normalized_metadata["synthetic_only"] = bool(news_list) and not bool(verifiable_news)
        if normalized_metadata["synthetic_only"]:
            warnings = list(normalized_metadata.get("warnings") or [])
            warning_text = "TextNews, CurrentTextSummary(TextNews). "
            if warning_text not in warnings:
                warnings.append(warning_text)
            normalized_metadata["warnings"] = warnings
            normalized_metadata["search_warnings"] = list(warnings)

        result = self._maybe_enrich_news(
            {
                "news": verifiable_news,
                "search_metadata": normalized_metadata,
                "runtime_meta": runtime_meta,
                "fallback_summary": (synthetic_news[0].get("summary", "") if synthetic_news else ""),
            },
            stock_name=stock_name,
            force_refresh=force_refresh,
            ai_enrich=ai_enrich_enabled,
        )
        result["impact_cards"] = self._build_impact_cards(result.get("news", []))
        self._set_cached_news_result(cache_key, result)
        return result

    def _news_result_cache_path(self):
        base_dir = getattr(self.storage, "base_dir", None)
        if base_dir is None:
            return None
        return base_dir / "weekly_news_result_cache.json"

    def _build_news_result_cache_key(
        self,
        *,
        stock_id: str,
        stock_name: str,
        time_range_days: int,
        ai_enrich: bool,
        tavily_enabled: bool,
        newsapi_enabled: bool,
        use_ai_search: bool,
        supports_web_search: bool,
    ) -> str:
        payload = {
            "stock_id": str(stock_id or "").strip().upper(),
            "stock_name": str(stock_name or "").strip().lower(),
            "time_range_days": int(time_range_days or 7),
            "ai_enrich": bool(ai_enrich),
            "tavily_enabled": bool(tavily_enabled),
            "newsapi_enabled": bool(newsapi_enabled),
            "use_ai_search": bool(use_ai_search),
            "supports_web_search": bool(supports_web_search),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_news_result_cache(self) -> Dict[str, Any]:
        path = self._news_result_cache_path()
        if path is None:
            return {}
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_news_result_cache(self, cache: Dict[str, Any]) -> None:
        path = self._news_result_cache_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_cached_news_result(self, cache_key: str, ttl_seconds: int = 6 * 60 * 60) -> Optional[Dict[str, Any]]:
        cache = self._load_news_result_cache()
        entry = cache.get(cache_key)
        if not isinstance(entry, dict):
            return None
        cached_at = float(entry.get("cached_at_epoch") or 0.0)
        if cached_at <= 0 or time.time() - cached_at > ttl_seconds:
            return None
        payload = entry.get("payload")
        return dict(payload) if isinstance(payload, dict) else None

    def _set_cached_news_result(self, cache_key: str, result: Dict[str, Any]) -> None:
        news = result.get("news") or []
        metadata = result.get("search_metadata") or {}
        if not news and int(metadata.get("successful_dimensions") or 0) <= 0:
            return
        payload = json.loads(json.dumps(result, ensure_ascii=False))
        cached_at = time.time()
        payload_metadata = dict(payload.get("search_metadata") or {})
        payload_metadata["cache_hit"] = False
        payload_metadata["cached_at_epoch"] = cached_at
        payload["search_metadata"] = payload_metadata
        runtime_meta = dict(payload.get("runtime_meta") or {})
        runtime_meta["cache_hit"] = False
        payload["runtime_meta"] = runtime_meta
        cache = self._load_news_result_cache()
        cache[cache_key] = {"cached_at_epoch": cached_at, "payload": payload}
        self._save_news_result_cache(cache)

    def _collect_news_external(
        self,
        stock_name: str,
        related_entities: List[str],
        time_range_days: int,
        playbook: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Collect news from external non-LLM source(s)."""
        try:
            ak_source = self.data_source_registry.get("akshare_chinese") if self.data_source_registry is not None else None
            if ak_source is not None:
                playbook_payload = dict(playbook or {})
                if stock_name and not playbook_payload.get("stock_name"):
                    playbook_payload["stock_name"] = stock_name
                source_result = ak_source.fetch(
                    stock_name=stock_name,
                    ticker=playbook_payload.get("ticker", ""),
                    search_keywords=playbook_payload.get("search_keywords") or [],
                    related_entities=related_entities,
                    time_range_days=time_range_days,
                    search_name=playbook_payload.get("search_name", ""),
                    playbook=playbook_payload,
                )
                result = source_result.data if isinstance(getattr(source_result, "data", None), dict) else {"news": [], "search_metadata": {}}
                if result.get("news"):
                    metadata = dict(result.get("search_metadata") or {})
                    metadata["search_source"] = "external"
                    return {"news": list(result.get("news") or []), "search_metadata": metadata}

            rss_source = self.data_source_registry.get("rss") if self.data_source_registry is not None else None
            if rss_source is not None:
                source_result = rss_source.fetch(
                    stock_name=stock_name,
                    ticker=(playbook or {}).get("ticker", ""),
                    search_keywords=(playbook or {}).get("search_keywords") or [],
                    related_entities=related_entities,
                    time_range_days=time_range_days,
                    search_name=(playbook or {}).get("search_name", ""),
                    playbook=playbook,
                )
                result = source_result.data if isinstance(getattr(source_result, "data", None), dict) else {"news": [], "search_metadata": {}}
            else:
                from .rss_news_client import collect_news_structured as rss_collect

                result = rss_collect(stock_name, related_entities, time_range_days)
            metadata = dict(result.get("search_metadata") or {})
            metadata["search_source"] = "external"
            return {
                "news": list(result.get("news") or []),
                "search_metadata": metadata,
            }
        except Exception as exc:
            return {
                "news": [],
                "search_metadata": {
                    "_is_metadata": True,
                    "search_source": "external",
                    "total_dimensions": 1,
                    "successful_dimensions": 0,
                    "failed_dimensions": [{"dimension": "external_rss", "error": str(exc)[:120]}],
                    "search_warnings": [
                        f"Google RSS search failed: {str(exc)[:60]}",
                        "Configure Tavily/News API or enable AI search.",
                    ],
                },
            }

    def analyze_file(self, file_path: str, force_refresh: bool = False) -> Dict[str, Any]:
        prompt = (
            "Extract investment-relevant insights from this file and return a concise summary. "
            "Include file type, key viewpoints, notable data points, and potential investment impact."
        )
        result = self._call_analyze_file(file_path=file_path, prompt=prompt, force_refresh=force_refresh)
        return {
            "filename": file_path.split("/")[-1],
            "summary": result,
            "analyzed_at": datetime.now().isoformat(),
            "runtime_meta": self._get_runtime_meta(),
        }

    def assess_impact(
        self,
        stock_id: str,
        time_range: str,
        auto_collected: List[Dict],
        user_uploaded: List[Dict],
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        portfolio = self.storage.get_portfolio_playbook()
        stock_playbook = self.storage.get_stock_playbook(stock_id)
        recent_history = self.storage.get_recent_research(stock_id, limit=3)
        research_context = self.storage.get_research_context(stock_id, limit=3)
        user_preferences = self.storage.get_preferences_for_prompt()
        historical_uploads = self.storage.get_historical_uploads(stock_id, limit=5)

        portfolio_str = json.dumps(portfolio, ensure_ascii=False, indent=2) if portfolio else "(No data)"
        stock_str = json.dumps(stock_playbook, ensure_ascii=False, indent=2) if stock_playbook else "(No data)"
        history_str = self._format_recent_history(research_context, recent_history)
        auto_str = self._format_auto_collected(auto_collected)
        uploaded_str = self._format_uploaded(user_uploaded)
        historical_str = self._format_historical_uploads(historical_uploads)

        prompt = render_prompt(
            IMPACT_ASSESSMENT_PROMPT_V1,
            max_chars=24000,
            recent_research_history=history_str,
            portfolio_playbook=portfolio_str,
            stock_playbook=stock_str,
            user_preferences=user_preferences,
            time_range=time_range,
            auto_collected_news=auto_str,
            user_uploaded_content=uploaded_str,
            historical_uploads=historical_str,
        )

        response = self._call_chat(prompt, force_refresh=force_refresh)
        result, parse_error = self._extract_json(response)
        if not result:
            result = {
                "judgment": {"needs_deep_research": True, "confidence": "medium", "urgency": "this_week"},
                "conclusion": {
                    "summary": response[:240],
                    "key_risk": "pending_manual_review",
                    "key_opportunity": "pending_manual_review",
                },
                "research_plan": {
                    "research_objective": "Validate major investment assumptions",
                    "research_modules": [],
                    "timeline": "3-5 days",
                },
                "parse_error": parse_error,
                "raw_response": response,
            }

        result.setdefault("what_changed_since_last_research", self._build_change_diff(recent_history, result))
        result.setdefault("us_market_factors", self._extract_us_market_factors(auto_collected))
        result["runtime_meta"] = self._get_runtime_meta()
        return result

    def _call_search_news_structured(self, **kwargs) -> List[Dict[str, Any]]:
        if hasattr(self.client, "search_news_structured_ex"):
            try:
                response = self.client.search_news_structured_ex(**kwargs)
                if isinstance(response, dict) and "ok" in response:
                    if response.get("ok"):
                        return response.get("data") or []
                    return [{"_is_metadata": True, "search_warnings": [response.get("error") or "search failed"]}]
            except TypeError:
                pass
        try:
            return self.client.search_news_structured(**kwargs)
        except TypeError:
            kwargs.pop("force_refresh", None)
            return self.client.search_news_structured(**kwargs)

    def _call_chat(
        self,
        prompt: str,
        force_refresh: bool = False,
        max_tokens: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> str:
        if hasattr(self.client, "chat_ex"):
            try:
                kwargs = {"force_refresh": force_refresh}
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if timeout_sec is not None:
                    kwargs["timeout_sec"] = timeout_sec
                response = self.client.chat_ex(prompt, **kwargs)
                if isinstance(response, dict) and "ok" in response:
                    if response.get("ok"):
                        return str(response.get("data") or "")
                    return str(response.get("error") or "TextFailed")
            except TypeError:
                pass
        try:
            kwargs = {"force_refresh": force_refresh}
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            if timeout_sec is not None:
                kwargs["timeout_sec"] = timeout_sec
            return self.client.chat(prompt, **kwargs)
        except TypeError:
            return self.client.chat(prompt)

    def _call_analyze_file(self, file_path: str, prompt: str, force_refresh: bool = False) -> str:
        if hasattr(self.client, "analyze_file_ex"):
            try:
                response = self.client.analyze_file_ex(file_path, prompt, force_refresh=force_refresh)
                if isinstance(response, dict) and "ok" in response:
                    if response.get("ok"):
                        return str(response.get("data") or "")
                    return str(response.get("error") or "TextFailed")
            except TypeError:
                pass
        try:
            return self.client.analyze_file(file_path, prompt, force_refresh=force_refresh)
        except TypeError:
            return self.client.analyze_file(file_path, prompt)

    def _get_runtime_meta(self) -> Dict[str, Any]:
        if hasattr(self.client, "get_runtime_meta"):
            return self.client.get_runtime_meta()
        return {
            "provider": "unknown",
            "model": "unknown",
            "base_url": "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "request_mode": "unknown",
            "degraded": False,
            "degraded_reason": "",
            "cache_hit": False,
            "runtime_seconds": 0.0,
        }

    def _get_capabilities(self) -> Dict[str, Any]:
        if hasattr(self.client, "capabilities"):
            try:
                caps = self.client.capabilities()
                if isinstance(caps, dict):
                    return caps
            except Exception:
                pass
        return {"supports_web_search": True, "supports_streaming": False, "supports_structured_json": True}

    def _normalize_search_metadata(
        self,
        raw_metadata: Dict[str, Any],
        runtime_seconds: float,
        runtime_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        warnings = raw_metadata.get("warnings") or raw_metadata.get("search_warnings") or []
        failed_dimensions = raw_metadata.get("failed_dimensions") or []
        total_dimensions = int(raw_metadata.get("total_dimensions") or 1)
        successful_dimensions = raw_metadata.get("successful_dimensions")
        if successful_dimensions is None:
            successful_dimensions = max(0, total_dimensions - len(failed_dimensions))
        normalized = {
            "_is_metadata": True,
            "total_dimensions": total_dimensions,
            "successful_dimensions": int(successful_dimensions),
            "failed_dimensions": failed_dimensions,
            "warnings": warnings,
            "runtime_seconds": runtime_seconds,
            "cache_hit": bool(runtime_meta.get("cache_hit")),
            "degraded_reason": runtime_meta.get("degraded_reason", ""),
        }
        normalized["search_warnings"] = list(warnings)
        return normalized

    def _enrich_news_with_llm(
        self,
        news_list: List[Dict[str, Any]],
        stock_name: str,
        force_refresh: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        if not news_list:
            return None
        rows = []
        for i, n in enumerate(news_list[:20]):
            title = (n.get("title") or "").strip() or "Untitled"
            summary = (n.get("summary") or "").strip()[:80]
            rows.append(f"[{i}] {title}" + (f" | {summary}" if summary else ""))

        prompt = render_prompt(
            NEWS_ENRICH_PROMPT_V1,
            max_chars=12000,
            stock_name=stock_name or "this_stock",
            news_items="\n".join(rows),
        )
        response = self._call_chat(
            prompt,
            force_refresh=force_refresh,
            max_tokens=900,
            timeout_sec=18.0,
        )
        parsed = self._extract_json_list(response)
        if not parsed:
            return None

        out: List[Dict[str, Any]] = []
        by_idx = {int(item.get("index", -1)): item for item in parsed if isinstance(item, dict)}
        for i, item in enumerate(news_list):
            patch = by_idx.get(i)
            # Filter out irrelevant news items when LLM marks them explicitly.
            if patch and patch.get("is_relevant") is False:
                continue
            merged = dict(item)
            if patch:
                if patch.get("importance") in {"high", "medium", "low", "Text", "Text", "Text"}:
                    merged["importance"] = patch["importance"]
                if patch.get("summary_short"):
                    merged["summary"] = str(patch["summary_short"]).strip()
                for key in (
                    "event_type",
                    "thesis_impact_direction",
                    "confidence",
                    "expected_horizon",
                    "actionability",
                    "required_follow_up_data",
                ):
                    if key in patch and patch[key]:
                        merged[key] = patch[key]
            out.append(merged)
        return out

    def _maybe_enrich_news(
        self,
        result: Dict[str, Any],
        stock_name: str,
        force_refresh: bool = False,
        ai_enrich: Optional[bool] = None,
    ) -> Dict[str, Any]:
        news = result.get("news") or []
        use_ai_enrich = self.storage.get_news_ai_enrich() if ai_enrich is None else bool(ai_enrich)
        if not news or not use_ai_enrich:
            return result
        if len(news) <= 1:
            return result
        enriched = self._enrich_news_with_llm(news, stock_name, force_refresh=force_refresh)
        if enriched is not None:
            result = {**result, "news": enriched}
        return result

    def _extract_json(self, response: str) -> Tuple[Optional[Dict], Optional[str]]:
        patterns = [
            r"```(?:json)?\s*([\s\S]*?)\s*```",
            r"\{[\s\S]*\}",
        ]
        for pattern in patterns:
            match = re.search(pattern, response)
            if not match:
                continue
            raw = match.group(1) if match.lastindex else match.group(0)
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed, None
            except json.JSONDecodeError as exc:
                err = f"JSON parse failed: {exc}"
                self.storage.log(err, "WARNING")
        return None, "Text AI Text JSON"

    def _extract_json_list(self, response: str) -> Optional[List[Dict[str, Any]]]:
        patterns = [r"```(?:json)?\s*([\s\S]*?)\s*```", r"\[[\s\S]*\]"]
        for pattern in patterns:
            match = re.search(pattern, response)
            if not match:
                continue
            raw = match.group(1) if match.lastindex else match.group(0)
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue
        return None

    def _build_impact_cards(self, news: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cards: List[Dict[str, Any]] = []
        for item in news[:8]:
            title = item.get("title") or "untitled_event"
            summary = item.get("summary") or ""
            event_type = item.get("event_type") or item.get("dimension") or "market_update"
            direction = item.get("thesis_impact_direction") or self._guess_direction(f"{title} {summary}")
            actionability = item.get("actionability") or ("add" if direction == "positive" else "reduce" if direction == "negative" else "watch")
            cards.append(
                {
                    "event_type": event_type,
                    "title": title,
                    "thesis_impact_direction": direction,
                    "confidence": item.get("confidence", "medium"),
                    "expected_horizon": item.get("expected_horizon", "medium"),
                    "actionability": actionability,
                    "required_follow_up_data": item.get("required_follow_up_data")
                    or ["next earnings", "estimate revisions", "valuation percentile"],
                }
            )
        return cards

    def _guess_direction(self, text: str) -> str:
        lowered = (text or "").lower()
        positive_tokens = ["beat", "raise", "upgrade", "growth", "record", "surge"]
        negative_tokens = ["miss", "cut", "downgrade", "risk", "decline", "drop", "investigation"]
        pos = sum(token in lowered for token in positive_tokens)
        neg = sum(token in lowered for token in negative_tokens)
        if pos > neg:
            return "positive"
        if neg > pos:
            return "negative"
        return "neutral"

    def _build_change_diff(self, recent_history: List[Dict[str, Any]], assessment: Dict[str, Any]) -> Dict[str, Any]:
        previous = (recent_history[0].get("research_result") if recent_history else {}) or {}
        current_summary = (assessment.get("conclusion") or {}).get("summary", "")
        prev_reasoning = previous.get("reasoning", "")
        changed = []
        if current_summary and current_summary != prev_reasoning:
            changed.append("summary_changed")
        if (assessment.get("judgment") or {}).get("needs_deep_research", False):
            changed.append("deep_research_triggered")
        unchanged = []
        if previous.get("recommendation") and previous.get("recommendation") == (assessment.get("conclusion") or {}).get("recommendation"):
            unchanged.append("core_recommendation_unchanged")
        return {
            "changed_points": changed[:5],
            "unchanged_points": unchanged[:5],
            "invalidation_risk": "high" if any("changed" in x for x in changed) else "medium",
        }

    def _extract_us_market_factors(self, news: List[Dict[str, Any]]) -> Dict[str, Any]:
        text = "\n".join(f"{n.get('title', '')} {n.get('summary', '')}" for n in news[:20]).lower()
        return {
            "earnings_guidance_delta": "present" if any(k in text for k in ["earnings", "guidance", "eps"]) else "unclear",
            "estimate_revision_direction": "up" if "upgrade" in text else "down" if "downgrade" in text else "flat",
            "valuation_percentile_vs_peers": "needs_data",
            "macro_sensitivity": {
                "rates": "high" if any(k in text for k in ["yield", "rate", "fed"]) else "medium",
                "fx": "medium" if any(k in text for k in ["dollar", "fx", "usd"]) else "low",
                "commodity": "medium" if any(k in text for k in ["oil", "gas", "metal"]) else "low",
            },
        }

    def _format_recent_history(self, research_context: List[Dict], recent_history: List[Dict]) -> str:
        if research_context:
            items = []
            for row in research_context[:3]:
                result = row.get("research_result", {})
                items.append(
                    f"- {row.get('date', '')[:10]}: recommendation={result.get('recommendation', 'unknown')}, "
                    f"confidence={result.get('confidence', 'unknown')}, reasoning={result.get('reasoning', '')[:180]}"
                )
            return "\n".join(items)
        if recent_history:
            items = []
            for row in recent_history[:3]:
                result = row.get("research_result", {})
                items.append(
                    f"- {row.get('date', '')[:10]}: recommendation={result.get('recommendation', 'unknown')}, "
                    f"reasoning={result.get('reasoning', '')[:180]}"
                )
            return "\n".join(items)
        return "(No dataHistoryResearch)"

    def _format_auto_collected(self, auto_collected: List[Dict]) -> str:
        if not auto_collected:
            return "(No data)"
        lines = []
        for item in auto_collected[:30]:
            lines.append(f"- [{item.get('date', '')}] {item.get('title', '')} | {item.get('summary', '')[:120]}")
        return "\n".join(lines)

    def _format_uploaded(self, user_uploaded: List[Dict]) -> str:
        if not user_uploaded:
            return "(No data)"
        lines = []
        for item in user_uploaded[:20]:
            lines.append(f"- {item.get('filename', '')}: {item.get('summary', '')[:150]}")
        return "\n".join(lines)

    def _format_historical_uploads(self, historical_uploads: List[Dict]) -> str:
        if not historical_uploads:
            return "(No dataHistoryUploadText)"
        lines = []
        for item in historical_uploads[:10]:
            lines.append(f"- [{item.get('date', '')}] {item.get('filename', '')}: {item.get('summary', '')[:180]}")
        return "\n".join(lines)
