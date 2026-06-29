"""Weekly review blueprint."""

from __future__ import annotations

from flask import Blueprint

from web.services.domain_services import WeeklyReviewService

def create_weekly_review_blueprint(service: WeeklyReviewService, auth_guard=None) -> Blueprint:
    bp = Blueprint("weekly_review_bp", __name__)
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/weekly-review", methods=["GET"])
    @auth_guard
    def api_get_weekly_review():
        return service.get_weekly_review()

    @bp.route("/api/weekly-reviews/export-markdown", methods=["GET"])
    @auth_guard
    def api_export_all_weekly_reviews_markdown():
        return service.export_all_weekly_reviews_markdown()

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

    @bp.route("/api/weekly-review/<week_id>/stocks/<stock_id>/commentary-summary", methods=["POST"])
    @auth_guard
    def api_generate_weekly_stock_ai_summary(week_id, stock_id):
        return service.generate_weekly_stock_ai_summary(stock_id, week_id)

    @bp.route("/api/weekly-review/<week_id>/commentary-summary/batch", methods=["POST"])
    @auth_guard
    def api_generate_weekly_stock_ai_summaries_batch(week_id):
        return service.generate_weekly_stock_ai_summaries_batch(week_id)

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

    @bp.route("/api/weekly-review/synthesize", methods=["POST"])
    @auth_guard
    def api_weekly_synthesize():
        return service.weekly_synthesize()

    @bp.route("/api/weekly-review/chat", methods=["POST"])
    @auth_guard
    def api_weekly_chat():
        return service.weekly_chat()

    return bp
