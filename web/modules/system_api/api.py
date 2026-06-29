from __future__ import annotations


def register_system_api_routes(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/auth/status", methods=["GET"])
    def api_auth_status():
        return service.auth_status()

    @bp.route("/api/auth/setup", methods=["POST"])
    def api_setup_auth():
        return service.setup_auth()

    @bp.route("/api/news-sources/health", methods=["GET"])
    @auth_guard
    def api_get_news_source_health():
        return service.news_source_health()

    @bp.route("/api/ima/config", methods=["GET"])
    @auth_guard
    def api_get_ima_config():
        return service.get_ima_config()

    @bp.route("/api/ima/config", methods=["POST"])
    @auth_guard
    def api_save_ima_config():
        return service.save_ima_config()
