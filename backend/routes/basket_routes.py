# -*- coding: utf-8 -*-
"""Basket API routes."""
from __future__ import annotations

import csv
import io
import json
from flask import Blueprint, jsonify, request, Response, stream_with_context

from backend.db import (
    get_db, get_dataset, get_basket_items, get_basket_item, add_basket_items,
    update_basket_item, bulk_update_basket,
    delete_basket_item, bulk_delete_basket, bulk_delete_basket_by_keys,
    get_all_columns_for_segment,
    get_basket_keys, get_basket_count,
)

bp = Blueprint("basket", __name__)


def _validate_updates(data):
    for key in ("label", "note"):
        if key in data and not isinstance(data[key], str):
            return f"{key} must be a string"
    if "status" in data and data["status"] not in ("draft", "confirmed"):
        return "status must be draft or confirmed"
    if "ids" in data and (not isinstance(data["ids"], list)
                          or any(type(i) is not int or i < 1 for i in data["ids"])):
        return "ids must be positive integers"
    return None


@bp.route("/api/basket", methods=["GET"])
def api_basket_list():
    status = request.args.get("status")
    dataset_key = request.args.get("dataset_key")
    include_values = request.args.get("include_values") == "1"
    conn = get_db()
    items = get_basket_items(conn, status=status, dataset_key=dataset_key,
                             include_values=include_values)
    return jsonify(items)


@bp.route("/api/basket", methods=["POST"])
def api_basket_add():
    data = request.get_json(force=True)
    items = data.get("items", [])
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items must be a nonempty array"}), 400
    conn = get_db()
    # 지운 데이터셋의 항목이 옛 브라우저 저장분 이관 등으로 되살아나지 않게 한다
    gone = sorted({it.get("dataset_key") for it in items if isinstance(it, dict)
                   and it.get("dataset_key") and get_dataset(conn, it["dataset_key"]) is None})
    items = [it for it in items if not (isinstance(it, dict) and it.get("dataset_key") in gone)]
    result = add_basket_items(conn, items) if items else {"ids": [], "count": 0, "updated": 0, "keys": [], "errors": []}
    if gone:
        result.setdefault("errors", []).extend(f"dataset not found: {k}" for k in gone)
    return jsonify(result)


@bp.route("/api/basket/keys", methods=["GET"])
def api_basket_keys():
    status = request.args.get("status")
    conn = get_db()
    keys = get_basket_keys(conn, status=status)
    return jsonify({"keys": keys})


@bp.route("/api/basket/count", methods=["GET"])
def api_basket_count():
    conn = get_db()
    counts = get_basket_count(conn)
    return jsonify(counts)


@bp.route("/api/basket/columns", methods=["GET"])
def api_basket_columns():
    """Return column names for datasets represented in the Basket.

    This intentionally reads metadata/distinct names only; discovering the
    picker options must not fetch full waveforms for representative items.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT dc.col_name AS col_name
        FROM basket_items b
        JOIN datasets ds ON ds.key = b.dataset_key
        JOIN dataset_columns dc ON dc.dataset_id = ds.id
        WHERE dc.col_name IS NOT NULL AND dc.col_name != ''
        ORDER BY 1
    """).fetchall()
    return jsonify({"columns": [row["col_name"] for row in rows]})


@bp.route("/api/basket/<int:item_id>", methods=["PUT"])
def api_basket_update(item_id: int):
    data = request.get_json(force=True)
    error = _validate_updates(data)
    if error:
        return jsonify(error=error), 400
    kwargs = {}
    for k in ("label", "status", "note"):
        if k in data:
            kwargs[k] = data[k]
    conn = get_db()
    ok = update_basket_item(conn, item_id, **kwargs)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/basket/bulk", methods=["PUT"])
