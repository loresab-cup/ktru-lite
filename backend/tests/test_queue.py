"""Broker failure does not discard committed work."""

import uuid

import tasks
import job_queue


def test_broker_failure_keeps_job_for_periodic_dispatch(monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(tasks.process_job, "apply_async", broken)
    assert job_queue.publish(uuid.uuid4()) is False
