# -*- coding: utf-8 -*-
"""Diagnostics service -- data quality diagnosis with collection aggregation.

Extracted from curator.py and routes/data_load.py to eliminate duplication
of the collection-vs-single source aggregation pattern.
"""
from __future__ import annotations

import numpy as np

from backend.db import (
    get_ts_column, get_ts_column_all_sources,
    get_ts_column_preprocessed, get_ts_column_preprocessed_all_sources,
)
from backend.preprocessing import _diagnose_timeseries


_EMPTY_AGG = {
    "interval": None, "gaps_found": 0, "points_missing": 0,
    "gap_ratio": 0.0, "outliers_iqr": 0, "outlier_ratio": 0.0,
    "nan_count": 0, "total_points": 0, "expected_points": 0,
}


def _aggregate_source_diagnostics(source_data_list: list[dict]) -> tuple[dict, np.ndarray]:
    """Run diagnostics on each source and aggregate counts.

    Returns (aggregated_diag, concatenated_values_array).
    """
    agg = dict(_EMPTY_AGG)
    all_vals: list = []

    for sd in source_data_list:
        all_vals.extend(sd["values"])
        seg_arr = np.array(sd["values"], dtype=np.float64)
        seg_diag = _diagnose_timeseries(sd["timestamps"], seg_arr)
        if agg["interval"] is None and seg_diag.get("interval"):
            agg["interval"] = seg_diag["interval"]
        agg["gaps_found"] += seg_diag.get("gaps_found", 0)
        agg["points_missing"] += seg_diag.get("points_missing", 0)
        agg["outliers_iqr"] += seg_diag.get("outliers_iqr", 0)
        agg["nan_count"] += seg_diag.get("nan_count", 0)
        agg["total_points"] += seg_diag.get("total_points", 0)
        agg["expected_points"] += seg_diag.get("expected_points", 0)

    if agg["expected_points"] > 0:
        agg["gap_ratio"] = round(
            agg["points_missing"] / agg["expected_points"], 4)
    if agg["total_points"] > 0:
        agg["outlier_ratio"] = round(
            agg["outliers_iqr"] / agg["total_points"], 4)

    arr = np.array(all_vals, dtype=np.float64) if all_vals else np.array([])
    return agg, arr


def diagnose_column(conn, dataset_id: int, ds_type: str, sources: list,
                    col_name: str, use_preprocessed: bool = False
                    ) -> tuple[dict, np.ndarray]:
    """Diagnose a single column, handling collection vs single source.

    Returns (diagnostics_dict, values_array).
    """
    if use_preprocessed:
        get_all = get_ts_column_preprocessed_all_sources
        get_single = get_ts_column_preprocessed
    else:
        get_all = get_ts_column_all_sources
        get_single = get_ts_column

    if ds_type == "collection":
        source_data = get_all(conn, dataset_id, col_name)
        return _aggregate_source_diagnostics(source_data or [])
    else:
        labels, vals = get_single(conn, sources[0]["key"], col_name)
        arr = np.array(vals, dtype=np.float64) if vals else np.array([])
        diag = _diagnose_timeseries(labels, arr) if len(arr) > 0 else {}
        return diag, arr
