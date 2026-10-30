# -*- coding: utf-8 -*-
"""Data loading routes -- diagnose, load, segments, get_data."""
from __future__ import annotations

import json
import logging
import uuid

import numpy as np
import pandas as pd
from flask import Blueprint, request, jsonify

from backend.app_config import cfg
from backend.diagnostics_service import diagnose_column
from backend.session_store import DATA_STORE
from backend.helpers import _safe_list, _safe_stats
from backend.preprocessing import _diagnose_timeseries
from backend.db import (
    get_db, get_dataset, get_dataset_sources,
    get_column_info, get_ts_column, get_ts_column_all_sources,
    get_segments, get_all_segment_values,
    has_preprocessed_data, get_preprocess_config,
    get_ts_column_preprocessed, get_ts_column_preprocessed_all_sources,
    has_ground_truth, get_labels_for_dataset_key,
)

bp = Blueprint("data_load", __name__)
logger = logging.getLogger(__name__)


@bp.route("/api/diagnose", methods=["POST"])
def diagnose_data():
    """Pre-load lightweight diagnostics: data quality + per-column meta."""
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    primary_col = body.get("primary_col", "")
    overlay_cols = body.get("overlay_cols", [])

    if not dataset_key or not primary_col:
        return jsonify(error="dataset_key and primary_col required"), 400

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404

    ds_type = ds["type"]
    sources = get_dataset_sources(conn, ds["id"])
    if not sources:
        conn.close()
        return jsonify(error="no sources"), 400

    all_cols = [primary_col] + [c for c in overlay_cols if c != primary_col]
    col_results = []
    has_pp = has_preprocessed_data(conn, ds["id"])

    for col_name in all_cols:
        col_info = get_column_info(conn, dataset_key, col_name)

        diag, values = diagnose_column(
            conn, ds["id"], ds_type, sources, col_name)

        has_valid = len(values) > 0 and not np.all(np.isnan(values))

        # Calculate out-of-range count based on val_min/val_max
        val_min = col_info.get("val_min")
        val_max = col_info.get("val_max")
        out_of_range = 0
        if has_valid and (val_min is not None or val_max is not None):
            clean = values[~np.isnan(values)]
            if val_min is not None:
                out_of_range += int((clean < val_min).sum())
            if val_max is not None:
                out_of_range += int((clean > val_max).sum())
        diag["out_of_range"] = out_of_range

        col_entry = {
            "col_name": col_name,
            "label": col_info.get("label", col_name),
            "unit": col_info.get("unit", ""),
            "val_min": val_min,
            "val_max": val_max,
            "diag": diag,
            "data_min": round(float(np.nanmin(values)), 4) if has_valid else None,
            "data_max": round(float(np.nanmax(values)), 4) if has_valid else None,
        }

        # Preprocessed data diagnostics
        if has_pp:
            pp_diag, pp_values = diagnose_column(
                conn, ds["id"], ds_type, sources, col_name,
                use_preprocessed=True)
            col_entry["diag_pp"] = pp_diag

            pp_valid = len(pp_values) > 0 and not np.all(np.isnan(pp_values))
            col_entry["pp_data_min"] = (
                round(float(np.nanmin(pp_values)), 4) if pp_valid else None)
            col_entry["pp_data_max"] = (
                round(float(np.nanmax(pp_values)), 4) if pp_valid else None)

        col_results.append(col_entry)

    has_issues = any(
        cr["diag"].get("gaps_found", 0) > 0 or
        cr["diag"].get("outliers_iqr", 0) > 0 or
        cr["diag"].get("nan_count", 0) > 0
        for cr in col_results
    )

    primary_diag = col_results[0]["diag"] if col_results else {}
    conn.close()

    return jsonify(
        has_issues=has_issues,
        has_preprocessed=has_pp,
        diagnostics=primary_diag,
        columns=col_results,
    )


