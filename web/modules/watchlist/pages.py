from __future__ import annotations


def register_watchlist_pages(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/watchlist")
    @bp.route("/watchlist/")
    @auth_guard
    def watchlist_page():
        return service.watchlist_page()
