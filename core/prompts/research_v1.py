"""Deep research prompts v1."""

DECISION_SNAPSHOT_PROMPT_V1 = """TextResearchText. TextTextDecisionText(Text). 
Text JSON: 
{
  "recommendation": "buy/add/hold/reduce/sell/watch",
  "confidence": "high/medium/low",
  "thesis_impact": "strengthen/weaken/shake/neutral",
  "key_finding": "Text",
  "reasoning": "2-4Text",
  "risk_flags": ["..."],
  "next_checks": ["..."]
}

Text: {stock_name}
Text: {trigger_reason}
Playbook: {stock_playbook}
Text: {environment_changes}
TextNewsSummary: {news_summary}
TextSearchResult(Text): {search_results}
"""


FULL_RESEARCH_PROMPT_V1 = """TextResearchText, TextResearchText(Markdown), Text JSON Text. 
Text: 
1) What changed since last research
2) Text(event / direction / confidence / horizon / actionability / follow-up)
3) Text: earnings/guidance, estimate revision, valuation percentile, macro sensitivity
4) RiskText

Text: {stock_name}
Text: {trigger_reason}
TextPlaybook: {portfolio_playbook}
TextPlaybook: {stock_playbook}
Text: {user_preferences}
HistoryResearch: {research_history}
Text: {environment_changes}
HistoryUploadText: {historical_uploads}
ResearchText: {research_plan}
TextSearchResult: {search_results}
TextNewsSummary: {news_summary}
"""

