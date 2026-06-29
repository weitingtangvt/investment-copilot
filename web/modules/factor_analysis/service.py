from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify

from web.request_parsing import load_json_object

@dataclass
class FactorAnalysisModuleService:
    run: Callable[[], Any]


@dataclass
class FactorAnalysisModuleDeps:
    get_storage: Callable[[], Any]
    get_json: Callable[[], Any]
    json_safe: Callable[[Any], Any]
    factor_analysis_service_factory: Callable[[], Any]
    logger: Any


def build_factor_analysis_module_service(deps: FactorAnalysisModuleDeps) -> FactorAnalysisModuleService:
    def run():
        try:
            data, error_response = load_json_object(deps.get_json)
            if error_response is not None:
                return error_response
            service = deps.factor_analysis_service_factory()
            result = service.run(data)
            return jsonify(deps.json_safe(result.payload)), result.status_code
        except Exception as exc:
            deps.logger.exception("Factor analysis failed")
            return jsonify({"error": f"TextAnalysisFailed: {exc}"}), 500

    return FactorAnalysisModuleService(run=run)
