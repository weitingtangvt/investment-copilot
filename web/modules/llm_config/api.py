from __future__ import annotations


def register_llm_config_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/llm-config", methods=["GET"])
    @auth_guard
    def api_get_llm_config():
        return service.get_llm_config()

    @bp.route("/api/llm-config", methods=["POST"])
    @auth_guard
    def api_save_llm_config():
        return service.save_llm_config()
