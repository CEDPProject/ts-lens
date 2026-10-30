# -*- coding: utf-8 -*-
"""Curator Agent -- data registration, preprocessing, quality diagnosis.

Responsibilities:
    - Dataset registration (CSV upload -> parse -> DB)
    - Metadata management (column config, dataset description)
    - Data preprocessing pipeline orchestration
    - Data quality diagnostics
    - Periodicity analysis
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from backend.db import (
    get_db, get_dataset, get_dataset_sources, get_dataset_columns,
    has_preprocessed_data,
    get_ts_column, get_ts_column_preprocessed,
    get_ts_column_all_sources, get_ts_column_preprocessed_all_sources,
)
from backend.diagnostics_service import diagnose_column

logger = logging.getLogger(__name__)


class CuratorAgent:
    """Manages data lifecycle: registration, preprocessing, diagnostics."""

    # ── Data Quality Diagnosis ────────────────────────────────

    @staticmethod
    def diagnose_dataset(dataset_key: str) -> dict:
        """Run quality diagnostics on all columns of a dataset."""
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            conn.close()
            return {"error": "dataset not found"}

        sources = get_dataset_sources(conn, ds["id"])
        columns = get_dataset_columns(conn, ds["id"])
        use_pp = has_preprocessed_data(conn, ds["id"])

        result_cols = []
        for col_def in columns:
            col_name = col_def["col_name"]

            diag, arr = diagnose_column(
                conn, ds["id"], ds["type"], sources, col_name)

            col_result = {
                "col_name": col_name,
                "label": col_def.get("label", col_name),
                "unit": col_def.get("unit", ""),
                "diag": diag,
                "data_min": round(float(np.nanmin(arr)), 4) if len(arr) > 0 else None,
                "data_max": round(float(np.nanmax(arr)), 4) if len(arr) > 0 else None,
            }

            if use_pp:
                pp_diag, pp_arr = diagnose_column(
                    conn, ds["id"], ds["type"], sources, col_name,
                    use_preprocessed=True)
                col_result["diag_pp"] = pp_diag
                col_result["pp_data_min"] = (
                    round(float(np.nanmin(pp_arr)), 4)
                    if len(pp_arr) > 0 else None)
                col_result["pp_data_max"] = (
                    round(float(np.nanmax(pp_arr)), 4)
                    if len(pp_arr) > 0 else None)

            result_cols.append(col_result)

        conn.close()
        return {
            "columns": result_cols,
            "has_preprocessed": use_pp,
        }

    # ── Periodicity Analysis ──────────────────────────────────

    @staticmethod
    def analyze_periodicity(values: np.ndarray,
                            interval_seconds: float = 3600.0) -> dict:
        """FFT-based periodicity analysis for a single column.

        Imported from data_load route's core function.
        """
        from backend.periodicity_service import analyze_periodicity_core
        return analyze_periodicity_core(values, interval_seconds)

    @staticmethod
    def analyze_periodicity_all(dataset_key: str) -> dict:
        """Batch periodicity analysis for all columns."""
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            conn.close()
            return {"error": "dataset not found"}

        sources = get_dataset_sources(conn, ds["id"])
        columns = get_dataset_columns(conn, ds["id"])
        use_pp = has_preprocessed_data(conn, ds["id"])

        # Detect sampling interval
        from backend.dataset_service import get_dataset_cache, detect_sampling_interval
        from backend.helpers import parse_interval_seconds
        from backend.periodicity_service import analyze_periodicity_core
        cache = get_dataset_cache(conn, ds["id"])
        interval_str = (cache or {}).get("sampling_interval")
        if not interval_str and sources:
            interval_str = detect_sampling_interval(conn, sources[0]["id"])
        interval_sec = parse_interval_seconds(interval_str)

        results = {}
        for col_def in columns:
            col_name = col_def["col_name"]
            try:
                if ds["type"] == "collection":
                    # Analyze each source independently (concat creates artifacts)
                    if use_pp:
                        all_sd = get_ts_column_preprocessed_all_sources(
                            conn, ds["id"], col_name)
                    else:
                        all_sd = get_ts_column_all_sources(
                            conn, ds["id"], col_name)
                    period_votes = {}
                    for sd in (all_sd or []):
                        arr = np.array(sd["values"], dtype=np.float64)
                        info = analyze_periodicity_core(arr, interval_sec)
                        if info["has_periodicity"] and info["periods"]:
                            top = info["periods"][0]
                            desc = top["description"]
                            prev = period_votes.get(desc, (0, 0.0))
                            period_votes[desc] = (prev[0] + 1, prev[1] + top["strength"])
                    if period_votes:
                        best = max(period_votes.items(), key=lambda x: x[1][0])
                        best_count = best[1][0]
                        total_sources = len(all_sd or [])
                        if total_sources > 0 and best_count > total_sources / 2:
                            results[col_name] = {
                                "has": True,
                                "period": best[0],
                                "strength": round(best[1][1] / best[1][0], 4),
                            }
                        else:
                            results[col_name] = {"has": False, "period": None, "strength": 0}
                    else:
                        results[col_name] = {"has": False, "period": None, "strength": 0}
                else:
                    if use_pp:
                        _, vals = get_ts_column_preprocessed(
                            conn, sources[0]["key"], col_name)
                    else:
                        _, vals = get_ts_column(
                            conn, sources[0]["key"], col_name)

                    arr = np.array(vals, dtype=np.float64) if vals else np.array([])
                    info = analyze_periodicity_core(arr, interval_sec)
                    top = info["periods"][0] if info["periods"] else None
                    results[col_name] = {
                        "has": info["has_periodicity"],
                        "period": top["description"] if top else None,
                        "strength": top["strength"] if top else 0,
                    }
            except Exception as e:
                # Reporting "no periodicity" on failure is indistinguishable from
                # a genuine negative -- log so a broken column is not hidden.
                logger.warning("Periodicity analysis failed for column %s: %s", col_name, e)
                results[col_name] = {"has": False, "period": None, "strength": 0}

        conn.close()
        return {"columns": results}

    # ── Preprocessing Pipeline ────────────────────────────────

    @staticmethod
    def preprocess_dataset(dataset_key: str, options: dict) -> dict:
        """Execute preprocessing pipeline on all columns.

        Delegates to existing route logic via preprocess_routes.
        Returns summary of preprocessing results.
        """
        # The actual execution is in preprocess_routes.py which handles
        # DB transactions, per-column processing, and result storage.
        # This agent method provides a clean interface.
        from backend.preprocess_service import execute_preprocessing
        return execute_preprocessing(dataset_key, options)

    # ── Metadata Management ───────────────────────────────────

    @staticmethod
    def get_dataset_info(dataset_key: str) -> Optional[dict]:
        """Get full dataset info with columns and metadata."""
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            conn.close()
            return None
        ds["columns"] = get_dataset_columns(conn, ds["id"])
        ds["sources"] = get_dataset_sources(conn, ds["id"])
        ds["has_preprocessed"] = has_preprocessed_data(conn, ds["id"])
        conn.close()
        return ds
