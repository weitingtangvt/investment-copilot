from __future__ import annotations


def register_preferences_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/preferences", methods=["GET"])
    @auth_guard
    def api_get_preferences():
        return service.get_preferences()

    @bp.route("/api/preferences", methods=["POST"])
    @auth_guard
    def api_save_preferences():
        return service.save_preferences()

    @bp.route("/api/preferences/add", methods=["POST"])
    @auth_guard
    def api_add_preference():
        return service.add_preference()

    @bp.route("/api/preferences/learn", methods=["POST"])
    @auth_guard
    def api_learn_preferences():
        return service.learn_preferences()

    @bp.route("/api/preferences/<pref_id>", methods=["PUT"])
    @auth_guard
    def api_update_preference(pref_id):
        return service.update_preference(pref_id)

    @bp.route("/api/preferences/<pref_id>", methods=["DELETE"])
    @auth_guard
    def api_delete_preference(pref_id):
        return service.delete_preference(pref_id)

    @bp.route("/api/preferences/<pref_id>/toggle", methods=["POST"])
    @auth_guard
    def api_toggle_preference(pref_id):
        return service.toggle_preference(pref_id)
