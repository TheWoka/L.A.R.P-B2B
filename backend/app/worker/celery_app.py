"""Приложение Celery. Запуск: `celery -A app.worker.celery_app worker -B --loglevel=info`."""

from celery import Celery

from app.core.config import get_settings
from app.core.logging import setup_logging

_settings = get_settings()

celery_app = Celery(
    "auditor",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_track_started=True,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "purge-expired-reports": {
            "task": "app.worker.tasks.purge_expired_reports",
            "schedule": 3600.0,
        },
    },
)

# `celery -A app.worker.celery_app` ищет в модуле атрибут `app` — даём ему ссылку.
app = celery_app

setup_logging()
