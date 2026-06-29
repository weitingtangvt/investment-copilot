from __future__ import annotations


def register_stocks_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/stock/<stock_id>", methods=["GET"])
    @auth_guard
    def api_get_stock(stock_id):
        return service.get_stock(stock_id)

    @bp.route("/api/stock/<stock_id>", methods=["POST"])
    @auth_guard
    def api_save_stock(stock_id):
        return service.save_stock(stock_id)

    @bp.route("/api/stock/<stock_id>/ticker", methods=["PUT"])
    @auth_guard
    def api_update_stock_ticker(stock_id):
        return service.update_stock_ticker(stock_id)

    @bp.route("/api/stock/<stock_id>", methods=["DELETE"])
    @auth_guard
    def api_delete_stock(stock_id):
        return service.delete_stock(stock_id)

    @bp.route("/api/price-chart", methods=["GET"])
    @auth_guard
    def api_generic_price_chart():
        return service.generic_price_chart()

    @bp.route("/api/stock-commentary", methods=["GET"])
    @auth_guard
    def api_get_stock_commentary():
        return service.stock_commentary()

    @bp.route("/api/stock-commentary/registry", methods=["GET"])
    @auth_guard
    def api_get_stock_commentary_registry():
        return service.stock_commentary_registry()

    @bp.route("/api/stock-commentary/registry/<stock_id>", methods=["POST"])
    @auth_guard
    def api_save_stock_commentary_registry(stock_id):
        return service.save_stock_commentary_registry(stock_id)

    @bp.route("/api/playbook/<stock_id>", methods=["GET"])
    @auth_guard
    def api_get_playbook(stock_id):
        return service.get_playbook(stock_id)

    @bp.route("/api/playbook/<stock_id>", methods=["POST"])
    @auth_guard
    def api_save_playbook(stock_id):
        return service.save_playbook(stock_id)
