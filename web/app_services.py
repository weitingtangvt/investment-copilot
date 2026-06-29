from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from core import ima_sync
from core.preference_learner import PreferenceLearner
from core.stock_commentary import get_keyword_pool, get_stock_commentary, normalize_keyword_list, save_keyword_entry
from core.zsxq_automation import ZsxqAutomation
from core.zsxq_cache_service import merge_topics_cache
from core.community_context import load_zsxq_chat_context
from web.modules.app_shell.service import AppShellModuleDeps, build_app_shell_module_service
from web.modules.broker_import.service import BrokerImportModuleDeps, build_broker_import_module_service
from web.modules.decision_review.service import DecisionReviewModuleDeps, build_decision_review_module_service
from web.modules.factor_analysis.service import FactorAnalysisModuleDeps, build_factor_analysis_module_service
from web.modules.llm_config.service import LLMConfigModuleDeps, build_llm_config_module_service
from web.modules.preferences.service import PreferencesModuleDeps, build_preferences_module_service
from web.modules.research.helpers import ResearchStepHelperDeps, build_research_step_helpers
from web.modules.research.service import ResearchModuleDeps, build_research_module_service
from web.modules.shell_pages.service import ShellPagesModuleDeps, build_shell_pages_module_service
from web.modules.stocks.service import StocksModuleDeps, build_stocks_module_service
from web.modules.system_api.service import SystemApiModuleDeps, build_system_api_module_service
from web.modules.task_api.service import TaskApiModuleDeps, build_task_api_module_service
from web.modules.xueqiu.helpers import XueqiuHelperDeps, build_xueqiu_module_helpers
from web.modules.xueqiu.service import XueqiuModuleDeps, build_xueqiu_module_service
from web.modules.xueqiu.task_runners import XueqiuTaskRunnerDeps, build_xueqiu_task_runners
from web.modules.zsxq.helpers import ZsxqHelperDeps, build_zsxq_module_helpers
from web.modules.zsxq.service import ZsxqModuleDeps, build_zsxq_module_service
@dataclass
class AppServiceDeps:
    app: Any
    storage: Any
    get_storage: Callable[[], Any]
    config_center: Any
    auth_service: Any
    logger: Any
    project_root: str
    web_root: str
    get_client: Callable[[], Any]
    reset_llm_client: Callable[[], Any]
    config_value: Callable[[str, Any], Any]
    get_runtime_meta: Callable[[], Any]
    get_week_id: Callable[..., str]
    get_weekly_review_manager: Callable[[], Any]
    get_env_collector: Callable[[], Any]
    get_research_engine: Callable[[], Any]
    get_stocks_with_research_status: Callable[[], Any]
    build_news_source_diagnostics: Callable[[], Any]
    resolve_effective_weekly_review: Callable[[], Any]
    get_task_manager: Callable[[], Any]
    patch_task_record: Callable[[str, dict[str, Any]], Any]
    task_api_helpers: Any
    safe_int: Callable[[Any, int], int]
    to_bool: Callable[[Any, bool], bool]
    json_safe: Callable[[Any], Any]
    chat_with_retry: Callable[..., Any]
    is_llm_failure_text: Callable[[str], bool]
    safe_filename_part: Callable[[Any, str], str]
    write_markdown_snapshot: Callable[[Any, str], Any]
    markdown_from_zsxq_daily_snapshot: Callable[[str, str, list[dict[str, Any]]], str]
    xueqiu_client_factory: Callable[..., Any]
    fetch_price_chart: Callable[..., Any]
    load_price_frames: Callable[..., dict[str, Any]]
    external_cash_flows_hkd: list[dict[str, Any]]
    review_projection_provider: Callable[[str, dict[str, Any]], dict[str, Any] | None]
    now_factory: Callable[[], datetime]
    render_template: Callable[..., Any]
    redirect: Callable[[str, int], Any]
    send_from_directory: Callable[..., Any]
    get_json: Callable[[], Any]
    factor_analysis_service_factory: Callable[[], Any]
    ima_sync_key_zsxq_daily: Callable[[str, str], str]
    unauthorized_response: Callable[[], Any]


