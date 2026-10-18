"""Suggest similar rows for explicit human review; never propagate decisions automatically."""

import hashlib
import json

import extractor
import search


def signature(item):
    attrs = item.attributes or {}
    if not attrs.get("name") or attrs.get("source") != "llm":
        return None
    columns = attrs.get("columns", {})
    title = next(
        (
            v
            for k, v in columns.items()
            if k.lower().startswith("наименование") or k.lower() in ("товар", "название")
        ),
        attrs["name"],
    )
    unit = next((v for k, v in columns.items() if k.lower().startswith("ед")), "")
    data = {
        "title": extractor.norm(title),
        "unit": search.unit_key(unit) or extractor.norm(unit),
        "attrs": {
            k: attrs.get(k) for k in ("name", "brand", "model", "measure", "characteristics")
        },
        "candidates": sorted(c["code"] for c in item.candidates or []),
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def snapshot(items):
    data = [[r.id, r.raw_text, r.attributes, r.candidates, r.status, r.ktru_code] for r in items]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
