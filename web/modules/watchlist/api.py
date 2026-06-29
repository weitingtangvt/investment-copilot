from __future__ import annotations


def register_watchlist_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/watchlist", methods=["GET"])
    @auth_guard
    def api_get_watchlist():
        return service.get_watchlist()

    @bp.route("/api/watchlist", methods=["POST"])
    @auth_guard
    def api_add_watch_candidate():
        return service.add_watch_candidate()

    @bp.route("/api/watchlist/<candidate_id>", methods=["PUT"])
    @auth_guard
    def api_update_watch_candidate(candidate_id):
        return service.update_watch_candidate(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>", methods=["DELETE"])
    @auth_guard
    def api_delete_watch_candidate(candidate_id):
        return service.delete_watch_candidate(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/ack-revisit", methods=["POST"])
    @auth_guard
    def api_ack_watch_candidate_revisit(candidate_id):
        return service.ack_watch_candidate_revisit(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/weekly-notes", methods=["POST"])
    @auth_guard
    def api_save_watch_candidate_weekly_note(candidate_id):
        return service.save_watch_candidate_weekly_note(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/zsxq-matches", methods=["GET"])
    @auth_guard
    def api_watch_candidate_zsxq_matches(candidate_id):
        return service.watch_candidate_zsxq_matches(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/filings", methods=["GET"])
    @auth_guard
    def api_watch_candidate_filings(candidate_id):
        return service.watch_candidate_filings(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/filings/refresh", methods=["POST"])
    @auth_guard
    def api_watch_candidate_filings_refresh(candidate_id):
        return service.refresh_watch_candidate_filings(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/refresh-performance", methods=["POST"])
    @auth_guard
    def api_refresh_watch_candidate_performance(candidate_id):
        return service.refresh_watch_candidate_performance(candidate_id)

    @bp.route("/api/watchlist/<candidate_id>/price-chart", methods=["GET"])
    @auth_guard
    def api_watch_candidate_price_chart(candidate_id):
        return service.watch_candidate_price_chart(candidate_id)

    @bp.route("/api/watchlist/refresh-all", methods=["POST"])
    @auth_guard
    def api_refresh_watchlist_all():
        return service.refresh_watchlist_all()

    @bp.route("/api/watchlist/<candidate_id>/ai-judgment", methods=["POST"])
    @auth_guard
    def api_watch_candidate_ai_judgment(candidate_id):
        return service.watch_candidate_ai_judgment(candidate_id)

    @bp.route("/api/watchlist/ai-judgment/batch", methods=["POST"])
    @auth_guard
    def api_watchlist_ai_judgment_batch():
        return service.watchlist_ai_judgment_batch()

    @bp.route("/api/watchlist/sync-ima", methods=["POST"])
    @auth_guard
    def api_sync_watchlist_to_ima():
        return service.sync_watchlist_to_ima()