@dataclass
class AppServices:
    xueqiu_task_runners: Any
    xueqiu_service: Any
    stocks_service: Any
    preferences_service: Any
    app_shell_service: Any
    shell_pages_service: Any
    decision_review_service: Any
    zsxq_service: Any
    llm_config_service: Any
    broker_import_service: Any
    system_api_service: Any
    task_api_service: Any
    research_service: Any
    factor_analysis_service: Any


def build_app_services(deps: AppServiceDeps) -> AppServices:
    def active_storage() -> Any:
        return deps.get_storage()

    xueqiu_helpers = build_xueqiu_module_helpers(
        XueqiuHelperDeps(
            get_storage=active_storage,
            safe_int=lambda value, default: deps.safe_int(value, default),
            get_task_manager=lambda: deps.get_task_manager(),
            task_status_message=lambda status, error="", explicit_message="": deps.task_api_helpers.task_status_message(
                status,
                error=error,
                explicit_message=explicit_message,
            ),
            client_factory=deps.xueqiu_client_factory,
        )
    )
    xueqiu_task_runners = build_xueqiu_task_runners(
        XueqiuTaskRunnerDeps(
            sanitize_user=xueqiu_helpers.sanitize_user,
            build_client=lambda settings=None: xueqiu_helpers.build_client(settings),
            patch_task_record=lambda task, patch: deps.patch_task_record(task, patch),
            safe_int=lambda value, default: deps.safe_int(value, default),
        )
    )
    xueqiu_service = build_xueqiu_module_service(
        XueqiuModuleDeps(
            get_storage=active_storage,
            render_template=lambda template_name: deps.render_template(template_name),
            safe_int=lambda value, default: deps.safe_int(value, default),
            get_settings=xueqiu_helpers.get_settings,
            save_settings=xueqiu_helpers.save_settings,
            sanitize_user=xueqiu_helpers.sanitize_user,
            auth_status=lambda settings=None: xueqiu_helpers.auth_status(settings),
            build_client=lambda settings=None: xueqiu_helpers.build_client(settings),
            feed_meta=xueqiu_helpers.feed_meta,
            merge_posts=xueqiu_helpers.merge_posts,
            get_task_manager=lambda: deps.get_task_manager(),
            export_task_payload=xueqiu_helpers.export_task_payload,
            session_task_payload=xueqiu_helpers.session_task_payload,
            export_task_get=xueqiu_helpers.export_task_get,
            session_task_get=xueqiu_helpers.session_task_get,
            send_from_directory=lambda directory, filename, as_attachment=False: deps.send_from_directory(
                directory,
                filename,
                as_attachment=as_attachment,
            ),
            logger=deps.logger,
        )
    )

    stocks_service = build_stocks_module_service(
        StocksModuleDeps(
            get_storage=active_storage,
            render_template=lambda template_name, **context: deps.render_template(template_name, **context),
            now_iso=lambda: deps.now_factory().isoformat(),
            get_stocks_with_research_status=deps.get_stocks_with_research_status,
            fetch_price_chart=lambda subject, range_name="1y": deps.fetch_price_chart(subject, range_name=range_name),
            json_safe=lambda value: deps.json_safe(value),
            get_stock_commentary=lambda storage_obj, records, rolling_days=7, week_id=None: get_stock_commentary(
                storage_obj,
                records,
                rolling_days=rolling_days,
                week_id=week_id,
            ),
            get_keyword_pool=lambda storage_obj: get_keyword_pool(storage_obj),
            save_keyword_entry=lambda storage_obj, **kwargs: save_keyword_entry(storage_obj, **kwargs),
            normalize_keyword_list=normalize_keyword_list,
            get_week_id=deps.get_week_id,
            safe_int=lambda value, default: deps.safe_int(value, default),
        )
    )

    preferences_service = build_preferences_module_service(
        PreferencesModuleDeps(
            get_storage=active_storage,
            render_template=lambda template_name, **context: deps.render_template(template_name, **context),
            get_client=lambda: deps.get_client(),
            preference_learner_factory=lambda runtime, storage_obj: PreferenceLearner(runtime, storage_obj),
        )
    )

    app_shell_service = build_app_shell_module_service(
        AppShellModuleDeps(
            render_template=lambda template_name, **context: deps.render_template(template_name, **context),
            get_stocks_with_research_status=deps.get_stocks_with_research_status,
            get_storage=active_storage,
            resolve_effective_weekly_review=deps.resolve_effective_weekly_review,
            get_app=lambda: deps.app,
            get_pid=os.getpid,
            get_cwd=os.getcwd,
            project_root=deps.project_root,
            web_root=deps.web_root,
        )
    )

    shell_pages_service = build_shell_pages_module_service(
        ShellPagesModuleDeps(
            render_template=lambda template_name: deps.render_template(template_name),
            redirect=lambda location, code=302: deps.redirect(location, code=code),
        )
    )

    decision_review_service = build_decision_review_module_service(
        DecisionReviewModuleDeps(
            get_weekly_review_manager=deps.get_weekly_review_manager,
            safe_int=deps.safe_int,
            json_safe=deps.json_safe,
            logger=deps.logger,
            review_projection_provider=deps.review_projection_provider,
        )
    )

    zsxq_helpers = build_zsxq_module_helpers(
        ZsxqHelperDeps(
            get_config=lambda: active_storage().get_config(),
            get_base_dir=lambda: active_storage().base_dir,
            safe_filename_part=lambda value, default: deps.safe_filename_part(value, default),
            get_ima_export_path=lambda snapshot_type, filename: active_storage().get_ima_export_path(snapshot_type, filename),
            get_ima_export_dir=lambda snapshot_type: active_storage().get_ima_export_dir(snapshot_type),
        )
    )

    zsxq_service = build_zsxq_module_service(
        ZsxqModuleDeps(
            get_storage=active_storage,
            get_zsxq_client=zsxq_helpers.get_zsxq_client,
            get_client=lambda: deps.get_client(),
            is_llm_failure_text=lambda text: deps.is_llm_failure_text(text),
            get_runtime_meta=lambda: deps.get_runtime_meta(),
            render_template=lambda template_name: deps.render_template(template_name),
            get_json=lambda: deps.get_json(),
            safe_int=lambda value, default: deps.safe_int(value, default),
            get_zsxq_paths=zsxq_helpers.get_zsxq_paths,
            resolve_existing_zsxq_file=zsxq_helpers.resolve_existing_zsxq_file,
            send_from_directory=lambda directory, filename, as_attachment=False: deps.send_from_directory(
                directory,
                filename,
                as_attachment=as_attachment,
            ),
            load_zsxq_chat_context=lambda base_dir, group_id: load_zsxq_chat_context(base_dir, group_id),
            json_safe=lambda value: deps.json_safe(value),
            merge_topics_cache=lambda base_dir, group_id, topics: merge_topics_cache(base_dir, group_id, topics),
            write_markdown_snapshot=lambda path, content: deps.write_markdown_snapshot(path, content),
            markdown_from_zsxq_daily_snapshot=lambda group_id, group_name, topics: deps.markdown_from_zsxq_daily_snapshot(
                group_id,
                group_name,
                topics,
            ),
            resolve_zsxq_daily_snapshot_path=zsxq_helpers.resolve_zsxq_daily_snapshot_path,
            find_existing_zsxq_daily_snapshot=zsxq_helpers.find_existing_zsxq_daily_snapshot,
            sync_snapshot_to_ima=ima_sync.sync_snapshot_to_ima,
            ima_sync_key_zsxq_daily=deps.ima_sync_key_zsxq_daily,
            now_factory=deps.now_factory,
            run_zsxq_auto_workflow=lambda group_id, runtime: ZsxqAutomation(
                active_storage(),
                zsxq_helpers.get_zsxq_client(),
            ).run_daily_workflow(group_id, runtime),
            chat_with_retry=lambda runtime, prompt, **kwargs: deps.chat_with_retry(runtime, prompt, **kwargs),
        )
    )

    llm_config_service = build_llm_config_module_service(
        LLMConfigModuleDeps(
            get_storage=active_storage,
            get_client=lambda: deps.get_client(),
            config_value=lambda key, default=None: deps.config_value(key, default),
            build_news_source_diagnostics=deps.build_news_source_diagnostics,
            get_json=lambda: deps.get_json(),
            to_bool=lambda value, default=False: deps.to_bool(value, default),
            reload_config_center=lambda: deps.config_center.reload_from_storage() if deps.config_center is not None else None,
            reset_llm_client=deps.reset_llm_client,
        )
    )

    broker_import_service = build_broker_import_module_service(
        BrokerImportModuleDeps(
            get_storage=active_storage,
            load_price_frames=lambda tickers, start_date=None, end_date=None: deps.load_price_frames(
                tickers,
                start_date=start_date,
                end_date=end_date,
            ),
            external_cash_flows_hkd=deps.external_cash_flows_hkd,
        )
    )

    system_api_service = build_system_api_module_service(
        SystemApiModuleDeps(
            auth_service=deps.auth_service,
            get_storage=active_storage,
            get_json=lambda: deps.get_json(),
            to_bool=lambda value, default=False: deps.to_bool(value, default),
            news_source_diagnostics=deps.build_news_source_diagnostics,
            now_iso=lambda: deps.now_factory().isoformat(timespec="seconds"),
            ima_client_factory=lambda client_id, api_key: ima_sync.IMAKnowledgeBaseClient(client_id, api_key),
            unauthorized_response=deps.unauthorized_response,
        )
    )

    task_api_service = build_task_api_module_service(
        TaskApiModuleDeps(
            get_json=lambda: deps.get_json(),
            get_task_manager=lambda: deps.get_task_manager(),
            task_payload=lambda *args, **kwargs: deps.task_api_helpers.task_payload(*args, **kwargs),
            task_error_response=lambda *args, **kwargs: deps.task_api_helpers.task_error_response(*args, **kwargs),
            split_csv_values=lambda values: deps.task_api_helpers.split_csv_values(values),
            safe_int=lambda value, default: deps.safe_int(value, default),
            to_bool=lambda value, default=False: deps.to_bool(value, default),
            logger=deps.logger,
        )
    )

    research_step_helpers = build_research_step_helpers(
        ResearchStepHelperDeps(
            get_client=lambda: deps.get_client(),
            get_env_collector=lambda: deps.get_env_collector(),
            get_research_engine=lambda: deps.get_research_engine(),
            get_storage_stock_name=lambda stock_id: active_storage().get_stock_playbook(stock_id) or {},
            get_runtime_meta=lambda: deps.get_runtime_meta(),
        )
    )
    research_service = build_research_module_service(
        ResearchModuleDeps(
            get_json=lambda: deps.get_json(),
            safe_int=lambda value, default: deps.safe_int(value, default),
            to_bool=lambda value, default=False: deps.to_bool(value, default),
            json_safe=lambda value: deps.json_safe(value),
            get_client=lambda: deps.get_client(),
            chat_with_retry=lambda runtime, prompt, **kwargs: deps.chat_with_retry(runtime, prompt, **kwargs),
            is_llm_failure_text=lambda text: deps.is_llm_failure_text(text),
            get_runtime_meta=lambda: deps.get_runtime_meta(),
            get_storage=active_storage,
            collect_environment_step=research_step_helpers.collect_environment_step,
            assess_impact_step=research_step_helpers.assess_impact_step,
            execute_research_step=research_step_helpers.execute_research_step,
        )
    )

    factor_analysis_service = build_factor_analysis_module_service(
        FactorAnalysisModuleDeps(
            get_storage=active_storage,
            get_json=lambda: deps.get_json(),
            json_safe=deps.json_safe,
            factor_analysis_service_factory=deps.factor_analysis_service_factory,
            logger=deps.logger,
        )
    )

    return AppServices(
        xueqiu_task_runners=xueqiu_task_runners,
        xueqiu_service=xueqiu_service,
        stocks_service=stocks_service,
        preferences_service=preferences_service,
        app_shell_service=app_shell_service,
        shell_pages_service=shell_pages_service,
        decision_review_service=decision_review_service,
        zsxq_service=zsxq_service,
        llm_config_service=llm_config_service,
        broker_import_service=broker_import_service,
        system_api_service=system_api_service,
        task_api_service=task_api_service,
        research_service=research_service,
        factor_analysis_service=factor_analysis_service,
    )
