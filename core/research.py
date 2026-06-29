"""Deep research engine with staged generation."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .prompt_utils import render_prompt
from .prompts.research_v1 import DECISION_SNAPSHOT_PROMPT_V1, FULL_RESEARCH_PROMPT_V1
from .storage import Storage

logger = logging.getLogger(__name__)


class ResearchEngine:
    """Execute staged deep research for investment workflows."""

    def __init__(self, client: Any, storage: Storage):
        self.client = client
        self.storage = storage

    def execute_research(
        self,
        stock_id: str,
        research_plan: Dict,
        environment_data: Dict,
        force_refresh: bool = False,
        staged_mode: bool = True,
        expand_full_report: bool = False,
    ) -> Dict:
        portfolio_playbook = self.storage.get_portfolio_playbook()
        stock_playbook = self.storage.get_stock_playbook(stock_id)
        recent_history = self.storage.get_recent_research(stock_id, limit=5)
        research_context = self.storage.get_research_context(stock_id, limit=3)
        user_preferences = self.storage.get_preferences_for_prompt()
        historical_uploads = self.storage.get_historical_uploads(stock_id, limit=5)
        stock_name = stock_playbook.get("stock_name", stock_id) if stock_playbook else stock_id

        search_results = self._execute_searches(
            research_plan=research_plan,
            playbook=stock_playbook,
            force_refresh=force_refresh,
            max_search_tasks=8,
        )
        context = self._build_prompt_context(
            stock_name=stock_name,
            research_plan=research_plan,
            environment_data=environment_data,
            portfolio_playbook=portfolio_playbook,
            stock_playbook=stock_playbook,
            user_preferences=user_preferences,
            research_context=research_context,
            recent_history=recent_history,
            historical_uploads=historical_uploads,
            search_results=search_results,
        )

        snapshot_report = self._generate_snapshot(context, force_refresh=force_refresh)
        snapshot_conclusion = self._extract_conclusion(snapshot_report)

        full_report = snapshot_report
        if not staged_mode:
            expand_full_report = True
        if expand_full_report:
            full_report = self._generate_full_report(context, force_refresh=force_refresh)

        conclusion = self._extract_conclusion(full_report)
        if not conclusion.get("_parse_success", False):
            conclusion = snapshot_conclusion

        key_findings = self._build_key_findings(conclusion, snapshot_conclusion)
        return {
            "snapshot_report": snapshot_report,
            "full_report": full_report,
            "full_report_generated": bool(expand_full_report),
            "conclusion": conclusion,
            "key_findings": key_findings,
            "search_results": search_results,
            "executed_at": datetime.now().isoformat(),
            "runtime_meta": self._get_runtime_meta(),
        }

    def _generate_snapshot(self, context: Dict[str, Any], force_refresh: bool = False) -> str:
        prompt = render_prompt(
            DECISION_SNAPSHOT_PROMPT_V1,
            max_chars=12000,
            **context,
        )
        return self._call_chat(prompt, force_refresh=force_refresh)

    def _generate_full_report(self, context: Dict[str, Any], force_refresh: bool = False) -> str:
        prompt = render_prompt(
            FULL_RESEARCH_PROMPT_V1,
            max_chars=28000,
            **context,
        )
        return self._call_chat(prompt, force_refresh=force_refresh)

    def _build_prompt_context(
        self,
        stock_name: str,
        research_plan: Dict,
        environment_data: Dict,
        portfolio_playbook: Optional[Dict],
        stock_playbook: Optional[Dict],
        user_preferences: str,
        research_context: List[Dict],
        recent_history: List[Dict],
        historical_uploads: List[Dict],
        search_results: str,
    ) -> Dict[str, str]:
        portfolio_str = json.dumps(portfolio_playbook, ensure_ascii=False, indent=2) if portfolio_playbook else "(No data)"
        stock_playbook_str = json.dumps(stock_playbook, ensure_ascii=False, indent=2) if stock_playbook else "(No data)"
        plan_str = json.dumps(research_plan, ensure_ascii=False, indent=2)
        history_str = self._format_history(research_context, recent_history)
        env_str = self._format_environment(environment_data)
        news_summary = self._format_news_summary(environment_data)
        historical_str = self._format_historical_uploads(historical_uploads)
        trigger_reason = research_plan.get("trigger_reason", "") if isinstance(research_plan, dict) else ""
        return {
            "stock_name": stock_name,
            "trigger_reason": trigger_reason,
            "portfolio_playbook": portfolio_str,
            "stock_playbook": stock_playbook_str,
            "user_preferences": user_preferences,
            "research_history": history_str,
            "environment_changes": env_str,
            "historical_uploads": historical_str,
            "research_plan": plan_str,
            "search_results": search_results,
            "news_summary": news_summary,
        }

    def _execute_searches(
        self,
        research_plan: Dict,
        playbook: Optional[Dict],
        force_refresh: bool = False,
        max_search_tasks: int = 6,  # Text 8 Text 6
    ) -> str:
        days = 14
        tasks: List[Tuple[str, str]] = []
        for module in (research_plan.get("research_modules", []) or [])[:4]:
            module_name = module.get("module_name", "ResearchText")
            for query in (module.get("search_queries", []) or [])[:2]:
                tasks.append((f"## Text: {module_name}\n### Text: {query}\n", query))
        if not tasks:
            for hypothesis in (research_plan.get("hypothesis_to_test", []) or [])[:2]:
                verify_query = hypothesis.get("how_to_verify")
                if verify_query:
                    tasks.append((f"### Text: {hypothesis.get('hypothesis', '')}\n", verify_query))
        if not tasks and research_plan.get("research_objective"):
            objective = research_plan.get("research_objective")
            tasks.append((f"### ResearchText: {objective}\n", objective))
        if not tasks:
            return "(TextSearch)"
        tasks = tasks[: max(1, max_search_tasks)]

        def run_one(indexed: Tuple[int, Tuple[str, str]]) -> Tuple[int, str, str]:
            idx, (label, query) = indexed
            try:
                result = self._call_search(query=query, days=days, force_refresh=force_refresh)
                return idx, label, result
            except Exception as exc:
                return idx, label, f"(SearchText: {exc})"

        results_by_idx: Dict[int, Tuple[str, str]] = {}
        max_workers = min(6, len(tasks))  # Text 4 Text 6
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(run_one, item) for item in enumerate(tasks)]
            for future in as_completed(futures):
                idx, label, content = future.result()
                results_by_idx[idx] = (label, content)

        lines: List[str] = []
        for idx in range(len(tasks)):
            label, content = results_by_idx.get(idx, tasks[idx])
            lines.extend([label, content, ""])
        return "\n".join(lines)

    def _call_chat(self, prompt: str, force_refresh: bool = False) -> str:
        try:
            return self.client.chat(prompt, force_refresh=force_refresh)
        except TypeError:
            return self.client.chat(prompt)

    def _call_search(self, query: str, days: int, force_refresh: bool = False) -> str:
        """
        Text Tavily/Google RSS TextSearch, Text LLM Web Search. 
        Text Tavily(Text, Text), Text Google RSS. 
        """
        # Text Tavily API
        tavily_key = self.storage.get_tavily_api_key()
        if tavily_key:
            try:
                from tavily import TavilyClient
                client = TavilyClient(api_key=tavily_key)
                response = client.search(
                    query=query,
                    topic="news",
                    days=days,
                    max_results=5,
                    search_depth="basic",
                )
                results = response.get("results", [])
                if results:
                    lines = []
                    for item in results[:5]:
                        title = item.get("title", "")
                        content = item.get("content", "")[:200]
                        url = item.get("url", "")
                        lines.append(f"**{title}**\n{content}\nText: {url}\n")
                    return "\n".join(lines)
            except Exception as e:
                logger.warning("Tavily SearchFailed: %s, Text Google RSS", e)

        # Text Google RSS
        try:
            from . import rss_news_client
            import feedparser
            import urllib.parse

            params = urllib.parse.urlencode({
                "q": query,
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            })
            url = f"https://news.google.com/rss/search?{params}"
            feed = feedparser.parse(url)

            if feed.entries:
                lines = []
                for entry in feed.entries[:5]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")[:200]
                    link = entry.get("link", "")
                    lines.append(f"**{title}**\n{summary}\nText: {link}\n")
                return "\n".join(lines)
            else:
                return f"(Google RSS TextNews: {query})"
        except Exception as e:
            logger.error("Google RSS SearchFailed: %s", e)
            return f"(SearchFailed: {str(e)[:100]})"

    def _get_runtime_meta(self) -> Dict[str, Any]:
        if hasattr(self.client, "get_runtime_meta"):
            return self.client.get_runtime_meta()
        return {
            "provider": "unknown",
            "model": "unknown",
            "base_url": "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "request_mode": "deep_synthesis",
            "degraded": False,
            "degraded_reason": "",
            "cache_hit": False,
            "runtime_seconds": 0.0,
        }

    def _format_environment(self, environment_data: Dict[str, Any]) -> str:
        lines: List[str] = []
        auto = environment_data.get("auto_collected") or []
        for item in auto[:30]:
            lines.append(f"- [{item.get('date', '')}] {item.get('title', '')}: {item.get('summary', '')[:120]}")
        uploaded = environment_data.get("user_uploaded") or []
        for item in uploaded[:20]:
            lines.append(f"- UploadText {item.get('filename', '')}: {item.get('summary', '')[:120]}")
        return "\n".join(lines) if lines else "(Text)"

    def _format_news_summary(self, environment_data: Dict[str, Any]) -> str:
        auto = environment_data.get("auto_collected") or []
        if not auto:
            return "(No dataNews)"
        lines = []
        for idx, item in enumerate(auto[:12], 1):
            lines.append(
                f"{idx}. {item.get('title', 'Text')} | {item.get('date', '')} | "
                f"{item.get('summary', '')[:140]}"
            )
        return "\n".join(lines)

    def _format_history(self, research_context: List[Dict], recent_history: List[Dict]) -> str:
        if research_context:
            lines = []
            for row in research_context[:4]:
                result = row.get("research_result", {})
                feedback = row.get("user_feedback", {})
                lines.append(
                    f"- {row.get('date', '')[:10]} | Text={result.get('recommendation', 'unknown')} | "
                    f"Text={result.get('confidence', 'unknown')} | Text={result.get('reasoning', '')[:180]} | "
                    f"Text={feedback.get('decision', 'n/a')}"
                )
            return "\n".join(lines)
        if recent_history:
            lines = []
            for row in recent_history[:4]:
                result = row.get("research_result", {})
                lines.append(
                    f"- {row.get('date', '')[:10]} | Text={result.get('recommendation', 'unknown')} | "
                    f"Text={result.get('reasoning', '')[:180]}"
                )
            return "\n".join(lines)
        return "(No dataHistoryResearch)"

    def _format_historical_uploads(self, historical_uploads: List[Dict]) -> str:
        if not historical_uploads:
            return "(No dataHistoryUploadText)"
        lines = []
        for item in historical_uploads[:10]:
            lines.append(f"- [{item.get('date', '')}] {item.get('filename', '')}: {item.get('summary', '')[:180]}")
        return "\n".join(lines)

    def _extract_conclusion(self, response: str) -> Dict:
        parse_error = None

        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                if isinstance(result, dict):
                    result["_parse_success"] = True
                    return result
            except json.JSONDecodeError as exc:
                parse_error = f"JSON parse failed(code block): {exc}"
                self.storage.log(parse_error, "WARNING")

        object_match = re.search(r"\{[\s\S]*\}", response)
        if object_match:
            try:
                result = json.loads(object_match.group(0))
                if isinstance(result, dict):
                    result["_parse_success"] = True
                    return result
            except json.JSONDecodeError as exc:
                parse_error = f"JSON parse failed(raw object): {exc}"
                self.storage.log(parse_error, "WARNING")

        return {
            "thesis_impact": "Text",
            "recommendation": "watch",
            "confidence": "low",
            "reasoning": response[:280] if response else "TextAutoText",
            "follow_up_items": [],
            "_parse_success": False,
            "_parse_error": parse_error or "Text JSON Text",
        }

    def _build_key_findings(self, conclusion: Dict, snapshot_conclusion: Dict) -> List[str]:
        source = conclusion if conclusion.get("_parse_success") else snapshot_conclusion
        findings: List[str] = []
        if source.get("key_finding"):
            findings.append(str(source["key_finding"]))
        if source.get("reasoning"):
            findings.append(str(source["reasoning"])[:180])
        risks = source.get("key_risks") or source.get("risk_flags") or []
        for risk in risks[:2]:
            findings.append(f"Risk: {risk}")
        next_checks = source.get("follow_up_items") or source.get("next_checks") or []
        for item in next_checks[:2]:
            findings.append(f"Text: {item}")
        return findings[:6]

    def save_research_record(
        self,
        stock_id: str,
        environment_data: Dict,
        impact_assessment: Dict,
        research_result: Optional[Dict],
        user_feedback: Optional[Dict] = None,
    ):
        record = {
            "trigger": "user_initiated",
            "environment_input": {
                "time_range": environment_data.get("time_range", "7d"),
                "auto_collected": environment_data.get("auto_collected", []),
                "user_uploaded": environment_data.get("user_uploaded", []),
            },
            "impact_assessment": {
                "needs_deep_research": impact_assessment.get("judgment", {}).get("needs_deep_research", False),
                "reason": impact_assessment.get("conclusion", {}).get("summary", ""),
                "affected_thesis_points": impact_assessment.get("research_plan", {}).get("related_playbook_points", []),
            },
            "research_plan": impact_assessment.get("research_plan"),
            "research_result": research_result.get("conclusion") if research_result else None,
            "full_report": research_result.get("full_report") if research_result else None,
            "snapshot_report": research_result.get("snapshot_report") if research_result else None,
            "user_feedback": user_feedback,
        }
        self.storage.add_research_record(stock_id, record)

    def collect_feedback(self, recommendation: str) -> Dict:
        return {
            "final_decision": None,
            "differs_from_recommendation": False,
            "reason": None,
            "actual_result": None,
        }

