# -*- coding: utf-8 -*-
"""NaN-safe JSON provider for Flask.

JavaScript JSON.parse does not support NaN/Infinity,
so we convert them to null before serialization.
"""
from __future__ import annotations

import math as _math
from flask.json.provider import DefaultJSONProvider


class NaNSafeJSONProvider(DefaultJSONProvider):
    """float NaN/Inf -> null."""

    @staticmethod
    def _clean(o):
        if isinstance(o, float) and (_math.isnan(o) or _math.isinf(o)):
            return None
        if isinstance(o, dict):
            return {k: NaNSafeJSONProvider._clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [NaNSafeJSONProvider._clean(v) for v in o]
        return o

    def dumps(self, obj, **kwargs):
        return super().dumps(self._clean(obj), **kwargs)
