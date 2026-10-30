# -*- coding: utf-8 -*-
"""Segment search routes -- preset-based and LLM-based segment-level pattern matching."""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import OrderedDict
from queue import Queue, Empty, Full
import numpy as np
from flask import Blueprint, request, jsonify, Response, current_app

from backend.app_config import (cfg, cfg_time_units, cfg_clustering,
                                cfg_all_presets, cfg_search)
from backend.db import (get_db, get_dataset,
                        get_dataset_sources, get_source,
                        get_ts_column_all_sources, has_preprocessed_data,
                        get_ts_column_preprocessed_all_sources,
                        has_ground_truth, get_ground_truth_labels,
                        get_overlapping_labels)
from backend.helpers import z_normalize, _safe_list, _VALID_METHODS
from backend.segment_search import (
    segment_search_multi_source, classify_segments_by_presets,
    representative_segment_length,
)
from backend.segment_cluster import cluster_segments
from backend.dataset_service import format_sampling_minutes
from backend.agents.interpreter import InterpreterAgent
from backend.agents.retriever import RetrieverAgent

bp = Blueprint("segment_search", __name__)
logger = logging.getLogger(__name__)

# Progress queue manager for SSE
_progress_queues: dict[str, Queue] = {}
_progress_results: dict[str, dict] = {}
_progress_lock = threading.Lock()
_progress_finished: dict[str, float] = {}


def _publish_progress(job_id, message):
    with _progress_lock:
        queue = _progress_queues.get(job_id)
        if queue is None:
            return
        # Keep recent progress, always make room for a terminal message.
        try:
            queue.put_nowait(message)
        except Full:
            try:
                queue.get_nowait()
            except Empty:
                pass
            queue.put_nowait(message)


@bp.errorhandler(ValueError)
def invalid_cluster_input(error):
    return jsonify(error=str(error)), 400


@bp.route("/api/segment_search/presets")
def api_segment_presets():
    """Return available preset shapes for UI."""
    dataset_id = request.args.get("dataset_id", "")
    tu = cfg_time_units()
    return jsonify(presets=InterpreterAgent.list_available_presets(dataset_id), time_units=[
        {"key": k, "label": v["label"], "minutes": v["minutes"]}
        for k, v in tu.items()
    ])


