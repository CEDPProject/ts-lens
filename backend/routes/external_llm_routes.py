"""Admin API for the external LLM connection (Data → 모델 연결). Keys never leave the server."""
from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from backend import external_llm

bp = Blueprint("external_llm", __name__)


@bp.route("/api/external_llm")
def api_get():
    return jsonify(settings=external_llm.public_view(),
                   providers=external_llm.PROVIDERS, tasks=external_llm.TASKS,
                   keep_secret=external_llm.KEEP_SECRET)


@bp.route("/api/external_llm", methods=["PUT"])
def api_put():
    body = request.get_json(force=True)
    try:
        view = external_llm.save(body)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, settings=view)


@bp.route("/api/external_llm", methods=["DELETE"])
def api_delete():
    external_llm.delete()
    return jsonify(ok=True, settings=external_llm.public_view())


@bp.route("/api/external_llm/test", methods=["POST"])
def api_test():
    """Round-trip a tiny prompt with the saved (or just-typed) settings."""
    body = request.get_json(silent=True) or {}
    data = external_llm.load()
    for k in ("provider", "model", "base_url"):
        if body.get(k):
            data[k] = str(body[k]).strip()
    if body.get("api_key") and body["api_key"] != external_llm.KEEP_SECRET:
        data["api_key"] = str(body["api_key"]).strip()
    if not data["api_key"] or not data["model"]:
        return jsonify(ok=False, error="모델 이름과 API 키가 있어야 시험할 수 있습니다"), 400
    from backend.llm_providers import call_external
    started = time.monotonic()
    try:
        text = call_external(data["provider"], [{"role": "user", "content": "Reply with the single word: ready"}],
                             external_llm.provider_cfg(data), system_prompt="", temperature=0.0, max_tokens=16)
    except Exception as e:  # 어떤 오류든 화면에 그대로 보여 준다 — 키·주소·모델 이름 문제가 대부분
        return jsonify(ok=False, error=str(e)[:400], seconds=round(time.monotonic() - started, 2))
    return jsonify(ok=True, reply=str(text).strip()[:80], seconds=round(time.monotonic() - started, 2))


# ─── 업로드 메타 추정 ───────────────────────────────────────────
# 업로드 미리보기(/api/upload 의 sid)에 담긴 DataFrame 을 요약해 외부 LLM 에 묻는다.
# 결과는 제안일 뿐이며 폼에 미리 채워진다. 관리자 경로 규칙(^/api/external_llm)에 걸려 관리자만 부른다.

@bp.route("/api/external_llm/infer_meta", methods=["POST"])
def api_infer_meta():
    import json
    import math
    import pandas as pd
    from backend.app_config import cfg, cfg_prompts
    from backend.csv_utils import guess_time_column
    from backend.i18n import get_lang
    from backend.llm_analysis import _extract_json
    from backend.llm_providers import call_external
    from backend.session_store import DATA_STORE

    if not external_llm.meta_inference_enabled():
        return jsonify(error="외부 LLM 메타 추정이 꺼져 있습니다 (데이터 관리 → 모델 연결)"), 400
    body = request.get_json(force=True)
    sids = body.get("sids") or ([body.get("sid")] if body.get("sid") else [])
    sids = [s for s in sids if isinstance(s, str) and s in DATA_STORE][:int(cfg("external_llm.meta_inference_max_files"))]
    if not sids:
        return jsonify(error="미리보기 세션을 찾을 수 없습니다. 파일을 다시 올려 주세요"), 404
    rows_n = int(cfg("external_llm.meta_inference_prompt_rows"))
    lang = (body.get("lang") or get_lang() or "ko")

    def _num(v):
        try:
            f = float(v)
            return None if math.isnan(f) or math.isinf(f) else round(f, 4)
        except (TypeError, ValueError):
            return None

    files = []
    for sid in sids[:5]:  # 컬렉션은 앞 다섯 파일만 보여 줘도 충분하다
        entry = DATA_STORE[sid]
        df = entry.get("df")
        if not isinstance(df, pd.DataFrame):
            continue
        time_col, index_type = guess_time_column(df)
        cols = []
        for c in df.columns:
            ser = df[c]
            info = {"name": str(c), "dtype": "numeric" if pd.api.types.is_numeric_dtype(ser) else "text",
                    "missing_ratio": round(float(ser.isna().mean()), 3)}
            if info["dtype"] == "numeric":
                d = ser.dropna()
                if len(d):
                    info.update(min=_num(d.min()), max=_num(d.max()), mean=_num(d.mean()), std=_num(d.std()),
                                negative_ratio=round(float((d < 0).mean()), 3))
            else:
                info["examples"] = [str(x) for x in ser.dropna().astype(str).unique()[:5]]
            cols.append(info)
        period = {}
        if time_col and index_type == "datetime":
            ts = pd.to_datetime(df[time_col], errors="coerce", dayfirst=bool(cfg("csv.time.dayfirst"))).dropna().sort_values()
            if len(ts) > 2:
                step = ts.diff().dropna().median()
                period = {"time_col": time_col, "start": str(ts.iloc[0]), "end": str(ts.iloc[-1]),
                          "median_step": str(step), "duplicate_timestamps": int(ts.duplicated().sum())}
        head = df.head(rows_n).astype(str).values.tolist()
        files.append({"filename": entry.get("filename", ""), "rows": int(len(df)), "columns": cols,
                      "time": period, "header": [str(c) for c in df.columns], "sample_rows": head})

    settings = external_llm.load()
    import logging
    logging.getLogger(__name__).info("infer_meta: %d file(s) → %s %s", len(files), settings["provider"], settings["model"])
    tags = [str(t) for t in (body.get("tags") or []) if t][:60]
    user = json.dumps({"lang": lang, "file_count": len(sids), "files": files, "available_tags": tags}, ensure_ascii=False)
    try:
        raw = call_external(settings["provider"], [{"role": "user", "content": user}],
                            external_llm.provider_cfg(settings),
                            system_prompt=cfg_prompts("dataset_meta_inference"), temperature=0.2,
                            max_tokens=int(cfg("external_llm.meta_inference_max_tokens")))
    except Exception as e:
        return jsonify(error=f"외부 LLM 호출 실패: {str(e)[:300]}"), 502
    # 바깥 JSON 이 아니라 안쪽 칸 조각({name,label,...})을 집는 일이 없도록 필수 키를 요구한다
    parsed = _extract_json(raw, require_keys=("dataset_name", "columns"))
    if not isinstance(parsed, dict):
        return jsonify(error="모델 응답을 JSON 으로 읽지 못했습니다", raw=str(raw)[:500]), 502
    # 칸 이름이 실제 칸과 맞는 것만 남긴다
    known = {c for f in files for c in f["header"]}
    parsed["columns"] = [c for c in (parsed.get("columns") or []) if isinstance(c, dict) and c.get("name") in known]
    return jsonify(ok=True, suggestion=parsed, model=settings["model"], provider=settings["provider"])
