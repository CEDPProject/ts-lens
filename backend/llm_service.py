# -*- coding: utf-8 -*-
"""LLM 서비스 — 자연어와 시계열 사이의 두 방향 변환.

운영 기본: 명확한 Text→TS는 결정적 생성, 복합 Text→TS와 TS→Text는 vLLM.
HuggingFace/VLM/ChatTS/Ollama 구현은 선택적 provider로 남아 있다.

High-level API: preload_model, get_status, list_models, set_active_model,
call_llm, generate_pattern_shape, describe_segment, describe_segment_multi.

Provider implementations are in backend.llm_providers.
Analysis/parsing utilities are in backend.llm_analysis.
"""
from __future__ import annotations

import logging
import re

from backend import runtime_state

from backend.app_config import (
    cfg, cfg_text_ts_raw, save_text_ts_config, cfg_prompts,
    cfg_lexicon_ko_raw,
)

# Provider implementations
import backend.llm_providers as _prov
from backend.llm_providers import (
    _call_vllm,
    _call_huggingface,
    _call_ollama,
    _load_vlm, _call_vlm,
    _load_chatts, _call_chatts, _call_chatts_describe,
    _call_chatts_generate_shape,
)

# Analysis / parsing utilities
from backend.llm_analysis import (
    _authoritative_interpretation,
    _compute_segment_features,
    _extract_json,
)

logger = logging.getLogger(__name__)

TASK_TEXT_TO_TS = "text_to_ts"
TASK_TS_TO_TEXT = "ts_to_text"


def _effective_config():
    # 관리자가 화면에서 연결한 외부 LLM 을 카탈로그에 얹는다(켜져 있을 때만)
    from backend import external_llm
    return external_llm.inject(runtime_state.effective_config(cfg_text_ts_raw()))


def _is_cpu_only_env() -> bool:
    """Return True when CUDA is unavailable or the chosen mode excludes it."""
    if runtime_state.force_cpu() or runtime_state.STATE["mode"] == "off":
        return True
    try:
        import torch
        return not torch.cuda.is_available()
    except Exception:
        return True


# (provider, base_url) -> (checked_at, reachable, reason); short TTL so status
# requests stay cheap without mixing two servers that use the same provider.
_reach_cache: dict[str, tuple[float, bool, str]] = {}
_REACH_TTL_SEC = cfg("llm.reachability_ttl_sec")


def _required_pcfg(pcfg: dict, key: str, provider: str):
    """프로바이더 설정값은 config/model/text_ts.yaml 이 단일 소스다.
    없으면 조용히 기본값을 쓰지 말고 어디를 고쳐야 하는지 알린다."""
    value = pcfg.get(key)
    if value is None:
        raise RuntimeError(
            f"'{key}' 설정이 없습니다 "
            f"(config/model/text_ts.yaml · providers.{provider})")
    return value


def _provider_reachable(provider: str, pcfg: dict) -> tuple[bool, str]:
    """Is the provider actually usable right now?

    External HTTP providers are probed because their server can be down while
    Finder itself remains healthy. Local providers are not probed -- loading
    weights just to answer a status call would be far too expensive.
    """
    if not runtime_state.provider_allowed(provider):
        return False, "현재 실행 모드에서 이 모델은 비활성화되어 있습니다."
    if provider not in {"ollama", "vllm", "verbalts", "cpu_llm", "chatts", "anthropic", "openai_compat"}:
        return True, ""

    import time
    if provider in {"anthropic", "openai_compat"}:
        from backend.llm_providers import external_reachable
        key = f"{provider}:{pcfg.get('base_url')}:{pcfg.get('model')}"
        cached = _reach_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < _REACH_TTL_SEC:
            return cached[1], cached[2]
        ok, reason = external_reachable(provider, pcfg)
        _reach_cache[key] = (now, ok, reason)
        return ok, reason
    # 주소는 config/model/text_ts.yaml 의 providers.<이름>.base_url 이 단일 소스다
    base_url = str(pcfg.get("base_url") or "").rstrip("/")
    cache_key = f"{provider}:{base_url}:{runtime_state.force_cpu()}"
    cached = _reach_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _REACH_TTL_SEC:
        return cached[1], cached[2]

    timeout = float(_required_pcfg(pcfg, "health_timeout", provider))
    try:
        import requests
        health_path = {"ollama": "/api/tags", "vllm": "/v1/models", "verbalts": "/health", "cpu_llm": "/v1/models", "chatts": "/health"}[provider]
        resp = requests.get(f"{base_url}{health_path}", timeout=timeout)
        ok = resp.status_code == 200
        if ok and provider in {"verbalts", "chatts"}:
            status = resp.json()
            ok = status.get("available") is True and status.get("engine") == provider
        if ok and runtime_state.force_cpu() and provider in {"verbalts", "cpu_llm"}:
            ok = resp.json().get("device") == "cpu"
        label = {"ollama": "Ollama", "vllm": "vLLM", "verbalts": "VerbalTS", "cpu_llm": "CPU LLM", "chatts": "ChatTS"}[provider]
        reason = "" if ok else f"{label} 서버 응답 {resp.status_code} ({base_url})"
    except Exception as e:
        label = {"ollama": "Ollama", "vllm": "vLLM", "verbalts": "VerbalTS", "cpu_llm": "CPU LLM", "chatts": "ChatTS"}[provider]
        ok, reason = False, f"{label} 서버에 연결할 수 없음 ({base_url}): {e}"

    if not ok:
        logger.warning("%s health check failed: %s", provider, reason)
    _reach_cache[cache_key] = (now, ok, reason)
    return ok, reason


def _model_supports_task(model_cfg: dict, task: str | None) -> bool:
    if not task:
        return True
    supports = model_cfg.get("supports", [TASK_TEXT_TO_TS, TASK_TS_TO_TEXT])
    return task in supports


def _model_cpu_compatible(model_key: str, model_cfg: dict) -> bool:
    """Can this model be used from a host without CUDA?

    An explicit `cpu_compatible` in the catalog always wins. Otherwise the
    answer follows the provider: Ollama runs inference in its own server
    process (often a different machine), so a CPU-only Flask host is
    irrelevant. Local providers load weights in-process and need a GPU.
    """
    if "cpu_compatible" in model_cfg:
        return bool(model_cfg.get("cpu_compatible"))
    return str(model_cfg.get("provider", "")).strip().lower() in {"ollama", "vllm", "verbalts", "cpu_llm", "chatts", "anthropic", "openai_compat"}


def _model_runtime_compatible(model_cfg: dict) -> bool:
    """Runtime compatibility filter (beyond CPU/GPU availability).

    ChatTS currently requires loading .bin checkpoints in many repos.
    On torch<2.6, this may be blocked by security restrictions.
    """
    provider = str(model_cfg.get("provider", "")).strip().lower()
    if provider == "chatts":
        # 별도 서버(providers.chatts.base_url)면 앱 환경의 torch 버전은 상관없다
        try:
            pcfg = _effective_config().get("providers", {}).get("chatts", {})
        except Exception:
            pcfg = {}
        if pcfg.get("base_url"):
            return True
        try:
            return _prov._torch_version_at_least(2, 6)
        except Exception:
            return False
    return True


