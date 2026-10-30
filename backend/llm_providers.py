# -*- coding: utf-8 -*-
"""LLM provider implementations -- HuggingFace, VLM, ChatTS, Ollama, vLLM.

Extracted from llm_service.py -- contains model loading, caching, and
provider-specific call functions.
"""
from __future__ import annotations

import json
import logging

from backend.llm_analysis import _build_structured_response, _extract_json

logger = logging.getLogger(__name__)


def _call_vllm(messages: list[dict], pcfg: dict,
               system_prompt: str = "",
               temperature: float | None = None,
               max_tokens: int | None = None) -> str:
    """Call a running vLLM OpenAI-compatible server.

    Finder must not load another copy of a model into an already full GPU.
    It reuses the long-lived vLLM server and sends only the prompt over HTTP.
    """
    import requests

    base_url = str(pcfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("vLLM base_url 이 설정되지 않았습니다 (config/model/text_ts.yaml)")
    model = pcfg.get("model", "")
    if not model:
        raise RuntimeError("vLLM model_id가 설정되지 않았습니다")

    chat = []
    if system_prompt:
        chat.append({"role": "system", "content": system_prompt})
    chat.extend(messages)
    payload = {
        "model": model,
        "messages": chat,
        "temperature": temperature if temperature is not None else pcfg.get("temperature"),
        "max_tokens": max_tokens if max_tokens else pcfg.get("max_tokens"),
    }
    timeout = float(pcfg.get("timeout"))
    # 외부 OpenAI 호환 서버(OpenAI·Gemini·원격 vLLM)는 키를 요구한다. 로컬 vLLM 은 비어 있다.
    headers = {"Authorization": f"Bearer {pcfg['api_key']}"} if pcfg.get("api_key") else {}
    try:
        resp = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if headers:   # 외부 OpenAI 호환 서버면 호출 수·토큰만 적는다(단가 미상 → 비용 0)
            from backend import external_llm
            u = data.get("usage") or {}
            external_llm.record_usage("openai_compat", model, int(u.get("prompt_tokens", 0) or 0), int(u.get("completion_tokens", 0) or 0))
        return data["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"vLLM API 호출 실패 ({base_url}): {e}") from e


def _torch_version_at_least(major: int, minor: int) -> bool:
    try:
        import torch
        ver = (torch.__version__ or "0.0").split("+", 1)[0]
        parts = ver.split(".")
        nums = []
        for p in parts[:2]:
            digits = "".join(ch for ch in p if ch.isdigit())
            nums.append(int(digits) if digits else 0)
        while len(nums) < 2:
            nums.append(0)
        return tuple(nums[:2]) >= (major, minor)
    except Exception:
        return False


def _is_torch_load_security_error(err: str) -> bool:
    e = (err or "").lower()
    return (
        "serious vulnerability" in e
        or "torch.load" in e and "weights_only" in e
        or "upgrade torch to at least v2.6" in e
    )


def _safetensors_missing_error(err: str) -> bool:
    e = (err or "").lower()
    return "safetensors" in e and (
        "not found" in e
        or "not available" in e
        or "no file named" in e
        or "cannot be loaded with use_safetensors=true" in e
    )

# ─── HuggingFace 모델 캐시 (한 번 로드하면 재사용) ──────────
_hf_pipeline = None
_hf_model_id = None

# ─── ChatTS 모델 캐시 ────────────────────────────────────────
_chatts_model = None
_chatts_tokenizer = None
_chatts_processor = None
_chatts_model_id = None

# ─── VLM (Qwen2.5-VL) 모델 캐시 ─────────────────────────────
_vlm_model = None
_vlm_processor = None
_vlm_model_id = None


# ─── HuggingFace ─────────────────────────────────────────────

def _call_huggingface(messages: list[dict], pcfg: dict,
                      system_prompt: str = "",
                      temperature: float | None = None,
                      max_tokens: int | None = None) -> str:
    """HuggingFace transformers pipeline으로 직접 실행."""
    global _hf_pipeline, _hf_model_id
    model_id = pcfg.get("model", "")
    if not model_id:
        raise RuntimeError("HuggingFace model_id가 설정되지 않았습니다")

    # 모델이 바뀌었거나 첫 로드일 때만 초기화
    if _hf_pipeline is None or _hf_model_id != model_id:
        import torch
        from transformers import pipeline as hf_pipeline, AutoTokenizer
        device = pcfg.get("device")
        if device == "auto":
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        use_gpu = device.startswith("cuda")
        logger.info(f"Loading {model_id} on {device}...")
        try:
            # Load tokenizer separately to set clean_up_tokenization_spaces
            tokenizer = AutoTokenizer.from_pretrained(
                model_id,
                trust_remote_code=True,
                clean_up_tokenization_spaces=False
            )
            # bfloat16 is more stable than float16 for Mistral and similar models
            if use_gpu:
                dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            else:
                dtype = torch.float32
            base_kwargs = {
                "task": "text-generation",
                "model": model_id,
                "tokenizer": tokenizer,
                "device": torch.device(device) if use_gpu else None,
                "torch_dtype": dtype,
                "trust_remote_code": True,
            }
            try:
                _hf_pipeline = hf_pipeline(
                    base_kwargs["task"],
                    model=base_kwargs["model"],
                    tokenizer=base_kwargs["tokenizer"],
                    device=base_kwargs["device"],
                    torch_dtype=base_kwargs["torch_dtype"],
                    trust_remote_code=base_kwargs["trust_remote_code"],
                    model_kwargs={"use_safetensors": True},
                )
            except Exception as e:
                # 안전한 기본값: safetensors 우선. safetensors 없는 모델은
                # torch>=2.6 인 경우에만 비-safetensors 로딩을 허용.
                if _safetensors_missing_error(str(e)) and _torch_version_at_least(2, 6):
                    _hf_pipeline = hf_pipeline(
                        base_kwargs["task"],
                        model=base_kwargs["model"],
                        tokenizer=base_kwargs["tokenizer"],
                        device=base_kwargs["device"],
                        torch_dtype=base_kwargs["torch_dtype"],
                        trust_remote_code=base_kwargs["trust_remote_code"],
                    )
                elif _is_torch_load_security_error(str(e)) and not _torch_version_at_least(2, 6):
                    raise RuntimeError(
                        "보안 제한으로 torch<2.6 환경에서 non-safetensors 모델 로딩이 차단되었습니다. "
                        "safetensors 제공 모델을 사용하거나 torch>=2.6으로 업그레이드하세요."
                    ) from e
                else:
                    raise
            # Override generation_config to prevent truncation
            if hasattr(_hf_pipeline, 'model') and hasattr(_hf_pipeline.model, 'generation_config'):
                _hf_pipeline.model.generation_config.max_length = 8192
            _hf_model_id = model_id
        except Exception as e:
            raise RuntimeError(f"모델 로드 실패: {e}")

    # Prefer chat-style input, but some tokenizer templates expect multimodal
    # message content entries with {"type": "text", ...}. If that fails,
    # fall back to a plain text prompt for broader model compatibility.
    chat = [{"role": "system", "content": system_prompt}] + messages
    temp = temperature if temperature is not None else pcfg.get("temperature")
    # per-call override(max_tokens) > 설정값 > 기본 2048
    max_tokens = max_tokens if max_tokens else pcfg.get("max_tokens")
    kwargs = {
        "max_new_tokens": max_tokens,
        "max_length": max_tokens + 1024,  # Override model's default max_length=20
        "temperature": temp if temp > 0 else 0.1,  # 0은 에러
        "do_sample": True,
        "return_full_text": False,
    }
    try:
        output = _hf_pipeline(chat, **kwargs)
        return output[0]["generated_text"]
    except Exception as e:
        if "type" not in str(e):
            raise
        logger.warning("Chat-template type error; falling back to plain prompt")
        plain_prompt = system_prompt + "\n\n" + "\n".join(
            str(m.get("content", "")) for m in messages
        )
        output = _hf_pipeline(plain_prompt, **kwargs)
        return output[0]["generated_text"]


# ─── VLM (Qwen2.5-VL) 전용 ─────────────────────────────────

def _load_vlm(model_id: str, device: str = "cuda:0"):
    """Qwen2.5-VL 모델 로드 (최초 1회)."""
    global _vlm_model, _vlm_processor, _vlm_model_id
    if _vlm_model is not None and _vlm_model_id == model_id:
        return
    import torch
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    logger.info(f"Loading {model_id} on {device}...")
    try:
        _vlm_model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=torch.float16,
            device_map=device, trust_remote_code=True,
            use_safetensors=True,
        )
    except Exception as e:
        if _safetensors_missing_error(str(e)) and _torch_version_at_least(2, 6):
            _vlm_model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                model_id, torch_dtype=torch.float16,
                device_map=device, trust_remote_code=True,
            )
        elif _is_torch_load_security_error(str(e)) and not _torch_version_at_least(2, 6):
            raise RuntimeError(
                "보안 제한으로 torch<2.6 환경에서 non-safetensors 모델 로딩이 차단되었습니다. "
                "safetensors 제공 모델을 사용하거나 torch>=2.6으로 업그레이드하세요."
            ) from e
        else:
            raise
    _vlm_processor = AutoProcessor.from_pretrained(
        model_id, trust_remote_code=True,
    )
    _vlm_model_id = model_id
    logger.info(f"{model_id} loaded OK")


