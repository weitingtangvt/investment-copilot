from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable


@dataclass
class USScreenerRuntimeHelperDeps:
    get_storage: Callable[[], Any]
    get_service: Callable[[], Any]
    get_job_thread: Callable[[], Any]
    set_job_thread: Callable[[Any], Any]
    get_retry_timer: Callable[[], Any]
    set_retry_timer: Callable[[Any], Any]
    get_retry_eta: Callable[[], Any]
    set_retry_eta: Callable[[Any], Any]
    job_lock: Any
    now_factory: Callable[[], datetime]
    seconds_until: Callable[[datetime | None], int]
    auto_resume_delay_seconds: int
    logger: Any
    thread_factory: Callable[..., Any] = threading.Thread
    timer_factory: Callable[..., Any] = threading.Timer
    run_job_callback: Callable[..., Any] | None = None


@dataclass
class USScreenerScanRunnerDeps:
    runtime_helpers: "USScreenerRuntimeHelpers"
    get_service: Callable[[], Any]
    get_storage: Callable[[], Any]
    patch_task_record: Callable[[dict[str, Any] | None, dict[str, Any]], Any]
    safe_int: Callable[[Any, int], int]
    to_bool: Callable[[Any, bool], bool]
    get_job_thread: Callable[[], Any]
    set_job_thread: Callable[[Any], Any]
    job_lock: Any
    current_thread_factory: Callable[[], Any]
    strategy_momentum_spike: str
    strategy_post_52w_low_reversal: str