def _get_task_active_model(lcfg: dict, task: str | None) -> str:
    if not task:
        return lcfg.get("active_model", "")
    tmap = lcfg.get("task_active_models", {}) or {}
    return tmap.get(task, lcfg.get("active_model", ""))


def _filtered_models(lcfg: dict, task: str | None = None,
                     cpu_only: bool | None = None) -> dict:
    models = dict(lcfg.get("models", {}) or {})
    if cpu_only is None:
        cpu_only = _is_cpu_only_env()

    out = {}
    for key, m in models.items():
        if not _model_supports_task(m, task):
            continue
        if not _model_runtime_compatible(m):
            continue
        if cpu_only and not _model_cpu_compatible(key, m):
            continue
        out[key] = m
    return out


def _listed_models(lcfg: dict, task: str | None = None) -> dict:
    models = dict(lcfg.get("models", {}) or {})
    out = {}
    for key, m in models.items():
        if not _model_supports_task(m, task):
            continue
        out[key] = m
    return out


def preload_model(model_key: str | None = None) -> str:
    """서버 시작 시 활성 모델을 미리 로드.

    Returns: 로드된 모델 ID 또는 빈 문자열.
    """
    lcfg = _effective_config()

    # Check if preload is enabled
    preload_setting = lcfg.get("preload_on_startup", "auto")
    if preload_setting == "auto":
        # Auto: only preload if GPU is available
        try:
            import torch
        except Exception:
            logger.info("Preload skipped (torch unavailable, preload_on_startup: auto)")
            return ""
        if not torch.cuda.is_available():
            logger.info("Preload skipped (no GPU, preload_on_startup: auto)")
            return ""
    elif not preload_setting:
        logger.info("Preload disabled in config (preload_on_startup: false)")
        return ""

    try:
        provider, model_id, pcfg = _resolve_model(lcfg, model_key)
    except Exception as e:
        logger.error(f"Model resolution failed: {e}")
        return ""

    mid = pcfg.get("model", "")
    if not mid:
        return ""

    # ChatTS provider — 별도 서버(base_url)면 앱 안에 올리지 않는다
    if provider == "chatts":
        if pcfg.get("base_url"):
            return mid
        try:
            device = pcfg.get("device")
            _load_chatts(mid, device)
            return mid
        except Exception as e:
            logger.error(f"ChatTS preload failed: {e}")
            return ""

    if provider == "vlm":
        try:
            device = pcfg.get("device")
            _load_vlm(mid, device)
            return mid
        except Exception as e:
            logger.error(f"VLM preload failed: {e}")
            return ""

    if provider not in {"huggingface"}:
        return ""
    if _prov._hf_pipeline is not None and _prov._hf_model_id == mid:
        return mid  # 이미 로드됨

    try:
        import torch
        from transformers import pipeline as hf_pipeline

        device = pcfg.get("device")
        if device == "auto":
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        use_gpu = device.startswith("cuda")
        logger.info(f"Preloading {mid} on {device}...")
        _prov._hf_pipeline = hf_pipeline(
            "text-generation",
            model=mid,
            device=torch.device(device) if use_gpu else None,
            torch_dtype=torch.float16 if use_gpu else torch.float32,
            trust_remote_code=True,
        )
        _prov._hf_model_id = mid
        logger.info(f"{mid} loaded OK")
        return mid
    except Exception as e:
        logger.error(f"Preload failed: {e}")
        # Fall back to a lighter catalog model if one is configured.
        # The fallback key must exist in `models:` -- otherwise _resolve_model
        # would fail again and the retry is pointless.
        fb_key = lcfg.get("fallback_model", "")
        if lcfg.get("fallback_to_cpu", False) and fb_key:
            catalog = lcfg.get("models", {}) or {}
            if fb_key not in catalog:
                logger.error(
                    "fallback_model '%s' is not in the text_ts.yaml catalog; skipping fallback",
                    fb_key)
            elif catalog[fb_key].get("model_id") == mid:
                logger.error("fallback_model '%s' is the model that just failed; skipping", fb_key)
            else:
                logger.info("Trying fallback model %s...", fb_key)
                try:
                    return preload_model(fb_key)
                except Exception as e2:
                    logger.error(f"Fallback also failed: {e2}")
        return ""



def _ts_model_pcfg(provider: str) -> dict:
    """vlm/chatts provider의 pcfg를 text_ts.yaml providers에서 로드."""
    providers = _effective_config().get("providers", {}) or {}
    return dict(providers.get(provider, {}))


def _resolve_model(lcfg: dict, model_key: str | None = None,
                   task: str | None = None) -> tuple[str, str, dict]:
    """active_model 또는 지정된 model_key로부터 (provider, model_id, provider_cfg)를 반환.

        Provider별 pcfg 출처:
            - huggingface: text_ts.yaml의 providers.huggingface
            - vlm / chatts: text_ts.yaml의 providers.vlm/chatts
    """
    models = _filtered_models(lcfg, task=task)
    key = model_key or _get_task_active_model(lcfg, task)
    selected_provider = lcfg.get("models", {}).get(key, {}).get("provider", "")
    if not runtime_state.provider_allowed(selected_provider):
        raise ValueError("현재 실행 모드에서 사용할 수 없는 모델입니다. 모델 선택을 확인하세요.")
    providers = lcfg.get("providers", {})

    if key not in models and models:
        key = next(iter(models.keys()))

    if key and key in models:
        m = models[key]
        provider = m.get("provider", lcfg.get("active_provider", "huggingface"))
        model_id = m.get("model_id", key)
        if provider in ("vlm", "chatts"):
            pcfg = _ts_model_pcfg(provider)
            pcfg["model"] = model_id  # 카탈로그의 model_id로 override
        else:
            pcfg = dict(providers.get(provider, {}))
            pcfg["model"] = model_id
        return provider, model_id, pcfg

    # active_model이 카탈로그에 없으면 active_provider 기반 fallback
    provider = lcfg.get("active_provider", "huggingface")
    if provider in ("vlm", "chatts"):
        pcfg = _ts_model_pcfg(provider)
    else:
        pcfg = dict(providers.get(provider, {}))
    return provider, pcfg.get("model", ""), pcfg


