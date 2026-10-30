"""Task ancestry and the optional AnyIO execution backend."""

from typing import Any, cast


class _UnavailableBackend:
    def __getattr__(self, name: str) -> Any:
        message = "install squid-ui[services] to run services and cleanup"
        raise RuntimeError(message)


try:
    import anyio
except ImportError:
    anyio = cast(Any, _UnavailableBackend())


def task_is_within(runner_ids: set[int]) -> bool:
    if not runner_ids:
        return False

    # Task groups decide ownership. Context variables come from whoever submits the
    # work, which can be a service handing a job to an application task group (or vice versa).
    parents = {task.id: task.parent_id for task in anyio.get_running_tasks()}
    current: int | None = anyio.get_current_task().id
    while current is not None:
        if current in runner_ids:
            return True
        current = parents.get(current)
    return False
