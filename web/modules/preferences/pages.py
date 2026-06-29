from __future__ import annotations


def register_preferences_pages(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/preferences")
    @bp.route("/preferences/")
    @auth_guard
    def preferences_page():
        return service.preferences_page()