def get_status(model_key: str | None = None,
               task: str | None = None) -> dict:
    """LLM 사용 가능 여부 확인."""
    lcfg = _effective_config()
    if not lcfg:
        return {"available": False, "reason": "config 없음"}

    models = _listed_models(lcfg, task=task)
    selected_key = model_key or _get_task_active_model(lcfg, task)
    if selected_key not in models and models:
        selected_key = next(iter(models))
    selected = models.get(selected_key, {})
    if not runtime_state.provider_allowed(selected.get("provider", "")):
        return {"available": False, "configured": bool(selected), "reachable": False,
                "reason": "CPU 기본 모드", "model_key": selected_key,
                "provider": selected.get("provider"), "cpu_only": True}
    provider, model_id, pcfg = _resolve_model(lcfg, selected_key, task=task)
    selected_cfg = models.get(selected_key, {})
    runtime_compatible = _model_runtime_compatible(selected_cfg) if selected_cfg else False
    host_compatible = (not _is_cpu_only_env() or _model_cpu_compatible(selected_key, selected_cfg)) if selected_cfg else False
    reachable, reason = _provider_reachable(provider, pcfg)
    if not runtime_compatible:
        reason = reason or "현재 Python/모델 런타임과 호환되지 않음"
    elif not host_compatible:
        reason = reason or "현재 호스트에서 실행할 수 없음"

    return {
        "available": runtime_compatible and host_compatible and reachable,
        "configured": bool(selected_cfg),
        "reachable": reachable,
        "reason": reason,
        "provider": provider,
        "model": model_id,
        "model_key": selected_key,
        "task": task or "",
        "cpu_only": _is_cpu_only_env(),
        "runtime_compatible": runtime_compatible,
        "host_compatible": host_compatible,
        "inference_devices": selected_cfg.get("inference_devices", []),
        "external_provider": provider in {"ollama", "vllm", "verbalts", "cpu_llm", "chatts", "anthropic", "openai_compat"},
    }


def list_models(task: str | None = None) -> list[dict]:
    """사용 가능한 모델 목록 반환.

    외부 endpoint가 여러 개면 상태 확인을 병렬로 수행한다. 꺼진 vLLM과
    Ollama의 timeout을 차례로 기다려 UI 초기화가 느려지는 것을 피한다.
    """
    from concurrent.futures import ThreadPoolExecutor

    lcfg = _effective_config()
    cpu_only = _is_cpu_only_env()
    models = _listed_models(lcfg, task=task)
    providers = lcfg.get("providers", {}) or {}
    active = _get_task_active_model(lcfg, task)
    prepared = []
    for key, m in models.items():
        runtime_compatible = _model_runtime_compatible(m)
        cpu_compatible = _model_cpu_compatible(key, m)
        provider = m.get("provider", "")
        pcfg = dict(providers.get(provider, {}) or {})
        pcfg["model"] = m.get("model_id", key)
        host_compatible = not cpu_only or cpu_compatible
        prepared.append(({
            "key": key,
            "provider": provider,
            "model_id": m.get("model_id", key),
            "description": m.get("description", ""),
            "supports": m.get("supports", [TASK_TEXT_TO_TS, TASK_TS_TO_TEXT]),
            "configured": True,
            "cpu_compatible": cpu_compatible,
            "inference_devices": m.get("inference_devices", []),
            "host_compatible": host_compatible,
            "runtime_compatible": runtime_compatible,
            "external_provider": provider in {"ollama", "vllm", "verbalts", "cpu_llm", "chatts", "anthropic", "openai_compat"},
            "active": key == active,
        }, pcfg))

    def probe(item):
        record, pcfg = item
        reachable, reason = _provider_reachable(record["provider"], pcfg)
        record["reachable"] = reachable
        record["reachability_reason"] = reason
        record["available"] = (
            record["runtime_compatible"] and record["host_compatible"] and reachable
        )
        return record

    if not prepared:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as pool:
        result = list(pool.map(probe, prepared))
    return result


def set_active_model(model_key: str, task: str | None = None) -> bool:
    """활성 모델을 변경하고 config 파일에 저장."""
    lcfg = cfg_text_ts_raw()
    models = _effective_config().get("models", {})   # 외부 LLM(external-llm)도 고를 수 있어야 한다
    if model_key not in models:
        return False
    if not _model_supports_task(models[model_key], task):
        return False
    if not runtime_state.provider_allowed(models[model_key].get("provider")):
        return False
    if runtime_state.STATE["managed"]:
        for target in ([task] if task else [TASK_TEXT_TO_TS, TASK_TS_TO_TEXT]):
            if _model_supports_task(models[model_key], target):
                runtime_state.STATE["models"][target] = model_key
        return True
    if task:
        tmap = lcfg.get("task_active_models", {}) or {}
        tmap[task] = model_key
        lcfg["task_active_models"] = tmap
    else:
        lcfg["active_model"] = model_key
        lcfg["active_provider"] = models[model_key].get("provider", lcfg.get("active_provider"))
    save_text_ts_config(lcfg)
    return True


# ─── System Prompt (config/prompts.yaml에서 로드) ────────────

def get_shape_generation_prompt() -> str:
    """config/prompts.yaml에서 shape_generation 프롬프트를 로드."""
    return cfg_prompts("shape_generation")

SHAPE_GENERATION_PROMPT = get_shape_generation_prompt()


def get_ts_description_prompt() -> str:
    """config/prompts.yaml에서 ts_description 프롬프트를 로드."""
    return cfg_prompts("ts_description")


def get_ts_description_multi_prompt() -> str:
    """config/prompts.yaml에서 ts_description_multi 프롬프트를 로드."""
    return cfg_prompts("ts_description_multi")


# ─── LLM Call ────────────────────────────────────────────────


def call_llm(user_message: str, context: str = "",
             model_key: str | None = None,
             system_prompt: str | None = None,
             temperature: float | None = None,
             task: str | None = None,
             max_tokens: int | None = None,
             ts_arrays: list | None = None) -> str:
    """LLM 호출. context는 데이터셋/컬럼 정보.

    max_tokens: 이번 호출에서만 적용할 생성 토큰 상한(설정값 override).
        짧은 출력이 확실한 작업(예: 짧은 shape 생성)에서 지연을 줄이는 데 사용.
    ts_arrays: ChatTS 에 함께 보낼 시계열. 프롬프트의 <ts><ts/> 자리와 순서가 같아야 한다.
    """
    cfg = _effective_config()
    provider, model_id, pcfg = _resolve_model(cfg, model_key, task=task)
    if runtime_state.force_cpu() and provider != "builtin":
        available, reason = _provider_reachable(provider, pcfg)
        if not available:
            raise ValueError(reason)
    sp = system_prompt or SHAPE_GENERATION_PROMPT

    content = user_message
    if context:
        content = f"## 현재 데이터셋 정보\n{context}\n\n## 사용자 질문\n{user_message}"

    messages = [{"role": "user", "content": content}]

    if provider == "builtin":
        return ""
    if provider == "chatts":
        return _call_chatts(ts_arrays or [], content, pcfg, temperature)
    elif provider in {"vllm", "cpu_llm"}:
        return _call_vllm(messages, pcfg, sp, temperature, max_tokens=max_tokens)
    elif provider == "ollama":
        return _call_ollama(messages, pcfg, sp, temperature, max_tokens=max_tokens)
    elif provider in {"anthropic", "openai_compat"}:
        from backend.llm_providers import call_external
        return call_external(provider, messages, pcfg, sp, temperature, max_tokens)
    elif provider == "huggingface":
        return _call_huggingface(messages, pcfg, sp, temperature, max_tokens=max_tokens)
    elif provider == "vlm":
        return _call_vlm(messages, pcfg, sp, temperature)
    else:
        raise RuntimeError(f"지원하지 않는 provider: {provider}")


# ─── Query Expansion (Paraphrase) ───────────────────────────