@bp.route("/api/segment_search", methods=["POST"])
def api_segment_search():
    """Segment-level pattern search.

    Request JSON:
    {
        "dataset_key": str,
        "col_name": str,
        "unit": str,
        "query_type": str,      # "preset"|"custom"|"llm"
        "preset_name": str,
        "query_values": [float],
        "nl_description": str,
        "method": str,
        "top_k": int,
        "threshold": float,
        "use_preprocessed": bool,
        "model_key": str,
        "overlay_cols": [str],
        "overlay_presets": {"col_name": "preset_name"},  # per-column preset
    }
    """
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON object required"), 400
    dataset_key = body.get("dataset_key", "")
    col_name = body.get("col_name", "")
    unit = body.get("unit", "day")
    query_type = body.get("query_type", "preset")
    method = body.get("method", cfg_search("default_method"))
    top_k = body.get("top_k", cfg_search("segment_top_k"))
    threshold = body.get("threshold", 0.0)
    use_pp = body.get("use_preprocessed", False)
    overlay_cols = body.get("overlay_cols", [])
    inline_segments = body.get("inline_segments", None)

    try:
        top_k = max(1, min(10000, int(top_k)))
        threshold = float(threshold)
    except (TypeError, ValueError):
        return jsonify(error="Invalid numeric search option"), 400
    if not math.isfinite(threshold):
        return jsonify(error="Search options must be finite"), 400
    if not isinstance(overlay_cols, list):
        return jsonify(error="overlay_cols must be an array"), 400

    if method not in _VALID_METHODS:
        return jsonify(error=f"Unsupported method: {method}"), 400

    if not dataset_key or not col_name:
        return jsonify(error="dataset_key and col_name are required"), 400

    tu = cfg_time_units()
    if unit not in tu:
        return jsonify(error=f"Unknown time unit: {unit}. "
                       f"Valid: {list(tu.keys())}"), 400

    # Use inline segments if provided (split mode)
    if inline_segments:
        sources_data = [
            {"source_key": seg.get("source_key") or dataset_key,
             "segment_key": seg.get("key", f"seg_{i}"),
             "label": seg.get("label", ""),
             "source_label": seg.get("source_label", ""),
             "timestamps": seg.get("timestamps", []),
             "values": seg.get("values", [])}
            for i, seg in enumerate(inline_segments) if seg.get("values")
        ]
        conn = None  # No DB connection needed for inline segments
    else:
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            if conn:
                conn.close()
            return jsonify(error="Dataset not found"), 404

        if use_pp and has_preprocessed_data(conn, ds["id"]):
            sources_data = get_ts_column_preprocessed_all_sources(conn, ds["id"], col_name)
        else:
            sources_data = get_ts_column_all_sources(conn, ds["id"], col_name)

    if not sources_data:
        if conn:
            conn.close()
        return jsonify(error=f"No data found for column '{col_name}'"), 404

    _is_inline = bool(inline_segments)
    if _is_inline:
        # Pre-segmented: derive seg_length from actual segment size
        seg_length = max(3, representative_segment_length(sources_data))
        first_ts = sources_data[0].get("timestamps") or []
        sampling_min = RetrieverAgent.detect_sampling(first_ts) if first_ts else 60
    else:
        first_ts = sources_data[0]["timestamps"]
        sampling_min = RetrieverAgent.detect_sampling(first_ts)
        seg_length = RetrieverAgent.get_segment_length(unit, sampling_min)

    llm_info = None
    if query_type == "preset":
        preset_name = body.get("preset_name", "bell")
        all_presets = cfg_all_presets(dataset_key)
        if preset_name not in all_presets:
            if conn:
                conn.close()
            return jsonify(error=f"Unknown preset: {preset_name}"), 400

        # Check if it's a custom preset (has 'values' key) or built-in (has 'formula' key)
        preset_def = all_presets[preset_name]
        if "values" in preset_def:
            # Custom preset: use stored values directly, resample to segment length
            raw_vals = preset_def["values"]
            query_shape = InterpreterAgent.generate_query_from_example(
                raw_vals, target_length=seg_length)
        else:
            # Built-in preset: generate from formula
            query_shape = InterpreterAgent.generate_query_from_preset(
                preset_name, seg_length)

    elif query_type == "custom":
        raw_vals = body.get("query_values", [])
        if not isinstance(raw_vals, list) or len(raw_vals) < 3:
            if conn:
                conn.close()
            return jsonify(error="query_values must have at least 3 points"), 400
        try:
            raw_arr = np.asarray(raw_vals, dtype=np.float64)
        except (TypeError, ValueError):
            if conn:
                conn.close()
            return jsonify(error="query_values must be numeric"), 400
        if raw_arr.ndim != 1 or not np.isfinite(raw_arr).all():
            if conn:
                conn.close()
            return jsonify(error="query_values must be finite"), 400
        query_shape = InterpreterAgent.generate_query_from_example(
            raw_arr, target_length=seg_length)

    elif query_type == "llm":
        nl_desc = body.get("nl_description", "").strip()
        if not nl_desc:
            if conn:
                conn.close()
            return jsonify(error="nl_description is required for query_type='llm'"), 400

        model_key = body.get("model_key", "") or None
        try:
            llm_result = InterpreterAgent.generate_query_from_text(
                nl_desc, length=seg_length,
                dataset_key=dataset_key, col_name=col_name,
                model_key=model_key,
            )
            query_shape = np.array(llm_result["values"], dtype=np.float64)
            query_shape = z_normalize(query_shape)
            llm_info = {
                "description": llm_result.get("description", ""),
                "raw_response": llm_result.get("raw_response", ""),
                "model_key": model_key or "",
                "engine": llm_result.get("engine", ""),
                "model": llm_result.get("model"),
                "llm_used": bool(llm_result.get("llm_used", False)),
                "fallback_reason": llm_result.get("fallback_reason"),
            }
        except Exception as e:
            if conn:
                conn.close()
            return jsonify(error=f"LLM shape generation failed: {e}"), 500
    else:
        if conn:
            conn.close()
        return jsonify(error=f"Unknown query_type: {query_type}"), 400

    # ── Overlay columns for multi-column search ──
    overlay_sources_list = []
    overlay_query_shapes = []
    matched_overlay_cols = []
    overlay_presets = body.get("overlay_presets", {})  # {col_name: preset_name}
    if overlay_cols:
        catalog = cfg_all_presets(dataset_key)
        if inline_segments:
            # Split/inline segments already carry their aligned overlay values.
            # Preserve that alignment instead of silently degrading to primary-only.
            for ov_col in overlay_cols:
                ov_sources = []
                for idx, seg in enumerate(inline_segments):
                    overlay = next((ov for ov in seg.get("overlays", [])
                                    if ov.get("col_name") == ov_col), None)
                    if not overlay or not overlay.get("values"):
                        ov_sources = []
                        break
                    ov_sources.append({
                        "source_key": seg.get("source_key") or dataset_key,
                        "segment_key": seg.get("key", f"seg_{idx}"),
                        "timestamps": overlay.get("timestamps") or seg.get("timestamps", []),
                        "values": overlay.get("values", []),
                    })
                if not ov_sources:
                    continue
                overlay_sources_list.append(ov_sources)
                matched_overlay_cols.append(ov_col)
                ov_preset = overlay_presets.get(ov_col, "")
                preset_def = catalog.get(ov_preset) if ov_preset else None
                if preset_def and "values" in preset_def:
                    overlay_query_shapes.append(
                        InterpreterAgent.generate_query_from_example(
                            preset_def["values"], target_length=seg_length))
                elif ov_preset and preset_def:
                    overlay_query_shapes.append(
                        InterpreterAgent.generate_query_from_preset(
                            ov_preset, seg_length))
                else:
                    overlay_query_shapes.append(query_shape)
        elif conn:
            for ov_col in overlay_cols:
                if use_pp and has_preprocessed_data(conn, ds["id"]):
                    ov_data = get_ts_column_preprocessed_all_sources(
                        conn, ds["id"], ov_col)
                else:
                    ov_data = get_ts_column_all_sources(conn, ds["id"], ov_col)
                if ov_data:
                    overlay_sources_list.append(ov_data)
                    matched_overlay_cols.append(ov_col)
                    # Per-column preset if specified, otherwise use primary shape
                    ov_preset = overlay_presets.get(ov_col, "")
                    if ov_preset and ov_preset in catalog:
                        preset_def = catalog[ov_preset]
                        if "values" in preset_def:
                            ov_shape = InterpreterAgent.generate_query_from_example(
                                preset_def["values"], target_length=seg_length)
                        else:
                            ov_shape = InterpreterAgent.generate_query_from_preset(
                                ov_preset, seg_length)
                        overlay_query_shapes.append(ov_shape)
                    else:
                        overlay_query_shapes.append(query_shape)

    if conn:
        conn.close()

    result = segment_search_multi_source(
        sources_data, query_shape,
        unit=unit, method=method, top_k=top_k,
        threshold=threshold, normalize=True,
        overlay_sources_list=overlay_sources_list or None,
        overlay_query_shapes=overlay_query_shapes or None,
        pre_segmented=_is_inline,
    )

    result["query"] = {
        "type": query_type,
        "preset_name": body.get("preset_name", "") if query_type == "preset" else "",
        "shape": _safe_list(query_shape),
        "length": len(query_shape),
        "unit": unit,
        "unit_label": tu.get(unit, {}).get("label", unit),
        "sampling_minutes": round(sampling_min, 2),
    }
    # Include overlay query shapes and column names
    if overlay_query_shapes:
        result["query"]["overlay_shapes"] = [
            _safe_list(s) for s in overlay_query_shapes
        ]
    if matched_overlay_cols:
        result["overlay_names"] = matched_overlay_cols
        result["query"]["overlay_presets"] = {
            col: overlay_presets.get(col, body.get("preset_name", ""))
            for col in matched_overlay_cols
        }
    result["columns_used"] = 1 + len(matched_overlay_cols)
    result["overlay_cols_requested"] = len(overlay_cols)
    result["overlay_cols_matched"] = len(matched_overlay_cols)
    result["missing_overlay_cols"] = [
        col for col in overlay_cols if col not in matched_overlay_cols
    ]
    if llm_info:
        result["query"]["llm"] = llm_info

    # ── Ground truth overlap check (skip for inline segments) ──
    if not inline_segments:
        conn2 = get_db()
        ds2 = get_dataset(conn2, dataset_key)
    else:
        conn2 = None
        ds2 = None

    if conn2 and ds2 and has_ground_truth(conn2, ds2["id"]):
        # Source key -> source_id cache
        _src_cache = {}
        for r in result.get("results", []):
            sk = r.get("source_key", "")
            if sk not in _src_cache:
                src = get_source(conn2, sk)
                _src_cache[sk] = src["id"] if src else None
            sid = _src_cache[sk]
            if sid:
                overlaps = get_overlapping_labels(conn2, sid,
                                                   r["start_ts"], r["end_ts"])
                r["ground_truth"] = {
                    "hit": len(overlaps) > 0,
                    "labels": [{"label_type": o["label_type"],
                                "start_ts": o["start_ts"],
                                "end_ts": o["end_ts"]}
                               for o in overlaps],
                }
            else:
                r["ground_truth"] = {"hit": False, "labels": []}

        # Precision / Recall summary
        results_list = result.get("results", [])
        hits = sum(1 for r in results_list
                   if r.get("ground_truth", {}).get("hit"))
        total_r = len(results_list)

        all_gt = get_ground_truth_labels(conn2, ds2["id"],
                                          label_type="anomaly")
        detected = set()
        for r in results_list:
            for gt in r.get("ground_truth", {}).get("labels", []):
                detected.add((gt["start_ts"], gt["end_ts"]))

        result["ground_truth_summary"] = {
            "precision": round(hits / total_r, 4) if total_r else 0,
            "recall": round(len(detected) / len(all_gt), 4) if all_gt else 0,
            "hits": hits,
            "total_results": total_r,
            "total_labels": len(all_gt),
            "detected_labels": len(detected),
        }
    if conn2:
        conn2.close()

    return jsonify(result)


