"""OpenAI Compatible API Text(Default GPT-5.4). """

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from urllib.parse import urlsplit, urlunsplit

import requests
from openai import OpenAI
from openai import AuthenticationError
from openai import APIError
from openai import BadRequestError
from openai import NotFoundError

logger = logging.getLogger(__name__)

MODEL = os.getenv("GPT53_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.4"


class GPT53Client:
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = (api_key or "").strip()
        self.model = (model or MODEL).strip() or MODEL
        self.base_url = self._normalize_base_url((base_url or os.getenv("OPENAI_BASE_URL") or "").strip()) or None
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self.client = OpenAI(**kwargs)

    def _normalize_base_url(self, base_url: str) -> str:
        raw = str(base_url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlsplit(raw)
        except Exception:
            return raw.rstrip("/")
        if not parsed.scheme or not parsed.netloc:
            return raw.rstrip("/")
        path = (parsed.path or "").rstrip("/")
        hostname = (parsed.netloc or "").lower()
        if "api.openai.com" in hostname:
            path = "/v1"
        elif path in {"", "/index.php"}:
            path = "/v1"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")

    def _using_official_openai(self) -> bool:
        return (not self.base_url) or ("api.openai.com" in self.base_url)

    def _candidate_models(self) -> List[str]:
        if not self._using_official_openai():
            return [self.model]
        models = [self.model]
        using_official = self._using_official_openai()
        if using_official and self.model == "gpt-5.3":
            models.extend(["gpt-5.2", "gpt-5"])
        if (not using_official) and self.model == "gpt-5.3":
            models.extend(["gpt-5.4", "gpt-5.2", "gpt-5"])
        if (not using_official) and self.model == "gpt-5.4":
            models.extend(["gpt-5.2", "gpt-5"])
        deduped = []
        for model in models:
            if model and model not in deduped:
                deduped.append(model)
        return deduped

    def _should_try_next_model(self, error: Exception) -> bool:
        lowered = str(error).lower()
        return any(
            token in lowered
            for token in (
                "model_not_found",
                "does not exist",
                "no available channel for model",
                "unsupported model",
                "not support model",
            )
        )

    def _is_retryable_api_error(self, error: Exception) -> bool:
        lowered = str(error).lower()
        return any(
            token in lowered
            for token in (
                "error code: 429",
                "error code: 500",
                "error code: 502",
                "error code: 503",
                "error code: 504",
                "service temporarily unavailable",
                "temporarily unavailable",
                "bad gateway",
                "gateway timeout",
                "timed out",
                "timeout",
                "overloaded",
                "rate limit",
            )
        )

    def _per_model_attempts(self) -> int:
        if not self._using_official_openai():
            return 1
        return 2

    def _count_cjk_chars(self, text: str) -> int:
        count = 0
        for ch in str(text or ""):
            code = ord(ch)
            if 0x4E00 <= code <= 0x9FFF:
                count += 1
        return count

    def _count_latin1_supplement_chars(self, text: str) -> int:
        count = 0
        for ch in str(text or ""):
            code = ord(ch)
            if 0x00C0 <= code <= 0x00FF:
                count += 1
        return count

    def _repair_utf8_latin1_mojibake(self, text: str) -> str:
        value = str(text or "")
        if not value:
            return value
        before_cjk = self._count_cjk_chars(value)
        before_latin1 = self._count_latin1_supplement_chars(value)
        if before_latin1 < 3:
            return value
        try:
            repaired = value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
        after_cjk = self._count_cjk_chars(repaired)
        after_latin1 = self._count_latin1_supplement_chars(repaired)
        # Accept repair only when Chinese readability clearly improves.
        if after_cjk >= max(2, before_cjk + 2) and after_latin1 <= max(1, before_latin1 // 4):
            return repaired
        return value

    def _sanitize_text_response(self, text: str) -> str:
        value = self._repair_utf8_latin1_mojibake(str(text or "")).strip()
        lowered = value.lower()
        if lowered.startswith("<!doctype") or lowered.startswith("<html") or ("<html" in lowered and "</html>" in lowered):
            logger.error("API returned an HTML page instead of model text")
            base_hint = self.base_url or "OpenAI default"
            return f"TextFailed: API Text HTML Text. Text Base URL Text /v1 Text. Current Base URL: {base_hint}"
        return value

    def _extract_text(self, response) -> str:
        try:
            text = response.choices[0].message.content or ""
            return self._sanitize_text_response(text)
        except Exception:
            return str(response)

    def _is_openai_parsing_import_error(self, error: Exception) -> bool:
        text = str(error or "").lower()
        return (
            isinstance(error, ImportError)
            and "solve_response_format_t" in text
            and "openai.lib._parsing" in text
        )

    def _chat_completions_call_raw_http(
        self,
        final_messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        timeout_sec: float = 35.0,
    ) -> str:
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        endpoint = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error = ""
        deadline = time.monotonic() + max(0.1, float(timeout_sec or 0.1))
        for model_name in self._candidate_models():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = "request timeout budget exhausted"
                break
            body = {
                "model": model_name,
                "messages": final_messages,
                "max_tokens": max_tokens,
            }
            try:
                resp = requests.post(endpoint, headers=headers, json=body, timeout=max(0.1, remaining))
            except requests.RequestException as exc:
                last_error = f"raw http request failed: {exc}"
                continue

            if resp.status_code >= 400:
                error_hint = ""
                try:
                    payload = resp.json()
                    error_hint = str((payload.get("error") or {}).get("message") or "").strip()
                except ValueError:
                    error_hint = (resp.text or "")[:300]
                if self._should_try_next_model(Exception(error_hint)):
                    continue
                last_error = f"http {resp.status_code}: {error_hint or 'unknown error'}"
                continue

            try:
                payload = resp.json()
            except ValueError:
                last_error = "raw http response is not json"
                continue

            text = str((((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
            if text:
                self.model = model_name
                return self._sanitize_text_response(text)
            last_error = "raw http response missing choices.message.content"

        return f"TextFailed: {last_error or 'raw http fallback failed'}"

    def _extract_stream_chat_content(self, raw_text: str) -> str:
        chunks: List[str] = []
        for raw_line in str(raw_text or "").splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for choice in (data.get("choices") or []):
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, str):
                    chunks.append(piece)
        return self._sanitize_text_response("".join(chunks).strip())

    def _chat_completions_call_stream_http(
        self,
        final_messages: List[Dict[str, str]],
        model_name: str,
        max_tokens: int,
        timeout_sec: float,
    ) -> str:
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        endpoint = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_name,
            "messages": final_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            resp = requests.post(endpoint, headers=headers, json=body, timeout=max(0.1, float(timeout_sec or 0.1)))
        except requests.RequestException:
            return ""
        if resp.status_code >= 400:
            return ""
        raw_text = ""
        raw_bytes = getattr(resp, "content", None)
        if isinstance(raw_bytes, (bytes, bytearray)):
            try:
                raw_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raw_text = raw_bytes.decode("utf-8", errors="replace")
        if not raw_text:
            raw_text = str(getattr(resp, "text", "") or "")
        return self._extract_stream_chat_content(raw_text)

    def _messages_to_prompt(self, messages: List[Dict]) -> str:
        parts = []
        for msg in messages:
            role = (msg.get("role") or "user").strip().lower()
            content = msg.get("content") or ""
            label = "User" if role == "user" else ("Assistant" if role == "assistant" else role.title())
            parts.append(f"{label}: {content}")
        parts.append("Assistant:")
        return "\n\n".join(parts)

    def _responses_call(
        self,
        prompt: str,
        instructions: Optional[str] = None,
        max_output_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        timeout_sec: float = 35.0,
    ) -> str:
        try:
            last_error = None
            using_official = self._using_official_openai()
            deadline = time.monotonic() + max(0.1, float(timeout_sec or 0.1))
            for model_name in self._candidate_models():
                if (deadline - time.monotonic()) <= 0:
                    break
                for attempt in range(1, self._per_model_attempts() + 1):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        response = self.client.responses.create(
                            model=model_name,
                            input=prompt,
                            instructions=instructions,
                            max_output_tokens=max_output_tokens,
                            tools=tools or [],
                            timeout=max(0.1, remaining),
                        )
                        self.model = model_name
                        return self._response_text(response)
                    except (BadRequestError, NotFoundError) as e:
                        last_error = e
                        if self._should_try_next_model(e):
                            logger.warning("GPT5.3 responses fallback from %s after model error: %s", model_name, e)
                            break
                        raise
                    except APIError as e:
                        last_error = e
                        if self._is_retryable_api_error(e) and attempt < self._per_model_attempts():
                            logger.warning("GPT5.3 responses retry %s attempt %d after API error: %s", model_name, attempt, e)
                            sleep_for = min(0.6 * attempt, max(0.0, deadline - time.monotonic()))
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                            continue
                        if self._should_try_next_model(e) or ((not using_official) and self._is_retryable_api_error(e)):
                            logger.warning("GPT5.3 responses fallback from %s after API error: %s", model_name, e)
                            break
                        raise
            if last_error:
                raise last_error
        except AuthenticationError as e:
            logger.error("GPT5.3 TextFailed: %s", e)
            base_hint = f"Current Base URL: {self.base_url}" if self.base_url else "Current Base URL: OpenAI DefaultText"
            error_msg = str(e)
            return (
                f"TextFailed: GPT5.3 API TextFailed. Text API Key Text, "
                f"TextSettingsText Base URL. {base_hint}. TextError: {error_msg}"
            )
        except APIError as e:
            msg = str(e)
            if "Request timed out" in msg:
                logger.warning("GPT5.3 API Text: %s", e)
            else:
                logger.error("GPT5.3 API Error: %s", e)
            error_msg = str(e)
            return f"TextFailed: GPT5.3 API TextError: {error_msg}"
        except Exception as e:
            logger.error("GPT5.3 TextFailed: %s", e)
            error_msg = str(e)
            try:
                return f"TextFailed: {error_msg}"
            except UnicodeEncodeError:
                return "TextFailed: API TextError(Text)"

    def _chat_completions_call(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 4096,
        timeout_sec: float = 35.0,
    ) -> str:
        final_messages: List[Dict[str, str]] = []
        if system:
            final_messages.append({"role": "system", "content": system})
        for msg in messages:
            role = (msg.get("role") or "user").strip().lower()
            if role not in {"system", "assistant", "user"}:
                role = "user"
            final_messages.append({"role": role, "content": str(msg.get("content") or "")})

        try:
            last_error = None
            using_official = self._using_official_openai()
            deadline = time.monotonic() + max(0.1, float(timeout_sec or 0.1))
            for model_name in self._candidate_models():
                if (deadline - time.monotonic()) <= 0:
                    break
                for attempt in range(1, self._per_model_attempts() + 1):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        response = self.client.chat.completions.create(
                            model=model_name,
                            messages=final_messages,
                            max_tokens=max_tokens,
                            timeout=max(0.1, remaining),
                        )
                        text = self._extract_text(response).strip()
                        if text:
                            self.model = model_name
                            return text
                        if not using_official:
                            stream_text = self._chat_completions_call_stream_http(
                                final_messages=final_messages,
                                model_name=model_name,
                                max_tokens=max_tokens,
                                timeout_sec=remaining,
                            ).strip()
                            if stream_text:
                                self.model = model_name
                                return stream_text
                    except (BadRequestError, NotFoundError) as e:
                        last_error = e
                        if self._should_try_next_model(e):
                            logger.warning("GPT5.3 chat fallback from %s after model error: %s", model_name, e)
                            break
                        raise
                    except APIError as e:
                        last_error = e
                        if self._is_retryable_api_error(e) and attempt < self._per_model_attempts():
                            logger.warning("GPT5.3 chat retry %s attempt %d after API error: %s", model_name, attempt, e)
                            sleep_for = min(0.6 * attempt, max(0.0, deadline - time.monotonic()))
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                            continue
                        if self._should_try_next_model(e) or ((not using_official) and self._is_retryable_api_error(e)):
                            logger.warning("GPT5.3 chat fallback from %s after API error: %s", model_name, e)
                            break
                        raise
                    except Exception as e:
                        if self._is_openai_parsing_import_error(e):
                            logger.warning(
                                "OpenAI SDK parsing import error detected, fallback to raw HTTP chat/completions: %s",
                                e,
                            )
                            return self._chat_completions_call_raw_http(
                                final_messages=final_messages,
                                max_tokens=max_tokens,
                                timeout_sec=timeout_sec,
                            )
                        raise
            if last_error:
                raise last_error
        except AuthenticationError as e:
            logger.error("GPT5.3 compatible chat/completions auth failed: %s", e)
            error_msg = str(e)
            return f"TextFailed: GPT5.3 API TextFailed: {error_msg}"
        except APIError as e:
            logger.error("GPT5.3 compatible chat/completions API error: %s", e)
            error_msg = str(e)
            return f"TextFailed: GPT5.3 API TextError: {error_msg}"
        except Exception as e:
            logger.error("GPT5.3 compatible chat/completions failed: %s", e)
            error_msg = str(e)
            try:
                # TextErrorText
                return f"TextFailed: {error_msg}"
            except UnicodeEncodeError:
                # Text, TextErrorText
                return "TextFailed: API TextError(Text)"
        return "TextFailed: OpenAI Compatible Text"

    def _estimate_timeout_sec(self, prompt: str, max_tokens: int = 4096) -> float:
        prompt_len = len(prompt or "")
        dynamic = 24.0 + (prompt_len / 420.0) + (max_tokens / 180.0)
        return max(25.0, min(90.0, dynamic))

    def _call(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 4096,
        timeout_sec: Optional[float] = None,
    ) -> str:
        prompt = self._messages_to_prompt(messages)
        timeout_budget = float(timeout_sec or self._estimate_timeout_sec(prompt, max_tokens))
        if not self._using_official_openai():
            return self._chat_completions_call(
                messages=messages,
                system=system,
                max_tokens=max_tokens,
                timeout_sec=timeout_budget,
            )
        return self._responses_call(
            prompt=prompt,
            instructions=system or None,
            max_output_tokens=max_tokens,
            tools=[],
            timeout_sec=timeout_budget,
        )

    def _response_text(self, response) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return self._sanitize_text_response(output_text)
        try:
            return self._extract_text(response)
        except Exception:
            return str(response)

    def _web_search_call(self, prompt: str, instructions: Optional[str] = None, max_output_tokens: int = 4096) -> str:
        using_official = self._using_official_openai()
        if not using_official:
            fallback = self._call(
                messages=[{"role": "user", "content": prompt}],
                system=(instructions or "") + "\nCurrentTextSearch, Text. ",
                max_tokens=max_output_tokens,
                timeout_sec=18.0,
            )
            if fallback.startswith("TextFailed"):
                return fallback
            return f"{fallback}\n\n[warning] third-party base_url detected, web_search skipped."

        # Gateways can intermittently timeout on web_search. Retry and then degrade to plain responses.
        for attempt in range(1, 3):
            result = self._responses_call(
                prompt=prompt,
                instructions=instructions,
                max_output_tokens=max_output_tokens,
                tools=[{"type": "web_search"}],
                timeout_sec=32.0,
            )
            if "Error code: 504" not in result:
                return result
            logger.warning("GPT5.3 web_search attempt %d hit 504", attempt)
            if attempt < 2:
                time.sleep(1.2 * attempt)

        fallback = self._responses_call(
            prompt=prompt,
            instructions=(instructions or "") + "\nText, Text. ",
            max_output_tokens=max_output_tokens,
            tools=[],
            timeout_sec=30.0,
        )
        if fallback.startswith("TextFailed"):
            return fallback
        return f"{fallback}\n\n[warning] web_search Text(504), TextAutoText. "

    def chat(
        self,
        prompt: str,
        history: Optional[List[Dict]] = None,
        max_tokens: int = 4096,
        timeout_sec: Optional[float] = None,
    ) -> str:
        messages = list(history or []) + [{"role": "user", "content": prompt}]
        return self._call(messages, max_tokens=max_tokens, timeout_sec=timeout_sec)

    def chat_with_system(self, system_prompt: str, user_message: str,
                         history: Optional[List[Dict]] = None) -> str:
        messages = list(history or []) + [{"role": "user", "content": user_message}]
        return self._call(messages, system=system_prompt)

    def search(self, query: str, time_range_days: int = 7) -> str:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=time_range_days)
        prompt = (
            f"TextSearchText, Text {start_date.strftime('%Y-%m-%d')} Text {end_date.strftime('%Y-%m-%d')} Text: \n\n{query}"
        )
        return self._web_search_call(prompt, instructions="TextResearchText, TextSearchText. ")

    def search_news_structured(self, stock_name: str, related_entities: List[str],
                               time_range_days: int = 7, playbook: Optional[Dict] = None,
                               force_refresh: bool = False) -> List[Dict]:
        metadata = {
            "_is_metadata": True,
            "total_dimensions": 1,
            "successful_dimensions": 0,
            "failed_dimensions": [],
            "search_warnings": []
        }

        # Text
        using_official = self._using_official_openai()
        if not using_official:
            # Text: Text, Text
            logger.info("Text %s, TextGenerateNewsSummary", self.base_url)

            entities_str = ", ".join(related_entities[:3]) if related_entities else "Text"
            prompt = f"""Text, Text {stock_name} Text(Text5Text). 

Text: {entities_str}

Text: 
1. Text, Text
2. Text, Text"Text"
3. Text JSON Text: [{{"title": "Text", "summary": "Summary", "source": "Text", "relevance_score": 0.5}}]
4. Text, Text []

Text JSON Text, Text. """

            result = self._call(
                messages=[{"role": "user", "content": prompt}],
                system="TextResearchText. Text, Text. ",
                max_tokens=1200,
                timeout_sec=20.0,
            )

            if result.startswith("TextFailed"):
                metadata["failed_dimensions"].append({"dimension": "model_knowledge", "error": result})
                metadata["search_warnings"].append(f"TextFailed: {result}")
                return [metadata]

            # Text JSON
            try:
                match = re.search(r'\[[\s\S]*?\]', result)
                if match:
                    news_list = json.loads(match.group())
                    if isinstance(news_list, list) and len(news_list) > 0:
                        normalized = []
                        for item in news_list:
                            if isinstance(item, dict):
                                row = dict(item)
                                row["is_verifiable"] = False  # Text
                                row["is_synthetic"] = True    # TextGenerate
                                row.setdefault("source", "Text")
                                normalized.append(row)

                        if normalized:
                            metadata["successful_dimensions"] = 1
                            metadata["search_warnings"].append(
                                "⚠️ CurrentTextGenerate, TextNewsSearch. "
                                "TextSearchText. "
                            )
                            return [metadata] + normalized
            except (json.JSONDecodeError, AttributeError) as e:
                logger.warning("Text JSON Failed: %s", e)

            # TextFailed, Text
            metadata["search_warnings"].append(
                f"CurrentText({self.base_url})TextSearch. "
                "Text: 1) TextSearchText; 2) ManualUploadNewsTextAnalysis. "
            )
            return [metadata]

        # Text OpenAI API TextSearch
        prompt = f"""TextSearch, Text {stock_name} Text {time_range_days} Text 3-5 Text. 
Text: {', '.join(related_entities) if related_entities else 'Text'}
Text JSON Text, Text: title, summary, source, relevance_score(0-1). """

        result = self._web_search_call(
            prompt,
            instructions="TextResearchText. TextSearchTextNews, Text JSON Text. ",
            max_output_tokens=900,
        )

        if result.startswith("TextFailed"):
            metadata["failed_dimensions"].append({"dimension": "latest_news", "error": result})
            metadata["search_warnings"].append(result)
            return [metadata]

        try:
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                news_list = json.loads(match.group())
                if isinstance(news_list, list) and len(news_list) > 0:
                    normalized = []
                    for item in news_list:
                        if isinstance(item, dict):
                            row = dict(item)
                            row.setdefault("is_verifiable", True)
                            row.setdefault("is_synthetic", False)
                            normalized.append(row)
                        else:
                            normalized.append(
                                {
                                    "title": str(item),
                                    "summary": "",
                                    "source": "GPT5.3 web_search",
                                    "relevance_score": 0.4,
                                    "is_verifiable": False,
                                    "is_synthetic": True,
                                }
                            )
                    metadata["successful_dimensions"] = 1
                    return [metadata] + normalized
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning("Text GPT5.3 News JSON Failed: %s", e)

        # TextFailed, TextSummary
        metadata["search_warnings"].append("TextSearchText, Text JSON Text, TextSummary. ")
        metadata["successful_dimensions"] = 1
        return [metadata, {"title": f"{stock_name} Text", "summary": result[:500],
                           "source": "GPT5.3 web_search", "relevance_score": 0.5,
                           "is_verifiable": False, "is_synthetic": True}]

    def search_stock_performance(self, stock_name: str, ticker: str = "", days: int = 7) -> str:
        prompt = f"Text {stock_name}({ticker})Text. Text, Text. "
        return self._call([{"role": "user", "content": prompt}])

    def analyze_file(self, file_path: str, prompt: str) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            messages = [{"role": "user", "content": f"{prompt}\n\nText: \n{content}"}]
            return self._call(messages, max_tokens=8192)
        except Exception as e:
            return f"TextFailed: {e}"

    def structured_output(self, prompt: str, schema_description: str) -> Dict:
        full_prompt = f"{prompt}\n\nText JSON: {schema_description}"
        result = self._call([{"role": "user", "content": full_prompt}])
        try:
            match = re.search(r'\{.*\}', result, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning("Text GPT5.3 TextFailed: %s", e)
        return {"raw": result}

    def capabilities(self) -> Dict[str, bool]:
        """Text, Text LLMRuntime Text"""
        # Text OpenAI API Text web_search
        supports_web_search = self._using_official_openai()
        return {
            "supports_web_search": supports_web_search,
            "supports_streaming": False,
            "supports_structured_json": True,
        }