def _build_lexicon_hint() -> str:
    """lexicon_ko.yaml의 primitives를 LLM 프롬프트용 짧은 힌트 문자열로 직렬화."""
    lex = cfg_lexicon_ko_raw()
    primitives = lex.get("primitives", {})
    if not primitives:
        return ""
    lines = []
    for key, item in primitives.items():
        syns = item.get("synonyms", [])
        en = item.get("en", "")
        if syns and en:
            lines.append(f"- {en} ({key}): {', '.join(syns[:6])}")
    return "\n".join(lines)


_PARAPHRASE_SYSTEM = """You are a Korean time-series query rephraser.
Given a user's natural-language pattern description, produce N paraphrases
that PRESERVE meaning but vary surface vocabulary. Use the lexicon hints
below when applicable.

## Rules
- Keep the same shape intent (no semantic drift).
- Vary synonyms, modifier intensity words, and ordering.
- Both Korean and English paraphrases are allowed; at least one paraphrase
  in the language opposite to the original is recommended (KO↔EN).
- Output ONLY a JSON array of strings. No other text.

## Korean time-series lexicon hints
{lexicon}

## Output format
["paraphrase 1", "paraphrase 2", ...]
"""


def generate_paraphrases(nl_text: str, n: int = 3,
                         model_key: str | None = None) -> list[str]:
    """원본 NL 쿼리에 대한 의미 보존 paraphrase N개 생성.

    Returns: 원본을 포함하지 않은 paraphrase 리스트 (길이 ≤ n).
    """
    if not nl_text or not nl_text.strip():
        return []

    lex_hint = _build_lexicon_hint()
    system_prompt = _PARAPHRASE_SYSTEM.format(lexicon=lex_hint or "(none)")
    user_msg = (
        f"## 원본 표현\n{nl_text}\n\n"
        f"## 요청\n위 표현을 {n}개의 다른 표현으로 바꿔써주세요. "
        f"의미는 유지하되 어휘/수식어를 다양하게."
    )

    try:
        raw = call_llm(user_msg, model_key=model_key,
                       system_prompt=system_prompt, temperature=0.5,
                       task=TASK_TEXT_TO_TS, max_tokens=512)
    except Exception as e:
        logger.warning(f"Paraphrase 생성 실패: {e}")
        return []

    # JSON 배열 파싱 시도
    parsed = _extract_json(raw)
    if isinstance(parsed, list):
        result = [str(x).strip() for x in parsed if str(x).strip()]
    else:
        # fallback: 줄바꿈/번호 매기기 등 자유 형식
        lines = [
            re.sub(r"^[\s\-\*\d\.\)\,\"\'\[\]]+", "", ln).strip().strip('"').strip("'")
            for ln in raw.splitlines()
        ]
        result = [ln for ln in lines if ln and len(ln) > 1]

    # 원본과 동일 표현 제거 + 중복 제거
    seen = {nl_text.strip()}
    deduped = []
    for p in result:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
        if len(deduped) >= n:
            break
    return deduped


# ─── Shape Generation ───────────────────────────────────────

def _salvage_shape(raw: str) -> dict | None:
    """잘리거나 형식이 깨진 LLM 응답에서 values 배열을 최대한 복구.

    Qwen 등 일부 모델이 값을 토큰 한계까지 과다 생성해 JSON이 잘리면
    _extract_json이 None을 반환한다. 이 경우 정규식으로 values 숫자열만 회수한다.
    """
    vals_m = re.search(r'"values"\s*:\s*\[(.*?)(?:\]|$)', raw, re.DOTALL)
    if not vals_m:
        return None
    nums = re.findall(r'-?\d+(?:\.\d+)?', vals_m.group(1))
    if len(nums) < 3:
        return None
    desc_m = re.search(r'"description"\s*:\s*"([^"]*)"', raw)
    return {
        "description": desc_m.group(1) if desc_m else "",
        "values": [float(n) for n in nums],
    }


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(t and t.lower() in text for t in terms)


def _primitive_terms() -> dict[str, list[str]]:
    """Lexicon-backed text hints for deterministic query synthesis."""
    lex = cfg_lexicon_ko_raw()
    primitives = lex.get("primitives", {}) or {}
    terms: dict[str, list[str]] = {
        "spike": ["spike", "peak", "sudden peak", "surge", "overshoot"],
        "dip": ["dip", "valley", "sag", "dropout", "short drop"],
        "rise": ["rise", "rising", "increase", "increasing", "ramp up", "upward"],
        "surge": ["surge", "jump", "sudden rise", "rapid rise", "shoot up"],
        "fall": ["fall", "falling", "decrease", "decline", "drop", "downward"],
        "crash": ["crash", "sudden drop", "plunge", "rapid fall", "sharp drop"],
        "flat": ["flat", "steady", "stable", "constant", "unchanged"],
        "bell": ["bell", "hill", "normal day", "clear sky", "single peak"],
        "v_shape": ["v-shape", "v shape", "drop and recover", "recover after drop"],
        "step_up": ["step up", "level shift up"],
        "step_down": ["step down", "level shift down"],
        "oscillation": ["oscillation", "fluctuation", "zigzag", "noisy", "jitter"],
        "periodic": ["periodic", "cycle", "repeating", "seasonal"],
        "cutout": ["cutout", "shutdown", "zero", "drop to zero"],
        "recovery": ["recovery", "recover", "normalize"],
    }
    for key, item in primitives.items():
        merged = terms.setdefault(key, [])
        merged.extend(str(s) for s in item.get("synonyms", []) if s)
        if item.get("en"):
            merged.append(str(item["en"]))
    return terms


def _position_from_text(text: str) -> float:
    if _contains_any(text, ["초반", "처음", "앞부분", "시작", "early", "beginning"]):
        return 0.25
    if _contains_any(text, ["후반", "끝", "마지막", "종료", "late", "end"]):
        return 0.75
    return 0.5


def _zscore_list(values) -> list[float]:
    import numpy as np

    arr = np.array(values, dtype=np.float64)
    std = float(arr.std())
    if std < 1e-10:
        return [0.0 for _ in arr]
    arr = (arr - float(arr.mean())) / std
    return [round(float(max(-3.0, min(3.0, v))), 4) for v in arr]