@bp.route("/api/load", methods=["POST"])
def load_data():
    """Load dataset + column data.
    Uses preprocessed data if available, otherwise original."""
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    primary_col = body.get("primary_col", "")
    overlay_cols = body.get("overlay_cols", [])

    if not dataset_key or not primary_col:
        return jsonify(error="dataset_key and primary_col required"), 400

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error=f"dataset '{dataset_key}' not found"), 400

    col_info = get_column_info(conn, dataset_key, primary_col)
    ds_type = ds["type"]
    sources = get_dataset_sources(conn, ds["id"])
    source_key = body.get("source_key", "")

    if not sources:
        conn.close()
        return jsonify(error="no sources"), 400

    use_pp = has_preprocessed_data(conn, ds["id"])
    pp_config = None
    if use_pp:
        pp_cfg = get_preprocess_config(conn, ds["id"])
        if pp_cfg:
            pp_config = json.loads(pp_cfg["config"])

    diag = {}
    drilldown = False

    # Drill-down: load single source from collection as continuous
    if source_key and ds_type == "collection":
        drilldown = True
        if use_pp:
            labels, val_list = get_ts_column_preprocessed(
                conn, source_key, primary_col)
        else:
            labels, val_list = get_ts_column(conn, source_key, primary_col)
        values = np.array(val_list, dtype=np.float64)
        diag = _diagnose_timeseries(labels, values) if len(values) > 0 else {}
    elif ds_type == "collection":
        if use_pp:
            all_source_data = get_ts_column_preprocessed_all_sources(
                conn, ds["id"], primary_col)
        else:
            all_source_data = get_ts_column_all_sources(conn, ds["id"], primary_col)
        all_values = []
        for sd in all_source_data:
            all_values.extend(sd["values"])
        values = np.array(all_values, dtype=np.float64) if all_values else np.array(
            [], dtype=np.float64)
        labels = []
    else:
        if use_pp:
            labels, val_list = get_ts_column_preprocessed(
                conn, sources[0]["key"], primary_col)
        else:
            labels, val_list = get_ts_column(conn, sources[0]["key"], primary_col)
        values = np.array(val_list, dtype=np.float64)
        diag = _diagnose_timeseries(labels, values)

    sid = str(uuid.uuid4())[:8]
    DATA_STORE[sid] = {
        "filename": f"{dataset_key}_{primary_col}",
        "values": values,
        "labels": labels,
        "diagnostics": diag,
        # 주기성 분석이 컬렉션을 소스별로 나눠 보려면 출처를 알아야 한다.
        # 이어 붙인 계열을 통째로 FFT 하면 경계에서 가짜 장주기가 잡힌다.
        "dataset_key": dataset_key,
        "primary_col": primary_col,
        "dataset_type": ds_type,
        "single_source": ds_type == "single" or bool(drilldown),
    }

    # Overlay columns
    overlays = []
    for ov_col in overlay_cols:
        ov_col_info = get_column_info(conn, dataset_key, ov_col)
        if ds_type == "single" or drilldown:
            # Single source: load from specific source
            src_key = source_key if drilldown else sources[0]["key"]
            if use_pp:
                ov_labels, ov_vals = get_ts_column_preprocessed(
                    conn, src_key, ov_col)
            else:
                ov_labels, ov_vals = get_ts_column(conn, src_key, ov_col)
        else:
            if use_pp:
                ov_all = get_ts_column_preprocessed_all_sources(
                    conn, ds["id"], ov_col)
            else:
                ov_all = get_ts_column_all_sources(conn, ds["id"], ov_col)
            if ov_all:
                ov_labels = ov_all[0]["timestamps"]
                ov_vals = ov_all[0]["values"]
            else:
                ov_labels, ov_vals = [], []
        ov_arr = np.array(ov_vals, dtype=np.float64) if ov_vals else np.array(
            [], dtype=np.float64)
        overlays.append({
            "name": ov_col_info["label"],
            "unit": ov_col_info.get("unit", ""),
            "col_name": ov_col,
            "values": _safe_list(ov_arr),
            "labels": ov_labels,
            "stats": _safe_stats(ov_arr),
        })

    # Ground truth labels (before closing conn)
    _has_gt = has_ground_truth(conn, ds["id"])
    _gt_labels = get_labels_for_dataset_key(conn, dataset_key) if _has_gt else []

    conn.close()

    DATA_STORE[sid]["overlays"] = overlays

    title = (f"{col_info['label']} ({col_info['unit']})"
             if col_info.get("unit") else col_info["label"])
    mode = "continuous" if drilldown else ("segment" if ds_type == "collection" else "continuous")
    stats = _safe_stats(values)
    idx_type = ds.get("index_type", "datetime") or "datetime"

    if drilldown:
        # Add source name to title for drill-down
        src_name = source_key.split("/")[-1] if "/" in source_key else source_key
        title = f"{title} - {src_name}"

    resp = {
        "sid": sid,
        "title": title,
        "mode": mode,
        "dataset_key": dataset_key,
        "dataset_type": ds_type,
        "index_type": idx_type,
        "primary_col": primary_col,
        "values": _safe_list(values),
        "labels": labels,
        "overlays": overlays,
        "stats": stats,
        "diagnostics": diag,
        "using_preprocessed": use_pp,
        "col_meta": {
            "val_min": col_info.get("val_min"),
            "val_max": col_info.get("val_max"),
        },
    }

    if drilldown:
        resp["drilldown"] = True
        resp["source_key"] = source_key
    else:
        if ds_type == "single" and sources:
            resp["source_key"] = sources[0]["key"]
        if use_pp and pp_config:
            resp["preprocess_config"] = pp_config

    if ds_type == "collection" and not drilldown:
        resp["source_count"] = len(sources)
        # 소스마다 길이가 다를 수 있다(관측 결측 등). 첫 소스 길이를 대표로
        # 내보내면 "8,757 pts" 처럼 사실과 다른 값이 표시된다. 최빈값을 쓰고,
        # 다른 길이가 섞여 있으면 화면이 근사값으로 표시하도록 알린다.
        from backend.segment_search import representative_segment_length
        _lens = [len(sd.get("values") or []) for sd in (all_source_data or [])]
        resp["seg_length"] = representative_segment_length(all_source_data or [])
        resp["seg_length_varies"] = len(set(_lens)) > 1
        if _lens:
            resp["seg_length_range"] = [min(_lens), max(_lens)]

    # Ground truth labels
    resp["has_ground_truth"] = _has_gt
    if _has_gt:
        resp["ground_truth_labels"] = _gt_labels

    return jsonify(**resp)