class USScreenerRuntimeHelpers:
    def __init__(self, deps: USScreenerRuntimeHelperDeps):
        self._deps = deps

    def start_thread(self, *, resume: bool = False) -> Any:
        thread = self._deps.thread_factory(
            target=self._deps.run_job_callback,
            kwargs={"resume": resume},
            name="us-screener-job-resume" if resume else "us-screener-job",
            daemon=True,
        )
        self._deps.set_job_thread(thread)
        thread.start()
        return thread

    def get_partial_summary(self) -> dict[str, Any] | None:
        partial = self._deps.get_storage().get_us_screener_partial()
        if not partial:
            return None
        total = int(partial.get("total_batches") or 0)
        completed = int(partial.get("completed_batches") or 0)
        pending = len(partial.get("pending_batches") or [])
        return {
            "run_id": partial.get("run_id"),
            "state": partial.get("state") or ("partial" if pending else "success"),
            "completed_batches": completed,
            "total_batches": total,
            "pending_batches": pending,
            "failed_batches": len(partial.get("failed_batches") or []),
            "updated_at": partial.get("updated_at"),
        }

    def save_status(self, **updates: Any) -> dict[str, Any]:
        payload = dict(updates or {})
        retry_eta = self._deps.get_retry_eta()
        if "partial_summary" not in payload or payload["partial_summary"] is None:
            payload.setdefault("partial_summary", self.get_partial_summary())
        if "auto_resume_eta" not in payload:
            payload["auto_resume_eta"] = retry_eta.isoformat(timespec="seconds") if retry_eta else ""
        if "auto_resume_in_seconds" not in payload:
            payload["auto_resume_in_seconds"] = self._deps.seconds_until(retry_eta)
        return self._deps.get_storage().save_us_screener_job_status(payload)

    def default_status(self) -> dict[str, Any]:
        storage = self._deps.get_storage()
        status = storage.get_us_screener_job_status()
        job_thread = self._deps.get_job_thread()
        is_running = job_thread is not None and job_thread.is_alive()
        if status.get("state") == "running" and not is_running:
            status = storage.save_us_screener_job_status(
                {
                    "state": "error",
                    "finished_at": self._deps.now_factory().isoformat(timespec="seconds"),
                    "step": "error",
                    "message": "Text, TextRefresh",
                    "last_error": str(status.get("last_error") or "Text"),
                }
            )
        partial_summary = self.get_partial_summary()
        status["partial_summary"] = partial_summary
        retry_eta = self._deps.get_retry_eta()
        if retry_eta:
            status["auto_resume_eta"] = retry_eta.isoformat(timespec="seconds")
            status["auto_resume_in_seconds"] = self._deps.seconds_until(retry_eta)
        else:
            status.setdefault("auto_resume_eta", "")
            status.setdefault("auto_resume_in_seconds", 0)
        return status

    def cancel_auto_retry(self) -> None:
        timer = self._deps.get_retry_timer()
        if timer:
            timer.cancel()
        self._deps.set_retry_timer(None)
        self._deps.set_retry_eta(None)
        self.save_status(auto_resume_eta="", auto_resume_in_seconds=0)

    def schedule_auto_retry(self, delay_seconds: int | None = None) -> None:
        try:
            parsed_delay = int(delay_seconds) if delay_seconds is not None else None
        except (TypeError, ValueError):
            parsed_delay = None
        base_delay = parsed_delay or self._deps.auto_resume_delay_seconds or 120
        delay = max(30, base_delay)

        timer = self._deps.get_retry_timer()
        if timer:
            timer.cancel()

        eta = self._deps.now_factory() + timedelta(seconds=delay)
        self._deps.set_retry_eta(eta)

        def _trigger() -> None:
            with self._deps.job_lock:
                self._deps.set_retry_timer(None)
                self._deps.set_retry_eta(None)
                job_thread = self._deps.get_job_thread()
                if job_thread is not None and job_thread.is_alive():
                    return
                storage = self._deps.get_storage()
                partial = storage.get_us_screener_partial()
                pending = partial.get("pending_batches") if isinstance(partial, dict) else None
                if not pending:
                    self.save_status(auto_resume_eta="", auto_resume_in_seconds=0)
                    return
                storage.save_us_screener_job_status(
                    {
                        "state": "running",
                        "step": "auto_resume",
                        "message": "AutoText",
                        "started_at": self._deps.now_factory().isoformat(timespec="seconds"),
                        "partial_summary": self.get_partial_summary(),
                    }
                )
                self.start_thread(resume=True)

        timer = self._deps.timer_factory(delay, _trigger)
        if hasattr(timer, "name"):
            timer.name = "us-screener-auto-resume"
        if hasattr(timer, "daemon"):
            timer.daemon = True
        timer.start()
        self._deps.set_retry_timer(timer)
        self.save_status(
            auto_resume_eta=eta.isoformat(timespec="seconds"),
            auto_resume_in_seconds=delay,
        )

    def run_job(self, *, resume: bool = False) -> None:
        storage = self._deps.get_storage()

        def progress(update: dict[str, Any]) -> None:
            if not isinstance(update, dict):
                return
            self.save_status(**update)

        try:
            result = self._deps.get_service().run(progress_callback=progress, resume=resume)
            market_date = str(result.get("as_of_market_date") or "").strip()
            if market_date:
                storage.save_us_screener_result(market_date, result)
            storage.save_us_screener_latest(result)
            partial_summary = self.get_partial_summary()
            if partial_summary and partial_summary.get("state") == "partial":
                self.save_status(
                    state="partial",
                    finished_at=self._deps.now_factory().isoformat(timespec="seconds"),
                    message="TextRefreshFailed, TextResultText",
                    step="partial",
                    last_error=str(partial_summary.get("last_error") or ""),
                    partial_summary=partial_summary,
                )
                self.schedule_auto_retry()
            else:
                self.cancel_auto_retry()
                self.save_status(
                    state="success",
                    finished_at=self._deps.now_factory().isoformat(timespec="seconds"),
                    message="Text",
                    step="done",
                    last_error="",
                    partial_summary=None,
                )
        except Exception as exc:
            if self._deps.logger:
                self._deps.logger.exception("us screener job failed")
            partial_summary = self.get_partial_summary()
            state = "partial" if partial_summary and partial_summary.get("pending_batches") else "error"
            message = "TextRefreshFailed, Text" if state == "partial" else "TextFailed"
            self.save_status(
                state=state,
                finished_at=self._deps.now_factory().isoformat(timespec="seconds"),
                message=message,
                step="error",
                last_error=str(exc),
                partial_summary=partial_summary,
            )
            if state == "partial":
                self.schedule_auto_retry()
            else:
                self.cancel_auto_retry()


def build_us_screener_runtime_helpers(deps: USScreenerRuntimeHelperDeps) -> USScreenerRuntimeHelpers:
    return USScreenerRuntimeHelpers(deps)


