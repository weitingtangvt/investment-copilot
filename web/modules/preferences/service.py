from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, request

from web.request_parsing import load_json_object
from web.services.domain_services import PreferencesService


@dataclass
class PreferencesModuleDeps:
    get_storage: Callable[[], Any]
    render_template: Callable[..., Any]
    get_client: Callable[[], Any]
    preference_learner_factory: Callable[[Any, Any], Any]


def build_preferences_module_service(deps: PreferencesModuleDeps) -> PreferencesService:
    def _json_object_body_or_error():
        return load_json_object()

    def preferences_page():
        prefs = deps.get_storage().get_user_preferences()
        interactions = prefs.get("interaction_log", [])
        return deps.render_template("preferences.html", preferences=prefs, interactions=interactions)

    def get_preferences():
        return jsonify(deps.get_storage().get_user_preferences())

    def save_preferences():
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        if "preference_summary" in data:
            deps.get_storage().update_preference_summary(data["preference_summary"])
        return jsonify({"success": True})

    def add_preference():
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        pref_id = deps.get_storage().add_preference(data)
        return jsonify({"success": True, "id": pref_id})

    def learn_preferences():
        try:
            client = deps.get_client()
            if not client:
                return jsonify({"error": "Text LLM Text", "extracted_preferences": []})
            learner = deps.preference_learner_factory(client, deps.get_storage())
            result = learner.learn_and_save_preferences()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc), "extracted_preferences": []})

    def update_preference(pref_id: str):
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        success = deps.get_storage().update_preference(pref_id, data)
        return jsonify({"success": success})

    def delete_preference(pref_id: str):
        success = deps.get_storage().delete_preference(pref_id)
        return jsonify({"success": success})

    def toggle_preference(pref_id: str):
        success = deps.get_storage().toggle_preference(pref_id)
        return jsonify({"success": success})

    return PreferencesService(
        preferences_page=preferences_page,
        get_preferences=get_preferences,
        save_preferences=save_preferences,
        add_preference=add_preference,
        learn_preferences=learn_preferences,
        update_preference=update_preference,
        delete_preference=delete_preference,
        toggle_preference=toggle_preference,
    )