def _deterministic_shape_from_text(nl_text: str, length: int) -> dict | None:
    """Generate a strong fallback waveform from explicit shape vocabulary.

    This is intentionally conservative: it only activates when the user text
    contains clear primitive words. It prevents Text mode from collapsing into
    weak near-flat LLM curves when the lexical intent is obvious.
    """
    import math
    import numpy as np

    text = (nl_text or "").strip().lower()
    if not text or length < 3:
        return None

    terms = _primitive_terms()
    hits = {key for key, vals in terms.items() if _contains_any(text, vals)}
    if not hits:
        return None

    # Composite phrases should beat individual token matches.
    if (hits & {"fall", "crash", "dip"}) and (hits & {"recovery", "rise"}):
        primitive = "v_shape"
    elif (hits & {"rise", "surge"}) and (hits & {"fall", "crash"}):
        primitive = "bell"
    else:
        priority = [
            "cutout", "step_up", "step_down", "spike", "dip", "surge",
            "crash", "bell", "v_shape", "oscillation", "periodic",
            "rise", "fall", "flat",
        ]
        primitive = next((p for p in priority if p in hits), "")

    if not primitive:
        return None

    x = np.linspace(0.0, 1.0, length)
    center = _position_from_text(text)
    sharp = _contains_any(text, [
        "급", "갑자기", "빠르게", "순식간", "sharp", "sudden", "rapid", "fast"
    ])
    width = 0.08 if sharp else 0.14

    if primitive == "rise":
        y = np.linspace(-1.8 if sharp else -1.4, 1.8 if sharp else 1.4, length)
    elif primitive == "fall":
        y = np.linspace(1.8 if sharp else 1.4, -1.8 if sharp else -1.4, length)
    elif primitive == "surge":
        knee = center
        y = -1.2 + 2.6 / (1.0 + np.exp(-(x - knee) / max(0.025, width / 2)))
    elif primitive == "crash":
        knee = center
        y = 1.2 - 2.6 / (1.0 + np.exp(-(x - knee) / max(0.025, width / 2)))
    elif primitive == "spike":
        y = -0.35 + 2.8 * np.exp(-0.5 * ((x - center) / width) ** 2)
    elif primitive == "dip":
        y = 0.35 - 2.8 * np.exp(-0.5 * ((x - center) / width) ** 2)
    elif primitive == "bell":
        y = -1.2 + 2.7 * np.sin(math.pi * x)
    elif primitive == "v_shape":
        y = -1.0 + 2.2 * np.abs((x - center) / max(center, 1.0 - center))
        y = np.clip(y, -1.2, 1.2)
    elif primitive == "step_up":
        y = np.where(x < center, -1.1, 1.1)
    elif primitive == "step_down":
        y = np.where(x < center, 1.1, -1.1)
    elif primitive == "cutout":
        y = np.where(x < center, 1.0, -1.8)
    elif primitive in {"oscillation", "periodic"}:
        cycles = 3 if primitive == "periodic" else 5
        y = np.sin(2 * math.pi * cycles * x)
    elif primitive == "flat":
        y = np.zeros(length)
    else:
        return None

    return {
        "primitive": primitive,
        "matched_primitives": sorted(hits),
        "description": f"deterministic {primitive} query synthesized from text",
        "values": _zscore_list(y),
    }


def _deterministic_first_is_safe(deterministic: dict) -> bool:
    """Whether a lexical shape is expressive enough to skip the LLM.

    A single word such as ``진동`` must not collapse a multi-stage request such
    as stable -> oscillate -> recover into one sine wave.  The deterministic
    generator intentionally supports only one shape family, plus the explicit
    rise/fall combinations represented by bell and V-shape primitives.
    """
    hits = set(deterministic.get("matched_primitives", []))
    families = set()
    family_map = {
        "up": {"rise", "surge", "step_up", "recovery"},
        "down": {"fall", "crash", "step_down", "cutout"},
        "extreme": {"spike", "dip", "bell", "v_shape"},
        "periodic": {"oscillation", "periodic"},
        "stable": {"flat"},
    }
    for family, members in family_map.items():
        if hits & members:
            families.add(family)

    primitive = deterministic.get("primitive", "")
    if len(families) <= 1:
        return True
    if primitive == "v_shape" and families <= {"up", "down", "extreme"}:
        return True
    if primitive == "bell" and families <= {"up", "down", "extreme"}:
        return True
    return False


def _shape_signal(values: list[float]) -> dict:
    import numpy as np

    arr = np.array(values, dtype=np.float64)
    if len(arr) < 3:
        return {"std": 0.0, "prominence": 0.0, "slope": 0.0}
    return {
        "std": float(arr.std()),
        "prominence": float(arr.max() - arr.min()),
        "slope": float(np.polyfit(np.arange(len(arr)), arr, 1)[0]),
    }


def _repair_weak_shape(nl_text: str, values: list[float], length: int) -> tuple[list[float], dict]:
    """Replace weak LLM output with a lexical primitive when it is clearly safer."""
    deterministic = _deterministic_shape_from_text(nl_text, length)
    signal = _shape_signal(values)
    repair = {
        "applied": False,
        "reason": "",
        "primitive": "",
        "llm_signal": signal,
    }
    if deterministic is None or not _deterministic_first_is_safe(deterministic):
        return _zscore_list(values), repair

    primitive = deterministic["primitive"]
    weak = signal["std"] < 0.20 or signal["prominence"] < 0.75
    flat_conflict = primitive != "flat" and signal["prominence"] < 1.25
    if weak or flat_conflict:
        repair.update({
            "applied": True,
            "reason": "weak_llm_signal",
            "primitive": primitive,
        })
        return deterministic["values"], repair

    return _zscore_list(values), repair