def _call_vlm(messages: list[dict], pcfg: dict,
              system_prompt: str = "", temperature: float | None = None,
              image_path: str | None = None) -> str:
    """Qwen2.5-VL 호출. 텍스트만 또는 텍스트+이미지."""
    model_id = pcfg.get("model")
    device = pcfg.get("device")
    _load_vlm(model_id, device)

    # Build conversation
    conversation = []
    if system_prompt:
        conversation.append({"role": "system", "content": [{"type": "text", "text": system_prompt}]})

    user_content = []
    if image_path:
        user_content.append({"type": "image", "image": f"file://{image_path}"})
    for msg in messages:
        user_content.append({"type": "text", "text": msg.get("content", "")})
    conversation.append({"role": "user", "content": user_content})

    text_input = _vlm_processor.apply_chat_template(
        conversation, tokenize=False, add_generation_prompt=True
    )

    if image_path:
        from qwen_vl_utils import process_vision_info
        image_inputs, video_inputs = process_vision_info(conversation)
        inputs = _vlm_processor(
            text=[text_input], images=image_inputs, videos=video_inputs,
            padding=True, return_tensors="pt"
        ).to(_vlm_model.device)
    else:
        inputs = _vlm_processor(
            text=[text_input], padding=True, return_tensors="pt"
        ).to(_vlm_model.device)

    temp = temperature if temperature is not None else pcfg.get("temperature")
    generated = _vlm_model.generate(
        **inputs, max_new_tokens=pcfg.get("max_tokens"),
        temperature=max(temp, 0.01), do_sample=True,
    )
    output_ids = generated[0][inputs["input_ids"].shape[1]:]
    return _vlm_processor.decode(output_ids, skip_special_tokens=True)


