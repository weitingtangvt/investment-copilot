from __future__ import annotations


def register_factor_analysis_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/factor-analysis", methods=["POST"])
    @auth_guard
    def api_factor_analysis():
        return service.run()
