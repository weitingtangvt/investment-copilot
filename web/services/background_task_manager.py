"""Persistent background task manager backed by APScheduler."""

from __future__ import annotations

import inspect
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from apscheduler.executors.pool import ThreadPoolExecutor as APSchedulerThreadPoolExecutor
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler

from core.storage import Storage


TaskRunner = Callable[..., Any]
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class BackgroundTaskManager:
    def __init__(
        self,
        storage: Optional[Storage] = None,
        *,
        max_workers: int = 4,
    ) -> None:
        self._storage = storage or Storage()
        self._runners: Dict[str, TaskRunner] = {}
        self._lock = threading.RLock()
        self._started = False
        self._scheduler = BackgroundScheduler(
            executors={"default": APSchedulerThreadPoolExecutor(max_workers=max(1, int(max_workers)))},
            job_defaults={"coalesce": False, "max_instances": 1},
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    @staticmethod
    def _job_id(task_id: str) -> str:
        return f"bg-task-{task_id}"

    def register_runner(self, name: str, runner: TaskRunner) -> None:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("runner name is required")
        if not callable(runner):
            raise ValueError("runner must be callable")
        with self._lock:
            self._runners[clean_name] = runner

    def submit_task(
        self,
        runner_name: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_runner = str(runner_name or "").strip()
        if not clean_runner:
            raise ValueError("runner_name is required")
        with self._lock:
            if clean_runner not in self._runners:
                raise KeyError(f"runner not registered: {clean_runner}")

        clean_task_id = str(task_id or uuid.uuid4().hex).strip()
        if not clean_task_id:
            raise ValueError("task_id is required")
        if self._storage.get_task_record(clean_task_id):
            raise ValueError(f"task already exists: {clean_task_id}")

        now = self._now_iso()
        record = {
            "task_id": clean_task_id,
            "runner_name": clean_runner,
            "payload": dict(payload or {}),
            "metadata": dict(metadata or {}),
            "status": "queued",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        saved = self._storage.save_task_record(clean_task_id, record)

        with self._lock:
            if self._started:
                self._schedule_task(clean_task_id)
        return saved

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._storage.get_task_record(task_id)

    def list_tasks(
        self,
        statuses: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._storage.list_task_records(statuses=statuses, limit=limit)

    def cancel_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        record = self._storage.get_task_record(task_id)
        if not record:
            return None

        status = str(record.get("status") or "").strip().lower()
        if status in TERMINAL_STATUSES:
            return record

        now = self._now_iso()
        cancelled = self._storage.save_task_record(
            str(record.get("task_id") or task_id),
            {
                "status": "cancelled",
                "cancelled_at": now,
                "updated_at": now,
            },
        )
        with self._lock:
            if self._started:
                self._unschedule_task(str(cancelled.get("task_id") or task_id))
        return cancelled

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._scheduler.start()
            self._started = True

        for task in self._storage.recover_pending_tasks():
            task_id = str(task.get("task_id") or "").strip()
            if not task_id:
                continue
            status = str(task.get("status") or "").strip().lower()
            if status == "interrupted":
                self._storage.save_task_record(
                    task_id,
                    {
                        "status": "queued",
                        "recovered_from": "interrupted",
                        "updated_at": self._now_iso(),
                    },
                )
            with self._lock:
                if self._started:
                    self._schedule_task(task_id)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if not self._started:
                return
            self._scheduler.shutdown(wait=wait)
            self._started = False

    def _schedule_task(self, task_id: str) -> None:
        self._scheduler.add_job(
            self._run_task_job,
            trigger="date",
            run_date=datetime.now(),
            args=[task_id],
            id=self._job_id(task_id),
            replace_existing=True,
            misfire_grace_time=60,
        )

    def _unschedule_task(self, task_id: str) -> None:
        try:
            self._scheduler.remove_job(self._job_id(task_id))
        except JobLookupError:
            return

    def _run_task_job(self, task_id: str) -> None:
        task = self._storage.get_task_record(task_id)
        if not task:
            return

        status = str(task.get("status") or "").strip().lower()
        if status in TERMINAL_STATUSES or status == "running":
            return

        runner_name = str(task.get("runner_name") or "").strip()
        with self._lock:
            runner = self._runners.get(runner_name)

        if not runner:
            now = self._now_iso()
            self._storage.save_task_record(
                task_id,
                {
                    "status": "failed",
                    "error": f"runner not registered: {runner_name}",
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            return

        now = self._now_iso()
        self._storage.save_task_record(
            task_id,
            {
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "error": None,
            },
        )

        try:
            result = self._invoke_runner(runner, task)
        except Exception as exc:
            latest = self._storage.get_task_record(task_id) or {}
            if str(latest.get("status") or "").strip().lower() == "cancelled":
                return
            done_at = self._now_iso()
            self._storage.save_task_record(
                task_id,
                {
                    "status": "failed",
                    "error": str(exc),
                    "completed_at": done_at,
                    "updated_at": done_at,
                },
            )
            return

        latest = self._storage.get_task_record(task_id) or {}
        if str(latest.get("status") or "").strip().lower() == "cancelled":
            return
        done_at = self._now_iso()
        self._storage.save_task_record(
            task_id,
            {
                "status": "completed",
                "result": result,
                "error": None,
                "completed_at": done_at,
                "updated_at": done_at,
            },
        )

    @staticmethod
    def _invoke_runner(runner: TaskRunner, task: Dict[str, Any]) -> Any:
        payload = task.get("payload")
        signature = inspect.signature(runner)
        params = list(signature.parameters.values())
        has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params)
        positional = [
            param
            for param in params
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]

        if has_varargs or len(positional) >= 2:
            return runner(payload, dict(task))
        if len(positional) == 1:
            return runner(payload)
        return runner()