@bp.route("/api/segment_search/time_info", methods=["POST"])
def api_segment_time_info():
    """Return time unit -> segment length mapping for a dataset."""
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")

    if not dataset_key:
        return jsonify(error="dataset_key required"), 400

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="Dataset not found"), 404

    sources = get_dataset_sources(conn, ds["id"])
    if not sources:
        conn.close()
        return jsonify(error="No sources in dataset"), 404

    src = sources[0]
    rows = conn.execute(
        "SELECT timestamp FROM ts_data "
        "WHERE source_id = ? ORDER BY timestamp LIMIT 100",
        (src["id"],),
    ).fetchall()
    conn.close()

    ts_list = [r["timestamp"] for r in rows]
    sampling_min = RetrieverAgent.detect_sampling(ts_list)

    units_info = []
    for key, info in cfg_time_units().items():
        length = RetrieverAgent.get_segment_length(key, sampling_min)
        units_info.append({
            "key": key,
            "label": info["label"],
            "minutes": info["minutes"],
            "segment_length": length,
        })

    return jsonify(
        sampling_minutes=round(sampling_min, 2),
        sampling_label=format_sampling_minutes(sampling_min),
        units=units_info,
    )


@bp.route("/api/segment_search/classify", methods=["POST"])
def api_segment_classify():
    """Classify all segments by their nearest preset shape.

    Request JSON:
    {
        "dataset_key": str,
        "col_name": str,
        "unit": str,
        "method": str,
        "use_preprocessed": bool,
        "inline_segments": list (optional) - for split mode
    }
    """
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    col_name = body.get("col_name", "")
    overlay_cols = body.get("overlay_cols", [])
    unit = body.get("unit", "day")
    method = body.get("method", cfg_search("default_method"))
    if method not in _VALID_METHODS:
        return jsonify(error=f"Unsupported method: {method}"), 400
    use_pp = body.get("use_preprocessed", False)
    inline_segments = body.get("inline_segments", None)

    if not dataset_key or not col_name:
        return jsonify(error="dataset_key and col_name are required"), 400

    tu = cfg_time_units()
    if unit not in tu:
        return jsonify(error=f"Unknown time unit: {unit}"), 400

    # Use inline segments if provided (split mode)
    if inline_segments:
        sources_data = [
            {"source_key": seg.get("source_key") or seg.get("key", f"seg_{i}"),
             "segment_key": seg.get("key", f"seg_{i}"),
             "timestamps": seg.get("timestamps", []),
             "values": seg.get("values", [])}
            for i, seg in enumerate(inline_segments) if seg.get("values")
        ]
        conn = None
        ds = {"id": None, "type": "collection"}  # Mock dataset for inline mode
    else:
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            if conn:
                conn.close()
            return jsonify(error="Dataset not found"), 404

        # Classify primary column from DB
        if use_pp and has_preprocessed_data(conn, ds["id"]):
            sources_data = get_ts_column_preprocessed_all_sources(conn, ds["id"], col_name)
        else:
            sources_data = get_ts_column_all_sources(conn, ds["id"], col_name)

    _is_inline = bool(inline_segments)

    if not sources_data:
        if conn:
            conn.close()
        return jsonify(error=f"No data found for column '{col_name}'"), 404

    result = classify_segments_by_presets(
        sources_data, unit=unit, method=method,
        pre_segmented=_is_inline,
        dataset_id=dataset_key,
    )
    result["unit"] = unit
    result["unit_label"] = tu.get(unit, {}).get("label", unit)
    result["method"] = method
    result["primary_col"] = col_name

    # Debug log
    gs = result.get("group_summary", {})
    logger.debug(f"Classify method={method} unit={unit} col={col_name} "
                 f"segments={result.get('segments_total')} unclassified={result.get('unclassified')} "
                 f"min_sim={result.get('min_similarity')}")
    for gk, gv in gs.items():
        logger.debug(f"  {gk}: count={gv.get('count')} avg_sim={gv.get('avg_sim')}")

    # Classify overlay columns independently. Inline split data already carries
    # aligned overlay arrays and must produce the same per-column tab contract.
    if overlay_cols:
        col_results = {col_name: {
            "groups": result.get("groups", {}),
            "group_summary": result.get("group_summary", {}),
            "segments_total": result.get("segments_total", 0),
            "unclassified": result.get("unclassified", 0),
        }}
        for ov_col in overlay_cols:
            if inline_segments:
                ov_data = []
                for i, seg in enumerate(inline_segments):
                    overlay = next((ov for ov in seg.get("overlays", [])
                                    if ov.get("col_name") == ov_col), None)
                    if overlay and overlay.get("values"):
                        ov_data.append({
                            "source_key": seg.get("source_key") or dataset_key,
                            "segment_key": seg.get("key", f"seg_{i}"),
                            "timestamps": overlay.get("timestamps") or seg.get("timestamps", []),
                            "values": overlay.get("values", []),
                        })
            elif use_pp and has_preprocessed_data(conn, ds["id"]):
                ov_data = get_ts_column_preprocessed_all_sources(conn, ds["id"], ov_col)
            else:
                ov_data = get_ts_column_all_sources(conn, ds["id"], ov_col)
            if ov_data:
                ov_result = classify_segments_by_presets(
                    ov_data, unit=unit, method=method,
                    pre_segmented=_is_inline,
                    dataset_id=dataset_key,
                )
                col_results[ov_col] = {
                    "groups": ov_result.get("groups", {}),
                    "group_summary": ov_result.get("group_summary", {}),
                    "segments_total": ov_result.get("segments_total", 0),
                    "unclassified": ov_result.get("unclassified", 0),
                }
        result["column_results"] = col_results
        result["columns_requested"] = [col_name] + overlay_cols
        result["columns_classified"] = list(col_results.keys())
        result["missing_columns"] = [
            col for col in overlay_cols if col not in col_results
        ]

    if conn:
        conn.close()
    return jsonify(result)


