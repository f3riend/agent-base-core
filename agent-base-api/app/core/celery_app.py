"""Celery application factory.

Activated only when USE_CELERY=true (or 1/yes) in the environment.
Uses Redis as both broker and result backend (configured via settings).
"""

from __future__ import annotations

from celery import Celery
from app.core.settings import settings

celery_app = Celery(
    "social_media_worker",
    broker=settings.celery.broker,
    backend=settings.celery.backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    # Image generation is heavy — never pre-fetch more than one task per worker
    worker_prefetch_multiplier=1,
    # Keep results 1 hour in Redis so the frontend can poll them
    result_expires=3600,
)

# `autodiscover_tasks(["app.tasks"])` yükler: app.tasks.tasks — bizde modül adı image_tasks.
import app.tasks.image_tasks  # noqa: E402, F401 — @celery_app.task kayıtları