def api_basket_bulk_update():
    data = request.get_json(force=True)
    error = _validate_updates(data)
    if error:
        return jsonify(error=error), 400
    ids = data.get("ids", [])
    label = data.get("label")
    status = data.get("status")
    conn = get_db()
    n = bulk_update_basket(conn, ids, label=label, status=status)
    return jsonify({"updated": n})


@bp.route("/api/basket/<int:item_id>", methods=["DELETE"])
def api_basket_delete(item_id: int):
    conn = get_db()
    ok = delete_basket_item(conn, item_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/basket/bulk", methods=["DELETE"])
def api_basket_bulk_delete():
    data = request.get_json(force=True)
    ids = data.get("ids", [])
    keys = data.get("keys", [])
    conn = get_db()
    n = 0
    if ids:
        n += bulk_delete_basket(conn, ids)
    if keys:
        n += bulk_delete_basket_by_keys(conn, keys)
    return jsonify({"deleted": n})


@bp.route("/api/basket/<int:item_id>/data", methods=["GET"])
def api_basket_item_data(item_id: int):
    conn = get_db()
    item = get_basket_item(conn, item_id)
    if not item:
        return jsonify({"error": "not found"}), 404
    rows = _merged_item_rows(conn, item)
    return jsonify({"item": item, "data": rows})


@bp.route("/api/basket/bulk-data", methods=["POST"])
def api_basket_bulk_data():
    """Fetch all-column data for multiple basket items.

    ``compact=1`` returns column-major, chart-sized arrays. Full long-format
    rows remain available for compatibility and exact-data workflows.
    """
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids required"}), 400
    if len(ids) > 100:
        return jsonify({"error": "at most 100 ids per request"}), 400
    compact = bool(data.get("compact", False))
    try:
        max_points = max(50, min(1000, int(data.get("max_points", 400))))
    except (TypeError, ValueError):
        return jsonify({"error": "max_points must be an integer"}), 400
    try:
        ids = list(dict.fromkeys(int(item_id) for item_id in ids))
    except (TypeError, ValueError):
        return jsonify({"error": "ids must be integers"}), 400
    conn = get_db()
    result = {}
    for item_id in ids:
        item = get_basket_item(conn, item_id)
        if not item:
            result[str(item_id)] = []
            continue
        rows = _merged_item_rows(conn, item)
        result[str(item_id)] = (
            _compact_rows(rows, max_points=max_points) if compact else rows
        )
    return jsonify(result)


def _rows_from_stored_values(item: dict) -> list[dict]:
    """담을 때 저장해 둔 구간 값을 long format 행으로. 타임스탬프는 알 수 없어 인덱스."""
    values = item.get("values")
    if values is None:
        try:
            values = json.loads(item.get("values_json") or "[]")
        except (ValueError, TypeError):
            return []
    col = item.get("col_name") or ""
    return [{"timestamp": i, "col_name": col, "value": v}
            for i, v in enumerate(values)]


def _rows_from_stored_series(item: dict) -> list[dict]:
    """Return the captured multi-column snapshot as long-format rows."""
    series = item.get("series")
    if series is None:
        try:
            series = json.loads(item.get("series_json") or "[]")
        except (ValueError, TypeError):
            return []
    rows = []
    for entry in series or []:
        col = entry.get("col_name") or ""
        values = entry.get("values") or []
        timestamps = entry.get("timestamps") or []
        for idx, value in enumerate(values):
            timestamp = timestamps[idx] if idx < len(timestamps) else idx
            rows.append({"timestamp": timestamp, "col_name": col, "value": value})
    return rows


def _merged_item_rows(conn, item: dict) -> list[dict]:
    """Use captured search columns verbatim and live DB only as enrichment.

    Similarity belongs to the captured waveform. If source data changes later,
    the Basket must not display a different waveform under the old score.
    """
    captured = _rows_from_stored_series(item)
    live = get_all_columns_for_segment(
        conn, item["source_key"], item["start_ts"], item["end_ts"],
        dataset_key=item.get("dataset_key", ""),
    )
    if not captured:
        return live or _rows_from_stored_values(item)
    captured_cols = {row["col_name"] for row in captured}
    enriched = [row for row in live if row["col_name"] not in captured_cols]
    return captured + enriched


def _compact_rows(rows: list[dict], max_points: int = 400) -> dict:
    """Convert long rows to compact column-major arrays for canvas charts."""
    grouped: dict[str, dict[str, list]] = {}
    for row in rows:
        col = row.get("col_name", "")
        series = grouped.setdefault(col, {"timestamps": [], "values": []})
        series["timestamps"].append(row.get("timestamp"))
        series["values"].append(row.get("value"))

    compact = []
    for col, series in grouped.items():
        timestamps = series["timestamps"]
        values = series["values"]
        original_points = len(values)
        if original_points > max_points:
            # Preserve local extrema instead of uniform sampling, which can
            # make narrow spikes disappear. Exact rows remain in CSV export.
            interior = original_points - 2
            bucket_count = max(1, (max_points - 2) // 2)
            indices = [0]
            for bucket in range(bucket_count):
                start = 1 + (bucket * interior) // bucket_count
                end = 1 + ((bucket + 1) * interior) // bucket_count
                if end <= start:
                    continue
                numeric = []
                for idx in range(start, end):
                    try:
                        value = float(values[idx])
                    except (TypeError, ValueError):
                        continue
                    if value == value and value not in (float("inf"), float("-inf")):
                        numeric.append((value, idx))
                if numeric:
                    chosen = {min(numeric)[1], max(numeric)[1]}
                    indices.extend(sorted(chosen))
                else:
                    indices.append(start)
            indices.append(original_points - 1)
            if len(indices) > max_points:
                indices = indices[:max_points - 1] + [original_points - 1]
            timestamps = [timestamps[i] for i in indices]
            values = [values[i] for i in indices]
        compact.append({
            "col_name": col,
            "timestamps": timestamps,
            "values": values,
            "original_points": original_points,
        })
    return {"series": compact, "columns": [entry["col_name"] for entry in compact]}


@bp.route("/api/basket/export", methods=["GET"])
def api_basket_export():
    status = request.args.get("status", "confirmed")
    label = request.args.get("label")
    dataset_key = request.args.get("dataset_key")
    conn = get_db()
    # Keep only lightweight metadata before streaming. Snapshot JSON is loaded
    # one item at a time below, otherwise a large export retains every waveform
    # in memory before the first byte is sent.
    items = get_basket_items(conn, status=status, dataset_key=dataset_key,
                             include_values=False)
    if label:
        items = [it for it in items if it["label"] == label]

    conn.close()

    def generate():
        # Response streaming starts after the original request teardown.
        # Own a fresh connection inside the generator's context.
        conn = get_db()
        buf = io.StringIO()
        writer = csv.writer(buf)
        try:
            writer.writerow([
                "basket_id", "label", "dataset_key", "source_key",
                "timestamp", "col_name", "value", "similarity", "distance",
                "search_method", "normalize", "context", "note"
            ])
            buffered_rows = 1
            for it in items:
                full_item = get_basket_item(conn, it["id"]) or it
                # Keep only one item's rows in memory; the response is streamed.
                for row in _merged_item_rows(conn, full_item):
                    writer.writerow([
                        it["id"], it["label"], it["dataset_key"], it["source_key"],
                        row["timestamp"], row["col_name"], row["value"],
                        it.get("similarity"), it.get("distance"),
                        it.get("search_method", ""), it.get("normalize"),
                        it["context"], it["note"],
                    ])
                    buffered_rows += 1
                    if buffered_rows >= 1000:
                        yield buf.getvalue()
                        buf.seek(0)
                        buf.truncate(0)
                        buffered_rows = 0
            if buf.tell():
                yield buf.getvalue()
        finally:
            conn.close()

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=basket_export.csv"},
    )