@bp.route("/api/segment_search/cluster_config")
def api_cluster_config():
    """Return clustering config defaults for frontend initialization."""
    cc = cfg_clustering()
    return jsonify(cc)


# ── Cluster data cache (server-side, keyed by dataset+columns) ──
_MAX_CLUSTER_CACHE = cfg("cache.cluster_max_entries")
_cluster_cache: OrderedDict = OrderedDict()
_cluster_cache_lock = threading.Lock()


def purge_cluster_cache(dataset_key: str) -> int:
    """지운 데이터셋의 군집 준비 배열을 메모리에서 뺀다."""
    with _cluster_cache_lock:
        stale = [k for k, v in _cluster_cache.items() if v.get("dataset_key") == dataset_key]
        for k in stale:
            del _cluster_cache[k]
    return len(stale)


def _make_cache_key(dataset_key: str, col_name: str,
                    overlay_cols: list[str] | None = None) -> str:
    # Snapshot ownership: tabs using different preprocessing or column order
    # must not overwrite each other's prepared arrays.
    return uuid.uuid4().hex


def _filter_sources_by_date(sources_data: list[dict],
                            date_start: str | None,
                            date_end: str | None) -> list[dict]:
    """Filter timestamps/values within [date_start, date_end]."""
    if not date_start and not date_end:
        return sources_data
    filtered = []
    for src in sources_data:
        ts = src.get("timestamps", [])
        vals = src.get("values", [])
        pairs = list(zip(ts, vals))
        if date_start:
            pairs = [(t, v) for t, v in pairs if t >= date_start]
        if date_end:
            pairs = [(t, v) for t, v in pairs if t <= date_end]
        if pairs:
            fts, fvs = zip(*pairs)
            filtered.append({**src, "timestamps": list(fts),
                             "values": list(fvs)})
    return filtered


@bp.route("/api/segment_search/prepare_cluster", methods=["POST"])
def api_prepare_cluster():
    """Load data once and cache for repeated clustering.

    Returns cache_key + data range info so frontend can show hints
    and reuse cached data for parameter changes.
    """
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    col_name = body.get("col_name", "")
    use_pp = body.get("use_preprocessed", False)
    overlay_cols = body.get("overlay_cols", [])

    if not dataset_key or not col_name:
        return jsonify(error="dataset_key and col_name are required"), 400

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="Dataset not found"), 404

    if use_pp and has_preprocessed_data(conn, ds["id"]):
        sources_data = get_ts_column_preprocessed_all_sources(
            conn, ds["id"], col_name)
    else:
        sources_data = get_ts_column_all_sources(conn, ds["id"], col_name)

    overlay_sources_list = []
    if overlay_cols:
        for ov_col in overlay_cols:
            if use_pp and has_preprocessed_data(conn, ds["id"]):
                ov_data = get_ts_column_preprocessed_all_sources(
                    conn, ds["id"], ov_col)
            else:
                ov_data = get_ts_column_all_sources(conn, ds["id"], ov_col)
            overlay_sources_list.append(ov_data)

    conn.close()

    if not sources_data:
        return jsonify(error=f"No data found for column '{col_name}'"), 404

    # Compute date range + sampling info
    all_ts = []
    total_points = 0
    for src in sources_data:
        ts = src.get("timestamps", [])
        all_ts.extend(ts)
        total_points += len(ts)
    all_ts.sort()

    sampling_min = RetrieverAgent.detect_sampling(all_ts) if all_ts else 60

    cache_key = _make_cache_key(dataset_key, col_name, overlay_cols)
    with _cluster_cache_lock:
        _cluster_cache[cache_key] = {
            "dataset_key": dataset_key,
            "sources_data": sources_data,
            "overlay_sources_list": overlay_sources_list,
        }
        while len(_cluster_cache) > _MAX_CLUSTER_CACHE:
            _cluster_cache.popitem(last=False)

    return jsonify(
        cache_key=cache_key,
        date_start=all_ts[0] if all_ts else "",
        date_end=all_ts[-1] if all_ts else "",
        total_points=total_points,
        sampling_minutes=round(sampling_min, 2),
        sampling_label=format_sampling_minutes(sampling_min),
    )


@bp.route("/api/segment_search/cluster", methods=["POST"])
def api_segment_cluster():
    return _cluster_request(request.get_json(force=True))


