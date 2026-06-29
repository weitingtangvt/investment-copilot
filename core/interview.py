"""Text"""

import json
import re
from typing import Any, Optional, Dict, List, Tuple

from .storage import Storage


PORTFOLIO_INTERVIEW_PROMPT = """## Text
Text, Text. 

## Text
Text: 
1. CurrentText/Text
2. Text
3. TextRiskText
4. Text

## Text
- Text
- Text, Text
- Text, Text
- TextConfirm
- Text

## Text
Text1 - Text:
  - "TextCurrentText?"
  - "Text?Text?"
  - "Text?"

Text2 - Text:
  - "TextCurrentText(Text, Text)Text?"
  - "Text?"

Text3 - Text:
  - "Text?(TextStock, TextCash)"
  - "Text?"
  - "TextHoldingsText?"

Text4 - ConfirmText:
  - Text, Generate JSON TextConfirm

## TextHistory
{conversation_history}

## Text
TextHistory, Text: 
1. Text, Text(Text, Text)
2. Text, Text JSON Text Playbook Text

Text, Text: 
```json
{{
  "market_views": {{
    "bullish_themes": [
      {{"theme": "Text", "reasoning": "Text", "confidence": "Text/Text/Text"}}
    ],
    "bearish_themes": [
      {{"theme": "Text", "reasoning": "Text", "confidence": "Text/Text/Text"}}
    ],
    "macro_views": ["Text1", "Text2"]
  }},
  "portfolio_strategy": {{
    "target_allocation": {{"Text1": "Text1", "Text2": "Text2"}},
    "risk_tolerance": "RiskText",
    "holding_period": "Text"
  }},
  "watchlist": ["Text1", "Text2"]
}}
```"""


STOCK_INTERVIEW_PROMPT = """## Text
Text, Text. 

## Text
Text: 
1. Text(Text)
2. Text Playbook Text
3. Text(Text)
4. Text(Text)
5. Text(Text, Text, Text)

## Text
- Text
- Text, Text
- Text, Text
- Text, TextConfirmText, Text
- Text, Text JSON Text Playbook Text
- Text

## Text Playbook
{portfolio_playbook}

## CurrentStock
TextBuy: {stock_name}

## TextHistory
{conversation_history}

## Text
TextHistory, Text: 
1. Text, Text(Text, Text)
2. Text, Text JSON Text Playbook Text
3. Text Playbook Text

Text, Text: 
```json
{{
  "stock_name": "StockText",
  "ticker": "StockTicker",
  "core_thesis": {{
    "summary": "Text",
    "key_points": ["Text1", "Text2"],
    "market_gap": "Text"
  }},
  "validation_signals": ["Text1", "Text2"],
  "invalidation_triggers": ["Text1", "Text2"],
  "operation_plan": {{
    "holding_period": "Text",
    "target_price": null,
    "stop_loss": null,
    "position_size": "Text"
  }},
  "related_entities": ["Text1", "Text2"]
}}
```"""


