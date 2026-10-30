# -*- coding: utf-8 -*-
"""Centralized config loader for all YAML config files.

All modules import from here instead of loading YAML themselves.
Usage:
    from backend.app_config import cfg, cfg_search, cfg_text_ts, cfg_prompts
    val = cfg("display.max_points")       # -> 50000
    val = cfg_search("dtw.band_ratio")    # -> 0.2
    val = cfg_text_ts("active_model")     # -> "exaone-ko-vllm"
"""
from __future__ import annotations

import logging
import hashlib
import threading
from functools import wraps
import os
import tempfile
from copy import deepcopy
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_CONFIG_PATH = _CONFIG_DIR / "app.yaml"
_cache: dict | None = None

_SEARCH_CONFIG_PATH = _CONFIG_DIR / "search.yaml"
_search_cache: dict | None = None

_TEXT_TS_CONFIG_PATH = _CONFIG_DIR / "model" / "text_ts.yaml"
_text_ts_cache: dict | None = None

_PROMPTS_CONFIG_PATH = _CONFIG_DIR / "prompts.yaml"
_prompts_cache: dict | None = None

_FOUNDATION_CONFIG_PATH = _CONFIG_DIR / "model" / "foundation.yaml"
_foundation_cache: dict | None = None

_LEXICON_KO_PATH = _CONFIG_DIR / "model" / "lexicon_ko.yaml"
_lexicon_ko_cache: dict | None = None

_QUICK_START_PATH = _CONFIG_DIR / "quick_start.yaml"
_quick_start_cache: dict | None = None


