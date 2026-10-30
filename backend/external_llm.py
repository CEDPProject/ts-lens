"""External LLM connection, edited from the admin UI and stored on the server.

The admin enters a provider, model and API key in Data → 모델 연결. It is saved to
`config/local/external_llm.json` (git-ignored, mode 600). The key never leaves the
server again — reads return only its last four characters.

While `enabled`, the connection is merged into the model catalog as one model,
`external-llm`, offered for the tasks the admin ticked (text→TS, TS→text) and used
by the dataset-metadata inference button when `use_for.meta_inference` is on.
Turning it off removes the model from every list; nothing else changes.

Providers:
  anthropic      official SDK (`anthropic`), model e.g. claude-opus-5-5
  openai_compat  any /v1/chat/completions server: OpenAI, Gemini's OpenAI endpoint,
                 a remote vLLM, … — base_url + key
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from backend.app_config import cfg
from backend.runtime_paths import APP_DIR

logger = logging.getLogger(__name__)

MODEL_KEY = "external-llm"
PROVIDERS = ("anthropic", "openai_compat")
TASKS = ("text_to_ts", "ts_to_text", "meta_inference")
KEEP_SECRET = "__keep__"   # PUT 에서 키 칸을 이 값으로 보내면 저장된 키를 유지한다

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": None}


def settings_path() -> Path:
    value = os.environ.get("TS_PATTERN_EXTERNAL_LLM_FILE") or cfg("external_llm.file")
    path = Path(value).expanduser()
    return path if path.is_absolute() else APP_DIR / path


def _empty() -> dict:
    return {"enabled": False, "provider": "anthropic", "model": "", "base_url": "", "api_key": "",
            "label": "", "use_for": {t: False for t in TASKS}, "updated_at": "",
            "monthly_warn_usd": float(cfg("external_llm.default_monthly_warn_usd")),
            "monthly_limit_usd": float(cfg("external_llm.default_monthly_limit_usd"))}


def load() -> dict:
    path = settings_path()
    with _lock:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            _cache.update(mtime=None, data=None)
            return _empty()
        if _cache["mtime"] == mtime and _cache["data"] is not None:
            return dict(_cache["data"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error("external_llm settings unreadable (%s): %s", path, e)
            data = {}
        merged = _empty()
        merged.update({k: v for k, v in (data or {}).items() if k in merged})
        merged["use_for"] = {t: bool((data.get("use_for") or {}).get(t)) for t in TASKS}
        _cache.update(mtime=mtime, data=merged)
        return dict(merged)


def _validate(data: dict) -> dict:
    out = _empty()
    out["enabled"] = bool(data.get("enabled"))
    provider = str(data.get("provider") or "anthropic").strip()
    if provider not in PROVIDERS:
        raise ValueError(f"provider 는 {', '.join(PROVIDERS)} 중 하나여야 합니다")
    out["provider"] = provider
    out["model"] = str(data.get("model") or "").strip()
    out["base_url"] = str(data.get("base_url") or "").strip().rstrip("/")
    out["api_key"] = str(data.get("api_key") or "").strip()
    out["label"] = str(data.get("label") or "").strip()[:60]
    out["use_for"] = {t: bool((data.get("use_for") or {}).get(t)) for t in TASKS}
    for k in ("monthly_warn_usd", "monthly_limit_usd"):
        try:
            v = float(data.get(k, out[k]))
        except (TypeError, ValueError):
            raise ValueError("경고·한도 금액은 숫자여야 합니다")
        if v < 0:
            raise ValueError("경고·한도 금액은 0 이상이어야 합니다")
        out[k] = v
    if out["enabled"]:
        if not out["model"]:
            raise ValueError("모델 이름을 적어 주세요")
        if not out["api_key"]:
            raise ValueError("API 키를 적어 주세요")
        if provider == "openai_compat":
            from urllib.parse import urlparse
            parsed = urlparse(out["base_url"])
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("OpenAI 호환 서버는 http 또는 https 주소가 필요합니다")
    return out


def save(data: dict) -> dict:
    """Validate and write atomically with mode 600. `api_key == KEEP_SECRET` keeps the stored key."""
    current = load()
    if data.get("api_key") == KEEP_SECRET:
        data = dict(data, api_key=current.get("api_key", ""))
    clean = _validate(data)
    clean["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        _cache.update(mtime=None, data=None)
    return public_view(clean)


def delete() -> None:
    path = settings_path()
    with _lock:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        _cache.update(mtime=None, data=None)


def public_view(data: dict | None = None) -> dict:
    """What the UI may see: everything but the key, plus its last four characters."""
    d = dict(data or load())
    key = d.pop("api_key", "")
    d["api_key_set"] = bool(key)
    d["api_key_hint"] = ("…" + key[-4:]) if len(key) >= 8 else ("설정됨" if key else "")
    d["model_key"] = MODEL_KEY
    d["usage"] = month_usage()
    d["usage"]["warn"] = d["monthly_limit_usd"] > 0 and d["usage"]["usd"] >= d["monthly_warn_usd"] > 0
    d["usage"]["blocked"] = d["monthly_limit_usd"] > 0 and d["usage"]["usd"] >= d["monthly_limit_usd"]
    return d


def provider_cfg(data: dict | None = None) -> dict:
    """pcfg for llm_providers — merged with the defaults in app.yaml external_llm.*"""
    d = data or load()
    return {
        "base_url": d["base_url"] or cfg("external_llm.anthropic_base_url") if d["provider"] == "anthropic" else d["base_url"],
        "api_key": d["api_key"],
        "model": d["model"],
        "timeout": cfg("external_llm.timeout"),
        "health_timeout": cfg("external_llm.health_timeout"),
        "max_tokens": cfg("external_llm.max_tokens"),
        "temperature": cfg("external_llm.temperature"),
    }


def inject(lcfg: dict) -> dict:
    """Merge the connection into a text_ts config dict (models + providers). No-op when off."""
    d = load()
    if not (d["enabled"] and d["api_key"] and d["model"]):
        return lcfg
    tasks = [t for t in ("text_to_ts", "ts_to_text") if d["use_for"].get(t)]
    if not tasks:
        return lcfg
    out = dict(lcfg)
    providers = dict(out.get("providers") or {})
    providers[d["provider"]] = provider_cfg(d)
    out["providers"] = providers
    models = dict(out.get("models") or {})
    models[MODEL_KEY] = {
        "provider": d["provider"],
        "model_id": d["model"],
        "description": d["label"] or f"외부 LLM · {d['provider']} · {d['model']}",
        "inference_devices": ["cpu"],   # 이 장비의 GPU 를 쓰지 않는다
        "cpu_compatible": True,
        "supports": tasks,
    }
    out["models"] = models
    return out


def meta_inference_enabled() -> bool:
    d = load()
    return bool(d["enabled"] and d["api_key"] and d["model"] and d["use_for"].get("meta_inference"))


# ─── 월 사용량·예산 ─────────────────────────────────────────────
# 호출마다 토큰 수를 적고 단가표(app.yaml external_llm.prices)로 비용을 추정한다.
# 콘솔 청구가 정본이고 이건 안전망이다: 경고 금액을 넘으면 화면에 경고, 한도를 넘으면 호출을 막는다.

def usage_path() -> Path:
    path = Path(cfg("external_llm.usage_file")).expanduser()
    return path if path.is_absolute() else APP_DIR / path


def _month_key() -> str:
    return time.strftime("%Y-%m")


def _load_usage() -> dict:
    try:
        return json.loads(usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = cfg("external_llm.prices") or {}
    row = prices.get("default") or {"input": 0, "output": 0}
    for prefix in sorted((k for k in prices if k != "default"), key=len, reverse=True):
        if str(model or "").startswith(prefix):
            row = prices[prefix]
            break
    return round(input_tokens / 1e6 * float(row["input"]) + output_tokens / 1e6 * float(row["output"]), 6)


def record_usage(provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
    usd = estimate_usd(model, input_tokens, output_tokens) if provider == "anthropic" else 0.0
    with _lock:
        data = _load_usage()
        m = data.setdefault(_month_key(), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
        m["calls"] += 1; m["input_tokens"] += int(input_tokens or 0); m["output_tokens"] += int(output_tokens or 0)
        m["usd"] = round(m["usd"] + usd, 6)
        m["last_call"] = time.strftime("%Y-%m-%d %H:%M:%S")
        path = usage_path(); path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600); os.replace(tmp, path)


def month_usage() -> dict:
    m = _load_usage().get(_month_key()) or {}
    return {"month": _month_key(), "calls": int(m.get("calls", 0)), "input_tokens": int(m.get("input_tokens", 0)),
            "output_tokens": int(m.get("output_tokens", 0)), "usd": round(float(m.get("usd", 0.0)), 4),
            "last_call": m.get("last_call", "")}


def check_budget() -> None:
    """Raise before a paid call when this month's estimate has reached the limit (0 = no limit)."""
    d = load()
    limit = float(d.get("monthly_limit_usd") or 0)
    if limit <= 0:
        return
    used = month_usage()["usd"]
    if used >= limit:
        raise RuntimeError(f"이번 달 외부 LLM 추정 비용 ${used:.2f} 가 한도 ${limit:.0f} 에 닿았습니다 — 모델 연결 탭에서 한도를 올리거나 다음 달까지 기다리세요")
    warn = float(d.get("monthly_warn_usd") or 0)
    if warn and used >= warn:
        logger.warning("외부 LLM 이번 달 추정 비용 $%.2f — 경고 기준 $%.0f 초과 (한도 $%.0f)", used, warn, limit)