def generate_pattern_shape(nl_text: str, length: int = 24,
                           dataset_context: str = "",
                           model_key: str | None = None) -> dict:
    """자연어 설명 -> 합성 시계열 형태(shape) 배열 생성.

    ChatTS provider인 경우에도 shape 생성은 ChatTS로 시도하고,
    JSON 파싱 실패 시 EXAONE fallback.

    Returns: {
        "description": str, "values": list[float], "length": int,
        "query": { "system_prompt": str, "user_message": str },
        "raw_response": str
    }
    """
    # 명확한 기본 형태는 생성 모델을 거칠 이유가 없다. 같은 의미를 LLM이 수백 개
    # 숫자로 다시 쓰게 하면 느리고 결과도 흔들린다. 복합·모호 질의만 아래 LLM으로 보낸다.
    cfg = _effective_config()
    selected = model_key or _get_task_active_model(cfg, TASK_TEXT_TO_TS)
    selected_provider = cfg.get("models", {}).get(selected, {}).get("provider")
    if not runtime_state.provider_allowed(selected_provider):
        raise ValueError("현재 실행 모드에서 사용할 수 없는 모델입니다. 모델 선택을 확인하세요.")
    if selected_provider == "builtin":
        shape = _deterministic_shape_from_text(nl_text, length)
        if shape is None or not _deterministic_first_is_safe(shape):
            raise ValueError("CPU 기본 모드는 상승·하강·봉우리·골·평탄 등 명확한 형태만 지원합니다. 복합 문장은 모델을 연결하거나 스케치로 입력하세요.")
        return {"description": shape["description"], "values": shape["values"],
                "length": len(shape["values"]), "engine": "deterministic", "model": "cpu-basic",
                "llm_used": False, "fallback_reason": None,
                "query": {"system_prompt": "", "user_message": nl_text}, "raw_response": "",
                "signal": {"applied": True, "primitive": shape["primitive"], "reason": "cpu_basic"}}
    if (cfg.get("models", {}).get(selected, {}).get("provider") == "verbalts"):
        # Numeric generator must bypass rule synthesis, JSON repair and LLM fallback.
        from backend.series_generator import generate
        if runtime_state.force_cpu():
            available, reason = _provider_reachable("verbalts", cfg["providers"]["verbalts"])
            if not available:
                raise ValueError(reason)
        return generate(nl_text, length, cfg.get("providers", {}).get("verbalts", {}))
    # 사용자가 외부 LLM(anthropic/openai_compat)을 직접 고른 경우는 규칙 선처리를 건너뛴다 —
    # 그 모델로 만들어 보려고 고른 것이므로.
    if cfg.get("deterministic_first", True) and selected_provider not in {"anthropic", "openai_compat"}:
        deterministic = _deterministic_shape_from_text(nl_text, length)
        if deterministic is not None and _deterministic_first_is_safe(deterministic):
            return {
                "description": deterministic["description"],
                "values": deterministic["values"],
                "length": len(deterministic["values"]),
                "signal": {
                    "applied": True,
                    "reason": "explicit_shape_vocabulary",
                    "primitive": deterministic["primitive"],
                    "llm_signal": {},
                },
                "query": {"system_prompt": "", "user_message": nl_text},
                "raw_response": "",
                "engine": "deterministic",
                "model": None,
                "llm_used": False,
                "fallback_reason": None,
            }

    # LLM은 검색 창 길이 전체를 숫자로 쓰지 않고 대표 제어점만 만든다. 144~2000개
    # 숫자를 토큰으로 생성하는 것이 Text Search 지연의 대부분이므로, 아래의 기존
    # 보간 단계가 최종 length로 늘린다. 아주 짧은 요청은 원래 길이를 유지한다.
    control_points = max(8, int(cfg.get("llm_control_points", 24)))
    generation_length = min(length, control_points)
    user_content = f"길이: {generation_length} 포인트\n"
    if dataset_context:
        user_content += f"\n## 데이터셋 정보\n{dataset_context}\n"
    user_content += f"\n## 패턴 설명\n{nl_text}"

    system_prompt = get_shape_generation_prompt()

    # ChatTS provider: shape 생성도 ChatTS로 시도
    provider, model_id, pcfg = _resolve_model(cfg, model_key, task=TASK_TEXT_TO_TS)
    try:
        if provider == "chatts":
            raw = _call_chatts_generate_shape(nl_text, length, pcfg)
        else:
            # 출력은 length개 숫자 + 짧은 JSON 래퍼뿐 — 토큰 상한을 길이에 맞춰
            # 좁히면 모델이 토큰 한계까지 장황하게 생성하는 것을 막아 지연을 크게 줄인다.
            # (잘려도 _salvage_shape 가 values 를 복구한다.)
            gen_max_tokens = min(512, max(128, generation_length * 6 + 80))
            raw = call_llm(user_content, model_key=model_key,
                           system_prompt=system_prompt, temperature=0.3,
                           task=TASK_TEXT_TO_TS, max_tokens=gen_max_tokens)
    except Exception as e:
        deterministic = _deterministic_shape_from_text(nl_text, length)
        if deterministic is None or not _deterministic_first_is_safe(deterministic):
            raise
        logger.warning(f"LLM shape generation failed; using deterministic fallback: {e}")
        return {
            "description": deterministic["description"],
            "values": deterministic["values"],
            "length": len(deterministic["values"]),
            "signal": {
                "applied": True,
                "reason": "llm_call_failed",
                "primitive": deterministic["primitive"],
                "llm_signal": {},
            },
            "query": {
                "system_prompt": system_prompt,
                "user_message": user_content,
            },
            "raw_response": "",
            "engine": "deterministic",
            "model": None,
            "llm_used": False,
            "fallback_reason": "llm_call_failed",
        }

    parsed = _extract_json(raw)
    if parsed is None:
        parsed = _salvage_shape(raw)
        if parsed is not None:
            logger.warning("LLM 응답 JSON 파싱 실패 → values 배열만 복구해 사용")
    if parsed is None:
        deterministic = _deterministic_shape_from_text(nl_text, length)
        if deterministic is not None and _deterministic_first_is_safe(deterministic):
            logger.warning("LLM 응답 파싱 실패 → deterministic text shape fallback 사용")
            return {
                "description": deterministic["description"],
                "values": deterministic["values"],
                "length": len(deterministic["values"]),
                "signal": {
                    "applied": True,
                    "reason": "llm_parse_failed",
                    "primitive": deterministic["primitive"],
                    "llm_signal": {},
                },
                "query": {
                    "system_prompt": system_prompt,
                    "user_message": user_content,
                },
                "raw_response": raw,
                "engine": "deterministic",
                "model": None,
                "llm_used": False,
                "fallback_reason": "llm_parse_failed",
            }
        logger.error(f"LLM raw response (first 500 chars): {raw[:500]}")
        raise RuntimeError(f"LLM 응답 파싱 실패: {raw[:300]}")

    values = parsed.get("values", [])
    if not isinstance(values, list) or len(values) < 3:
        raise RuntimeError(
            f"유효하지 않은 패턴 배열: 길이 {len(values) if isinstance(values, list) else 'N/A'}"
        )

    # 값 검증: float 변환
    clean_values = []
    for v in values:
        try:
            clean_values.append(float(v))
        except (ValueError, TypeError):
            clean_values.append(0.0)

    # 길이 불일치 시 리샘플
    if len(clean_values) != length:
        import numpy as np
        x_old = np.linspace(0, 1, len(clean_values))
        x_new = np.linspace(0, 1, length)
        clean_values = list(np.interp(x_new, x_old, clean_values))

    clean_values, repair_info = _repair_weak_shape(nl_text, clean_values, length)

    return {
        "description": parsed.get("description", nl_text),
        "values": clean_values,
        "length": len(clean_values),
        "signal": repair_info,
        # LLM에 실제로 보낸 쿼리와 원본 응답 — UI에서 투명하게 표시
        "query": {
            "system_prompt": system_prompt,
            "user_message": user_content,
        },
        "raw_response": raw,
        "engine": provider,
        "model": model_id,
        "llm_used": True,
        "fallback_reason": None,
    }


def generate_pattern_shape_ensemble(
    nl_text: str,
    length: int = 24,
    dataset_context: str = "",
    model_key: str | None = None,
    n_paraphrases: int = 3,
) -> dict:
    """원본 + paraphrase N개로 shape을 각각 생성 (lexical ensemble).

    Returns: {
        "queries": [
            { "text": str, "is_original": bool, "values": [...],
              "length": int, "description": str, "error": str|None },
            ...
        ],
        "paraphrases": [str, ...],   # 원본 제외, LLM이 반환한 그대로
    }
    """
    cfg = _effective_config()
    selected = model_key or _get_task_active_model(cfg, TASK_TEXT_TO_TS)
    if cfg.get("models", {}).get(selected, {}).get("provider") in {"verbalts", "builtin"}:
        result = generate_pattern_shape(nl_text, length, dataset_context, model_key)
        return {"queries": [dict(result, text=nl_text, is_original=True, error=None)],
                "paraphrases": []}
    paraphrases = generate_paraphrases(nl_text, n=n_paraphrases, model_key=model_key)

    # 원본을 첫 항목, 이후 paraphrase 들
    candidates = [(nl_text, True)] + [(p, False) for p in paraphrases]
    queries = []
    for text, is_original in candidates:
        entry = {
            "text": text,
            "is_original": is_original,
            "values": [],
            "length": 0,
            "description": "",
            "error": None,
            "engine": "",
            "model": None,
            "llm_used": False,
            "fallback_reason": None,
        }
        try:
            res = generate_pattern_shape(
                text, length=length,
                dataset_context=dataset_context,
                model_key=model_key,
            )
            entry["values"] = res.get("values", [])
            entry["length"] = res.get("length", len(entry["values"]))
            entry["description"] = res.get("description", text)
            entry["engine"] = res.get("engine", "")
            entry["model"] = res.get("model")
            entry["llm_used"] = bool(res.get("llm_used", False))
            entry["fallback_reason"] = res.get("fallback_reason")
        except Exception as e:
            entry["error"] = str(e)
            logger.warning(f"Ensemble shape 생성 실패 ({text!r}): {e}")
        queries.append(entry)

    return {
        "queries": queries,
        "paraphrases": paraphrases,
    }


