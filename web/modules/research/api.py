from __future__ import annotations


def register_research_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/research/<stock_id>/environment", methods=["POST"])
    @auth_guard
    def api_collect_environment(stock_id):
        return service.collect_environment(stock_id)

    @bp.route("/api/research/<stock_id>/assess", methods=["POST"])
    @auth_guard
    def api_assess_impact(stock_id):
        return service.assess_impact(stock_id)

    @bp.route("/api/research/<stock_id>/adjust-plan", methods=["POST"])
    @auth_guard
    def api_adjust_plan(stock_id):
        return service.adjust_plan(stock_id)

    @bp.route("/api/research/<stock_id>/follow-up", methods=["POST"])
    @auth_guard
    def api_follow_up_research(stock_id):
        return service.follow_up_research(stock_id)

    @bp.route("/api/research/<stock_id>/execute", methods=["POST"])
    @auth_guard
    def api_execute_research(stock_id):
        return service.execute_research(stock_id)

    @bp.route("/api/research/<stock_id>/history", methods=["GET"])
    @auth_guard
    def api_get_research_history(stock_id):
        return service.get_research_history(stock_id)

    @bp.route("/api/research/<stock_id>/feedback", methods=["POST"])
    @auth_guard
    def api_save_research_feedback(stock_id):
        return service.save_research_feedback(stock_id)

    @bp.route("/api/research/<stock_id>/context", methods=["GET"])
    @auth_guard
    def api_get_research_context(stock_id):
        return service.get_research_context(stock_id)

    @bp.route("/api/research/<stock_id>/milestone/<record_id>", methods=["POST"])
    @auth_guard
    def api_toggle_milestone(stock_id, record_id):
        return service.toggle_milestone(stock_id, record_id)

    @bp.route("/api/batch-scan/stock/<stock_id>", methods=["POST"])
    @auth_guard
    def api_scan_single_stock(stock_id):
        return service.scan_single_stock(stock_id)

    @bp.route("/api/batch-scan/research/<stock_id>", methods=["POST"])
    @auth_guard
    def api_batch_research_stock(stock_id):
        return service.batch_research_stock(stock_id)