# ─── ChatTS 전용 ────────────────────────────────────────────

def _load_chatts(model_id: str, device: str = "cuda:0"):
    """ChatTS 모델 로드 (최초 1회)."""
    global _chatts_model, _chatts_tokenizer, _chatts_processor, _chatts_model_id
    if _chatts_model is not None and _chatts_model_id == model_id:
        return
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, AutoProcessor
    logger.info(f"Loading {model_id} on {device}...")
    try:
        # ChatTS 리포는 safetensors가 없는 경우가 있어 강제하지 않는다.
        _chatts_model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True,
            device_map=device, torch_dtype=torch.float16,
        )
    except Exception as e:
        if _is_torch_load_security_error(str(e)) and not _torch_version_at_least(2, 6):
            raise RuntimeError(
                "현재 ChatTS 가중치 포맷(.bin)은 torch<2.6 환경 보안 제한에 걸립니다. "
                "torch>=2.6 업그레이드 또는 safetensors 제공 모델 사용이 필요합니다."
            ) from e
        raise
    _chatts_tokenizer = AutoTokenizer.from_pretrained(
        model_id, trust_remote_code=True,
    )
    _chatts_processor = AutoProcessor.from_pretrained(
        model_id, trust_remote_code=True, tokenizer=_chatts_tokenizer,
    )
    _chatts_model_id = model_id
    logger.info(f"{model_id} loaded OK")


