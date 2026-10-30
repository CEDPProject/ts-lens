# -*- coding: utf-8 -*-
"""Preprocessing service -- execute preprocessing pipeline.

Extracted from routes/preprocess_routes.py to break circular dependency
(agents/curator.py was importing from route module).
"""
from __future__ import annotations

import json

import numpy as np

from backend.app_config import cfg
from backend.db import (
    get_db, get_dataset, get_dataset_sources, get_dataset_columns,
    clear_preprocessed_data, insert_ts_preprocessed_bulk,
    get_ts_column, save_preprocess_config,
)
from backend.preprocessing import _preprocess_timeseries


def execute_preprocessing(dataset_key: str, options: dict) -> dict:
    """Core preprocessing logic -- called by CuratorAgent and route handler.

    Returns dict with 'ok' and 'summary', or 'error' on failure.
    """
    if not isinstance(options, dict):
        return {"error": "options must be an object"}
    if not isinstance(options.get("col_limits", {}), dict):
        return {"error": "col_limits must be an object"}
    if not dataset_key:
        return {"error": "dataset_key required"}

    conn = get_db()
    try:
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            return {"error": "dataset not found"}

        sources = get_dataset_sources(conn, ds["id"])
        cols = get_dataset_columns(conn, ds["id"])
        col_limits = options.get("col_limits", {})

        if not sources:
            return {"error": "no sources"}

        # Preserve the previous result until every column and summary succeeds.
        conn.execute("BEGIN IMMEDIATE")
        clear_preprocessed_data(conn, ds["id"])

        summary = {
            "total_original": 0,
            "total_final": 0,
            "per_column": {},
        }

        for col_def in cols:
            col_name = col_def["col_name"]
            col_summary = {
                "nan_interpolated": 0,
                "range_replaced": 0,
                "gaps_found": 0,
                "points_inserted": 0,
                "clamped_count": 0,
                "original_points": 0,
                "final_points": 0,
            }

            pp_opts = {
                "fill_gaps": options.get("fill_gaps", False),
                "clamp_outliers": options.get("clamp_outliers", False),
                "clamp_method": options.get("clamp_method", "iqr"),
                "clamp_k": float(options.get("clamp_k", 3.0)),
                "value_range": options.get("value_range", False),
                "range_mode": options.get("range_mode", "clamp"),
                "interpolate_nan": options.get("interpolate_nan", True),
                "interpolation_method": options.get(
                    "interpolation_method",
                    cfg("preprocessing.default_interpolation")),
            }
            lim = col_limits.get(col_name, {})
            pp_opts["val_min"] = lim.get("val_min", col_def.get("val_min"))
            pp_opts["val_max"] = lim.get("val_max", col_def.get("val_max"))

            for src in sources:
                labels, val_list = get_ts_column(conn, src["key"], col_name)
                if not labels:
                    continue
                values = np.array(val_list, dtype=np.float64)
                original_count = len(values)
                col_summary["original_points"] += original_count
                summary["total_original"] += original_count

                new_labels, new_values, info = _preprocess_timeseries(
                    labels, values, pp_opts)

                if "nan" in info:
                    col_summary["nan_interpolated"] += info["nan"].get("interpolated", 0)
                if "range" in info:
                    col_summary["range_replaced"] += info["range"].get("range_replaced", 0)
                if "gap" in info:
                    col_summary["gaps_found"] += info["gap"].get("gaps_found", 0)
                    col_summary["points_inserted"] += info["gap"].get("points_inserted", 0)
                if "clamp" in info:
                    col_summary["clamped_count"] += info["clamp"].get("clamped_count", 0)

                col_summary["final_points"] += len(new_values)
                summary["total_final"] += len(new_values)

                rows = [(ts, col_name, float(v))
                        for ts, v in zip(new_labels, new_values.tolist())]
                insert_ts_preprocessed_bulk(conn, src["id"], rows)

            summary["per_column"][col_name] = col_summary

        save_preprocess_config(conn, ds["id"],
                               json.dumps(options, ensure_ascii=False),
                               json.dumps(summary, ensure_ascii=False), commit=False)
        conn.execute("DELETE FROM dataset_cache WHERE dataset_id = ?", (ds["id"],))
        conn.commit()

        return {"ok": True, "summary": summary}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()
