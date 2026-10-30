# -*- coding: utf-8 -*-
"""Retriever Agent -- similarity search and classification.

Responsibilities:
    - Sliding-window search (continuous time series)
    - Segment-level search (preset / LLM / QBE)
    - Multi-source segment search
    - Preset-based auto-classification of all segments
"""
from __future__ import annotations

import numpy as np

from backend.db import (
    get_db, get_dataset, get_dataset_sources,
    get_ts_column,
    has_preprocessed_data,
    get_ts_column_preprocessed,
)
from backend.helpers import (
    z_normalize, compute_distance, distance_to_similarity,
    _DEFAULT_NAN_MODE, _DEFAULT_NAN_RATIO,
)
from backend.segment_search import (
    segment_search_multi_source,
    classify_segments_by_presets, detect_sampling_minutes,
    time_unit_to_length,
)
from backend.session_store import DATA_STORE


class RetrieverAgent:
    """Finds similar segments / windows in time series data."""

    # ── Sliding-window search (continuous) ────────────────────

    @staticmethod
    def search_sliding_window(sid: str, query_shape: list[float],
                              method: str = "euclidean",
                              top_k: int = 20,
                              normalize: bool = True,
                              ) -> dict:
        """Slide a query window over continuous data and find top matches.

        Uses DATA_STORE[sid] for the loaded time series.
        """
        entry = DATA_STORE.get(sid)
        if entry is None:
            return {"error": "No data loaded for this session"}

        values = entry["values"]
        query = np.array(query_shape, dtype=np.float64)
        qlen = len(query)

        if normalize:
            query = z_normalize(query)

        results = []
        N = len(values)
        for i in range(N - qlen + 1):
            window = values[i:i + qlen].copy()
            if np.isnan(window).sum() > qlen * 0.5:
                continue
            if normalize:
                window = z_normalize(window)
            dist = compute_distance(query, window, method)
            sim = distance_to_similarity(dist, method)
            results.append({
                "start_idx": i,
                "end_idx": i + qlen - 1,
                "similarity": round(float(sim), 6),
                "distance": round(float(dist), 6),
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return {
            "results": results[:top_k],
            "total_windows": N - qlen + 1,
            "query_length": qlen,
        }

    # ── Segment-level search ──────────────────────────────────

    @staticmethod
    def search_segments(dataset_key: str, col_name: str,
                        query_shape: np.ndarray,
                        unit: str = "day",
                        method: str = "euclidean",
                        top_k: int = 20,
                        normalize: bool = True,
                        nan_mode: str = _DEFAULT_NAN_MODE,
                        nan_max_ratio: float = _DEFAULT_NAN_RATIO) -> dict:
        """Search for similar segments in a dataset column."""
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            conn.close()
            return {"error": "dataset not found"}

        sources = get_dataset_sources(conn, ds["id"])
        use_pp = has_preprocessed_data(conn, ds["id"])

        sources_data = []
        for src in sources:
            if use_pp:
                labels, vals = get_ts_column_preprocessed(
                    conn, src["key"], col_name)
            else:
                labels, vals = get_ts_column(conn, src["key"], col_name)
            if labels and vals:
                sources_data.append({
                    "source_key": src["key"],
                    "timestamps": labels,
                    "values": vals,
                })
        conn.close()

        if not sources_data:
            return {"error": "no data for this column"}

        return segment_search_multi_source(
            sources_data, query_shape,
            unit=unit, method=method, top_k=top_k,
            normalize=normalize,
            nan_mode=nan_mode, nan_max_ratio=nan_max_ratio,
        )

    # ── Preset-based auto-classification ──────────────────────

    @staticmethod
    def classify_all_presets(dataset_key: str, col_name: str,
                             unit: str = "day",
                             method: str = "euclidean",
                             nan_mode: str = _DEFAULT_NAN_MODE,
                             nan_max_ratio: float = _DEFAULT_NAN_RATIO) -> dict:
        """Classify all segments by their nearest preset shape."""
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            conn.close()
            return {"error": "dataset not found"}

        sources = get_dataset_sources(conn, ds["id"])
        use_pp = has_preprocessed_data(conn, ds["id"])

        sources_data = []
        for src in sources:
            if use_pp:
                labels, vals = get_ts_column_preprocessed(
                    conn, src["key"], col_name)
            else:
                labels, vals = get_ts_column(conn, src["key"], col_name)
            if labels and vals:
                sources_data.append({
                    "source_key": src["key"],
                    "timestamps": labels,
                    "values": vals,
                })
        conn.close()

        if not sources_data:
            return {"error": "no data for this column"}

        return classify_segments_by_presets(
            sources_data, unit=unit, method=method,
            nan_mode=nan_mode, nan_max_ratio=nan_max_ratio,
        )

    # ── Utility ───────────────────────────────────────────────

    @staticmethod
    def detect_sampling(timestamps: list[str]) -> float:
        """Detect sampling interval in minutes."""
        return detect_sampling_minutes(timestamps)

    @staticmethod
    def get_segment_length(unit: str, sampling_minutes: float) -> int:
        """Convert time unit + sampling interval to segment length."""
        return time_unit_to_length(unit, sampling_minutes)
