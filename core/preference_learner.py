"""Text"""

import json
import re
from typing import Any, Dict, List, Optional
from datetime import datetime

from .storage import Storage


PREFERENCE_EXTRACTION_PROMPT = """## Text
TextAnalysisText, TextDecisionText. 

## Text
Text, Text. Text"TextXText, TextY"Text. 

## Text
{interaction_data}

## Text
Text JSON Text: 

```json
{{
  "extracted_preferences": [
    {{
      "trigger": "Text(Text)",
      "my_response": "Text/Text(Text)",
      "category": "Text(decision_style/risk_tolerance/research_focus/communication_style)",
      "confidence": "Text/Text/Text",
      "reasoning": "Text"
    }}
  ],
  "preference_summary": {{
    "decision_style": "TextDecisionText(Text: Text, Text)",
    "risk_tolerance": "RiskText(Text: Text, Text)",
    "research_focus": ["TextResearchText, Text: Text", "Text"],
    "disliked_patterns": ["Text, Text: TextAnalysis", "Text"],
    "custom_rules": ["Text, Text: Text"]
  }}
}}
```

Text: 
1. Text, Text
2. Text, Text
3. Text, Text"""


class PreferenceLearner:
    """Text"""

    def __init__(self, client: Any, storage: Storage):
        self.client = client
        self.storage = storage

    def log_feedback_interaction(
        self,
        stock_id: str,
        stock_name: str,
        context: Dict,
        feedback: Dict
    ):
        """Text"""
        interaction = {
            "type": "research_feedback",
            "stock_id": stock_id,
            "stock_name": stock_name,
            "context": {
                "ai_recommendation": context.get("recommendation", ""),
                "ai_confidence": context.get("confidence", ""),
                "ai_reasoning": context.get("reasoning", ""),
                "thesis_impact": context.get("thesis_impact", "")
            },
            "user_feedback": {
                "decision": feedback.get("final_decision", ""),
                "feedback_on_research": feedback.get("feedback_on_research", ""),
                "needs_further_research": feedback.get("needs_further_research", ""),
                "further_research_direction": feedback.get("further_research_direction", ""),
                "tracking_metrics": feedback.get("tracking_metrics", [])
            }
        }
        self.storage.log_interaction(interaction)

    def log_plan_adjustment(
        self,
        stock_id: str,
        stock_name: str,
        original_plan: Dict,
        adjustment_request: str,
        adjusted_plan: Dict
    ):
        """Text"""
        interaction = {
            "type": "plan_adjustment",
            "stock_id": stock_id,
            "stock_name": stock_name,
            "context": {
                "original_objective": original_plan.get("research_objective", ""),
                "original_modules": [m.get("module_name", "") for m in original_plan.get("research_modules", [])]
            },
            "user_adjustment": adjustment_request,
            "result": {
                "new_objective": adjusted_plan.get("research_objective", ""),
                "new_modules": [m.get("module_name", "") for m in adjusted_plan.get("research_modules", [])]
            }
        }
        self.storage.log_interaction(interaction)

    def log_follow_up_question(
        self,
        stock_id: str,
        stock_name: str,
        research_context: str,
        question: str
    ):
        """Text"""
        interaction = {
            "type": "follow_up_question",
            "stock_id": stock_id,
            "stock_name": stock_name,
            "context": research_context[:200],  # Text
            "user_question": question
        }
        self.storage.log_interaction(interaction)

    def log_playbook_edit(
        self,
        stock_id: str,
        stock_name: str,
        edit_type: str,
        changes: Dict
    ):
        """Text Playbook Text"""
        interaction = {
            "type": "playbook_edit",
            "stock_id": stock_id,
            "stock_name": stock_name,
            "edit_type": edit_type,  # "add_point", "remove_point", "modify_thesis" Text
            "changes": changes
        }
        self.storage.log_interaction(interaction)

    def log_weekly_view_interaction(
        self,
        stock_id: str,
        stock_name: str,
        week_id: str,
        user_view: str
    ):
        """TextWeekly ReviewText"""
        interaction = {
            "type": "weekly_view",
            "stock_id": stock_id,
            "stock_name": stock_name,
            "week_id": week_id,
            "user_view": user_view
        }
        self.storage.log_interaction(interaction)

    def extract_preferences_from_interactions(self, limit: int = 20) -> Dict:
        """Text"""
        interactions = self.storage.get_recent_interactions(limit)

        if not interactions:
            return {"extracted_preferences": [], "preference_summary": {}}

        # Text
        interaction_text = self._format_interactions(interactions)

        # Text AI Text
        prompt = PREFERENCE_EXTRACTION_PROMPT.format(interaction_data=interaction_text)
        response = self.client.chat(prompt)

        # TextResult
        result = self._extract_json(response)
        if not result:
            return {"extracted_preferences": [], "preference_summary": {}}

        return result

    def learn_and_save_preferences(self) -> Dict:
        """TextSaveText"""
        result = self.extract_preferences_from_interactions()

        # SaveText
        for pref in result.get("extracted_preferences", []):
            # Text
            if not self._preference_exists(pref):
                self.storage.add_preference({
                    "trigger": pref.get("trigger", ""),
                    "my_response": pref.get("my_response", ""),
                    "category": pref.get("category", "general"),
                    "confidence": pref.get("confidence", "Text"),
                    "reasoning": pref.get("reasoning", ""),
                    "source": "auto_extracted"
                })

        # Text
        summary = result.get("preference_summary", {})
        if summary:
            current_summary = self.storage.get_user_preferences().get("preference_summary", {})
            # Text
            merged_summary = self._merge_summaries(current_summary, summary)
            self.storage.update_preference_summary(merged_summary)

        return result

    def _preference_exists(self, new_pref: Dict) -> bool:
        """Text"""
        existing = self.storage.get_active_preferences()
        new_trigger = new_pref.get("trigger", "").lower()

        for pref in existing:
            existing_trigger = pref.get("trigger", "").lower()
            # Text
            if new_trigger in existing_trigger or existing_trigger in new_trigger:
                return True
        return False

    def _merge_summaries(self, current: Dict, new: Dict) -> Dict:
        """Text"""
        merged = current.copy()

        # Text: Text
        for field in ["decision_style", "risk_tolerance"]:
            if new.get(field) and len(new.get(field, "")) > len(current.get(field, "")):
                merged[field] = new[field]

        # Text: Text
        for field in ["research_focus", "disliked_patterns", "custom_rules"]:
            current_list = set(current.get(field, []))
            new_list = set(new.get(field, []))
            merged[field] = list(current_list | new_list)

        return merged

    def _format_interactions(self, interactions: List[Dict]) -> str:
        """Text"""
        lines = []
        for i, inter in enumerate(interactions, 1):
            lines.append(f"\n### Text {i} ({inter.get('type', 'unknown')})")
            lines.append(f"Text: {inter.get('timestamp', '')[:10]}")

            if inter.get("stock_name"):
                lines.append(f"Stock: {inter.get('stock_name')}")

            if inter["type"] == "research_feedback":
                ctx = inter.get("context", {})
                fb = inter.get("user_feedback", {})
                lines.append(f"AIText: {ctx.get('ai_recommendation', '')} (Text: {ctx.get('ai_confidence', '')})")
                lines.append(f"TextDecision: {fb.get('decision', '')}")
                if fb.get("feedback_on_research"):
                    lines.append(f"Text: {fb.get('feedback_on_research')}")
                if fb.get("further_research_direction"):
                    lines.append(f"TextResearchText: {fb.get('further_research_direction')}")

            elif inter["type"] == "plan_adjustment":
                lines.append(f"Text: {inter.get('user_adjustment', '')}")

            elif inter["type"] == "follow_up_question":
                lines.append(f"Text: {inter.get('user_question', '')}")

            elif inter["type"] == "playbook_edit":
                lines.append(f"Text: {inter.get('edit_type', '')}")
                lines.append(f"Text: {json.dumps(inter.get('changes', {}), ensure_ascii=False)}")

        return "\n".join(lines)

    def _extract_json(self, response: str) -> Optional[Dict]:
        """Text JSON"""
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        return None

    def add_manual_preference(
        self,
        trigger: str,
        my_response: str,
        category: str = "general"
    ) -> str:
        """ManualText"""
        return self.storage.add_preference({
            "trigger": trigger,
            "my_response": my_response,
            "category": category,
            "confidence": "Text",
            "reasoning": "TextManualText",
            "source": "manual"
        })

    def get_preferences_context(self) -> str:
        """Text prompt Text"""
        return self.storage.get_preferences_for_prompt()
