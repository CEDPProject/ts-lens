# -*- coding: utf-8 -*-
"""Dataset CRUD routes -- list, sources, preview, delete, files, column limits."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import yaml
from flask import Blueprint, request, jsonify, send_file

from backend.app_config import cfg, drop_dataset_presets
from backend.db import (get_db, get_dataset, get_all_datasets, get_dataset_columns,
                        get_dataset_sources, count_dataset_points,
                        count_points, get_source, delete_dataset, delete_source,
                        dataset_delete_summary,
                        update_column_meta, get_stored_files,
                        get_ts_column, get_ts_column_all_sources, DATA_DIR,
                        has_ground_truth,
                        get_labels_for_dataset_key)
from backend.dataset_service import (
    get_dataset_cache, build_dataset_cache, invalidate_dataset_cache,
)

bp = Blueprint("datasets", __name__)


@bp.route("/api/db/datasets")
def api_db_datasets():
    """All datasets + columns + source count + metadata."""
    conn = get_db()
    try:
        conn.execute("SELECT metadata FROM datasets LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE datasets ADD COLUMN metadata TEXT DEFAULT '{}'")
        conn.commit()

    datasets = get_all_datasets(conn)
    result = []
    preview_count = cfg("display.sources_preview_count")

    for ds in datasets:
        cols = get_dataset_columns(conn, ds["id"])
        sources = get_dataset_sources(conn, ds["id"])

        # Try cache first
        cache = get_dataset_cache(conn, ds["id"])
        if cache is None:
            # Build cache live
            cache = build_dataset_cache(conn, ds["id"], sources, cols)

        # Merge total_points from cache into cols
        pts_cache = cache.get("total_points_per_col", {})
        for c in cols:
            # get(키, 기본값) 은 기본값을 먼저 계산한다. 캐시가 있어도 COUNT 가 매번 돌아
            # 컬럼 수만큼 전수 질의가 나가므로, 없을 때만 세도록 갈라 쓴다
            cached = pts_cache.get(c["col_name"])
            c["total_points"] = (cached if cached is not None
                                 else count_dataset_points(conn, ds["id"], c["col_name"]))

        # Parse stored metadata
        meta_raw = ds.get("metadata") or "{}"
        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {}

        # Merge cached auto-detected info
        if cache.get("time_start"):
            meta["time_start"] = cache["time_start"]
        if cache.get("time_end"):
            meta["time_end"] = cache["time_end"]
        if cache.get("sampling_interval") and "sampling_interval" not in meta:
            meta["sampling_interval"] = cache["sampling_interval"]

        # Count labels and group by type
        label_count = conn.execute(
            "SELECT COUNT(*) FROM ground_truth_labels WHERE dataset_id = ?",
            (ds["id"],)
        ).fetchone()[0]

        # Get label type breakdown
        label_types_rows = conn.execute(
            "SELECT label_type, COUNT(*) as cnt FROM ground_truth_labels "
            "WHERE dataset_id = ? GROUP BY label_type ORDER BY cnt DESC",
            (ds["id"],)
        ).fetchall()
        label_types = [{"type": r["label_type"], "count": r["cnt"]} for r in label_types_rows]

        result.append({
            "id": ds["id"],
            "key": ds["key"],
            "name": ds["name"],
            "description": ds["description"],
            "type": ds["type"],
            "created_at": ds["created_at"],
            "columns": cols,
            "source_count": len(sources),
            "sources": [{"key": s["key"], "description": s["description"]}
                        for s in sources[:preview_count]],
            "metadata": meta,
            "label_count": label_count,
            "label_types": label_types,
        })
    conn.close()
    return jsonify(datasets=result)


@bp.route("/api/db/datasets/<dataset_key>/sources")
def api_db_dataset_sources(dataset_key):
    """All sources for a dataset."""
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    sources = get_dataset_sources(conn, ds["id"])
    cols = get_dataset_columns(conn, ds["id"])
    result = []
    for s in sources:
        pts = count_points(conn, s["key"], cols[0]["col_name"]) if cols else 0
        result.append({
            "id": s["id"],
            "key": s["key"],
            "description": s["description"],
            "time_col": s["time_col"],
            "points": pts,
        })
    conn.close()
    return jsonify(dataset_key=dataset_key, sources=result)


@bp.route("/api/db/sources/<path:source_key>/preview")
def api_db_source_preview(source_key):
    """Preview first N rows of source data."""
    limit = request.args.get("limit", cfg("display.preview_limit"), type=int)
    conn = get_db()
    src = get_source(conn, source_key)
    if src is None:
        conn.close()
        return jsonify(error="source not found"), 404

    cols = get_dataset_columns(conn, src["dataset_id"])
    col_names = [c["col_name"] for c in cols]

    ts_rows = conn.execute(
        "SELECT DISTINCT timestamp FROM ts_data "
        "WHERE source_id = ? ORDER BY timestamp LIMIT ?",
        (src["id"], limit),
    ).fetchall()
    timestamps = [r["timestamp"] for r in ts_rows]

    rows = []
    for ts in timestamps:
        row_data = conn.execute(
            "SELECT col_name, value FROM ts_data "
            "WHERE source_id = ? AND timestamp = ?",
            (src["id"], ts),
        ).fetchall()
        entry = {"timestamp": ts}
        for rd in row_data:
            entry[rd["col_name"]] = rd["value"]
        rows.append(entry)

    conn.close()
    return jsonify(
        source_key=source_key,
        time_col=src["time_col"],
        columns=[dict(c) for c in cols],
        col_names=col_names,
        rows=rows,
        total_timestamps=len(timestamps),
    )


@bp.route("/api/datasets")
def api_datasets():
    """Lightweight dataset list for UI rendering."""
    conn = get_db()
    datasets = get_all_datasets(conn)
    result = []
    for ds in datasets:
        cols = get_dataset_columns(conn, ds["id"])
        sources = get_dataset_sources(conn, ds["id"])
        result.append({
            "key": ds["key"],
            "name": ds["name"] or ds["key"],
            "description": ds["description"],
            "type": ds["type"],
            "index_type": ds.get("index_type", "datetime") or "datetime",
            "columns": cols,
            "source_count": len(sources),
            "has_ground_truth": has_ground_truth(conn, ds["id"]),
        })
    conn.close()
    return jsonify(datasets=result)


@bp.route("/api/db/datasets/<dataset_key>/delete_summary")
def api_delete_summary(dataset_key):
    """What a delete would remove — shown in the confirm dialog."""
    conn = get_db()
    try:
        summary = dataset_delete_summary(conn, dataset_key)
    finally:
        conn.close()
    if summary is None:
        return jsonify(error="dataset not found"), 404
    return jsonify(summary)


@bp.route("/api/db/datasets/<dataset_key>", methods=["DELETE"])
def api_delete_dataset(dataset_key):
    """Delete dataset (including sources, segments). ?confirm=<dataset_key> required."""
    if request.args.get("confirm") != dataset_key:
        return jsonify(error="confirm must equal the dataset key"), 400
    conn = get_db()
    try:
        ok = delete_dataset(conn, dataset_key)
    finally:
        conn.close()
    if not ok:
        return jsonify(error="dataset not found"), 404
    # DB 밖에 남는 것: 프리셋 파일, 불러 둔 세션, 군집 준비 배열
    from backend.routes.segment_search_routes import purge_cluster_cache
    from backend.session_store import purge_dataset_sessions
    drop_dataset_presets(dataset_key)
    purge_dataset_sessions(dataset_key)
    purge_cluster_cache(dataset_key)
    return jsonify(ok=True)


@bp.route("/api/db/sources/<path:source_key>", methods=["DELETE"])
def api_delete_source(source_key):
    """Delete source."""
    conn = get_db()
    # Invalidate parent dataset cache
    src = get_source(conn, source_key)
    if src:
        invalidate_dataset_cache(conn, src["dataset_id"])
        ds_key = conn.execute("SELECT key FROM datasets WHERE id = ?",
                              (src["dataset_id"],)).fetchone()["key"]
    ok = delete_source(conn, source_key)
    conn.close()
    if ok:
        from backend.routes.segment_search_routes import purge_cluster_cache
        from backend.session_store import purge_dataset_sessions
        purge_dataset_sessions(ds_key)
        purge_cluster_cache(ds_key)
        return jsonify(ok=True)
    return jsonify(error="source not found"), 404


@bp.route("/api/db/datasets/<dataset_key>/files")
def api_dataset_files(dataset_key):
    """List stored original CSV files for a dataset."""
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    files = get_stored_files(conn, ds["id"])
    conn.close()
    return jsonify(dataset_key=dataset_key, files=files)


@bp.route("/api/db/datasets/<dataset_key>/files/<path:filename>/download")
def api_download_file(dataset_key, filename):
    """Download stored original CSV file."""
    from backend.runtime_paths import stored_path
    conn = get_db()
    try:
        ds = get_dataset(conn, dataset_key)
        files = get_stored_files(conn, ds["id"]) if ds else []
    finally:
        conn.close()
    item = next((f for f in files if f["filename"] == filename), None)
    if item is None:
        return jsonify(error="file not found"), 404
    try:
        file_path = stored_path(DATA_DIR, item["rel_path"])
    except ValueError:
        return jsonify(error="file not found"), 404
    if not file_path.is_file():
        return jsonify(error="file not found"), 404
    return send_file(file_path, as_attachment=True, download_name=filename)


@bp.route("/api/meta_presets")
def api_meta_presets():
    """Metadata presets YAML -> JSON."""
    preset_path = Path(__file__).parent.parent.parent / "config" / "meta_presets.yaml"
    if not preset_path.is_file():
        return jsonify({})
    with open(preset_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return jsonify(data or {})


@bp.route("/api/column_limits/<dataset_key>")
def api_column_limits(dataset_key):
    """Column description/min/max for a dataset."""
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    cols = get_dataset_columns(conn, ds["id"])
    conn.close()
    return jsonify(columns=[{
        "col_name": c["col_name"],
        "label": c["label"],
        "unit": c["unit"],
        "description": c.get("description", ""),
        "val_min": c["val_min"],
        "val_max": c["val_max"],
    } for c in cols])


@bp.route("/api/column_limits/<dataset_key>", methods=["POST"])
def api_update_column_limits(dataset_key):
    """Update column description/min/max.

    Request JSON: { columns: [{ col_name, description, val_min, val_max }, ...] }
    """
    body = request.get_json(force=True)
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    columns = body.get("columns", [])
    if not isinstance(columns, list):
        return jsonify(error="columns must be an array"), 400
    known = {c["col_name"]: c for c in get_dataset_columns(conn, ds["id"])}
    updates = []
    try:
        for col_data in columns:
            if not isinstance(col_data, dict) or not isinstance(col_data.get("col_name"), str) or col_data["col_name"] not in known:
                raise ValueError("Unknown column")
            col_name = col_data["col_name"]
            kwargs = {}
            if "description" in col_data:
                if not isinstance(col_data["description"], str):
                    raise ValueError("description must be a string")
                kwargs["description"] = col_data["description"]
            for key in ("val_min", "val_max"):
                if key in col_data:
                    value = col_data[key]
                    if value is not None:
                        if isinstance(value, bool):
                            raise ValueError("limits must be finite numbers")
                        value = float(value)
                        if not math.isfinite(value):
                            raise ValueError("limits must be finite numbers")
                    kwargs[key] = value
            low = kwargs.get("val_min", known[col_name]["val_min"])
            high = kwargs.get("val_max", known[col_name]["val_max"])
            if low is not None and high is not None and low > high:
                raise ValueError("val_min must not exceed val_max")
            known[col_name].update(kwargs)
            updates.append((col_name, kwargs))
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    with conn:
        for col_name, kwargs in updates:
            if kwargs:
                update_column_meta(conn, ds["id"], col_name, commit=False, **kwargs)
    conn.close()
    return jsonify(ok=True)


@bp.route("/api/column_limits/<dataset_key>/auto_compute", methods=["POST"])
def api_auto_compute_column_limits(dataset_key):
    """Auto-compute min/max from actual data for all columns."""
    body = request.get_json(force=True) if request.is_json else {}
    force = body.get("force", False)

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404

    cols = get_dataset_columns(conn, ds["id"])
    sources = get_dataset_sources(conn, ds["id"])
    if not sources:
        conn.close()
        return jsonify(error="no sources"), 400

    updated = []
    for c in cols:
        col_name = c["col_name"]
        if not force and c.get("val_min") is not None and c.get("val_max") is not None:
            updated.append({"col_name": col_name, "val_min": c["val_min"],
                            "val_max": c["val_max"], "status": "existing"})
            continue
        all_vals = []
        if ds["type"] == "collection":
            all_source_data = get_ts_column_all_sources(conn, ds["id"], col_name)
            for sd in all_source_data:
                all_vals.extend(sd["values"])
        else:
            _, val_list = get_ts_column(conn, sources[0]["key"], col_name)
            all_vals = val_list
        arr = np.array(all_vals, dtype=np.float64)
        clean = arr[np.isfinite(arr)] if len(arr) > 0 else arr
        if len(clean) == 0:
            updated.append({"col_name": col_name, "val_min": None,
                            "val_max": None, "status": "no_data"})
            continue
        vmin = float(np.min(clean))
        vmax = float(np.max(clean))
        update_column_meta(conn, ds["id"], col_name, val_min=vmin, val_max=vmax)
        updated.append({"col_name": col_name, "val_min": vmin,
                        "val_max": vmax, "status": "computed"})
    conn.close()
    return jsonify(ok=True, columns=updated)


@bp.route("/api/db/datasets/<dataset_key>/refresh_cache", methods=["POST"])
def api_refresh_cache(dataset_key):
    """Manually refresh dataset cache."""
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404
    sources = get_dataset_sources(conn, ds["id"])
    cols = get_dataset_columns(conn, ds["id"])
    cache = build_dataset_cache(conn, ds["id"], sources, cols)
    conn.close()
    return jsonify(ok=True, cache=cache)


@bp.route("/api/labels")
def api_labels():
    """Get ground truth labels for a dataset."""
    dataset_key = request.args.get("dataset_key", "")
    label_type = request.args.get("label_type", None) or None

    if not dataset_key:
        return jsonify(error="dataset_key required"), 400

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404

    labels = get_labels_for_dataset_key(conn, dataset_key, label_type)
    conn.close()
    return jsonify(dataset_key=dataset_key, labels=labels, count=len(labels))