def build_us_screener_scan_task_runner(deps: USScreenerScanRunnerDeps):
    def _runner(
        payload: dict[str, Any] | None,
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_payload = dict(payload or {})
        resume = deps.to_bool(request_payload.get("resume"), False)
        current_thread = deps.current_thread_factory()

        with deps.job_lock:
            job_thread = deps.get_job_thread()
            if (
                job_thread is not None
                and job_thread.is_alive()
                and job_thread is not current_thread
            ):
                raise RuntimeError("Text")
            deps.set_job_thread(current_thread)

        started_at = deps.runtime_helpers._deps.now_factory().isoformat(timespec="seconds")
        deps.runtime_helpers.cancel_auto_retry()
        deps.runtime_helpers.save_status(
            state="running",
            started_at=started_at,
            finished_at="",
            step="resume_queued" if resume else "queued",
            message="TextRefreshText" if resume else "Text",
            last_error="",
            partial_summary=deps.runtime_helpers.get_partial_summary(),
        )
        deps.patch_task_record(
            task,
            {
                "message": "US screener scan is running.",
                "resume": resume,
                "started_at": started_at,
            },
        )

        def _on_progress(update: dict[str, Any]) -> None:
            if not isinstance(update, dict):
                return
            deps.runtime_helpers.save_status(**update)
            task_patch: dict[str, Any] = {}
            if update.get("message"):
                task_patch["message"] = str(update.get("message") or "").strip()
            if update.get("step"):
                task_patch["step"] = str(update.get("step") or "").strip()
            if isinstance(update.get("progress"), dict):
                task_patch["progress"] = dict(update.get("progress") or {})
            if task_patch:
                deps.patch_task_record(task, task_patch)

        try:
            result = deps.get_service().run(progress_callback=_on_progress, resume=resume)
            market_date = str(result.get("as_of_market_date") or "").strip()
            storage = deps.get_storage()
            if market_date:
                storage.save_us_screener_result(market_date, result)
            storage.save_us_screener_latest(result)

            partial_summary = deps.runtime_helpers.get_partial_summary()
            if partial_summary and partial_summary.get("state") == "partial":
                deps.runtime_helpers.save_status(
                    state="partial",
                    finished_at=deps.runtime_helpers._deps.now_factory().isoformat(timespec="seconds"),
                    message="TextRefreshFailed, TextResultText",
                    step="partial",
                    last_error=str(partial_summary.get("last_error") or ""),
                    partial_summary=partial_summary,
                )
                deps.runtime_helpers.schedule_auto_retry()
                final_state = "partial"
                final_message = "US screener finished with partial batches."
            else:
                deps.runtime_helpers.cancel_auto_retry()
                deps.runtime_helpers.save_status(
                    state="success",
                    finished_at=deps.runtime_helpers._deps.now_factory().isoformat(timespec="seconds"),
                    message="Text",
                    step="done",
                    last_error="",
                    partial_summary=None,
                )
                final_state = "success"
                final_message = "US screener scan completed."

            strategies = result.get("strategies") if isinstance(result.get("strategies"), dict) else {}
            momentum = (
                strategies.get(deps.strategy_momentum_spike)
                if isinstance(strategies.get(deps.strategy_momentum_spike), dict)
                else {}
            )
            reversal = (
                strategies.get(deps.strategy_post_52w_low_reversal)
                if isinstance(strategies.get(deps.strategy_post_52w_low_reversal), dict)
                else {}
            )

            payload_result = {
                "success": bool(result.get("success", True)),
                "resume": resume,
                "message": final_message,
                "summary": {
                    "state": final_state,
                    "as_of_market_date": market_date,
                    "generated_at": str(result.get("generated_at") or ""),
                    "momentum_matched": deps.safe_int(momentum.get("matched"), 0),
                    "post_52w_low_reversal_matched": deps.safe_int(reversal.get("matched"), 0),
                    "stats": result.get("stats") if isinstance(result.get("stats"), dict) else {},
                    "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
                },
            }
            deps.patch_task_record(task, payload_result)
            return payload_result
        except Exception as exc:
            partial_summary = deps.runtime_helpers.get_partial_summary()
            state = "partial" if partial_summary and partial_summary.get("pending_batches") else "error"
            message = "TextRefreshFailed, Text" if state == "partial" else "TextFailed"
            deps.runtime_helpers.save_status(
                state=state,
                finished_at=deps.runtime_helpers._deps.now_factory().isoformat(timespec="seconds"),
                message=message,
                step="error",
                last_error=str(exc),
                partial_summary=partial_summary,
            )
            if state == "partial":
                deps.runtime_helpers.schedule_auto_retry()
            else:
                deps.runtime_helpers.cancel_auto_retry()
            deps.patch_task_record(task, {"message": message})
            raise
        finally:
            with deps.job_lock:
                if deps.get_job_thread() is current_thread:
                    deps.set_job_thread(None)

    return _runner
