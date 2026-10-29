"""Publish durable job identifiers; PostgreSQL remains the source of job status."""

import logging

logger = logging.getLogger(__name__)


def publish(job_id):
    from celery_tasks import process_job

    try:
        process_job.apply_async(args=[str(job_id)], task_id=str(job_id), retry=False)
        return True
    except Exception:
        # Committed queued rows are retried by the periodic dispatcher after broker recovery.
        logger.exception("Broker unavailable; committed job remains queued")
        return False
