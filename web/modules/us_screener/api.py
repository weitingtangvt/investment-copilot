from __future__ import annotations


def register_us_screener_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/us-screener/latest", methods=["GET"])
    @auth_guard
    def api_get_us_screener_latest():
        return service.latest()

    @bp.route("/api/us-screener/status", methods=["GET"])
    @auth_guard
    def api_get_us_screener_status():
        return service.status()

    @bp.route("/api/us-screener/context", methods=["GET"])
    @auth_guard
    def api_get_us_screener_context():
        return service.context()

    @bp.route("/api/us-screener/stock-overview", methods=["GET"])
    @auth_guard
    def api_get_us_screener_stock_overview():
        return service.stock_overview()

    @bp.route("/api/us-screener/alerts", methods=["GET"])
    @auth_guard
    def api_get_us_screener_alerts():
        return service.alerts()

    @bp.route("/api/us-screener/alerts", methods=["POST"])
    @auth_guard
    def api_create_us_screener_alert():
        return service.create_alert()

    @bp.route("/api/us-screener/alerts/<alert_id>", methods=["DELETE"])
    @auth_guard
    def api_delete_us_screener_alert(alert_id):
        return service.delete_alert(alert_id)

    @bp.route("/api/us-screener/research-queue", methods=["GET"])
    @auth_guard
    def api_get_us_screener_research_queue():
        return service.research_queue()

    @bp.route("/api/us-screener/research-queue", methods=["POST"])
    @auth_guard
    def api_create_us_screener_research_queue_item():
        return service.create_research_queue_item()

    @bp.route("/api/us-screener/research-queue/<item_id>", methods=["PATCH"])
    @auth_guard
    def api_update_us_screener_research_queue_item(item_id):
        return service.update_research_queue_item(item_id)

    @bp.route("/api/us-screener/research-queue/<item_id>", methods=["DELETE"])
    @auth_guard
    def api_delete_us_screener_research_queue_item(item_id):
        return service.delete_research_queue_item(item_id)

    @bp.route("/api/us-screener/ai-brief", methods=["POST"])
    @auth_guard
    def api_us_screener_ai_brief():
        return service.ai_brief()

    @bp.route("/api/us-screener/run", methods=["POST"])
    @auth_guard
    def api_run_us_screener():
        return service.run()

    @bp.route("/api/us-screener/scan", methods=["POST"])
    @auth_guard
    def api_submit_us_screener_scan_task():
        return service.scan()
