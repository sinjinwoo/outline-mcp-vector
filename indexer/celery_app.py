import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/1")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", str(60 * 60)))  # hourly

celery_app = Celery("indexer", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # A task is only removed from the queue once it finishes, so a worker
    # crash mid-task redelivers it instead of silently dropping it.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "periodic-outline-sync": {
            "task": "indexer.run_sync",
            "schedule": SYNC_INTERVAL_SECONDS,
            "args": (False,),  # incremental — webhooks already handle live updates
        },
    },
)

# `celery -A indexer.celery_app worker` only imports this module, so tasks
# must be imported here or the worker starts up with an empty task registry.
# Placed last so indexer/tasks.py's `from indexer.celery_app import celery_app`
# resolves against the already-initialized module instead of circular-importing.
from indexer import tasks  # noqa: F401,E402
