from __future__ import annotations


def register_us_screener_pages(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/us-screener")
    @bp.route("/us-screener/")
    @auth_guard
    def us_screener_page():
        return service.us_screener_page()