def _call_chatts_http(ts_arrays: list, prompt: str, pcfg: dict,
                      temperature: float | None = None) -> str:
    """별도 ChatTS 서버(tools/models/serve_chatts.py)를 HTTP로 부른다.

    ChatTS 가중치·원격 코드는 앱 환경(torch 2.5, transformers 5)에서 올라가지 않아
    다른 파이썬 환경의 서버가 담당한다. 주소는 text_ts.yaml providers.chatts.base_url.
    """
    import requests
    base_url = str(pcfg.get("base_url") or "").rstrip("/")
    # 값은 text_ts.yaml providers.chatts 가 단일 소스 — 코드에 기본값을 두지 않는다
    temp = temperature if temperature is not None else pcfg["temperature"]
    resp = requests.post(
        f"{base_url}/describe",
        json={"timeseries": [[float(v) for v in a] for a in ts_arrays], "prompt": prompt,
              "max_tokens": int(pcfg["max_tokens"]), "temperature": float(temp)},
        timeout=float(pcfg["timeout"]),
    )
    resp.raise_for_status()
    data = resp.json()
    if "text" not in data:
        raise RuntimeError(f"ChatTS 서버 응답에 text 가 없습니다: {str(data)[:200]}")
    return str(data["text"])


def _call_chatts(ts_arrays: list, prompt: str, pcfg: dict,
                 temperature: float | None = None) -> str:
    """ChatTS로 시계열 해석. ts_arrays는 numpy 배열 리스트.

    providers.chatts 에 base_url 이 있으면 별도 서버를 쓰고, 없으면 같은 프로세스에 올린다.
    """
    if pcfg.get("base_url"):
        return _call_chatts_http(ts_arrays, prompt, pcfg, temperature)
    model_id = pcfg.get("model")
    device = pcfg.get("device")
    _load_chatts(model_id, device)

    # 프롬프트 포맷: Qwen 스타일
    formatted = (
        f"<|im_start|>system\nYou are a time series analysis expert. "
        f"Respond in JSON format with both Korean and English.<|im_end|>\n"
        f"<|im_start|>user\n{prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

    inputs = _chatts_processor(
        text=[formatted], timeseries=ts_arrays,
        padding=True, return_tensors="pt",
    )
    inputs = {
        k: v.to(device=_chatts_model.device, dtype=_chatts_model.dtype)
        if v.is_floating_point() else v.to(_chatts_model.device)
        for k, v in inputs.items()
    }

    temp = temperature if temperature is not None else pcfg.get("temperature")
    outputs = _chatts_model.generate(
        **inputs,
        max_new_tokens=pcfg.get("max_tokens"),
        temperature=max(temp, 0.1),
        do_sample=True,
    )
    # 입력 토큰 이후 부분만 디코딩
    generated = outputs[0][inputs["input_ids"].shape[1]:]
    return _chatts_tokenizer.decode(generated, skip_special_tokens=True)


def _call_chatts_describe(arr, context: str, analysis: dict, pcfg: dict) -> str:
    """ChatTS로 시계열 세그먼트 해석. 시계열을 직접 NumPy로 전달.

    ChatTS 응답(자유 텍스트)과 사전 분석을 결합하여 구조화된 JSON 문자열 반환.
    """
    import numpy as np

    # 길이 조정 (ChatTS 권장: 64~1024)
    ts = arr.astype(np.float32)
    if len(ts) > 1024:
        indices = np.linspace(0, len(ts) - 1, 1024, dtype=int)
        ts = ts[indices]

    # ChatTS에는 간단한 질문만
    prompt = (
        f"I have a time series of length {len(ts)}: <ts><ts/>.\n"
        f"Describe the overall shape, trend, notable patterns, and any anomalies in this time series. "
        f"Be specific and concise."
    )

    chatts_text = _call_chatts([ts], prompt, pcfg, temperature=0.2)

    # ChatTS 자유 텍스트 + 사전 분석을 결합하여 구조화된 JSON 생성
    return _build_structured_response(chatts_text, analysis)


def _call_chatts_generate_shape(nl_text: str, length: int, pcfg: dict) -> str:
    """ChatTS로 자연어 설명에서 시계열 형태(z-score 배열)를 생성.

    ChatTS에 간단한 프롬프트를 보내고, 응답에서 숫자 배열 추출.
    JSON 파싱 실패 시 프로그래밍적으로 기본 shape 생성.
    """
    import numpy as np

    prompt = (
        f"Generate a time series of exactly {length} z-score normalized values "
        f"(range -3 to +3) that matches this description: '{nl_text}'. "
        f"Output ONLY a JSON object with 'description' (string) and 'values' (array of {length} floats). "
        f"Example: {{\"description\": \"rising trend\", \"values\": [0.0, 0.1, 0.3, ...]}}"
    )

    # ChatTS는 <ts> 없이 텍스트만 보냄 (shape 생성은 시계열 입력 불필요)
    model_id = pcfg.get("model")
    device = pcfg.get("device")
    _load_chatts(model_id, device)

    formatted = (
        f"<|im_start|>system\nYou are a time series generation expert. "
        f"Output valid JSON only.<|im_end|>\n"
        f"<|im_start|>user\n{prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

    inputs = _chatts_processor(
        text=[formatted], timeseries=[],
        padding=True, return_tensors="pt",
    )
    inputs = {
        k: v.to(device=_chatts_model.device, dtype=_chatts_model.dtype)
        if v.is_floating_point() else v.to(_chatts_model.device)
        for k, v in inputs.items()
    }

    outputs = _chatts_model.generate(
        **inputs, max_new_tokens=pcfg.get("max_tokens"),
        temperature=0.3, do_sample=True,
    )
    generated = outputs[0][inputs["input_ids"].shape[1]:]
    raw = _chatts_tokenizer.decode(generated, skip_special_tokens=True)

    # JSON 파싱 시도
    parsed = _extract_json(raw)
    if parsed and isinstance(parsed.get("values"), list) and len(parsed["values"]) >= 3:
        return raw

    # 파싱 실패 -> 텍스트 LLM fallback
    # 모델 키를 박지 않고 task_active_models.text_to_ts 를 그대로 사용한다
    # (이전에는 카탈로그에서 사라진 키가 하드코딩돼 있어 fallback이 항상 실패했다).
    # Lazy import to avoid circular dependency with llm_service
    logger.info("Shape generation JSON parse failed, falling back to the active text_to_ts model")
    try:
        from backend.llm_service import call_llm, get_shape_generation_prompt
        system_prompt = get_shape_generation_prompt()
        user_content = f"길이: {length} 포인트\n\n## 패턴 설명\n{nl_text}"
        return call_llm(user_content, task="text_to_ts",
                        system_prompt=system_prompt, temperature=0.3)
    except Exception as e:
        # 최종 fallback: 프로그래밍적으로 기본 shape 생성
        logger.error(f"Text LLM fallback also failed: {e}")
        vals = list(np.linspace(-1, 1, length))
        return json.dumps({"description": nl_text, "values": vals})


# ─── Ollama 전용 ────────────────────────────────────────────

def _call_ollama(messages: list[dict], pcfg: dict,
                 system_prompt: str = "",
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> str:
    """Ollama API 호출. 로컬 서버 (localhost:11434) 사용."""
    import requests

    model = pcfg.get("model")
    base_url = pcfg.get("base_url")
    if not base_url:
        raise RuntimeError("Ollama base_url 이 설정되지 않았습니다 (config/model/text_ts.yaml)")
    temp = temperature if temperature is not None else pcfg.get("temperature")
    num_predict = max_tokens if max_tokens else pcfg.get("max_tokens")

    # Ollama chat API 형식
    ollama_messages = []
    if system_prompt:
        ollama_messages.append({"role": "system", "content": system_prompt})
    for msg in messages:
        ollama_messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", "")
        })

    payload = {
        "model": model,
        "messages": ollama_messages,
        "stream": False,
        "options": {
            "temperature": temp if temp > 0 else 0.1,
            "num_predict": num_predict,
        }
    }

    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama API 호출 실패: {e}")


# ─── 외부 LLM (관리자가 화면에서 연결) ───────────────────────────

def _call_anthropic(messages: list[dict], pcfg: dict, system_prompt: str = "",
                    temperature: float | None = None, max_tokens: int | None = None) -> str:
    """Claude via the official SDK. pcfg comes from backend.external_llm.provider_cfg."""
    import anthropic
    from backend import external_llm
    external_llm.check_budget()   # 월 한도에 닿았으면 부르지 않는다

    client = anthropic.Anthropic(api_key=pcfg["api_key"], base_url=pcfg.get("base_url") or None,
                                 timeout=float(pcfg["timeout"]), max_retries=2)
    model = pcfg["model"]
    chat = [{"role": m["role"], "content": m["content"]} for m in messages
            if m.get("role") in ("user", "assistant")]
    # 5세대 모델은 생각(thinking) 토큰이 max_tokens 에 포함된다. 파형 생성처럼 상한을 수십 토큰으로
    # 좁혀 보내면 생각만 하다 끝나 빈 응답이 온다 — 바닥값 아래로는 내려가지 않게 한다.
    from backend.app_config import cfg
    floor = int(cfg("external_llm.min_output_tokens"))
    kwargs = {"model": model, "max_tokens": max(int(max_tokens or pcfg["max_tokens"]), floor), "messages": chat}
    if system_prompt:
        kwargs["system"] = system_prompt
    # 5세대 모델은 temperature 를 받지 않는다(400). 그 전 모델에만 넘긴다.
    if temperature is not None and not model.startswith(("claude-opus-5", "claude-sonnet-5", "claude-fable-5")):
        kwargs["temperature"] = float(temperature)
    try:
        if model.startswith(("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")):
            # 안전 분류기가 거절하면 서버가 대체 모델로 이어 준다 — 거절 응답을 빈 결과로 받지 않기 위해
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Anthropic 인증 실패 — API 키를 확인하세요") from e
    except anthropic.NotFoundError as e:
        raise RuntimeError(f"Anthropic 모델을 찾을 수 없음: {model}") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Anthropic 요청 한도 초과 — 잠시 뒤 다시 시도") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic API 오류 {e.status_code}: {getattr(e, 'message', e)}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"Anthropic 서버에 연결할 수 없음: {e}") from e
    if getattr(resp, "stop_reason", "") == "refusal":
        raise RuntimeError("모델이 요청을 거절했습니다(안전 분류기)")
    usage = getattr(resp, "usage", None)
    if usage is not None:
        external_llm.record_usage("anthropic", model, int(getattr(usage, "input_tokens", 0) or 0),
                                  int(getattr(usage, "output_tokens", 0) or 0))
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    if getattr(resp, "stop_reason", "") == "max_tokens":
        # 잘린 답은 비어 있든 반쯤 있든 쓸 수 없다(반쪽 JSON 을 집어 엉뚱한 값이 채워진다)
        raise RuntimeError(f"출력 토큰 한도({kwargs['max_tokens']})에 걸려 답이 잘렸습니다")
    return text


def call_external(provider: str, messages: list[dict], pcfg: dict, system_prompt: str = "",
                  temperature: float | None = None, max_tokens: int | None = None) -> str:
    if provider == "anthropic":
        return _call_anthropic(messages, pcfg, system_prompt, temperature, max_tokens)
    if provider == "openai_compat":
        return _call_vllm(messages, pcfg, system_prompt, temperature, max_tokens=max_tokens)
    raise RuntimeError(f"지원하지 않는 외부 제공자: {provider}")


def external_reachable(provider: str, pcfg: dict) -> tuple[bool, str]:
    """Cheap liveness probe (no generation): model lookup / model list."""
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=pcfg["api_key"], base_url=pcfg.get("base_url") or None,
                                         timeout=float(pcfg["health_timeout"]), max_retries=0)
            client.models.retrieve(pcfg["model"])
            return True, ""
        import requests
        resp = requests.get(f"{pcfg['base_url'].rstrip('/')}/v1/models", timeout=float(pcfg["health_timeout"]),
                            headers={"Authorization": f"Bearer {pcfg['api_key']}"} if pcfg.get("api_key") else {})
        return (resp.status_code == 200), ("" if resp.status_code == 200 else f"외부 서버 응답 {resp.status_code}")
    except Exception as e:
        return False, f"외부 LLM 에 연결할 수 없음: {str(e)[:160]}"