def _cluster_request(body, progress_cb=None):
    """Shared synchronous/streaming data selection and clustering contract."""
    cc = cfg_clustering()

    use_existing = body.get("use_existing_segments", False)
    unit = None if use_existing else body.get("unit", "day")
    profile_unit = body.get("profile_unit", None)  # for inline segs: split profile cols
    method = body.get("method", cc.get("default_method", "euclidean"))
    algorithm = body.get("algorithm", cc.get("default_algorithm", "kmeans"))
    overlay_cols = body.get("overlay_cols", [])
    profile_cols = body.get("profile_cols", [])

    km_cfg = cc.get("kmeans", {})
    k = body.get("k", None)
    k_min = body.get("k_min", km_cfg.get("k_min", 2))
    k_max = body.get("k_max", km_cfg.get("k_max", 15))
    auto_k_strategy = body.get("auto_k_strategy", km_cfg.get("auto_k_strategy", "elbow"))

    normalize = body.get("normalize", cc.get("normalize", True))
    nan_mode = body.get("nan_mode", "linear")
    nan_max_ratio = body.get("nan_max_ratio", 0.5)

    date_start = body.get("date_start", None)
    date_end = body.get("date_end", None)

    tu = cfg_time_units()
    if unit is not None and unit not in tu:
        return jsonify(error=f"Unknown time unit: {unit}"), 400

    # Try cache first
    cache_key = body.get("cache_key", "")
    with _cluster_cache_lock:
        cached = _cluster_cache.get(cache_key) if cache_key else None

    if cached:
        sources_data = cached["sources_data"]
        overlay_sources_list = cached["overlay_sources_list"]
    else:
        # Fallback: load from DB
        dataset_key = body.get("dataset_key", "")
        col_name = body.get("col_name", "")
        use_pp = body.get("use_preprocessed", False)

        if not dataset_key or not col_name:
            return jsonify(error="dataset_key/col_name or cache_key required"), 400

        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            if conn:
                conn.close()
            return jsonify(error="Dataset not found"), 404

        if use_pp and has_preprocessed_data(conn, ds["id"]):
            sources_data = get_ts_column_preprocessed_all_sources(
                conn, ds["id"], col_name)
        else:
            sources_data = get_ts_column_all_sources(conn, ds["id"], col_name)

        overlay_sources_list = []
        if overlay_cols:
            for ov_col in overlay_cols:
                if use_pp and has_preprocessed_data(conn, ds["id"]):
                    ov_data = get_ts_column_preprocessed_all_sources(
                        conn, ds["id"], ov_col)
                else:
                    ov_data = get_ts_column_all_sources(conn, ds["id"], ov_col)
                overlay_sources_list.append(ov_data)
        conn.close()

    # Inline segments override: split-mode sends segment values directly
    inline_segments = body.get("inline_segments", None)
    _is_inline = False
    if inline_segments and use_existing:
        sources_data = [
            {"source_key": seg.get("source_key") or seg.get("key", f"seg_{i}"),
             "segment_key": seg.get("key", f"seg_{i}"),
             "timestamps": seg.get("timestamps", seg.get("labels", [])),
             "values": seg["values"]}
            for i, seg in enumerate(inline_segments) if seg.get("values")
        ]
        # Reconstruct overlay sources from inline segment overlay data (joint clustering)
        if overlay_cols:
            overlay_sources_list = []
            for ov_col in overlay_cols:
                ov_sources = []
                for i, seg in enumerate(inline_segments):
                    seg_overlays = seg.get("overlays", {})
                    if ov_col in seg_overlays:
                        ov_sources.append({
                            "source_key": seg.get("source_key") or seg.get("key", f"seg_{i}"),
                            "segment_key": seg.get("key", f"seg_{i}"),
                            "timestamps": [],
                            "values": seg_overlays[ov_col]
                        })
                overlay_sources_list.append(ov_sources)
        else:
            overlay_sources_list = []
        _is_inline = True

    if not sources_data:
        return jsonify(error="No data found"), 404

    # Apply date range filter (skip for inline segments — already filtered)
    if (date_start or date_end) and not _is_inline:
        sources_data = _filter_sources_by_date(sources_data, date_start, date_end)
        if overlay_sources_list:
            overlay_sources_list = [
                _filter_sources_by_date(ov, date_start, date_end)
                for ov in overlay_sources_list
            ]

    if not sources_data:
        return jsonify(error="No data in selected date range"), 404

    # Load profile columns (for auto-profile: avg shape of other cols per cluster)
    # Primary column reuses already-loaded sources_data (no extra DB query)
    col_name = body.get("col_name", "")
    profile_sources_list = None
    if profile_cols:
        profile_sources_list = []
        need_db = [pc for pc in profile_cols if pc != col_name]
        # Primary column first — reuse already-loaded data (works for all
        # modes: inline segments have timestamps=[] which cluster_segments
        # handles via the no-timestamps branch)
        if col_name in profile_cols:
            profile_sources_list.append((col_name, sources_data))
        if need_db:
            conn2 = get_db()
            ds2 = get_dataset(conn2, body.get("dataset_key", ""))
            if ds2:
                use_pp = body.get("use_preprocessed", False)
                for pc in need_db:
                    if use_pp and has_preprocessed_data(conn2, ds2["id"]):
                        pc_data = get_ts_column_preprocessed_all_sources(conn2, ds2["id"], pc)
                    else:
                        pc_data = get_ts_column_all_sources(conn2, ds2["id"], pc)
                    if pc_data:
                        if date_start or date_end:
                            pc_data = _filter_sources_by_date(pc_data, date_start, date_end)
                        profile_sources_list.append((pc, pc_data))
            conn2.close()

    result = cluster_segments(
        sources_data, unit=unit, method=method,
        algorithm=algorithm,
        k=k, k_min=k_min, k_max=k_max,
        auto_k_strategy=auto_k_strategy,
        normalize=normalize,
        nan_mode=nan_mode, nan_max_ratio=nan_max_ratio,
        overlay_sources_list=overlay_sources_list or None,
        profile_sources_list=profile_sources_list,
        profile_unit=profile_unit,
        progress_cb=progress_cb,
    )
    result["unit"] = unit or "segment"
    result["unit_label"] = tu.get(unit, {}).get("label", "Segment") if unit else "Segment"
    result["method"] = method

    # Debug log
    gs = result.get("group_summary", {})
    logger.debug(f"Cluster method={method} algo={algorithm} k={result.get('k')} "
                 f"normalize={normalize} unit={unit} segments={result.get('segments_total')} "
                 f"silhouette={result.get('silhouette_score')}")
    for gk, gv in gs.items():
        logger.debug(f"  {gk}: count={gv.get('count')} avg_sim={gv.get('avg_sim')}")

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────
# SSE-based clustering with progress
# ─────────────────────────────────────────────────────────────────

