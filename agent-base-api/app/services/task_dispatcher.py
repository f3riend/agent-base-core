"""Task dispatcher — routes work to Celery when USE_CELERY=true,
otherwise runs synchronously.

Usage:
    dispatch("generate_images", generate_images_fn, prompt, count, ...)
"""

from __future__ import annotations

import os
from typing import Any, Callable


def use_celery() -> bool:
    return (os.getenv("USE_CELERY") or "").strip().lower() in ("1", "true", "yes")


def dispatch(task_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Route a task to Celery or run it synchronously.

    Args:
        task_name: Logical task name used to look up the Celery task function.
        fn: Synchronous fallback callable (used when USE_CELERY=false).
        *args: Positional arguments forwarded to ``fn`` or the Celery task.
        **kwargs: Keyword arguments forwarded to ``fn`` or the Celery task.

    Returns:
        When queued: ``{"queued": True, "task_id": "...", "task_name": "..."}``
        When sync:   ``{"queued": False, "task_name": "...", "result": <fn output>}``
    """
    if use_celery():
        try:
            from app.tasks.image_tasks import (
                caption_generate_task,
                caption_revize_task,
                generate_from_reference_task,
                generate_images_task,
                holiday_generate_task,
                revise_image_task,
                publish_content_task,
                video_generate_task,
            )

            task_map: dict[str, Any] = {
                "generate_images": generate_images_task,
                "generate_from_reference": generate_from_reference_task,
                "revise_image": revise_image_task,
                "publish_content": publish_content_task,
                "holiday_generate": holiday_generate_task,
                "video_generate": video_generate_task,
                "caption_generate": caption_generate_task,
                "caption_revize": caption_revize_task,
            }
            task_fn = task_map.get(task_name)
            if task_fn is not None:
                result = task_fn.delay(*args, **kwargs)
                return {"queued": True, "task_id": result.id, "task_name": task_name}
        except Exception:
            # Celery import/runtime issue: degrade gracefully to sync fallback.
            pass
        # Task not mapped (or Celery unavailable) — fall through to sync execution

    result = fn(*args, **kwargs)
    return {"queued": False, "task_name": task_name, "result": result}
