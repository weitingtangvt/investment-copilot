from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, session

from web.request_parsing import load_json_object
from web.services.domain_services import SystemApiService


@dataclass
class SystemApiModuleDeps:
    auth_service: Any
    get_storage: Callable[[], Any]
    get_json: Callable[[], dict[str, Any]]
    to_bool: Callable[[Any, bool], bool]
    news_source_diagnostics: Callable[[], list[dict[str, Any]]]
    now_iso: Callable[[], str]
    ima_client_factory: Callable[[str, str], Any]
    unauthorized_response: Callable[[], Any]


def build_system_api_module_service(deps: SystemApiModuleDeps) -> SystemApiService:
    def _json_object_body_or_error():
        return load_json_object(deps.get_json)

    def auth_status():
        cfg = deps.auth_service.get_auth_config()
        return jsonify(
            {
                "enabled": bool(cfg.get("enabled")),
                "has_password": bool(cfg.get("password_hash")),
                "authenticated": bool(session.get("authenticated")),
            }
        )

    def setup_auth():
        if not deps.auth_service.can_setup_auth():
            return deps.unauthorized_response()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        enable = deps.to_bool(data.get("enable", data.get("enabled", True)), True)
        password = str(data.get("password") or "").strip()
        if enable and not password and not bool(deps.auth_service.get_auth_config().get("password_hash")):
            return jsonify({"success": False, "error": "Password is required for first-time auth enable"}), 400
        cfg = deps.auth_service.save_auth(password=password, enable=enable)
        if password and deps.auth_service.check_auth(password):
            session["authenticated"] = True
        return jsonify({"success": True, "auth_enabled": bool(cfg.get("auth_enabled"))})

    def news_source_health():
        storage = deps.get_storage()
        return jsonify(
            {
                "generated_at": deps.now_iso(),
                "strategy": storage.get_news_aggregation_strategy(),
                "use_ai_search": storage.get_news_use_ai_search(),
                "sources": deps.news_source_diagnostics(),
            }
        )

    def get_ima_config():
        config = deps.get_storage().get_ima_config()
        return jsonify(
            {
                "success": True,
                "configured": bool(config.get("client_id") and config.get("api_key")),
                "client_id": str(config.get("client_id") or ""),
                "has_api_key": bool(config.get("api_key")),
                "knowledge_base_id": str(config.get("knowledge_base_id") or ""),
                "knowledge_base_name": str(config.get("knowledge_base_name") or ""),
            }
        )

    def save_ima_config():
        storage = deps.get_storage()
        data, error_response = _json_object_body_or_error()
        if error_response is not None:
            return error_response
        current = storage.get_ima_config()

        posted_client_id = str(data.get("client_id") or "").strip() if "client_id" in data else ""
        posted_api_key = str(data.get("api_key") or "").strip() if "api_key" in data else ""
        merged_client_id = posted_client_id or str(current.get("client_id") or "").strip()
        merged_api_key = posted_api_key or str(current.get("api_key") or "").strip()

        knowledge_base_id = str(current.get("knowledge_base_id") or "").strip()
        knowledge_base_name = str(current.get("knowledge_base_name") or "").strip()
        validated = False

        if merged_client_id and merged_api_key:
            try:
                target = deps.ima_client_factory(merged_client_id, merged_api_key).detect_target_knowledge_base()
                knowledge_base_id = target["id"]
                knowledge_base_name = target["name"]
                validated = True
            except Exception as exc:
                return jsonify({"success": False, "error": f"IMA TextFailed: {exc}"}), 400

        saved = storage.save_ima_config(
            client_id=posted_client_id or None,
            api_key=posted_api_key or None,
            knowledge_base_id=knowledge_base_id or None,
            knowledge_base_name=knowledge_base_name or None,
        )
        return jsonify(
            {
                "success": True,
                "configured": bool(saved.get("client_id") and saved.get("api_key")),
                "validated": validated,
                "client_id": str(saved.get("client_id") or ""),
                "has_api_key": bool(saved.get("api_key")),
                "knowledge_base_id": str(saved.get("knowledge_base_id") or ""),
                "knowledge_base_name": str(saved.get("knowledge_base_name") or ""),
            }
        )

    return SystemApiService(
        auth_status=auth_status,
        setup_auth=setup_auth,
        news_source_health=news_source_health,
        get_ima_config=get_ima_config,
        save_ima_config=save_ima_config,
    )
