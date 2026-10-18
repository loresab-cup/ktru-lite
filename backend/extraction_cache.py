"""Bounded in-memory reuse of validated LLM attributes for exactly equal source text."""

import copy
import hashlib
import threading
import time
from collections import OrderedDict


class ValidatedCache:
    def __init__(self, limit=5000, ttl=1800, clock=time.monotonic):
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.rows = OrderedDict()
        self.lock = threading.Lock()

    def get(self, text, context):
        key = hashlib.sha256((context + "\0" + text).encode()).digest()
        with self.lock:
            entry = self.rows.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires <= self.clock():
                del self.rows[key]
                return None
            self.rows.move_to_end(key)
            return copy.deepcopy(value) | {"cache_hit": True}

    def put(self, text, context, value):
        if value.get("source") != "llm":
            return
        key = hashlib.sha256((context + "\0" + text).encode()).digest()
        with self.lock:
            self.rows[key] = (self.clock() + self.ttl, copy.deepcopy(value))
            self.rows.move_to_end(key)
            while len(self.rows) > self.limit:
                self.rows.popitem(last=False)
