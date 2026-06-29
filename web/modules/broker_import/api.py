from __future__ import annotations


def register_broker_import_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/ibkr/trades", methods=["GET"])
    @auth_guard
    def api_get_ibkr_trades():
        return service.get_ibkr_trades()

    @bp.route("/api/ibkr/trades/import", methods=["POST"])
    @auth_guard
    def api_import_ibkr_trades():
        return service.import_ibkr_trades()

    @bp.route("/api/ibkr/derived-portfolio", methods=["GET"])
    @auth_guard
    def api_get_ibkr_derived_portfolio():
        return service.get_ibkr_derived_portfolio()