# ─── TS-to-Text: 시계열 → 자연어 해석 ────────────────────────

def describe_segment(
    values: list[float],
    col_name: str = "",
    col_label: str = "",
    col_unit: str = "",
    unit_label: str = "",
    timestamps: list[str] | None = None,
    dataset_context: str = "",
    model_key: str | None = None,
) -> dict:
    """단일 컬럼 시계열 세그먼트를 자연어로 해석.

    Returns: {
        "shape": str, "description": str, "characteristics": list[str],
        "raw_response": str
    }
    """
    import numpy as np
    arr = np.array(values, dtype=np.float64)
    arr_clean = arr[~np.isnan(arr)]

    stats = {}
    analysis = {}
    if len(arr_clean) > 0:
        stats = {
            "mean": round(float(np.mean(arr_clean)), 2),
            "std": round(float(np.std(arr_clean)), 2),
            "min": round(float(np.min(arr_clean)), 2),
            "max": round(float(np.max(arr_clean)), 2),
            "length": len(arr),
        }
        analysis = _compute_segment_features(arr_clean)

    # Build user message
    parts = []
    if col_name:
        parts.append(f"## Column: {col_name}")
        if col_label:
            parts.append(f"Label: {col_label}")
        if col_unit:
            parts.append(f"Unit: {col_unit}")
    if unit_label:
        parts.append(f"Time unit: {unit_label}")
    if timestamps and len(timestamps) >= 2:
        parts.append(f"Period: {timestamps[0]} ~ {timestamps[-1]}")
    if stats:
        parts.append(f"Stats: mean={stats['mean']}, std={stats['std']}, "
                      f"min={stats['min']}, max={stats['max']}, points={stats['length']}")
    if analysis:
        parts.append("\n## Pre-computed Analysis")
        for k, v in analysis.items():
            parts.append(f"- {k}: {v}")
    if dataset_context:
        parts.append(f"\n{dataset_context}")

    # Downsample values for LLM (max 50 points to save tokens)
    display_vals = values
    if len(values) > 50:
        step = len(values) / 50
        display_vals = [values[int(i * step)] for i in range(50)]
    parts.append(f"\n## Values ({len(display_vals)} points, sampled)")
    parts.append(str([round(v, 3) if v is not None and not (isinstance(v, float) and v != v) else None
                       for v in display_vals]))

    user_content = "\n".join(parts)

    # ChatTS provider: 시계열을 NumPy로 직접 전달
    lcfg = _effective_config()
    provider, model_id, pcfg = _resolve_model(lcfg, model_key, task=TASK_TS_TO_TEXT)
    if provider == "builtin":
        raw = ""
    elif provider == "chatts":
        raw = _call_chatts_describe(arr_clean, user_content, analysis, pcfg)
    else:
        system_prompt = get_ts_description_prompt()
        raw = call_llm(user_content, model_key=model_key,
                       system_prompt=system_prompt, temperature=0.2,
                       task=TASK_TS_TO_TEXT, max_tokens=int(cfg("llm.describe_max_tokens")))

    parsed = _extract_json(raw, require_keys=("summary", "shape", "description"))
    authoritative = _authoritative_interpretation(analysis)
    if parsed is None:
        return {
            "shape": "unknown",
            "description": authoritative["summary"],
            "summary": authoritative["summary"],
            "trend": authoritative["trend"],
            "volatility": authoritative["volatility"],
            "features": authoritative["features"],
            "characteristics": authoritative["features"],
            "stats": stats,
            "analysis": analysis,
            "raw_response": raw,
            "engine": "statistics" if provider == "builtin" else provider,
            "llm_used": provider != "builtin",
            "model": model_id,
            "field_sources": {
                "shape": "not_generated" if provider == "builtin" else "llm_unparsed",
                "trend": "computed",
                "volatility": "computed",
                "features": "computed",
                "summary": "computed",
            },
        }

    # 서술(summary)은 모델이 쓴 문장을 쓴다 — 시계열을 직접 읽는 ChatTS 의 모양·사건 묘사가
    # 통계 한 줄보다 낫다(2026-10-07 비교). 숫자가 들어가는 trend·volatility·features 는
    # 계산값이 정본이라 모델 값을 쓰지 않는다. 모델 문장이 없으면 통계 요약으로 돌아간다.
    summary = authoritative["summary"]
    summary_source = "computed"
    llm_summary = parsed.get("summary")
    if isinstance(llm_summary, dict):   # 이중 언어 응답은 언어별로 둔다
        ko = str(llm_summary.get("ko") or "").strip(); en = str(llm_summary.get("en") or "").strip()
        if len(ko) >= 20 or len(en) >= 20:
            summary = {"en": en or ko, "ko": ko or en}
            summary_source = "llm"
    elif isinstance(llm_summary, str) and len(llm_summary.strip()) >= 20:
        text = llm_summary.strip()
        summary = {"en": text, "ko": text}
        summary_source = "llm"
    return {
        "shape": parsed.get("shape", "unknown"),
        "description": summary,
        "summary": summary,
        "trend": authoritative["trend"],
        "volatility": authoritative["volatility"],
        "features": authoritative["features"],
        "characteristics": authoritative["features"],
        "stats": stats,
        "analysis": analysis,
        "raw_response": raw,
        "engine": "statistics" if provider == "builtin" else provider,
        "llm_used": provider != "builtin",
        "model": model_id,
        "field_sources": {
            "shape": "llm",
            "trend": "computed",
            "volatility": "computed",
            "features": "computed",
            "summary": summary_source,
        },
    }


def _chatts_multi_prompt(series: list) -> tuple[str, list]:
    """ChatTS 다변량 프롬프트. 열마다 <ts><ts/> 자리를 두고 값을 같은 순서로 넘긴다."""
    import numpy as np
    lines, arrays = [f"I have {len(series)} time series from the same period."], []
    for cd, arr in series:
        ts = arr.astype(np.float32)
        if len(ts) > 1024:  # 단일 해석과 같은 ChatTS 권장 상한
            ts = ts[np.linspace(0, len(ts) - 1, 1024, dtype=int)]
        meta = ", ".join(v for v in (cd.get("col_label"), cd.get("col_unit")) if v)
        name = f"{cd.get('col_name', '')} ({meta})" if meta else cd.get("col_name", "")
        lines.append(f"{name} of length {len(ts)}: <ts><ts/>")
        arrays.append(ts)
    lines.append("Describe the shape of each series and how they relate to each other. "
                 "Be specific and concise.")
    return "\n".join(lines), arrays


