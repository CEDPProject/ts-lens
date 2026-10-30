"""Client for the isolated, numeric VerbalTS generator."""
from __future__ import annotations

import math
import requests


def _required(config: dict, key: str):
    """설정값은 config/model/text_ts.yaml 이 단일 소스다. 없으면 조용히 기본값을
    쓰지 말고, 어디를 고쳐야 하는지 알려 준다."""
    value = config.get(key)
    if value is None:
        raise RuntimeError(
            f"VerbalTS 설정 '{key}' 이 없습니다 (config/model/text_ts.yaml · providers.verbalts)")
    return float(value)


def generate(description: str, length: int, config: dict) -> dict:
    base = str(config.get("base_url") or "").rstrip("/")
    if not base:
        # 이 함수는 실패를 예외로 알린다 (아래 분기들과 같은 규칙)
        raise RuntimeError(
            "VerbalTS base_url 이 설정되지 않았습니다 (config/model/text_ts.yaml)")
    try:
        response = requests.post(
            base + "/generate", json={"description": description, "length": length},
            timeout=(3, _required(config, "timeout")),
        )
        data = response.json()
        if 400 <= response.status_code < 500:
            # 서버가 입력을 거절했다(예: 영어가 아닌 문장). 사용자가 고칠 수 있으니 400 으로 돌려준다.
            raise ValueError(data.get("error", f"HTTP {response.status_code}"))
        if response.status_code != 200:
            raise RuntimeError(data.get("error", f"HTTP {response.status_code}"))
        values = data.get("values")
        if (not isinstance(values, list) or len(values) != length
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in values)):
            raise RuntimeError("VerbalTS returned an invalid numeric series")
        if data.get("engine") != "verbalts":
            raise RuntimeError("Unexpected generator response")
        return data
    except requests.RequestException as exc:
        raise RuntimeError("VerbalTS 서버에 연결할 수 없습니다. tools/models/serve_verbalts.py 실행 상태를 확인하세요.") from exc
