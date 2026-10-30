# -*- coding: utf-8 -*-
"""Dataset service -- time range detection, sampling interval, cache management."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from .app_config import cfg
from .helpers import format_interval, format_interval_minutes


# ── Time range / sampling detection ──────────────────────────

def detect_time_range(conn: sqlite3.Connection,
                      dataset_id: int,
                      sources: list[dict]) -> tuple[str, str] | None:
    """Auto-detect time range across all sources."""
    if not sources:
        return None
    first_src = sources[0]
    last_src = sources[-1]
    row_first = conn.execute(
        "SELECT MIN(timestamp) as ts FROM ts_data WHERE source_id = ?",
        (first_src["id"],),
    ).fetchone()
    row_last = conn.execute(
        "SELECT MAX(timestamp) as ts FROM ts_data WHERE source_id = ?",
        (last_src["id"],),
    ).fetchone()
    if row_first and row_last and row_first["ts"] and row_last["ts"]:
        return (row_first["ts"], row_last["ts"])
    return None


def detect_sampling_interval(conn: sqlite3.Connection,
                             source_id: int) -> str | None:
    """Estimate sampling interval from first source timestamps."""
    sample_size = cfg("detection.interval_sample_size")
    rows = conn.execute(
        "SELECT DISTINCT timestamp FROM ts_data "
        "WHERE source_id = ? ORDER BY timestamp LIMIT ?",
        (source_id, max(50, sample_size)),
    ).fetchall()
    if len(rows) < 2:
        return None
    stamps = [r["timestamp"] for r in rows]
    try:
        parsed = pd.to_datetime(stamps)
    except Exception:
        return None
    diffs = parsed[1:] - parsed[:-1]
    median_diff = diffs.median()
    total_seconds = median_diff.total_seconds()
    if total_seconds <= 0:
        return None
    return format_interval(total_seconds)


# Re-export for backward compatibility
format_sampling_minutes = format_interval_minutes


# ── Dataset cache ────────────────────────────────────────────

def get_dataset_cache(conn: sqlite3.Connection,
                      dataset_id: int) -> dict | None:
    """Get cached dataset metadata. Returns None if not cached."""
    row = conn.execute(
        "SELECT cache_data FROM dataset_cache WHERE dataset_id = ?",
        (dataset_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["cache_data"])
    except (json.JSONDecodeError, TypeError):
        return None


def update_dataset_cache(conn: sqlite3.Connection,
                         dataset_id: int,
                         cache_data: dict, *, commit: bool = True) -> None:
    """Upsert dataset cache."""
    conn.execute(
        "INSERT OR REPLACE INTO dataset_cache (dataset_id, cache_data, updated_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        (dataset_id, json.dumps(cache_data, ensure_ascii=False)),
    )
    if commit:
        conn.commit()


def invalidate_dataset_cache(conn: sqlite3.Connection,
                             dataset_id: int) -> None:
    """Remove cached data for a dataset."""
    conn.execute("DELETE FROM dataset_cache WHERE dataset_id = ?",
                 (dataset_id,))
    conn.commit()


def build_dataset_cache(conn: sqlite3.Connection,
                        dataset_id: int,
                        sources: list[dict],
                        columns: list[dict], *, commit: bool = True) -> dict:
    """Build and save dataset cache."""
    from .db import count_dataset_points

    cache = {
        "source_count": len(sources),
        "total_points_per_col": {},
    }

    # Time range
    time_range = detect_time_range(conn, dataset_id, sources)
    if time_range:
        cache["time_start"] = time_range[0]
        cache["time_end"] = time_range[1]

    # Sampling interval
    if sources:
        si = detect_sampling_interval(conn, sources[0]["id"])
        if si:
            cache["sampling_interval"] = si

    # Points per column
    for col in columns:
        cache["total_points_per_col"][col["col_name"]] = \
            count_dataset_points(conn, dataset_id, col["col_name"])

    update_dataset_cache(conn, dataset_id, cache, commit=commit)
    return cache
