# -*- coding: utf-8 -*-
"""Labeler Agent -- pattern library management and labeling loop.

Responsibilities:
    - Pattern definition CRUD (create, read, update, delete)
    - Instance management (accept / reject search results)
    - Shape refinement (average accepted instances -> improved shape)
    - Pattern library queries and statistics
"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional

import numpy as np

from backend.db import (
    get_db, get_ts_column,
    create_pattern_def, get_pattern_defs, get_pattern_def,
    update_pattern_def, delete_pattern_def,
    add_pattern_instance, get_pattern_instances,
    update_instance_status, get_pattern_stats,
)
from backend.helpers import z_normalize, _resample, _safe_list


class LabelerAgent:
    """Manages the pattern library and labeling loop."""

    # ── Pattern CRUD ──────────────────────────────────────────

    @staticmethod
    def list_patterns(dataset_key: Optional[str] = None) -> list[dict]:
        """Return all pattern definitions, optionally filtered by dataset."""
        conn = get_db()
        patterns = get_pattern_defs(conn, dataset_key)
        for p in patterns:
            p["stats"] = get_pattern_stats(conn, p["id"])
            if isinstance(p.get("shape"), str):
                try:
                    p["shape"] = json.loads(p["shape"])
                except (json.JSONDecodeError, TypeError):
                    p["shape"] = []
        conn.close()
        return patterns

    @staticmethod
    def get_pattern(pattern_id: int) -> Optional[dict]:
        """Return pattern definition with instances and stats."""
        conn = get_db()
        pdef = get_pattern_def(conn, pattern_id)
        if pdef is None:
            conn.close()
            return None
        if isinstance(pdef.get("shape"), str):
            try:
                pdef["shape"] = json.loads(pdef["shape"])
            except (json.JSONDecodeError, TypeError):
                pdef["shape"] = []
        pdef["stats"] = get_pattern_stats(conn, pattern_id)
        pdef["instances"] = get_pattern_instances(conn, pattern_id)
        conn.close()
        return pdef

    @staticmethod
    def create_pattern(name: str, description: str = "",
                       shape: list = None, dataset_key: str = "",
                       col_name: str = "", method: str = "euclidean") -> int:
        """Create a new pattern definition. Returns pattern_id."""
        conn = get_db()
        pid = create_pattern_def(
            conn,
            name=name,
            description=description,
            shape=json.dumps(shape or [], ensure_ascii=False),
            dataset_key=dataset_key,
            col_name=col_name,
            method=method,
        )
        conn.close()
        return pid

    @staticmethod
    def update_pattern(pattern_id: int, **updates) -> bool:
        """Update pattern definition fields."""
        if "shape" in updates and isinstance(updates["shape"], list):
            updates["shape"] = json.dumps(updates["shape"], ensure_ascii=False)
        conn = get_db()
        ok = update_pattern_def(conn, pattern_id, **updates)
        conn.close()
        return ok

    @staticmethod
    def delete_pattern(pattern_id: int) -> bool:
        """Delete pattern and all its instances."""
        conn = get_db()
        ok = delete_pattern_def(conn, pattern_id)
        conn.close()
        return ok

    # ── Instance management (Accept / Reject) ─────────────────

    @staticmethod
    def accept_result(pattern_id: int, source_key: str, col_name: str,
                      start_ts: str, end_ts: str,
                      similarity: float = 0.0) -> int:
        """Accept a search result as a pattern instance."""
        conn = get_db()
        iid = add_pattern_instance(
            conn, pattern_id,
            source_key=source_key, col_name=col_name,
            start_ts=start_ts, end_ts=end_ts,
            similarity=similarity, status="accepted",
        )
        conn.close()
        return iid

    @staticmethod
    def reject_result(pattern_id: int, source_key: str, col_name: str,
                      start_ts: str, end_ts: str,
                      similarity: float = 0.0) -> int:
        """Reject a search result as a pattern instance."""
        conn = get_db()
        iid = add_pattern_instance(
            conn, pattern_id,
            source_key=source_key, col_name=col_name,
            start_ts=start_ts, end_ts=end_ts,
            similarity=similarity, status="rejected",
        )
        conn.close()
        return iid

    @staticmethod
    def update_instance_status(instance_id: int, status: str) -> bool:
        """Change instance status (accepted / rejected / pending)."""
        if status not in ("accepted", "rejected", "pending"):
            raise ValueError(f"Invalid status: {status}")
        conn = get_db()
        ok = update_instance_status(conn, instance_id, status)
        conn.close()
        return ok

    # ── Shape refinement (Labeling Loop core) ─────────────────

    @staticmethod
    def refine_pattern(pattern_id: int) -> dict:
        """Refine pattern shape by averaging accepted instances.

        This is the core of the Labeling Loop:
        Accept results -> average their shapes -> produce improved query shape.

        Returns dict with refined_shape and n_instances, or error.
        """
        conn = get_db()
        pdef = get_pattern_def(conn, pattern_id)
        if pdef is None:
            conn.close()
            return {"error": "pattern not found"}

        instances = get_pattern_instances(conn, pattern_id, status="accepted")
        if len(instances) < 1:
            conn.close()
            return {"error": "accepted instances required"}

        # Determine target shape length from existing pattern
        target_len = None
        if isinstance(pdef.get("shape"), str):
            try:
                orig_shape = json.loads(pdef["shape"])
                target_len = len(orig_shape) if orig_shape else None
            except (json.JSONDecodeError, TypeError):
                pass

        # Collect and normalize instance shapes
        shapes = []
        for inst in instances:
            ts_list, val_list = get_ts_column(
                conn, inst["source_key"], inst["col_name"])
            if not ts_list:
                continue
            seg_vals = [v for t, v in zip(ts_list, val_list)
                        if inst["start_ts"] <= t <= inst["end_ts"]]
            if len(seg_vals) < 3:
                continue
            arr = np.array(seg_vals, dtype=np.float64)
            arr = z_normalize(arr)
            shapes.append(arr)

        if not shapes:
            conn.close()
            return {"error": "no valid segment data"}

        # Resample all to same length and average
        if target_len is None:
            target_len = int(np.median([len(s) for s in shapes]))
        resampled = [_resample(s, target_len) for s in shapes]
        avg_shape = np.mean(resampled, axis=0)
        avg_shape = z_normalize(avg_shape)

        # Save refined shape
        update_pattern_def(
            conn, pattern_id,
            shape=json.dumps(_safe_list(avg_shape), ensure_ascii=False))
        conn.close()

        return {
            "refined_shape": _safe_list(avg_shape),
            "n_instances": len(shapes),
        }

    # ── Export ─────────────────────────────────────────────────────

    @staticmethod
    def export_patterns_csv(dataset_key: Optional[str] = None) -> str:
        """Export pattern library as CSV string.

        Each row is a pattern instance; pattern-level fields are repeated.
        Patterns with no instances still get one row (instance fields blank).
        """
        conn = get_db()
        patterns = get_pattern_defs(conn, dataset_key)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "pattern_id", "pattern_name", "description",
            "dataset_key", "col_name", "method",
            "created_at", "updated_at",
            "instance_id", "source_key", "inst_col_name",
            "start_ts", "end_ts", "similarity", "status",
            "inst_created_at",
        ])

        for p in patterns:
            pid = p["id"]
            instances = get_pattern_instances(conn, pid)
            base = [
                pid, p["name"], p.get("description", ""),
                p.get("dataset_key", ""), p.get("col_name", ""),
                p.get("method", ""), p.get("created_at", ""),
                p.get("updated_at", ""),
            ]
            if instances:
                for inst in instances:
                    writer.writerow(base + [
                        inst["id"], inst["source_key"], inst["col_name"],
                        inst["start_ts"], inst["end_ts"],
                        inst.get("similarity", ""),
                        inst["status"], inst.get("created_at", ""),
                    ])
            else:
                writer.writerow(base + [""] * 8)

        conn.close()
        return buf.getvalue()