@bp.route("/api/segments/<dataset_key>/<col_name>")
def load_segments(dataset_key, col_name):
    """Load segments (with overlay columns).
    collection: each source is a segment (or split by time_unit if provided)
    single: use segments table"""
    overlay_cols_str = request.args.get("overlay_cols", "")
    overlay_cols = [c.strip() for c in overlay_cols_str.split(",") if c.strip()]
    time_unit = request.args.get("time_unit", "")  # For collection datasets: split by time unit

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 400

    if ds["type"] == "collection":
        from backend.segment_search import split_into_segments

        all_source_data = get_ts_column_all_sources(conn, ds["id"], col_name)

        # If time_unit is specified, split each source by time unit
        if time_unit and time_unit != 'none':
            all_segments = []
            for src in all_source_data:
                src_key = src.get("source_key", "")
                ts_list = src.get("timestamps", [])
                val_list = src.get("values", [])
                if not ts_list or not val_list:
                    continue
                # Split this source by time unit
                segs = split_into_segments(ts_list, val_list, time_unit, src_key, require_complete=True)
                for seg in segs:
                    seg["source_label"] = src.get("description") or src_key
                    seg["label"] = f'{seg["source_label"]} · {seg["label"]}'
                all_segments.extend(segs)

            # Load overlay columns for split segments
            ov_by_key = {}  # key = "source_key/segment_key"
            for ov_col in overlay_cols:
                ov_col_info = get_column_info(conn, dataset_key, ov_col)
                ov_name = ov_col_info["label"] if ov_col_info else ov_col
                ov_all = get_ts_column_all_sources(conn, ds["id"], ov_col)
                for ov_src in ov_all:
                    src_key = ov_src.get("source_key", "")
                    ts_list = ov_src.get("timestamps", [])
                    val_list = ov_src.get("values", [])
                    if not ts_list or not val_list:
                        continue
                    ov_segs = split_into_segments(ts_list, val_list, time_unit, src_key, require_complete=True)
                    for ov_seg in ov_segs:
                        seg_key = ov_seg["key"]
                        if seg_key not in ov_by_key:
                            ov_by_key[seg_key] = []
                        ov_by_key[seg_key].append({
                            "name": ov_name,
                            "col_name": ov_col,
                            "values": ov_seg["values"],
                        })

            conn.close()
            segments = []
            for i, seg in enumerate(all_segments):
                segment = {
                    "index": i,
                    "label": seg["label"],
                    "source_key": seg["source_key"],
                    "source_label": seg.get("source_label", ""),
                    "key": seg["key"],
                    "timestamps": seg["timestamps"],
                    "values": seg["values"],
                    "labels": seg["timestamps"],
                }
                seg_key = seg["key"]
                if seg_key in ov_by_key:
                    segment["overlays"] = ov_by_key[seg_key]
                segments.append(segment)
        else:
            # Original behavior: each source = one segment
            ov_by_source = {}
            for ov_col in overlay_cols:
                ov_col_info = get_column_info(conn, dataset_key, ov_col)
                ov_name = ov_col_info["label"] if ov_col_info else ov_col
                ov_all = get_ts_column_all_sources(conn, ds["id"], ov_col)
                for sd in ov_all:
                    if sd["source_key"] not in ov_by_source:
                        ov_by_source[sd["source_key"]] = []
                    ov_by_source[sd["source_key"]].append({
                        "name": ov_name,
                        "col_name": ov_col,
                        "values": sd["values"],
                    })
            conn.close()
            segments = []
            for i, sd in enumerate(all_source_data):
                seg = {
                    "index": i,
                    "label": sd["description"] or sd["source_key"].split("/")[-1],
                    "source_key": sd["source_key"],
                    "values": sd["values"],
                    "labels": sd["timestamps"],
                }
                if sd["source_key"] in ov_by_source:
                    seg["overlays"] = ov_by_source[sd["source_key"]]
                segments.append(seg)
        return jsonify(
            dataset_key=dataset_key,
            col_name=col_name,
            count=len(segments),
            segments=segments,
        )

    sources = get_dataset_sources(conn, ds["id"])
    if not sources:
        conn.close()
        return jsonify(error="no sources"), 400
    source_key = sources[0]["key"]
    seg_meta = get_segments(conn, source_key, col_name)
    seg_data = get_all_segment_values(conn, source_key, col_name, seg_meta)

    ov_seg_lists = {}
    for ov_col in overlay_cols:
        ov_col_info = get_column_info(conn, dataset_key, ov_col)
        ov_name = ov_col_info["label"] if ov_col_info else ov_col
        ov_meta = get_segments(conn, source_key, ov_col)
        ov_vals = get_all_segment_values(conn, source_key, ov_col, ov_meta)
        ov_seg_lists[ov_col] = [
            (ov_name, val_list) for _, (_, val_list) in zip(ov_meta, ov_vals)
        ]
    conn.close()

    segments = []
    for idx_m, (meta, (ts_list, val_list)) in enumerate(zip(seg_meta, seg_data)):
        seg = {
            "index": meta["seg_index"],
            "label": meta["seg_label"],
            "source_key": source_key,
            "start": ts_list[0] if ts_list else "",
            "end": ts_list[-1] if ts_list else "",
            "values": val_list,
            "labels": ts_list,
        }
        seg_overlays = []
        for ov_col in overlay_cols:
            if idx_m < len(ov_seg_lists.get(ov_col, [])):
                ov_name, ov_vals = ov_seg_lists[ov_col][idx_m]
                seg_overlays.append({
                    "name": ov_name, "col_name": ov_col, "values": ov_vals})
        if seg_overlays:
            seg["overlays"] = seg_overlays
        segments.append(seg)

    return jsonify(
        dataset_key=dataset_key,
        col_name=col_name,
        count=len(segments),
        segments=segments,
    )


