from __future__ import annotations


def register_stocks_pages(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/stock/<stock_id>")
    @bp.route("/stock/<stock_id>/")
    @auth_guard
    def stock_detail_page(stock_id):
        return service.stock_detail_page(stock_id)

    @bp.route("/stocks")
    @bp.route("/stocks/")
    @auth_guard
    def stocks_page():
        return service.stocks_page()

    @bp.route("/playbook")
    @bp.route("/playbook/")
    @auth_guard
    def playbook_page():
        return service.playbook_page()