class InterviewManager:
    """Text"""

    def __init__(self, client: Any, storage: Storage):
        self.client = client
        self.storage = storage
        self.conversation_history: List[Dict] = []

    def reset(self):
        """TextHistory"""
        self.conversation_history = []

    def _format_history(self) -> str:
        """TextHistory"""
        if not self.conversation_history:
            return "(No data)"
        lines = []
        for msg in self.conversation_history:
            role = "Text" if msg["role"] == "assistant" else "Text"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def _extract_json(self, response: str) -> Optional[Dict]:
        """Text JSON, TextSuccess"""
        # Text1: Text markdown TickerText(Text Playbook Text)
        json_matches = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        for json_str in reversed(json_matches):  # Text
            try:
                result = json.loads(json_str)
                # Text Playbook Text(Text)
                if isinstance(result, dict) and (
                    'core_thesis' in result or  # Text Playbook
                    'market_views' in result or  # Text Playbook
                    'stock_name' in result
                ):
                    return result
            except json.JSONDecodeError:
                continue

        # Text2: Text { ... } Text JSON(TextTickerText)
        brace_match = re.search(r'\{[\s\S]*\}', response)
        if brace_match:
            try:
                result = json.loads(brace_match.group())
                if isinstance(result, dict) and (
                    'core_thesis' in result or
                    'market_views' in result or
                    'stock_name' in result
                ):
                    return result
            except json.JSONDecodeError:
                pass

        # Text3: Text
        try:
            result = json.loads(response)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Text4: Text(Text)
        for json_str in reversed(json_matches):
            cleaned = re.sub(r',(\s*[}\]])', r'\1', json_str)  # Text
            try:
                result = json.loads(cleaned)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue

        return None

    def _is_summary(self, response: str) -> bool:
        """Text(Text JSON)"""
        return bool(re.search(r'```(?:json)?\s*\{', response))

    # ==================== Text Playbook Text ====================

    def start_portfolio_interview(self) -> str:
        """Text Playbook Text"""
        self.reset()
        # Text
        first_question = "TextCurrentText?"
        self.conversation_history.append({"role": "assistant", "content": first_question})
        return first_question

    def continue_portfolio_interview(self, user_input: str) -> Tuple[str, Optional[Dict]]:
        """Text Playbook Text

        Text: (AI Text, Text Playbook Text, Text None)
        """
        self.conversation_history.append({"role": "user", "content": user_input})

        prompt = PORTFOLIO_INTERVIEW_PROMPT.format(
            conversation_history=self._format_history()
        )

        response = self.client.chat(prompt)

        # Text
        playbook = self._extract_json(response)

        if playbook:
            # SaveTextHistoryText playbook
            playbook["interview_transcript"] = self.conversation_history.copy()
            return response, playbook
        else:
            self.conversation_history.append({"role": "assistant", "content": response})
            return response, None

    # ==================== Text Playbook Text ====================

    def start_stock_interview(self, stock_name: str) -> str:
        """Text Playbook Text"""
        self.reset()

        # Text Playbook
        portfolio = self.storage.get_portfolio_playbook()
        if portfolio:
            # Text Playbook GenerateText
            bullish = portfolio.get("market_views", {}).get("bullish_themes", [])
            if bullish:
                themes = [t.get("theme", t) if isinstance(t, dict) else t for t in bullish]
                first_question = f"Text, Text{stock_name}. \n\nText Playbook Text{themes[0]}. {stock_name}Text?"
            else:
                first_question = f"Text, Text{stock_name}. \n\nTextBuy{stock_name}?Text?"
        else:
            first_question = f"Text, Text{stock_name}. \n\nTextBuy{stock_name}?Text?"

        self.conversation_history.append({"role": "assistant", "content": first_question})
        return first_question

    def continue_stock_interview(self, user_input: str, stock_name: str) -> Tuple[str, Optional[Dict]]:
        """Text Playbook Text

        Text: (AI Text, Text Playbook Text, Text None)
        """
        self.conversation_history.append({"role": "user", "content": user_input})

        # Text Playbook
        portfolio = self.storage.get_portfolio_playbook()
        portfolio_str = json.dumps(portfolio, ensure_ascii=False, indent=2) if portfolio else "(No data)"

        prompt = STOCK_INTERVIEW_PROMPT.format(
            portfolio_playbook=portfolio_str,
            stock_name=stock_name,
            conversation_history=self._format_history()
        )

        response = self.client.chat(prompt)

        # Text
        playbook = self._extract_json(response)

        if playbook:
            # Text stock_name
            if "stock_name" not in playbook:
                playbook["stock_name"] = stock_name
            # SaveTextHistory
            playbook["interview_transcript"] = self.conversation_history.copy()
            return response, playbook
        else:
            self.conversation_history.append({"role": "assistant", "content": response})
            return response, None

    # ==================== Text ====================

    def start_update_portfolio_interview(self, current_playbook: Dict) -> str:
        """Text Playbook Text"""
        self.reset()
        first_question = "Text, Text. \n\nTextCurrentText?"
        self.conversation_history.append({"role": "assistant", "content": first_question})
        return first_question

    def start_update_stock_interview(self, stock_name: str, current_playbook: Dict) -> str:
        """Text Playbook Text"""
        self.reset()
        summary = current_playbook.get("core_thesis", {}).get("summary", "")
        first_question = f"Text, Text{stock_name}Text. \n\nCurrentText{summary}, Text?"
        self.conversation_history.append({"role": "assistant", "content": first_question})
        return first_question
