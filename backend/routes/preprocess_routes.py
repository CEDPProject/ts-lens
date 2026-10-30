# -*- coding: utf-8 -*-
"""Preprocessing routes -- status, execute, delete, column preview."""
from __future__ import annotations

import json

import numpy as np
from flask import Blueprint, request, jsonify

from backend.app_config import cfg
from backend.db import (get_db, get_dataset, get_dataset_sources,
                        get_preprocess_config,
                        delete_preprocess, has_preprocessed_data,
                        get_ts_column, get_ts_column_all_sources,
                        get_ts_column_preprocessed, get_ts_column_preprocessed_all_sources)
from backend.helpers import _safe_list
from backend.dataset_service import invalidate_dataset_cache
from backend.preprocess_service import execute_preprocessing

bp = Blueprint("preprocess", __name__)


@bp.route("/api/preprocess/status/<dataset_key>")
def api_preprocess_status(dataset_key):
    """Query preprocessing status for a dataset."""
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    pp_cfg = get_preprocess_config(conn, ds["id"])
    has_pp = has_preprocessed_data(conn, ds["id"])
    conn.close()
    if pp_cfg is None:
        return jsonify(has_preprocessed=False, config=None, summary=None,
                       created_at=None)
    return jsonify(
        has_preprocessed=has_pp,
        config=json.loads(pp_cfg["config"]),
        summary=json.loads(pp_cfg["summary"]),
        created_at=pp_cfg["created_at"],
    )


@bp.route("/api/preprocess/interpolation_methods")
def api_interpolation_methods():
    """Return available interpolation methods from config."""
    methods = cfg("preprocessing.interpolation_methods")
    default = cfg("preprocessing.default_interpolation")
    return jsonify(methods=methods, default=default)


@bp.route("/api/preprocess/column_preview/<dataset_key>/<col_name>")
def api_preprocess_column_preview(dataset_key, col_name):
    """Column time series preview (original + preprocessed comparison).
    Downsamples to ~2000pt."""
    try:
        max_pts = int(request.args.get("max_pts", 2000))
        if not 2 <= max_pts <= 50000:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(error="max_pts must be an integer between 2 and 50000"), 400
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404

    sources = get_dataset_sources(conn, ds["id"])
    if not sources:
        conn.close()
        return jsonify(error="no sources"), 400

    # Original data
    if ds["type"] == "collection":
        all_sd = get_ts_column_all_sources(conn, ds["id"], col_name)
        orig_labels, orig_vals = [], []
        for sd in all_sd:
            orig_labels.extend(sd["timestamps"])
            orig_vals.extend(sd["values"])
    else:
        orig_labels, orig_vals = get_ts_column(conn, sources[0]["key"], col_name)

    orig_values = np.array(orig_vals, dtype=np.float64) if orig_vals else np.array([])

    # Preprocessed data
    pp_labels, pp_values = None, None
    has_pp = has_preprocessed_data(conn, ds["id"])
    if has_pp:
        if ds["type"] == "collection":
            all_sd = get_ts_column_preprocessed_all_sources(conn, ds["id"], col_name)
            pp_l, pp_v = [], []
            for sd in all_sd:
                pp_l.extend(sd["timestamps"])
                pp_v.extend(sd["values"])
            pp_labels = pp_l
            pp_values = np.array(pp_v, dtype=np.float64) if pp_v else None
        else:
            pp_l, pp_v = get_ts_column_preprocessed(conn, sources[0]["key"], col_name)
            if pp_l:
                pp_labels = pp_l
                pp_values = np.array(pp_v, dtype=np.float64)

    conn.close()

    def downsample(labels, values, target):
        n = len(values)
        if n <= target:
            return labels, _safe_list(values)
        step = max(1, n // target)
        idx = list(range(0, n, step))
        return [labels[i] for i in idx], _safe_list(values[idx])

    result = {"col_name": col_name, "has_preprocessed": has_pp}

    if len(orig_values) > 0:
        ol, ov = downsample(orig_labels, orig_values, max_pts)
        result["original"] = {"labels": ol, "values": ov, "total": len(orig_values)}
    else:
        result["original"] = {"labels": [], "values": [], "total": 0}

    if pp_values is not None and len(pp_values) > 0:
        pl, pv = downsample(pp_labels, pp_values, max_pts)
        result["preprocessed"] = {"labels": pl, "values": pv, "total": len(pp_values)}
    else:
        result["preprocessed"] = None

    return jsonify(result)



@bp.route("/api/preprocess/execute", methods=["POST"])
def api_preprocess_execute():
    """Execute preprocessing: read original ts_data, preprocess, save to ts_data_preprocessed.

    Request JSON:
      dataset_key: str
      options: {
        fill_gaps: bool,
        clamp_outliers: bool, clamp_method: str, clamp_k: float,
        value_range: bool, range_mode: str,
        interpolation_method: str,  # NEW: linear/cubic/spline/nearest/zero/previous
        col_limits: { col_name: { val_min, val_max }, ... }
      }
    """
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    options = body.get("options", {})

    if not dataset_key:
        return jsonify(error="dataset_key required"), 400

    result = execute_preprocessing(dataset_key, options)

    if "error" in result:
        code = 404 if "not found" in result["error"] else 400
        return jsonify(error=result["error"]), code

    return jsonify(ok=result["ok"], summary=result["summary"])


@bp.route("/api/preprocess/delete", methods=["POST"])
def api_preprocess_delete():
    """Delete preprocessed data (revert to original)."""
    body = request.get_json(force=True)
    dataset_key = body.get("dataset_key", "")
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    delete_preprocess(conn, ds["id"])
    invalidate_dataset_cache(conn, ds["id"])
    conn.close()
    return jsonify(ok=True)
