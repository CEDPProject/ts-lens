# -*- coding: utf-8 -*-
"""Label import routes."""
import io
import json
import math
import pandas as pd
from flask import Blueprint, request, jsonify

from backend.db import get_db, get_dataset, get_dataset_sources

bp = Blueprint("label_import", __name__)


@bp.route("/api/import_labels", methods=["POST"])
def import_labels():
    """Import ground truth labels from CSV.

    Expected CSV format:
    start_ts,end_ts,label_type,label_value,confidence,notes
    2023-01-01 08:00:00,2023-01-01 10:00:00,event,rush_hour,0.95,Morning traffic

    Request:
      - file: labels CSV
      - dataset_key: target dataset
      - source_key: (optional) specific source for collection datasets
    """
    # Parse file
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file provided"), 400

    dataset_key = request.form.get("dataset_key")
    if not dataset_key:
        return jsonify(error="dataset_key required"), 400

    source_key = request.form.get("source_key")  # Optional

    try:
        content = f.read().decode("utf-8-sig")
        labels_df = pd.read_csv(io.StringIO(content))
    except Exception as e:
        return jsonify(error=f"CSV parse failed: {e}"), 400

    # Validate columns
    required_cols = ['start_ts', 'end_ts', 'label_type']
    missing = [c for c in required_cols if c not in labels_df.columns]
    if missing:
        return jsonify(error=f"Missing required columns: {missing}"), 400

    # Get dataset
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if not ds:
        return jsonify(error=f"Dataset '{dataset_key}' not found"), 404

    dataset_id = ds["id"]

    # Get sources
    sources = get_dataset_sources(conn, dataset_id)
    if not sources:
        return jsonify(error="No sources found for dataset"), 404

    # Determine source_id
    if source_key:
        # Find specific source
        source_id = None
        for src in sources:
            if src["key"] == source_key:
                source_id = src["id"]
                break
        if source_id is None:
            return jsonify(error=f"Source '{source_key}' not found"), 404
    else:
        # Use first source (for continuous datasets)
        if len(sources) > 1:
            return jsonify(
                error=f"Multiple sources found ({len(sources)}). Please specify source_key.",
                sources=[s["key"] for s in sources]
            ), 400
        source_id = sources[0]["id"]

    # Validate every row before opening a write transaction. A malformed last
    # confidence value must not leave a partially inserted batch or a writer lock.
    records = []
    def text_field(row, name):
        value = row.get(name, "")
        return "" if pd.isna(value) else str(value).strip()
    try:
        for number, (_, row) in enumerate(labels_df.iterrows(), 2):
            start_ts, end_ts = text_field(row, "start_ts"), text_field(row, "end_ts")
            label_type = text_field(row, "label_type")
            if not start_ts or not end_ts or not label_type:
                raise ValueError(f"Row {number}: start_ts, end_ts and label_type are required")
            value, notes = text_field(row, "label_value"), text_field(row, "notes")
            confidence = row.get("confidence", 1.0)
            confidence = 1.0 if pd.isna(confidence) else float(confidence)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError(f"Row {number}: confidence must be between 0 and 1")
            records.append((dataset_id, source_id, start_ts, end_ts, label_type,
                            ": ".join(part for part in (value, notes) if part),
                            json.dumps({"label_value": value, "confidence": confidence}, allow_nan=False)))
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    with conn:
        conn.executemany(
            "INSERT INTO ground_truth_labels "
            "(dataset_id,source_id,start_ts,end_ts,label_type,description,metadata) "
            "VALUES (?,?,?,?,?,?,?)", records)
    inserted = len(records)

    conn.commit()

    return jsonify({
        "success": True,
        "message": f"Imported {inserted} labels",
        "dataset_key": dataset_key,
        "source_id": source_id,
        "labels_count": inserted
    })


@bp.route("/api/labels/<dataset_key>", methods=["GET"])
def get_labels(dataset_key):
    """Get all labels for a dataset."""
    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if not ds:
        return jsonify(error="Dataset not found"), 404

    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            l.id, l.source_id, l.label_type, l.start_ts, l.end_ts,
            l.description, l.metadata, l.created_at,
            s.key as source_key
        FROM ground_truth_labels l
        JOIN sources s ON s.id = l.source_id
        WHERE l.dataset_id = ?
        ORDER BY l.start_ts
    """, (ds["id"],))

    labels = []
    for row in cursor.fetchall():
        meta = json.loads(row[6]) if row[6] else {}
        labels.append({
            'id': row[0],
            'source_id': row[1],
            'source_key': row[8],
            'label_type': row[2],
            'start_ts': row[3],
            'end_ts': row[4],
            'description': row[5],
            'label_value': meta.get('label_value', ''),
            'confidence': meta.get('confidence', 1.0),
            'created_at': row[7]
        })

    return jsonify({
        'dataset_key': dataset_key,
        'labels': labels,
        'count': len(labels)
    })
