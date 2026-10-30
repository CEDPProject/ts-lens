# -*- coding: utf-8 -*-
"""In-memory session store for loaded time series data.

Thread-safe LRU dict with maximum size limit.
Single-user tool -- simple dict keyed by session ID.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from backend.app_config import cfg

_MAX_ENTRIES = cfg("session.max_entries")
_lock = threading.Lock()
_store: OrderedDict = OrderedDict()


class _LRUStore:
    """Thread-safe, size-limited dict with LRU eviction."""

    def get(self, key, default=None):
        with _lock:
            if key in _store:
                _store.move_to_end(key)
                return _store[key]
            return default

    def __setitem__(self, key, value):
        with _lock:
            if key in _store:
                _store.move_to_end(key)
            _store[key] = value
            while len(_store) > _MAX_ENTRIES:
                _store.popitem(last=False)

    def __getitem__(self, key):
        with _lock:
            _store.move_to_end(key)
            return _store[key]

    def __contains__(self, key):
        with _lock:
            return key in _store

    def __delitem__(self, key):
        with _lock:
            del _store[key]

    def pop(self, key, *args):
        with _lock:
            return _store.pop(key, *args)

    def keys(self):
        with _lock:
            return list(_store.keys())

    def values(self):
        with _lock:
            return list(_store.values())

    def items(self):
        with _lock:
            return list(_store.items())

    def __len__(self):
        with _lock:
            return len(_store)


DATA_STORE = _LRUStore()


def purge_dataset_sessions(dataset_key: str) -> int:
    """지운 데이터셋을 불러 둔 세션을 메모리에서 뺀다."""
    with _lock:
        stale = [k for k, v in _store.items()
                 if isinstance(v, dict) and v.get("dataset_key") == dataset_key]
        for k in stale:
            del _store[k]
    return len(stale)
