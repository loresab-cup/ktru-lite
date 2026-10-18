"""Repeat jobs reuse only validated answers, with bounded lifetime and isolated values."""

from concurrent.futures import CancelledError

import pytest

import confidence
import extractor
from extraction_cache import ValidatedCache


def test_cache_ttl_capacity_context_and_copy_isolation():
    now = [0.0]
    cache = ValidatedCache(limit=2, ttl=10, clock=lambda: now[0])
    row = {"source": "llm", "characteristics": [{"value": "А4"}]}
    cache.put("paper", "model1", row)
    row["characteristics"][0]["value"] = "changed"
    result = cache.get("paper", "model1")
    assert result["characteristics"][0]["value"] == "А4" and result["cache_hit"]
    result["characteristics"].clear()
    assert cache.get("paper", "model1")["characteristics"]
    assert cache.get("paper ", "model1") is None
    assert cache.get("paper", "model2") is None
    cache.put("fallback", "model1", {"source": "rules"})
    assert cache.get("fallback", "model1") is None
    cache.put("second", "model1", row)
    cache.put("third", "model1", row)
    assert cache.get("paper", "model1") is None
    now[0] = 10.0
    assert cache.get("third", "model1") is None


def test_repeat_job_skips_model_but_cancelled_job_cannot_read_cache(monkeypatch):
    cache = ValidatedCache()
    monkeypatch.setattr(extractor, "_validated_cache", cache)
    calls = []

    def extract(rows, deadline):
        calls.extend(rows)
        return [{"name": text, "source": "llm", "characteristics": []} for text in rows]

    monkeypatch.setattr(extractor, "extract_chunk", extract)
    first = extractor.JobExtractor(["Бумага А4", "Бумага А3"])
    assert first.get("Бумага А4")["source"] == "llm"
    first.get("Бумага А3")
    first.close()
    second = extractor.JobExtractor(["Бумага А4", "Бумага А3"])
    assert second.get("Бумага А4")["cache_hit"] is True
    assert calls == ["Бумага А4", "Бумага А3"]
    second.close()
    with pytest.raises(CancelledError):
        second.get("Бумага А4")
    monkeypatch.setattr(extractor, "MODEL", extractor.MODEL + "-different")
    third = extractor.JobExtractor(["Бумага А4"])
    assert not third.get("Бумага А4").get("cache_hit")
    third.close()
    assert len(calls) == 3


def test_equal_candidates_cannot_be_assigned_automatically():
    candidates = [
        {"code": str(i), "name": "Бумага", "status": "ACTIVE", "score": 1.0, "rank_score": 1.3}
        for i in range(2)
    ]
    verdict = confidence.assess(candidates)
    assert verdict["confidence"] > 85
    assert verdict["status"] == "needs_review"
    assert "кандидаты равны" in verdict["summary"]