def describe_segment_multi(
    columns_data: list[dict],
    unit_label: str = "",
    timestamps: list[str] | None = None,
    dataset_context: str = "",
    model_key: str | None = None,
) -> dict:
    """다변량 시계열 세그먼트를 종합 해석.

    columns_data: list of {
        "col_name": str, "col_label": str, "col_unit": str,
        "values": list[float], "description": str (단일 분석 결과)
    }

    Returns: {
        "situation": str, "description": str,
        "correlations": list[str], "tags": list[str],
        "raw_response": str
    }
    """
    parts = []
    if unit_label:
        parts.append(f"Time unit: {unit_label}")
    if timestamps and len(timestamps) >= 2:
        parts.append(f"Period: {timestamps[0]} ~ {timestamps[-1]}")
    if dataset_context:
        parts.append(f"\n{dataset_context}")

    import numpy as np

    parts.append("\n## Columns")
    computed_columns = []
    numeric_columns = []
    chatts_series = []
    for input_index, cd in enumerate(columns_data):
        vals = cd.get("values", [])
        if vals:
            arr = np.array(vals, dtype=np.float64)
            arr_c = arr[np.isfinite(arr)]
            if len(arr_c) > 0:
                # Invalid/empty columns must not occupy a position in the LLM
                # prompt.  Otherwise a model may omit them and shift every
                # subsequent interpretation to the wrong sensor.
                parts.append(f"\n### {cd.get('col_name', '')}")
                if cd.get("col_label"):
                    parts.append(f"Label: {cd['col_label']}")
                if cd.get("col_unit"):
                    parts.append(f"Unit: {cd['col_unit']}")
                if cd.get("description"):
                    parts.append(f"Analysis: {cd['description']}")
                analysis = _compute_segment_features(arr_c)
                authoritative = _authoritative_interpretation(analysis)
                stats = {
                    "mean": round(float(np.mean(arr_c)), 2),
                    "std": round(float(np.std(arr_c)), 2),
                    "min": round(float(np.min(arr_c)), 2),
                    "max": round(float(np.max(arr_c)), 2),
                    "length": len(arr),
                }
                parts.append(f"Stats: mean={np.mean(arr_c):.2f}, "
                             f"std={np.std(arr_c):.2f}, "
                             f"min={np.min(arr_c):.2f}, max={np.max(arr_c):.2f}")
                parts.append(f"Pre-computed analysis: {analysis}")
                computed_columns.append({
                    "_input_index": input_index,
                    "col_name": cd.get("col_name", ""),
                    "col_label": cd.get("col_label", ""),
                    "stats": stats,
                    "analysis": analysis,
                    **authoritative,
                })
                numeric_columns.append((cd.get("col_name", ""), arr))
                chatts_series.append((cd, arr_c))

    if not computed_columns:
        raise ValueError("at least one column must contain a finite numeric value")

    computed_correlations = []
    for i in range(len(numeric_columns)):
        for j in range(i + 1, len(numeric_columns)):
            name_a, arr_a = numeric_columns[i]
            name_b, arr_b = numeric_columns[j]
            size = min(len(arr_a), len(arr_b))
            a, b = arr_a[:size], arr_b[:size]
            mask = np.isfinite(a) & np.isfinite(b)
            if int(mask.sum()) < 3 or np.std(a[mask]) < 1e-10 or np.std(b[mask]) < 1e-10:
                continue
            corr = float(np.corrcoef(a[mask], b[mask])[0, 1])
            if np.isfinite(corr):
                computed_correlations.append({
                    "ko": f"{name_a} ↔ {name_b}: Pearson r={corr:.2f}",
                    "en": f"{name_a} ↔ {name_b}: Pearson r={corr:.2f}",
                    "columns": [name_a, name_b],
                    "pearson_r": round(corr, 4),
                    "n": int(mask.sum()),
                })

    summary = {
        "ko": f"{len(computed_columns)}개 컬럼의 서버 계산 결과",
        "en": f"Server-computed facts for {len(computed_columns)} columns",
    }
    if computed_correlations:
        strongest = max(computed_correlations, key=lambda item: abs(item["pearson_r"]))
        summary["ko"] += f" · 최대 절대 상관 {strongest['ko']}"
        summary["en"] += f" · strongest absolute correlation {strongest['en']}"

    user_content = "\n".join(parts)
    system_prompt = get_ts_description_multi_prompt()

    cfg = _effective_config()
    provider, model_id, _ = _resolve_model(cfg, model_key, task=TASK_TS_TO_TEXT)

    ts_arrays = None
    if provider == "chatts":
        # ChatTS 는 값을 직접 읽는다. 문장만 보내면 서버가 400 으로 거절한다.
        user_content, ts_arrays = _chatts_multi_prompt(chatts_series)

    raw = "" if provider == "builtin" else call_llm(
        user_content, model_key=model_key, system_prompt=system_prompt,
        temperature=0.2, task=TASK_TS_TO_TEXT, ts_arrays=ts_arrays)

    parsed = _extract_json(raw)
    situation = parsed.get("situation", "") if parsed else ""
    if provider == "chatts" and not situation:
        # ChatTS 는 JSON 이 아닌 자유 문장으로 답한다. 그 문장을 상황 설명으로 쓴다.
        situation = raw.strip()
    llm_columns = parsed.get("columns", []) if parsed else []
    llm_by_name = {
        str(item.get("col_name")): item
        for item in llm_columns
        if isinstance(item, dict) and item.get("col_name")
    }
    result_cols = []
    for valid_index, facts in enumerate(computed_columns):
        input_index = facts.pop("_input_index")
        llm_col = llm_by_name.get(str(facts.get("col_name")), {})
        if not llm_col and valid_index < len(llm_columns) and isinstance(llm_columns[valid_index], dict):
            llm_col = llm_columns[valid_index]
        has_llm_shape = bool(llm_col.get("shape"))
        result_cols.append({
            **facts,
            "shape": llm_col.get("shape", "unknown"),
            "field_sources": {
                "shape": "not_generated" if provider == "builtin" else ("llm" if has_llm_shape else "llm_unmatched"),
                "trend": "computed",
                "volatility": "computed",
                "features": "computed",
                "summary": "computed",
            },
        })

    return {
        "columns": result_cols,
        "situation": situation,
        "description": summary,
        "correlations": computed_correlations,
        "tags": parsed.get("tags", []) if parsed else [],
        "raw_response": raw,
        "engine": "statistics" if provider == "builtin" else provider,
        "llm_used": provider != "builtin",
        "model": model_id,
        "field_sources": {
            "situation": "not_generated" if provider == "builtin" else ("llm" if parsed or situation else "llm_unparsed"),
            "description": "computed",
            "correlations": "computed",
            "tags": "not_generated" if provider == "builtin" else ("llm" if parsed else "llm_unparsed"),
        },
    }