def _write_yaml(path: Path, data) -> None:
    """Replace only after a complete write, preserving the previous file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _load_yaml(path: Path) -> dict:
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML file {path}: {e}")
            return {}
        except IOError as e:
            logger.error(f"Failed to read file {path}: {e}")
            return {}
    return {}


_warned_missing: set = set()


def _get_by_dotpath(data: dict, dotpath: str, default=None, source: str = ""):
    """yaml 에서 값을 읽는다. 설정은 yaml 이 단일 소스이므로 호출부에 기본값을
    두지 않는다. 키가 없으면 조용히 넘어가지 말고 한 번은 알린다 —
    코드와 yaml 이 서로 다른 값을 갖고 갈라지는 일을 막기 위해서다."""
    if not dotpath:
        return data
    keys = dotpath.split(".")
    for k in keys:
        if isinstance(data, dict) and k in data:
            data = data[k]
        else:
            tag = f"{source}:{dotpath}"
            if tag not in _warned_missing:
                _warned_missing.add(tag)
                logger.warning(
                    "설정 키를 찾지 못했습니다: %s (%s). yaml 에 추가하세요.",
                    dotpath, source or "config")
            return default
    return data


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = _load_yaml(_CONFIG_PATH)
    return _cache


def cfg(dotpath: str, default=None):
    """Get a config value from app.yaml by dot-separated path."""
    return _get_by_dotpath(_load(), dotpath, default, "app.yaml")


# ── search.yaml ──────────────────────────────────────────────

def _load_search() -> dict:
    global _search_cache
    if _search_cache is None:
        _search_cache = _load_yaml(_SEARCH_CONFIG_PATH)
    return _search_cache


def cfg_search(dotpath: str = "", default=None):
    """Get a config value from search.yaml by dot-separated path."""
    return _get_by_dotpath(_load_search(), dotpath, default, "search.yaml")


# ── text_ts.yaml ─────────────────────────────────────────────

def _load_text_ts() -> dict:
    global _text_ts_cache
    if _text_ts_cache is None:
        _text_ts_cache = _load_yaml(_TEXT_TS_CONFIG_PATH)
    return _text_ts_cache


def cfg_text_ts(dotpath: str = "", default=None):
    """Get a config value from text_ts.yaml by dot-separated path."""
    return _get_by_dotpath(_load_text_ts(), dotpath, default, "text_ts.yaml")


def cfg_text_ts_raw() -> dict:
    """Return the full text_ts.yaml dict (for write-back or full iteration)."""
    return deepcopy(_load_text_ts())


def save_text_ts_config(data: dict) -> None:
    """Write updated config back to text_ts.yaml and invalidate cache."""
    global _text_ts_cache
    _write_yaml(_TEXT_TS_CONFIG_PATH, data)
    _text_ts_cache = None


# ── prompts.yaml ─────────────────────────────────────────────

def _load_prompts() -> dict:
    global _prompts_cache
    if _prompts_cache is None:
        _prompts_cache = _load_yaml(_PROMPTS_CONFIG_PATH)
    return _prompts_cache


def cfg_prompts(dotpath: str = "", default=None):
    """Get a config value from prompts.yaml by dot-separated path."""
    return _get_by_dotpath(_load_prompts(), dotpath, default, "prompts.yaml")


# ── foundation.yaml ──────────────────────────────────────────

def _load_foundation() -> dict:
    global _foundation_cache
    if _foundation_cache is None:
        _foundation_cache = _load_yaml(_FOUNDATION_CONFIG_PATH)
    return _foundation_cache


def cfg_foundation(dotpath: str = "", default=None):
    """Get a config value from foundation.yaml by dot-separated path.

    Example: cfg_foundation("moment.model") -> "AutonLab/MOMENT-1-small"
    """
    return _get_by_dotpath(_load_foundation(), dotpath, default, "model/foundation.yaml")


def cfg_foundation_raw() -> dict:
    """Return the full foundation.yaml dict."""
    return dict(_load_foundation())


# ── lexicon_ko.yaml ──────────────────────────────────────────

def _load_lexicon_ko() -> dict:
    global _lexicon_ko_cache
    if _lexicon_ko_cache is None:
        _lexicon_ko_cache = _load_yaml(_LEXICON_KO_PATH)
    return _lexicon_ko_cache


def cfg_lexicon_ko(dotpath: str = "", default=None):
    """Get a value from model/lexicon_ko.yaml by dot-separated path."""
    return _get_by_dotpath(_load_lexicon_ko(), dotpath, default, "lexicon_ko.yaml")


def cfg_lexicon_ko_raw() -> dict:
    """Return the full lexicon_ko.yaml dict."""
    return dict(_load_lexicon_ko())


# ── Convenience shortcuts ────────────────────────────────────

def cfg_time_units() -> dict:
    """Return time_units dict from config."""
    return cfg("time_units")


def cfg_preset_catalog() -> dict:
    """Return preset shape catalog from config."""
    return cfg("preset_shapes.catalog")


def cfg_clustering() -> dict:
    """Return clustering config dict."""
    return cfg("clustering")


# ── custom_presets (per-dataset) ────────────────────────────

_CUSTOM_PRESETS_DIR = _CONFIG_DIR / "custom_presets"
_custom_presets_cache: dict[str, dict] = {}


_preset_lock = threading.RLock()


def _preset_transaction(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _preset_lock:
            return func(*args, **kwargs)
    return wrapped


def _preset_path(dataset_id: str, hidden: bool = False) -> Path:
    from backend.runtime_paths import validate_component, stored_path
    if not isinstance(dataset_id, str):
        raise ValueError("dataset_id must be a string")
    validate_component(dataset_id)
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in dataset_id)
    suffix = "_hidden.yaml" if hidden else ".yaml"
    # Keep unambiguous legacy filenames. Punctuation and the reserved suffix
    # must not alias another dataset or its hidden-presets list.
    if dataset_id == safe_id and not dataset_id.endswith("_hidden"):
        return stored_path(_CUSTOM_PRESETS_DIR, dataset_id + suffix)
    digest = hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()
    path = stored_path(_CUSTOM_PRESETS_DIR, f"datasets/{digest}/" + ("hidden.yaml" if hidden else "presets.yaml"))
    legacy = stored_path(_CUSTOM_PRESETS_DIR, safe_id + suffix)
    if not path.exists() and legacy.exists():
        raise ValueError(
            f"Ambiguous legacy preset file {legacy.name}; verify its dataset and "
            f"copy it to {path.relative_to(_CUSTOM_PRESETS_DIR)} before use.")
    return path


def _get_dataset_preset_path(dataset_id: str) -> Path:
    """Get the YAML path for a specific dataset's custom presets."""
    return _preset_path(dataset_id)


@_preset_transaction
def _load_custom_presets_for_dataset(dataset_id: str) -> dict:
    """Load custom presets for a specific dataset."""
    if dataset_id not in _custom_presets_cache:
        path = _get_dataset_preset_path(dataset_id)
        data = _load_yaml(path)
        if not isinstance(data, dict):
            raise ValueError(f"Invalid preset mapping: {path.name}")
        _custom_presets_cache[dataset_id] = data
    return _custom_presets_cache[dataset_id]


