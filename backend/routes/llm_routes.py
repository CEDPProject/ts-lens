# -*- coding: utf-8 -*-
"""LLM integration routes -- pattern generation, model management, description."""
from __future__ import annotations

import math
from numbers import Real

from flask import Blueprint, request, jsonify

from backend.app_config import cfg_time_units, cfg_text_ts_raw
from backend.agents.interpreter import InterpreterAgent

bp = Blueprint("llm", __name__)


def _numeric_series_error(values, *, minimum: int = 3) -> str:
    """Return a client-facing validation error, or an empty string if valid."""
    if not isinstance(values, list):
        return "values must be a JSON array"
    if len(values) < minimum:
        return f"values must have at least {minimum} points"
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
            return "values must contain only finite numbers"
    return ""


@bp.route("/api/runtime")
def api_runtime():
    from backend.runtime_state import STATE
    return jsonify(STATE)


@bp.route("/api/llm_status")
def api_llm_status():
    """LLM availability check."""
    task = request.args.get("task", "").strip() or None
    return jsonify(InterpreterAgent.get_llm_status(task=task))


@bp.route("/api/generate_pattern", methods=["POST"])
def api_generate_pattern():
    """NL description -> synthetic pattern shape.

    Request JSON: {
        description, length, dataset_key, col_name, model_key,
        expand: bool (default false),  # true 면 paraphrase ensemble
        n_paraphrases: int (default 3)
    }
    Response (expand=false): {
        description, values, length, query, raw_response,
        engine, model, llm_used, fallback_reason
    }
    Response (expand=true ): { queries: [...], paraphrases: [...] }
        each queries[i] = { text, is_original, values, length, description, error }
    """
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON body must be an object"), 400
    description_value = body.get("description", "")
    if not isinstance(description_value, str):
        return jsonify(error="description must be a string"), 400
    description = description_value.strip()
    length = body.get("length", 24)
    dataset_key = body.get("dataset_key", "")
    col_name = body.get("col_name", "")
    model_key = body.get("model_key", "") or None
    expand_value = body.get("expand", False)
    if not isinstance(expand_value, bool):
        return jsonify(error="expand must be a boolean"), 400
    expand = expand_value
    try:
        n_paraphrases = int(body.get("n_paraphrases", 3) or 3)
    except (TypeError, ValueError):
        return jsonify(error="n_paraphrases must be an integer"), 400

    if not description:
        return jsonify(error="pattern description is required"), 400

    # Load length limits from purpose-based model config
    # 길이 한계는 config/model/text_ts.yaml 이 단일 소스다
    _llm_cfg = cfg_text_ts_raw()
    length_range = _llm_cfg.get("pattern_length_range")

    try:
        length = max(length_range[0], min(length_range[1], int(length)))
    except (ValueError, TypeError):
        length = _llm_cfg.get("default_pattern_length", 24)

    try:
        if expand:
            result = InterpreterAgent.generate_queries_from_text_ensemble(
                description, length=length,
                dataset_key=dataset_key, col_name=col_name,
                model_key=model_key,
                n_paraphrases=max(1, min(8, n_paraphrases)),
            )
        else:
            result = InterpreterAgent.generate_query_from_text(
                description, length=length,
                dataset_key=dataset_key, col_name=col_name,
                model_key=model_key,
            )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=str(e)), 500


@bp.route("/api/llm_models")
def api_llm_models():
    """Available LLM model list."""
    task = request.args.get("task", "").strip() or None
    return jsonify(models=InterpreterAgent.list_models(task=task))


@bp.route("/api/llm_models/activate", methods=["POST"])
def api_llm_models_activate():
    """Change active model."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON body must be an object"), 400
    key = body.get("model_key", "")
    task = (body.get("task", "") or "").strip() or None
    if not key:
        return jsonify(error="model_key required"), 400
    ok = InterpreterAgent.set_active_model(key, task=task)
    if not ok:
        return jsonify(error="unknown model"), 404
    return jsonify(ok=True, status=InterpreterAgent.get_llm_status(key, task=task))


@bp.route("/api/describe_segment", methods=["POST"])
def api_describe_segment():
    """Describe a time series segment in natural language."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON body must be an object"), 400
    col_name = body.get("col_name", "")
    values = body.get("values", [])
    timestamps = body.get("timestamps", [])
    dataset_key = body.get("dataset_key", "")
    unit = body.get("unit", "")
    model_key = body.get("model_key", "") or None

    values_error = _numeric_series_error(values)
    if values_error:
        return jsonify(error=values_error), 400

    tu = cfg_time_units()
    if unit and unit not in tu:
        return jsonify(error=f"unknown time unit: {unit}"), 400
    unit_label = tu.get(unit, {}).get("label", "") if unit else ""

    try:
        result = InterpreterAgent.describe_segment(
            values=values,
            col_name=col_name,
            unit_label=unit_label,
            timestamps=timestamps,
            dataset_key=dataset_key,
            model_key=model_key,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=str(e)), 500


@bp.route("/api/describe_segment_multi", methods=["POST"])
def api_describe_segment_multi():
    """Describe a multi-column segment in natural language."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON body must be an object"), 400
    columns = body.get("columns", [])
    timestamps = body.get("timestamps", [])
    dataset_key = body.get("dataset_key", "")
    unit = body.get("unit", "")
    model_key = body.get("model_key", "") or None

    if not isinstance(columns, list) or not columns:
        return jsonify(error="columns list is required"), 400
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            return jsonify(error=f"columns[{index}] must be an object"), 400
        values = column.get("values", [])
        # Empty columns are intentionally allowed and omitted by the service;
        # a populated column must still obey the numeric-series contract.
        if values:
            values_error = _numeric_series_error(values, minimum=1)
            if values_error:
                return jsonify(error=f"columns[{index}].{values_error}"), 400

    tu = cfg_time_units()
    if unit and unit not in tu:
        return jsonify(error=f"unknown time unit: {unit}"), 400
    unit_label = tu.get(unit, {}).get("label", "") if unit else ""

    try:
        result = InterpreterAgent.describe_segment_multi(
            columns_data=columns,
            unit_label=unit_label,
            timestamps=timestamps,
            dataset_key=dataset_key,
            model_key=model_key,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=str(e)), 500
