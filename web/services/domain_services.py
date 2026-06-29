"""Domain service adapters for route blueprints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class LLMConfigService:
    get_llm_config: Callable[[], Any]
    save_llm_config: Callable[[], Any]


@dataclass
class BrokerImportService:
    get_ibkr_trades: Callable[[], Any]
    import_ibkr_trades: Callable[[], Any]
    get_ibkr_derived_portfolio: Callable[[], Any]


@dataclass
class ShellPagesService:
    decision_review_page: Callable[[], Any]
    portfolio_analysis_page: Callable[[], Any]
    portfolio_analysis_page_legacy: Callable[[], Any]
    portfolio_analytics_page: Callable[[], Any]
    research_history: Callable[[], Any]
    twitter_page: Callable[[], Any]
    wechat_page: Callable[[], Any]
    settings_page: Callable[[], Any]
    add_stock_page: Callable[[], Any]


@dataclass
class AppShellService:
    index_page: Callable[[], Any]
    health_check: Callable[[], Any]
    route_list: Callable[[], Any]
    healthz: Callable[[], Any]


@dataclass
class SystemApiService:
    auth_status: Callable[[], Any]
    setup_auth: Callable[[], Any]
    news_source_health: Callable[[], Any]
    get_ima_config: Callable[[], Any]
    save_ima_config: Callable[[], Any]


@dataclass
class TaskApiService:
    create_task: Callable[[], Any]
    get_task: Callable[[str], Any]
    list_tasks: Callable[[], Any]
    cancel_task: Callable[[str], Any]
    submit_us_screener_scan_task: Callable[[], Any]


@dataclass
class XueqiuService:
    page: Callable[[], Any]
    get_config: Callable[[], Any]
    save_config: Callable[[], Any]
    add_user: Callable[[], Any]
    remove_user: Callable[[], Any]
    feed: Callable[[], Any]
    feed_single: Callable[[str], Any]
    start_export_history: Callable[[], Any]
    get_export_history: Callable[[str], Any]
    download_export_history: Callable[[str, str], Any]
    start_prepare_session: Callable[[], Any]
    get_prepare_session: Callable[[str], Any]


@dataclass
class StocksService:
    stock_detail_page: Callable[[str], Any]
    stocks_page: Callable[[], Any]
    get_stock: Callable[[str], Any]
    save_stock: Callable[[str], Any]
    update_stock_ticker: Callable[[str], Any]
    delete_stock: Callable[[str], Any]
    generic_price_chart: Callable[[], Any]
    stock_commentary: Callable[[], Any]
    stock_commentary_registry: Callable[[], Any]
    save_stock_commentary_registry: Callable[[str], Any]
    playbook_page: Callable[[], Any]
    get_playbook: Callable[[str], Any]
    save_playbook: Callable[[str], Any]


@dataclass
class ResearchService:
    collect_environment: Callable[[str], Any]
    assess_impact: Callable[[str], Any]
    adjust_plan: Callable[[str], Any]
    follow_up_research: Callable[[str], Any]
    execute_research: Callable[[str], Any]
    get_research_history: Callable[[str], Any]
    save_research_feedback: Callable[[str], Any]
    get_research_context: Callable[[str], Any]
    toggle_milestone: Callable[[str, str], Any]
    scan_single_stock: Callable[[str], Any]
    batch_research_stock: Callable[[str], Any]


@dataclass
class PreferencesService:
    preferences_page: Callable[[], Any]
    get_preferences: Callable[[], Any]
    save_preferences: Callable[[], Any]
    add_preference: Callable[[], Any]
    learn_preferences: Callable[[], Any]
    update_preference: Callable[[str], Any]
    delete_preference: Callable[[str], Any]
    toggle_preference: Callable[[str], Any]


@dataclass
class WeeklyReviewService:
    get_weekly_review: Callable[[], Any]
    export_current_weekly_review_markdown: Callable[[], Any]
    export_all_weekly_reviews_markdown: Callable[[], Any]
    export_recent_weekly_reviews_markdown: Callable[[], Any]
    get_market_context: Callable[[], Any]
    refresh_market_context: Callable[[], Any]
    summarize_market_context: Callable[[], Any]
    refresh_macro_events: Callable[[], Any]
    refresh_stock_news: Callable[[str], Any]
    refresh_stock_performance: Callable[[str], Any]
    refresh_all_news: Callable[[], Any]
    refresh_all_news_and_scan: Callable[[], Any]
    refresh_portfolio_prices: Callable[[], Any]
    refresh_all_performance: Callable[[], Any]
    generate_news_summary: Callable[[str], Any]
    generate_weekly_stock_ai_summary: Callable[[str, str], Any]
    generate_weekly_stock_ai_summaries_batch: Callable[[str], Any]
    save_stock_weekly_view: Callable[[str], Any]
    save_weekly_portfolio: Callable[[], Any]
    save_rebalancing_ops: Callable[[], Any]
    apply_rebalancing: Callable[[], Any]
    get_portfolio_performance: Callable[[], Any]
    download_quantstats_report: Callable[[str], Any]
    weekly_synthesize: Callable[[], Any]
    weekly_chat: Callable[[], Any]
    get_portfolio_decision_memo: Callable[[str], Any]
    generate_portfolio_decision_memo: Callable[[str], Any]
    save_portfolio_decision_memo_feedback: Callable[[str], Any]


@dataclass
class ZsxqService:
    zsxq_page: Callable[[], Any]
    get_config: Callable[[], Any]
    save_config: Callable[[], Any]
    get_groups: Callable[[], Any]
    get_topics: Callable[[str], Any]
    download_file: Callable[[str, str], Any]
    view_file: Callable[[str, str], Any]
    cache_topics: Callable[[str], Any]
    sync_topics_to_ima: Callable[[str], Any]
    get_cache_file: Callable[[str], Any]
    save_cache_file: Callable[[str], Any]
    chat: Callable[[], Any]
    auto_workflow: Callable[[str], Any]
    get_all_dynamics: Callable[[], Any]
    all_dynamics_workflow: Callable[[], Any]
    cache_all_dynamics: Callable[[], Any]