@_preset_transaction
def cfg_custom_presets(dataset_id: str | None = None) -> dict:
    """Return custom presets for a dataset. If None, returns empty dict."""
    if not dataset_id:
        return {}
    return deepcopy(_load_custom_presets_for_dataset(dataset_id))


@_preset_transaction
def save_custom_preset(name: str, preset_data: dict, dataset_id: str) -> None:
    """Save a custom preset for a specific dataset."""
    _CUSTOM_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    presets = deepcopy(_load_custom_presets_for_dataset(dataset_id))
    presets[name] = preset_data
    path = _get_dataset_preset_path(dataset_id)
    _write_yaml(path, presets)
    _custom_presets_cache[dataset_id] = presets


@_preset_transaction
def delete_custom_preset(name: str, dataset_id: str) -> bool:
    """Delete a custom preset from a dataset. Returns True if deleted."""
    presets = deepcopy(_load_custom_presets_for_dataset(dataset_id))
    if name in presets:
        del presets[name]
        path = _get_dataset_preset_path(dataset_id)
        _write_yaml(path, presets)
        _custom_presets_cache[dataset_id] = presets
        return True
    return False


def cfg_all_presets(dataset_id: str | None = None) -> dict:
    """Return combined catalog: built-in + custom presets for a dataset."""
    builtin = cfg_preset_catalog()
    custom = cfg_custom_presets(dataset_id)
    combined = dict(builtin)
    for k, v in custom.items():
        combined[f"custom_{k}"] = v
    return combined


# ── Hidden presets (per-dataset) ──
_hidden_presets_cache: dict[str, set] = {}


def _get_hidden_path(dataset_id: str) -> Path:
    """Get the YAML path for a specific dataset's hidden presets list."""
    return _preset_path(dataset_id, hidden=True)


@_preset_transaction
def load_hidden_presets(dataset_id: str) -> set:
    """Load hidden preset names for a dataset."""
    if not dataset_id:
        return set()
    if dataset_id in _hidden_presets_cache:
        return set(_hidden_presets_cache[dataset_id])
    path = _get_hidden_path(dataset_id)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or []
            if isinstance(data, list):
                hidden = set(data)
            else:
                logger.warning(f"Invalid hidden presets format in {path}, expected list")
                hidden = set()
        except (yaml.YAMLError, IOError) as e:
            logger.error(f"Failed to load hidden presets from {path}: {e}")
            hidden = set()
    else:
        # Default: 내장 도형을 모두 보여 준다. 숨김은 사용자가 직접 지정할 때만
        # 파일로 남는다. (예전 기본값은 "전부 숨김"이라 새 데이터셋의 도형 목록이
        # 비고 분류가 100% Unclassified 로 떨어졌다.)
        hidden = set()
    _hidden_presets_cache[dataset_id] = hidden
    return set(hidden)


@_preset_transaction
def drop_dataset_presets(dataset_id: str) -> None:
    """데이터셋을 지울 때 그 데이터셋의 사용자 프리셋·숨김 목록 파일과 캐시를 함께 지운다."""
    _custom_presets_cache.pop(dataset_id, None)
    _hidden_presets_cache.pop(dataset_id, None)
    for hidden in (False, True):
        try:
            path = _preset_path(dataset_id, hidden=hidden)
        except ValueError:
            logger.warning("Preset cleanup skipped for %s", dataset_id, exc_info=True)
            continue
        path.unlink(missing_ok=True)
        if path.parent != _CUSTOM_PRESETS_DIR:
            try:
                path.parent.rmdir()
            except OSError:
                pass


@_preset_transaction
def hide_preset(name: str, dataset_id: str) -> None:
    """Hide a preset for a specific dataset."""
    _CUSTOM_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    hidden = set(load_hidden_presets(dataset_id))
    hidden.add(name)
    _write_yaml(_get_hidden_path(dataset_id), sorted(hidden))
    _hidden_presets_cache[dataset_id] = hidden


@_preset_transaction
def unhide_preset(name: str, dataset_id: str) -> None:
    """Unhide a preset for a specific dataset."""
    _CUSTOM_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    hidden = set(load_hidden_presets(dataset_id))
    hidden.discard(name)
    _write_yaml(_get_hidden_path(dataset_id), sorted(hidden))
    _hidden_presets_cache[dataset_id] = hidden


def cfg_quick_start() -> dict:
    """Finder 첫 화면 시작 카드 (config/quick_start.yaml)."""
    global _quick_start_cache
    if _quick_start_cache is None:
        _quick_start_cache = _load_yaml(_QUICK_START_PATH)
    return _quick_start_cache
