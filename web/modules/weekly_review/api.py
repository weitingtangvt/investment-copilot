from __future__ import annotations


def register_weekly_review_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/weekly-review", methods=["GET"])
    @auth_guard
    def api_get_weekly_review():
        return service.get_weekly_review()

    @bp.route("/api/weekly-review/generate", methods=["POST"])
    @auth_guard
    def api_submit_weekly_review_generate_task():
        return service.submit_weekly_review_generate_task()

    @bp.route("/api/weekly-reviews/export-markdown", methods=["GET"])
    @auth_guard
    def api_export_all_weekly_reviews_markdown():
        return service.export_all_weekly_reviews_markdown()

    @bp.route("/api/weekly-review/export-markdown", methods=["GET"])
    @auth_guard
    def api_export_current_weekly_review_markdown():
        return service.export_current_weekly_review_markdown()

    @bp.route("/api/weekly-reviews/export-recent", methods=["GET"])
    @auth_guard
    def api_export_recent_weekly_reviews_markdown():
        return service.export_recent_weekly_reviews_markdown()

    @bp.route("/api/weekly-review/market-context", methods=["GET"])
    @auth_guard
    def api_get_market_context():
        return service.get_market_context()

    @bp.route("/api/weekly-review/market-context/refresh", methods=["POST"])
    @auth_guard
    def api_refresh_market_context():
        return service.refresh_market_context()

    @bp.route("/api/weekly-review/market-context/summarize", methods=["POST"])
    @auth_guard
    def api_summarize_market_context():
        return service.summarize_market_context()

    @bp.route("/api/weekly-review/macro-events/refresh", methods=["POST"])
    @auth_guard
    def api_refresh_macro_events():
        return service.refresh_macro_events()

    @bp.route("/api/weekly-review/refresh/<stock_id>/news", methods=["POST"])
    @auth_guard
    def api_refresh_stock_news(stock_id):
        return service.refresh_stock_news(stock_id)

    @bp.route("/api/weekly-review/refresh/<stock_id>/performance", methods=["POST"])
    @auth_guard
    def api_refresh_stock_performance(stock_id):
        return service.refresh_stock_performance(stock_id)

    @bp.route("/api/weekly-review/refresh-all/news", methods=["POST"])
    @auth_guard
    def api_refresh_all_news():
        return service.refresh_all_news()

    @bp.route("/api/weekly-review/refresh-all/news-and-scan", methods=["POST"])
    @auth_guard
    def api_refresh_all_news_and_scan():
        return service.refresh_all_news_and_scan()

    @bp.route("/api/weekly-review/refresh-portfolio-prices", methods=["POST"])
    @auth_guard
    def api_refresh_portfolio_prices():
        return service.refresh_portfolio_prices()

    @bp.route("/api/weekly-review/refresh-all/performance", methods=["POST"])
    @auth_guard
    def api_refresh_all_performance():
        return service.refresh_all_performance()

    @bp.route("/api/weekly-review/<stock_id>/news-summary", methods=["POST"])
    @auth_guard
    def api_generate_news_summary(stock_id):
        return service.generate_news_summary(stock_id)

    @bp.route("/api/weekly-review/<week_id>/stocks/<stock_id>/zsxq-matches", methods=["GET"])
    @auth_guard
    def api_get_weekly_review_stock_zsxq_matches(week_id, stock_id):
        return service.get_weekly_review_stock_zsxq_matches(week_id, stock_id)

    @bp.route("/api/weekly-review/<week_id>/stocks/<stock_id>/filings", methods=["GET"])
    @auth_guard
    def api_get_weekly_review_stock_filings(week_id, stock_id):
        return service.get_weekly_review_stock_filings(week_id, stock_id)

    @bp.route("/api/weekly-review/<week_id>/stocks/<stock_id>/filings/refresh", methods=["POST"])
    @auth_guard
    def api_refresh_weekly_review_stock_filings(week_id, stock_id):
        return service.refresh_weekly_review_stock_filings(week_id, stock_id)

    @bp.route("/api/weekly-review/<week_id>/stocks/<stock_id>/commentary-summary", methods=["POST"])
    @auth_guard
    def api_generate_weekly_stock_ai_summary(week_id, stock_id):
        return service.generate_weekly_stock_ai_summary(stock_id, week_id)

    @bp.route("/api/weekly-review/<week_id>/commentary-summary/batch", methods=["POST"])
    @auth_guard
    def api_generate_weekly_stock_ai_summaries_batch(week_id):
        return service.generate_weekly_stock_ai_summaries_batch(week_id)

    @bp.route("/api/weekly-review/<week_id>/sync-ima", methods=["POST"])
    @auth_guard
    def api_sync_weekly_review_to_ima(week_id):
        return service.sync_weekly_review_to_ima(week_id)

    @bp.route("/api/weekly-review/<stock_id>/view", methods=["POST"])
    @auth_guard
    def api_save_stock_weekly_view(stock_id):
        return service.save_stock_weekly_view(stock_id)

    @bp.route("/api/weekly-review/portfolio", methods=["POST"])
    @auth_guard
    def api_save_weekly_portfolio():
        return service.save_weekly_portfolio()

    @bp.route("/api/weekly-review/rebalancing", methods=["POST"])
    @auth_guard
    def api_save_rebalancing_ops():
        return service.save_rebalancing_ops()

    @bp.route("/api/weekly-review/rebalancing/apply", methods=["POST"])
    @auth_guard
    def api_apply_rebalancing():
        return service.apply_rebalancing()

    @bp.route("/api/weekly-review/portfolio-performance", methods=["GET"])
    @auth_guard
    def api_get_portfolio_performance():
        return service.get_portfolio_performance()

    @bp.route("/api/portfolio-analytics/quantstats/<report_type>", methods=["GET"])
    @auth_guard
    def api_download_quantstats_report(report_type):
        return service.download_quantstats_report(report_type)

    @bp.route("/api/weekly-review/synthesize", methods=["POST"])
    @auth_guard
    def api_weekly_synthesize():
        return service.weekly_synthesize()

    @bp.route("/api/weekly-review/chat", methods=["POST"])
    @auth_guard
    def api_weekly_chat():
        return service.weekly_chat()

    @bp.route("/api/weekly-review/<week_id>/decision-logs", methods=["GET"])
    @auth_guard
    def api_get_weekly_decision_logs(week_id):
        return service.get_decision_logs(week_id)

    @bp.route("/api/weekly-review/<week_id>/decision-logs", methods=["POST"])
    @auth_guard
    def api_create_weekly_decision_log(week_id):
        return service.create_decision_log(week_id)

    @bp.route("/api/weekly-review/<week_id>/decision-logs/<log_id>", methods=["PUT"])
    @auth_guard
    def api_update_weekly_decision_log(week_id, log_id):
        return service.update_decision_log(week_id, log_id)

    @bp.route("/api/weekly-review/<week_id>/decision-logs/<log_id>", methods=["DELETE"])
    @auth_guard
    def api_delete_weekly_decision_log(week_id, log_id):
        return service.delete_decision_log(week_id, log_id)

    @bp.route("/api/weekly-review/<week_id>/decision-memo", methods=["GET"])
    @auth_guard
    def api_get_portfolio_decision_memo(week_id):
        return service.get_portfolio_decision_memo(week_id)

    @bp.route("/api/weekly-review/<week_id>/decision-memo/generate", methods=["POST"])
    @auth_guard
    def api_generate_portfolio_decision_memo(week_id):
        return service.generate_portfolio_decision_memo(week_id)

    @bp.route("/api/weekly-review/<week_id>/decision-memo/feedback", methods=["POST"])
    @auth_guard
    def api_save_portfolio_decision_memo_feedback(week_id):
        return service.save_portfolio_decision_memo_feedback(week_id)
