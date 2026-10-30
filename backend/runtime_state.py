"""Process-local startup decisions; never rewrite the user's model selection YAML."""
from copy import deepcopy

STATE = {"managed": False, "mode": "manual", "gpu": False, "models": {}, "services": []}
CPU_KEY = "cpu-basic"


def configure(mode, gpu, models, services):
    STATE.update(managed=True, mode=mode, gpu=gpu, models=dict(models), services=services)


def effective_config(config):
    if not STATE["managed"]:
        return config
    result = deepcopy(config)
    result["task_active_models"] = dict(STATE["models"])
    result["active_model"] = result["task_active_models"].get("text_to_ts", CPU_KEY)
    return result


def require_optional_models():
    if STATE["managed"] and STATE["mode"] == "off":
        raise RuntimeError("AI 끄기 모드에서는 추가 가중치를 로드하지 않습니다.")


def provider_allowed(provider):
    if not STATE["managed"]:
        return True
    if STATE["mode"] == "off":
        return provider == "builtin"
    if force_cpu():
        # 외부 API 는 이 장비의 GPU 를 쓰지 않으므로 CPU 모드에서도 된다
        return provider in {"builtin", "verbalts", "cpu_llm", "anthropic", "openai_compat"}
    return True


def force_cpu():
    return STATE["managed"] and (STATE["mode"] == "cpu" or (STATE["mode"] == "auto" and not STATE["gpu"]))
