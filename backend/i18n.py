# -*- coding: utf-8 -*-
"""
Internationalization (i18n) support.

Loads translations from JSON files (web/i18n/ko.json, web/i18n/en.json).
Language preference is stored in a cookie ('lang').
Provides a context processor that injects `t` dict into all templates.
"""
from __future__ import annotations

import json
import os
from typing import Any

from flask import request

_I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "i18n")
_DEFAULT_LANG = "ko"
_SUPPORTED = ("ko", "en")


class _DotDict(dict):
    """Dict subclass that allows attribute-style access (t.nav.home)."""

    def __getattr__(self, key: str) -> Any:
        try:
            val = self[key]
        except KeyError:
            return f"[{key}]"  # fallback: show key name
        return val

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


def _to_dotdict(d: dict) -> _DotDict:
    """Recursively convert nested dicts to _DotDict."""
    dd = _DotDict()
    for k, v in d.items():
        dd[k] = _to_dotdict(v) if isinstance(v, dict) else v
    return dd


_lang_cache: dict[str, tuple[float, _DotDict]] = {}

def _load_lang(lang: str) -> _DotDict:
    """Load language JSON file with mtime-based cache invalidation."""
    path = os.path.join(_I18N_DIR, f"{lang}.json")
    if not os.path.exists(path):
        path = os.path.join(_I18N_DIR, f"{_DEFAULT_LANG}.json")
    mtime = os.path.getmtime(path)
    cached = _lang_cache.get(lang)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = _to_dotdict(data)
    _lang_cache[lang] = (mtime, result)
    return result


def get_lang() -> str:
    """Get current language from cookie, default to 'ko'."""
    lang = request.cookies.get("lang", _DEFAULT_LANG)
    if lang not in _SUPPORTED:
        lang = _DEFAULT_LANG
    return lang


def get_translations() -> _DotDict:
    """Get translation dict for current language."""
    return _load_lang(get_lang())


def i18n_context() -> dict:
    """Context processor: injects `t` (translations) and `lang` into templates."""
    lang = get_lang()
    return {"t": _load_lang(lang), "lang": lang}
