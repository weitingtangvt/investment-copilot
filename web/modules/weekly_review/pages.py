from __future__ import annotations


def register_weekly_review_pages(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/weekly-review")
    @bp.route("/weekly-review/")
    @auth_guard
    def weekly_review_page():
        return service.weekly_review_page()

    @bp.route("/weekly_review")
    @bp.route("/weekly_review/")
    @auth_guard
    def weekly_review_alias():
        return service.weekly_review_alias_redirect()
