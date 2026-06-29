from __future__ import annotations


def register_task_api(bp, service, auth_guard=None) -> None:
    auth_guard = auth_guard or (lambda f: f)

    @bp.route("/api/tasks", methods=["POST"])
    @auth_guard
    def api_create_task():
        return service.create_task()

    @bp.route("/api/tasks/<task_id>", methods=["GET"])
    @auth_guard
    def api_get_task(task_id: str):
        return service.get_task(task_id)

    @bp.route("/api/tasks", methods=["GET"])
    @auth_guard
    def api_list_tasks():
        return service.list_tasks()

    @bp.route("/api/tasks/<task_id>/cancel", methods=["POST"])
    @auth_guard
    def api_cancel_task(task_id: str):
        return service.cancel_task(task_id)
