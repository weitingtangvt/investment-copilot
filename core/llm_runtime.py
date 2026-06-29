"""Provider-neutral LLM runtime contract + policy + metadata + cache."""

from __future__ import annotations

import copy
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .llm_cache import LLMCache

logger = logging.getLogger(__name__)


def _looks_like_html_error_page(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    text = result.strip().lower()
    if not text:
        return False
    if text.startswith("<!doctype") or text.startswith("<html"):
        return True
    return ("<html" in text and "</html>" in text) or ("<body" in text and "</body>" in text)


class LLMRuntime:
    """Unified runtime wrapper around concrete LLM clients."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        client: Any,
        cache: Optional[LLMCache] = None,
    ):
        self.provider = (provider or "gpt53").strip().lower()
        self.model = (model or "").strip()
        self.base_url = (base_url or "").strip()
        self.client = client
        self.cache = cache or LLMCache()
        self._last_runtime_meta: Dict[str, Any] = self._build_runtime_meta(request_mode="idle")

    def capabilities(self) -> Dict[str, bool]:
        # Text
        is_custom = self.provider.startswith("custom:")

        # DefaultTextweb_search, Text
        supports_web_search = True

        if self.provider == "gpt53" and self.base_url and "api.openai.com" not in self.base_url:
            supports_web_search = False
        if self.provider == "gemini" and self.base_url:
            lowered = self.base_url.lower()
            if ("googleapis.com" not in lowered) and ("google.com" not in lowered):
                supports_web_search = False

        # Text: Textbase_urlTextOpenAITextSearchText
        if is_custom and self.base_url:
            lowered = self.base_url.lower()
            # TextOpenAIText, Textweb_search
            if "api.openai.com" not in lowered:
                supports_web_search = False

        return {
            "supports_web_search": supports_web_search,
            "supports_streaming": False,
            "supports_structured_json": True,
        }

    def __getattr__(self, item: str) -> Any:
        return getattr(self.client, item)

    def get_runtime_meta(self) -> Dict[str, Any]:
        return copy.deepcopy(self._last_runtime_meta)

    def chat(
        self,
        prompt: str,
        history: Optional[List[Dict]] = None,
        force_refresh: bool = False,
        max_tokens: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> str:
        payload = {
            "prompt": prompt,
            "history": history or [],
            "max_tokens": max_tokens,
            "timeout_sec": timeout_sec,
        }
        return self._run_call(
            method="chat",
            request_mode="chat",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=1,
            runner=lambda: self._call_chat_with_optional_budget(
                prompt=prompt,
                history=history,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
            ),
        )

    def chat_ex(
        self,
        prompt: str,
        history: Optional[List[Dict]] = None,
        force_refresh: bool = False,
        max_tokens: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        data = self.chat(
            prompt=prompt,
            history=history,
            force_refresh=force_refresh,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
        )
        return self._build_contract(data)

    def chat_with_system(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[List[Dict]] = None,
        force_refresh: bool = False,
    ) -> str:
        payload = {"system_prompt": system_prompt, "user_message": user_message, "history": history or []}
        return self._run_call(
            method="chat_with_system",
            request_mode="chat",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=1,
            runner=lambda: self.client.chat_with_system(system_prompt, user_message, history=history),
        )

    def search(self, query: str, time_range_days: int = 7, force_refresh: bool = False) -> str:
        payload = {"query": query, "time_range_days": int(time_range_days)}
        return self._run_call(
            method="search",
            request_mode="market_search",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=1,
            runner=lambda: self.client.search(query, time_range_days=time_range_days),
        )

    def search_ex(self, query: str, time_range_days: int = 7, force_refresh: bool = False) -> Dict[str, Any]:
        data = self.search(query=query, time_range_days=time_range_days, force_refresh=force_refresh)
        return self._build_contract(data)

    def search_news_structured(
        self,
        stock_name: str,
        related_entities: List[str],
        time_range_days: int = 7,
        playbook: Optional[Dict] = None,
        force_refresh: bool = False,
    ) -> List[Dict]:
        payload = {
            "stock_name": stock_name,
            "related_entities": related_entities or [],
            "time_range_days": int(time_range_days),
            "playbook": playbook or {},
        }
        return self._run_call(
            method="search_news_structured",
            request_mode="market_search",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=0,
            runner=lambda: self.client.search_news_structured(
                stock_name=stock_name,
                related_entities=related_entities,
                time_range_days=time_range_days,
                playbook=playbook,
            ),
        )

    def search_news_structured_ex(
        self,
        stock_name: str,
        related_entities: List[str],
        time_range_days: int = 7,
        playbook: Optional[Dict] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        data = self.search_news_structured(
            stock_name=stock_name,
            related_entities=related_entities,
            time_range_days=time_range_days,
            playbook=playbook,
            force_refresh=force_refresh,
        )
        return self._build_contract(data)

    def _call_chat_with_optional_budget(
        self,
        prompt: str,
        history: Optional[List[Dict]],
        max_tokens: Optional[int],
        timeout_sec: Optional[float],
    ) -> str:
        try:
            kwargs: Dict[str, Any] = {"history": history}
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            if timeout_sec is not None:
                kwargs["timeout_sec"] = timeout_sec
            return self.client.chat(prompt, **kwargs)
        except TypeError:
            return self.client.chat(prompt, history=history)

    def structured_output(
        self,
        prompt: str,
        schema_description: str,
        force_refresh: bool = False,
    ) -> Dict:
        payload = {"prompt": prompt, "schema_description": schema_description}
        return self._run_call(
            method="structured_output",
            request_mode="deep_synthesis",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=1,
            runner=lambda: self.client.structured_output(prompt, schema_description),
        )

    def structured_output_ex(
        self,
        prompt: str,
        schema_description: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        data = self.structured_output(
            prompt=prompt,
            schema_description=schema_description,
            force_refresh=force_refresh,
        )
        return self._build_contract(data)

    def analyze_file(self, file_path: str, prompt: str, force_refresh: bool = False) -> str:
        payload = {"file_path": file_path, "prompt": prompt}
        return self._run_call(
            method="analyze_file",
            request_mode="deep_synthesis",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=1,
            runner=lambda: self.client.analyze_file(file_path, prompt),
        )

    def analyze_file_ex(self, file_path: str, prompt: str, force_refresh: bool = False) -> Dict[str, Any]:
        data = self.analyze_file(file_path=file_path, prompt=prompt, force_refresh=force_refresh)
        return self._build_contract(data)

    def search_stock_performance(
        self,
        stock_name: str,
        ticker: str = "",
        days: int = 7,
        force_refresh: bool = False,
    ) -> str:
        payload = {"stock_name": stock_name, "ticker": ticker, "days": int(days)}
        return self._run_call(
            method="search_stock_performance",
            request_mode="market_search",
            payload=payload,
            force_refresh=force_refresh,
            retry_times=1,
            runner=lambda: self.client.search_stock_performance(stock_name, ticker=ticker, days=days),
        )

    def search_stock_performance_ex(
        self,
        stock_name: str,
        ticker: str = "",
        days: int = 7,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        data = self.search_stock_performance(
            stock_name=stock_name,
            ticker=ticker,
            days=days,
            force_refresh=force_refresh,
        )
        return self._build_contract(data)

    def _build_contract(self, data: Any) -> Dict[str, Any]:
        ok = not self._is_error_result(data)
        return {
            "ok": bool(ok),
            "data": data if ok else None,
            "error": None if ok else str(data),
            "meta": self.get_runtime_meta(),
        }

    def _run_call(
        self,
        method: str,
        request_mode: str,
        payload: Any,
        force_refresh: bool,
        retry_times: int,
        runner: Callable[[], Any],
    ) -> Any:
        start = time.time()
        cache_key = self.cache.build_key(self.provider, self.model, method, payload)
        if not force_refresh:
            hit, cached = self.cache.get(cache_key)
            if hit:
                self._last_runtime_meta = self._build_runtime_meta(
                    request_mode=request_mode,
                    cache_hit=True,
                    degraded=self._is_degraded_result(cached),
                    degraded_reason=self._infer_degraded_reason(cached),
                    runtime_seconds=round(time.time() - start, 3),
                )
                return cached

        attempt = 0
        last_result: Any = None
        while attempt <= retry_times:
            attempt += 1
            try:
                result = runner()
                last_result = result
                if self._is_error_result(result) and attempt <= retry_times:
                    continue
                break
            except Exception as exc:  # pragma: no cover - defensive runtime capture
                logger.warning("LLM runtime call failed (%s attempt %s): %s", method, attempt, exc)
                last_result = f"TextFailed: {exc}"
                if attempt > retry_times:
                    break

        result = last_result
        degraded = self._is_degraded_result(result)
        degraded_reason = self._infer_degraded_reason(result)
        runtime_seconds = round(time.time() - start, 3)
        if not self._is_error_result(result):
            self.cache.set(cache_key, result)
        self._last_runtime_meta = self._build_runtime_meta(
            request_mode=request_mode,
            cache_hit=False,
            degraded=degraded,
            degraded_reason=degraded_reason,
            runtime_seconds=runtime_seconds,
        )
        return result

    def _build_runtime_meta(
        self,
        request_mode: str,
        cache_hit: bool = False,
        degraded: bool = False,
        degraded_reason: str = "",
        runtime_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "request_mode": request_mode,
            "degraded": bool(degraded),
            "degraded_reason": degraded_reason or "",
            "cache_hit": bool(cache_hit),
            "runtime_seconds": runtime_seconds,
        }

    def _is_error_result(self, result: Any) -> bool:
        if isinstance(result, str):
            if not result.strip():
                return True
            lowered = result.lower()
            if _looks_like_html_error_page(result):
                return True
            if result.startswith("TextFailed"):
                return True
            return any(token in lowered for token in ["error code", "request timed out", "api Error"])
        return False

    def _is_degraded_result(self, result: Any) -> bool:
        if isinstance(result, str):
            lowered = result.lower()
            return any(token in lowered for token in ["Text", "fallback", "warning", "Text"])
        if isinstance(result, list) and result:
            head = result[0]
            if isinstance(head, dict) and head.get("_is_metadata"):
                if head.get("failed_dimensions"):
                    return True
                warnings = head.get("warnings") or head.get("search_warnings") or []
                return bool(warnings)
        if isinstance(result, dict):
            return bool(result.get("degraded") or result.get("degraded_reason"))
        return False

    def _infer_degraded_reason(self, result: Any) -> str:
        if isinstance(result, str):
            if _looks_like_html_error_page(result):
                return "html_error_page"
            if "504" in result:
                return "gateway_timeout_504"
            if "timed out" in result.lower():
                return "request_timeout"
            if "invalid api key" in result.lower():
                return "invalid_api_key"
            if "warning" in result.lower():
                return "warning_fallback"
            return ""
        if isinstance(result, list) and result:
            head = result[0]
            if isinstance(head, dict) and head.get("_is_metadata"):
                warnings = head.get("warnings") or head.get("search_warnings") or []
                if warnings:
                    return "; ".join(str(x) for x in warnings[:2])
        if isinstance(result, dict):
            return str(result.get("degraded_reason") or "")
        return ""
