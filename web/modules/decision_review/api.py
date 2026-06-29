from __future__ import annotations


def register_decision_review_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/decision-review", methods=["GET"])
    @auth_guard
    def api_decision_review():
        return service.index()