@bp.route("/api/segment_search/segment_cluster_stream", methods=["POST"])
def api_segment_cluster_stream_start():
    """Start a bounded cluster job and return a job_id for SSE streaming."""
    body = request.get_json(force=True)
    job_id = uuid.uuid4().hex
    app = current_app._get_current_object()
    with _progress_lock:
        now = time.monotonic()
        for old_id, finished in list(_progress_finished.items()):
            if now - finished > cfg("clustering.jobs.retention_seconds"):
                _progress_queues.pop(old_id, None)
                _progress_results.pop(old_id, None)
                _progress_finished.pop(old_id, None)
        active = len(_progress_queues) - len(_progress_finished)
        if active >= cfg("clustering.jobs.max_active"):
            return jsonify(error="Clustering capacity reached; wait for an active job"), 429
        # Bound completed results even if the browser never subscribes.
        while len(_progress_queues) >= cfg("clustering.jobs.max_retained") and _progress_finished:
            old_id = min(_progress_finished, key=_progress_finished.get)
            _progress_queues.pop(old_id, None)
            _progress_results.pop(old_id, None)
            _progress_finished.pop(old_id, None)
        _progress_queues[job_id] = Queue(maxsize=cfg("clustering.jobs.max_messages"))

    def run_cluster():
        try:
            with app.app_context():
                result = _run_cluster_with_progress(body, job_id)
            _publish_progress(job_id, ("done", result))
        except Exception as error:
            logger.exception("Cluster job %s failed", job_id)
            _publish_progress(job_id, ("error", str(error)))
        finally:
            with _progress_lock:
                if job_id in _progress_queues:
                    _progress_finished[job_id] = time.monotonic()

    thread = threading.Thread(target=run_cluster, daemon=True)
    try:
        thread.start()
    except Exception:
        with _progress_lock:
            _progress_queues.pop(job_id, None)
        raise
    return jsonify({"job_id": job_id})


@bp.route("/api/segment_search/segment_cluster_progress/<job_id>")
def api_segment_cluster_progress(job_id):
    """SSE uses Flask's JSON provider so non-finite numbers remain valid JSON."""
    dumps = current_app.json.dumps
    with _progress_lock:
        queue = _progress_queues.get(job_id)
    if queue is None:
        return jsonify(error="Invalid or expired job_id"), 404

    def generate():
        try:
            while True:
                try:
                    kind, value = queue.get(timeout=15)
                    payload = ({"type": "done", "result": value} if kind == "done"
                               else {"type": kind, "message": value})
                    yield "data: " + dumps(payload) + "\n\n"
                    if kind in {"done", "error"}:
                        break
                except Empty:
                    yield "data: " + dumps({"type": "heartbeat"}) + "\n\n"
        finally:
            # Keep a disconnected active job counted until it finishes.
            with _progress_lock:
                if job_id in _progress_finished:
                    _progress_queues.pop(job_id, None)
                    _progress_results.pop(job_id, None)
                    _progress_finished.pop(job_id, None)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run_cluster_with_progress(body: dict, job_id: str) -> dict:
    response = current_app.make_response(_cluster_request(
        body, progress_cb=lambda message: _publish_progress(job_id, ("progress", message))))
    result = response.get_json()
    if response.status_code >= 400:
        raise ValueError(result.get("error", "Clustering failed"))
    return result


@bp.route("/api/evaluate_cluster", methods=["POST"])
def api_evaluate_cluster():
    """LLM으로 클러스터의 의미/품질 평가 (AI Agent Discovery용)"""
    from backend.llm_service import call_llm
    import json

    data = request.json or {}
    description = data.get("description", "")
    shape = data.get("shape", "")
    cluster_size = data.get("size", 0)
    total_segments = data.get("total_segments", 0)

    # 클러스터 비율 계산
    ratio = round(cluster_size / total_segments * 100, 1) if total_segments > 0 else 0

    prompt = f"""다음 시계열 패턴 클러스터를 평가해주세요.

패턴 형태: {shape}
패턴 설명: {description}
클러스터 크기: {cluster_size}개 세그먼트 (전체의 {ratio}%)

평가 기준:
1. 이 패턴이 실제로 의미 있는 패턴인가? (노이즈나 잡음이 아닌가)
2. 라벨을 붙일 가치가 있는가?
3. 패턴이 명확하고 일관성이 있는가?

반드시 아래 JSON 형식으로만 응답하세요:
{{"meaningful": true 또는 false, "confidence": 0-100 사이 정수, "reason": "판단 이유", "suggested_label": "추천 라벨명"}}"""

    try:
        response = call_llm(prompt, task="evaluate")

        # JSON 파싱 시도
        try:
            # JSON 부분만 추출
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                result = json.loads(json_str)
                return jsonify(result)
        except json.JSONDecodeError:
            pass

        # 파싱 실패시 기본값 반환
        return jsonify({
            "meaningful": cluster_size >= 3,
            "confidence": 50,
            "reason": "LLM 응답 파싱 실패, 기본 평가 적용",
            "suggested_label": shape if shape and shape != "Unknown" else f"Pattern {cluster_size}",
            "raw_response": response
        })

    except Exception as e:
        # LLM 실패 시 클러스터 크기 기반 기본 평가
        # 클러스터가 클수록 신뢰도 높음
        logger.warning("Cluster LLM evaluation failed, using size-based fallback: %s", e)
        base_confidence = 50
        if ratio >= 20:  # 전체의 20% 이상
            base_confidence = 80
        elif ratio >= 10:  # 전체의 10% 이상
            base_confidence = 70
        elif ratio >= 5:  # 전체의 5% 이상
            base_confidence = 60
        elif cluster_size >= 10:
            base_confidence = 55

        return jsonify({
            "meaningful": cluster_size >= 5,
            "confidence": base_confidence,
            "reason": "클러스터 크기 기반 자동 평가",
            "suggested_label": shape if shape and shape != "Unknown" else f"Cluster ({cluster_size}개)",
            "fallback": True
        })


# ─────────────────────────────────────────────────────────────────
# MOMENT Foundation Model APIs
# ─────────────────────────────────────────────────────────────────

@bp.route("/api/moment/analyze_pattern", methods=["POST"])
def api_moment_analyze_pattern():
    """Analyze a single pattern using MOMENT."""
    try:
        from backend import moment_service

        data = request.json or {}
        values = data.get("values", [])

        if not values or len(values) < 8:
            return jsonify({
                "error": "At least 8 data points required",
                "shape": "데이터 부족",
                "description": "MOMENT 분석에는 최소 8개 데이터 포인트가 필요합니다.",
                "confidence": 0,
            }), 400

        result = moment_service.analyze_pattern(values)
        return jsonify(result)

    except ImportError as e:
        return jsonify({
            "error": f"MOMENT not installed: {e}",
            "shape": "MOMENT 미설치",
            "description": "momentfm 패키지가 설치되지 않았습니다.",
            "confidence": 0,
        }), 500
    except Exception as e:
        logger.exception("MOMENT analyze_pattern failed")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/moment/analyze_clusters", methods=["POST"])
