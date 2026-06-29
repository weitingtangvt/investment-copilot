"""Environment prompts v1 (US equities first)."""

NEWS_ENRICH_PROMPT_V1 = """TextResearchText. TextNewsText JSON Text: 
Text: index, is_relevant(true/false, NewsText), importance(Text/Text/Text), summary_short(<=30Text), event_type, thesis_impact_direction(positive/negative/neutral),
confidence(high/medium/low), expected_horizon(short/medium/long), actionability(watch/add/reduce/exit/hold), required_follow_up_data(Text). 
Text JSON. TextNewsText(TextNews), is_relevant Text false. 

Text: {stock_name}
NewsText:
{news_items}
"""


IMPACT_ASSESSMENT_PROMPT_V1 = """TextResearchText. Text JSON: 
- judgment: needs_deep_research, confidence, urgency
- conclusion: summary, key_risk, key_opportunity
- research_plan: research_objective, hypothesis_to_test[], research_modules[], key_metrics_to_track[], timeline
- what_changed_since_last_research: changed_points[], unchanged_points[], invalidation_risk

Text: 
1) Text; 
2) Text earnings/guidance, estimate revision, valuation percentile, macro sensitivity; 
3) Text, Text. 

HistoryResearch:
{recent_research_history}

TextPlaybook:
{portfolio_playbook}

TextPlaybook:
{stock_playbook}

Text:
{user_preferences}

Text: {time_range}

AutoTextNews:
{auto_collected_news}

TextUploadText:
{user_uploaded_content}

HistoryUploadText:
{historical_uploads}
"""