@bp.route("/api/data/<sid>")
def get_data(sid):
    """Return time series data for selected column."""
    entry = DATA_STORE.get(sid)
    if not entry:
        return jsonify(error="session not found"), 404

    val_col = request.args.get("value")
    time_col = request.args.get("time", "")
    # 이 경로는 CSV 업로드 세션(upload.py 가 df 를 넣는다) 전용이다.
    # /api/load 로 만든 세션 id 를 주면 KeyError 로 500 이 났다.
    if "df" not in entry:
        return jsonify(
            error="This session has no uploaded table. "
                  "/api/data is for CSV upload sessions."), 400
    df = entry["df"]

    if val_col not in df.columns:
        return jsonify(error=f"Column '{val_col}' not found"), 400

    series = pd.to_numeric(df[val_col], errors="coerce")
    mask = series.notna()
    full_values = series[mask].values
    full_labels = (df[time_col][mask].astype(str).tolist()
                   if time_col and time_col in df.columns
                   else list(range(len(full_values))))

    entry["values"] = np.array(full_values, dtype=np.float64)
    entry["labels"] = full_labels

    display_values = _safe_list(full_values)
    display_labels = full_labels
    MAX_DISPLAY = cfg("display.max_points")
    if len(display_values) > MAX_DISPLAY:
        step = len(display_values) // MAX_DISPLAY
        display_values = display_values[::step]
        display_labels = display_labels[::step]

    return jsonify(
        values=display_values,
        labels=[str(l) for l in display_labels],
        stats=_safe_stats(full_values),
    )