def api_moment_analyze_clusters():
    """Analyze multiple cluster centroids using MOMENT.

    Request JSON:
    {
        "centroids": [[...], [...], ...],  # List of centroid arrays
        "sizes": [100, 50, ...],            # Optional: cluster sizes
        "total": 500                         # Optional: total segments
    }

    Returns analysis for each centroid including:
    - shape, description, confidence
    - similarity to other clusters
    """
    try:
        from backend import moment_service

        data = request.json or {}
        centroids = data.get("centroids", [])
        sizes = data.get("sizes", [])
        total = data.get("total", sum(sizes) if sizes else 0)

        if not centroids:
            return jsonify({"error": "No centroids provided"}), 400

        # Analyze all centroids
        results = moment_service.analyze_clusters(centroids)

        # Add size/ratio info if provided
        for i, result in enumerate(results):
            if i < len(sizes):
                result["size"] = sizes[i]
                result["ratio"] = round(sizes[i] / total * 100, 1) if total > 0 else 0

        # Find representative pattern (largest with high confidence)
        representative_idx = 0
        best_score = 0
        for i, r in enumerate(results):
            size = r.get("size", 0)
            conf = r.get("confidence", 0)
            score = size * conf
            if score > best_score:
                best_score = score
                representative_idx = i

        return jsonify({
            "results": results,
            "representative_idx": representative_idx,
            "total_clusters": len(centroids),
        })

    except ImportError as e:
        return jsonify({"error": f"MOMENT not installed: {e}"}), 500
    except Exception as e:
        logger.exception("MOMENT analyze_clusters failed")
        return jsonify({"error": str(e)}), 500


def _cluster_brief(i: int, mr: dict) -> str:
    """One line per cluster for the joint labelling prompt."""
    st = mr.get("stats") or {}
    parts = [
        f"#{i}",
        f"shape={mr.get('shape', 'Unknown')}",
        f"size={mr.get('size', 0)} ({mr.get('ratio', 0)}%)",
    ]
    if st:
        parts += [
            f"level mean={st.get('level_mean')} (min {st.get('level_min')}, max {st.get('level_max')})",
            f"trend={st.get('trend')}%",
            f"volatility={st.get('volatility')}%",
            f"peak at {st.get('peak_at')}, valley at {st.get('valley_at')} of the window",
        ]
    return " | ".join(str(p) for p in parts)


def _fallback_labels(moment_results: list) -> list:
    """Deterministic distinct labels when the LLM is unusable.

    Same shape family gets split by level so two clusters never share a name.
    """
    out = []
    by_shape: dict[str, list[int]] = {}
    for i, mr in enumerate(moment_results):
        by_shape.setdefault(str(mr.get("shape", "pattern")), []).append(i)
    tags = ["strongest", "strong", "moderate", "mild", "faint"]
    for shape, idxs in by_shape.items():
        base = shape.lower().replace(" ", "_")
        if len(idxs) == 1:
            out.append((idxs[0], base))
            continue
        ranked = sorted(
            idxs,
            key=lambda i: abs((moment_results[i].get("stats") or {}).get("level_mean") or 0),
            reverse=True,
        )
        for rank, i in enumerate(ranked):
            tag = tags[rank] if rank < len(tags) else f"g{rank + 1}"
            out.append((i, f"{base}_{tag}"))
    return [lbl for _, lbl in sorted(out)]


def _dedupe_labels(labels: list, moment_results: list) -> list:
    """Guarantee distinct labels even if the model repeats itself."""
    seen: dict[str, list[int]] = {}
    for i, lbl in enumerate(labels):
        seen.setdefault(lbl, []).append(i)
    tags = ["strongest", "strong", "moderate", "mild", "faint"]
    fixed = list(labels)
    for lbl, idxs in seen.items():
        if len(idxs) < 2:
            continue
        ranked = sorted(
            idxs,
            key=lambda i: abs((moment_results[i].get("stats") or {}).get("level_mean") or 0),
            reverse=True,
        )
        for rank, i in enumerate(ranked):
            tag = tags[rank] if rank < len(tags) else f"g{rank + 1}"
            fixed[i] = f"{lbl}_{tag}"
    return fixed


def _parse_eval_json(text: str, count: int) -> list:
    """Pull `count` evaluation objects out of an LLM reply (array or loose objects)."""
    import json
    import re

    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    arr = re.search(r"\[[\s\S]*\]", text)
    if arr:
        try:
            parsed = json.loads(arr.group())
            if isinstance(parsed, list):
                return parsed[:count]
        except Exception:
            pass
    objs = []
    for m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            objs.append(json.loads(m.group()))
        except Exception:
            continue
    return objs[:count]


def _evaluate_clusters_together(llm_service, moment_results: list, dataset_info: dict) -> list:
    """Label every cluster in one pass so the names actually separate them.

    Judging clusters one at a time hides the siblings from the model, which is
    how five clusters ended up all named "midday_peak". Here the model sees the
    whole set and is required to return distinct labels; whatever it returns is
    still de-duplicated defensively.
    """
    n = len(moment_results)
    briefs = "\n".join(_cluster_brief(i, mr) for i, mr in enumerate(moment_results))
    prompt = f"""You are labelling {n} clusters found in one time series dataset.

[Dataset]
- Columns: {_ctx_columns(dataset_info)}
- Segment unit: {dataset_info.get('unit', 'unknown')}
- Domain: {dataset_info.get('domain', 'general')}
- The first column listed is the clustered signal; values are its raw level
  unless stated otherwise. Negative levels mean the signal sits below zero
  (for an error column that is under-prediction).

[Clusters]
{briefs}

Name each cluster so a human can tell them apart at a glance. Requirements:
- Every domain_label MUST be different from the others. Clusters sharing a
  shape family must be separated by what differs: level, depth, timing of the
  peak/valley, volatility, or how common they are.
- Use short snake_case, 2-4 words, describing the behaviour (e.g.
  deep_midday_drop, mild_evening_dip, flat_overnight).
- reason: one sentence naming what sets this cluster apart from the others.
- meaningful=false only for a cluster with no usable structure; a plain or
  flat shape is still a legitimate regime.

Respond with ONLY a JSON array of {n} objects, in the same order as the
clusters above:
[{{"index": 0, "meaningful": true, "confidence": 0-100,
   "reason": "...", "domain_label": "...", "merge_candidate": false}}]"""

    parsed = []
    try:
        reply = llm_service.call_llm(user_message=prompt, task="ts_to_text", temperature=0.1)
        parsed = _parse_eval_json(reply, n)
    except Exception as e:
        logger.warning(f"Joint cluster evaluation failed, using statistical labels: {e}")

    fallback = _fallback_labels(moment_results)
    labels, evals = [], []
    for i, mr in enumerate(moment_results):
        p = parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else {}
        labels.append(str(p.get("domain_label") or fallback[i]).strip() or fallback[i])
    labels = _dedupe_labels(labels, moment_results)

    for i, mr in enumerate(moment_results):
        p = parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else {}
        evals.append({
            **mr,
            "meaningful": p.get("meaningful", True) is not False,
            "llm_confidence": p.get("confidence", mr.get("confidence", 50)),
            "reason": p.get("reason", mr.get("description", "")),
            "domain_label": labels[i],
            "merge_candidate": bool(p.get("merge_candidate", False)),
            "labelled_by": "llm" if p else "statistical",
        })
    return evals


def _ctx_columns(dataset_info: dict) -> str:
    """Column context for the prompt. The client may send `column` or `columns`."""
    cols = dataset_info.get("columns") or dataset_info.get("column") or ""
    if isinstance(cols, (list, tuple)):
        cols = ", ".join(str(c) for c in cols if c)
    return str(cols) or "unknown"


@bp.route("/api/evaluate_clusters_llm", methods=["POST"])
def api_evaluate_clusters_llm():
    """Evaluate MOMENT analysis results using LLM for domain-specific interpretation.

    Request JSON:
    {
        "moment_results": [...],  # Results from /api/moment/analyze_clusters
        "dataset_info": {
            "column": "active_power",
            "unit": "hourly",
            "domain": "solar"
        }
    }

    Returns evaluations with:
    - meaningful: bool
    - confidence: 0-100
    - reason: explanation
    - domain_label: suggested domain-specific label
    - merge_candidate: bool
    """
    try:
        from backend import llm_service

        data = request.json or {}
        moment_results = data.get("moment_results", [])
        dataset_info = data.get("dataset_info", {})

        if not moment_results:
            return jsonify({"error": "No moment_results provided"}), 400

        # 클러스터를 하나씩 따로 평가하면 서로를 모르는 채 이름을 짓게 되어
        # 같은 라벨(midday_peak x4)이 반복된다. 전체를 한 번에 보여주고
        # "서로 구별되는" 이름을 요구한다.
        evaluations = _evaluate_clusters_together(llm_service, moment_results, dataset_info)

        return jsonify({
            "evaluations": evaluations,
            # 호출 실패 시 _evaluate_clusters_together는 통계 라벨을 보존한다.
            # 그 경우 성공처럼 LLM 사용을 보고하면 UI와 운영 기록이 거짓이 된다.
            "llm_used": any(e.get("labelled_by") == "llm" for e in evaluations),
        })

    except ImportError as e:
        return jsonify({"error": f"LLM service not available: {e}"}), 500
    except Exception as e:
        logger.exception("LLM cluster evaluation failed")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────
# HIL Discovery APIs - Find More / Subdivide
# ─────────────────────────────────────────────────────────────────

@bp.route("/api/segment_search/similar", methods=["POST"])
def api_segment_search_similar():
    """Find segments similar to a query centroid (HIL - Find More).

    Request JSON:
    {
        "dataset_key": str,
        "col_name": str,
        "unit": str,
        "query_values": [float],  # centroid to search for
        "top_k": int,
    }
    """
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    col_name = body.get("col_name", "")
    unit = body.get("unit", "day")
    query_values = body.get("query_values", [])
    top_k = body.get("top_k", cfg_search("segment_top_k"))

    if not dataset_key or not col_name:
        return jsonify(error="dataset_key and col_name are required"), 400
    if not query_values or len(query_values) < 3:
        return jsonify(error="query_values must have at least 3 points"), 400

    tu = cfg_time_units()
    if unit not in tu:
        return jsonify(error=f"Unknown time unit: {unit}"), 400

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="Dataset not found"), 404

    sources_data = get_ts_column_all_sources(conn, ds["id"], col_name)
    conn.close()

    if not sources_data:
        return jsonify(error=f"No data found for column '{col_name}'"), 404

    # Generate query shape from provided centroid
    first_ts = sources_data[0]["timestamps"]
    sampling_min = RetrieverAgent.detect_sampling(first_ts)
    seg_length = RetrieverAgent.get_segment_length(unit, sampling_min)

    query_shape = InterpreterAgent.generate_query_from_example(
        query_values, target_length=seg_length
    )

    # Run search
    result = segment_search_multi_source(
        sources_data, query_shape,
        unit=unit, method="shape", top_k=top_k,
        threshold=0.0, normalize=True,
    )

    return jsonify({
        "segments": result.get("results", []),
        "total_found": len(result.get("results", [])),
        "unit": unit,
    })


@bp.route("/api/segment_search/subdivide", methods=["POST"])
def api_segment_search_subdivide():
    """Subdivide a cluster's segments into finer sub-clusters (HIL - Subdivide).

    Request JSON:
    {
        "dataset_key": str,
        "col_name": str,
        "segments": [{"values": [...], ...}],  # segments to subdivide
        "k": int,  # number of sub-clusters
    }
    """
    body = request.get_json(force=True)
    segments = body.get("segments", [])
    k = body.get("k", cfg_clustering().get("subdivide_k", 3))

    if not segments or len(segments) < 4:
        return jsonify(error="Need at least 4 segments to subdivide"), 400

    # Prepare inline segments for clustering
    inline_segments = [
        {"key": f"sub_{i}", "values": seg.get("values", [])}
        for i, seg in enumerate(segments)
        if seg.get("values")
    ]

    if len(inline_segments) < k:
        return jsonify(error="Not enough valid segments"), 400

    # Convert to sources_data format for cluster_segments
    sources_data = [
        {"source_key": seg["key"], "timestamps": [], "values": seg["values"]}
        for seg in inline_segments
    ]

    # Run clustering
    result = cluster_segments(
        sources_data,
        unit=None,  # Pre-segmented
        method="shape",
        algorithm="kmeans",
        k=min(k, len(inline_segments) // 2),
        normalize=True,
    )

    return jsonify(result)
