# -*- coding: utf-8 -*-
"""Data transformation routes -- concat segments, split continuous."""
from __future__ import annotations

import logging
import uuid
from collections import OrderedDict

import numpy as np
import pandas as pd
from flask import Blueprint, request, jsonify

from backend.app_config import cfg
from backend.session_store import DATA_STORE
from backend.helpers import _safe_list, _safe_stats
from backend.preprocessing import _get_boundary_key
from backend.source_provenance import sampling_interval, complete_window
from backend.db import (
    get_db, get_dataset, get_dataset_sources, get_column_info,
    get_segments, get_all_segment_values,
    has_preprocessed_data,
    get_ts_column_all_sources, get_ts_column_preprocessed_all_sources,
)

bp = Blueprint("data_transform", __name__)
logger = logging.getLogger(__name__)


@bp.route("/api/concat_segments/<dataset_key>/<col_name>")
def concat_segments(dataset_key, col_name):
    """Concatenate segments into continuous time series with gap markers."""
    overlay_cols_str = request.args.get("overlay_cols", "")
    overlay_cols = [c.strip() for c in overlay_cols_str.split(",") if c.strip()]

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 400

    col_info = get_column_info(conn, dataset_key, col_name)
    use_pp = has_preprocessed_data(conn, ds["id"])

    if ds["type"] == "collection":
        if use_pp:
            all_source_data = get_ts_column_preprocessed_all_sources(
                conn, ds["id"], col_name)
        else:
            all_source_data = get_ts_column_all_sources(conn, ds["id"], col_name)
    else:
        sources = get_dataset_sources(conn, ds["id"])
        if not sources:
            conn.close()
            return jsonify(error="no sources"), 400
        seg_meta = get_segments(conn, sources[0]["key"], col_name)
        seg_vals = get_all_segment_values(
            conn, sources[0]["key"], col_name, seg_meta)
        all_source_data = []
        for meta, (ts_list, val_list) in zip(seg_meta, seg_vals):
            all_source_data.append({
                "source_key": sources[0]["key"],
                "segment_key": str(meta["seg_index"]),
                "description": meta["seg_label"],
                "timestamps": ts_list,
                "values": val_list,
            })

    # Load overlay columns
    overlays = []
    for ov_col in overlay_cols:
        ov_col_info = get_column_info(conn, dataset_key, ov_col)
        ov_name = ov_col_info["label"] if ov_col_info else ov_col
        ov_unit = ov_col_info.get("unit", "") if ov_col_info else ""
        if ds["type"] == "collection":
            if use_pp:
                ov_all = get_ts_column_preprocessed_all_sources(
                    conn, ds["id"], ov_col)
            else:
                ov_all = get_ts_column_all_sources(conn, ds["id"], ov_col)
        else:
            ov_meta = get_segments(conn, sources[0]["key"], ov_col)
            ov_vals_list = get_all_segment_values(
                conn, sources[0]["key"], ov_col, ov_meta)
            ov_all = []
            for m, (t, v) in zip(ov_meta, ov_vals_list):
                ov_all.append({"source_key": sources[0]["key"],
                               "segment_key": str(m["seg_index"]), "timestamps": t, "values": v})
        ov_labels_all, ov_vals_all = [], []
        for sd in ov_all:
            ov_labels_all.extend(sd["timestamps"])
            ov_vals_all.extend(sd["values"])
        ov_arr = np.array(ov_vals_all, dtype=np.float64) if ov_vals_all else np.array(
            [], dtype=np.float64)
        overlays.append({
            "name": ov_name,
            "unit": ov_unit,
            "col_name": ov_col,
            "values": _safe_list(ov_arr),
            "labels": ov_labels_all,
            "stats": _safe_stats(ov_arr),
            "_ov_all": ov_all,  # Store raw source data for gap insertion
        })

    idx_type = ds.get("index_type", "datetime") or "datetime"

    gap_size_default = cfg("segment.gap_size_default")
    gap_size_min = cfg("segment.gap_size_min")
    gap_size_max = cfg("segment.gap_size_max")
    try:
        gap_size = int(request.args.get("gap_size", gap_size_default))
    except (TypeError, ValueError):
        conn.close()
        return jsonify(error="gap_size must be an integer"), 400
    gap_size = max(gap_size_min, min(gap_size, gap_size_max))

    # Concatenate with gap markers
    all_labels = []
    all_values = []
    seg_boundaries = []
    seg_names = []
    gap_ranges = []
    source_spans = []
    running_idx = 0
    for i, sd in enumerate(all_source_data):
        if i > 0:
            gap_start = len(all_values)
            for _g in range(gap_size):
                all_labels.append("")
                all_values.append(None)
            gap_ranges.append([gap_start, len(all_values) - 1])
        seg_boundaries.append(len(all_values))
        seg_names.append(sd.get("description", f"seg_{i}"))

        if idx_type == "numeric":
            for j, ts in enumerate(sd["timestamps"]):
                running_idx += 1
                all_labels.append(str(running_idx))
        else:
            all_labels.extend(sd["timestamps"])
        all_values.extend(sd["values"])
        source_spans.append({
            "start_idx": seg_boundaries[-1], "end_idx": len(all_values),
            "source_key": sd.get("source_key", ""),
            "source_label": sd.get("description") or sd.get("source_key", ""),
            "parent_segment_key": sd.get("segment_key", str(i)),
            "timestamps": sd["timestamps"],
        })

    # Insert gaps into overlay data too (use stored _ov_all to match primary column sources)
    for ov in overlays:
        ov_labels_gap = []
        ov_vals_gap = []
        ov_sources = ov.get("_ov_all", [])  # Use already-fetched data
        by_source = {(s.get("source_key", ""), s.get("segment_key", "")): s
                     for s in ov_sources}
        for j, primary in enumerate(all_source_data):
            if j > 0:
                for _g in range(gap_size):
                    ov_labels_gap.append("")
                    ov_vals_gap.append(None)
            osd = by_source.get((primary.get("source_key", ""), primary.get("segment_key", "")), {})
            lookup = dict(zip(osd.get("timestamps", []), osd.get("values", [])))
            ov_labels_gap.extend(primary["timestamps"])
            ov_vals_gap.extend(lookup.get(t) for t in primary["timestamps"])
        if ov_labels_gap:
            ov["labels"] = ov_labels_gap
            ov["values"] = ov_vals_gap
        # Clean up temporary data
        if "_ov_all" in ov:
            del ov["_ov_all"]

    conn.close()

    values = np.array([v if v is not None else np.nan for v in all_values],
                      dtype=np.float64)
    sid = str(uuid.uuid4())[:8]
    DATA_STORE[sid] = {
        "filename": f"{dataset_key}_{col_name}_concat",
        "values": values,
        "labels": all_labels,
        "overlays": overlays,
        "seg_boundaries": seg_boundaries,
        "gap_ranges": gap_ranges,
        "source_spans": source_spans,
        "dataset_key": dataset_key,
    }

    title = (f"{col_info['label']} ({col_info['unit']})"
             if col_info and col_info.get("unit")
             else (col_info["label"] if col_info else col_name))

    return jsonify(
        sid=sid,
        title=title + " (concat)",
        index_type=idx_type,
        values=_safe_list(values),
        labels=all_labels,
        overlays=overlays,
        seg_boundaries=seg_boundaries,
        seg_names=seg_names,
        gap_ranges=gap_ranges,
        source_spans=source_spans,
        gap_size=gap_size,
        stats=_safe_stats(values),
    )


@bp.route("/api/split_continuous/<sid>", methods=["POST"])
def split_continuous(sid):
    """Split continuous data into segments.
    - boundary_minutes > 0: timestamp boundary split
    - boundary_minutes == 0: fixed point count split"""
    data = DATA_STORE.get(sid)
    if data is None:
        return jsonify(error="session not found"), 400

    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON object required"), 400
    try:
        body["seg_len"] = int(body.get("seg_len", cfg("segment.default_length")))
        body["boundary_minutes"] = int(body.get("boundary_minutes", 0))
        body["stride"] = int(body.get("stride", 0))
    except (TypeError, ValueError):
        return jsonify(error="Split sizes must be integers"), 400
    if body["seg_len"] < 1 or body["boundary_minutes"] < 0 or body["stride"] < 0:
        return jsonify(error="Split length must be positive; boundary and stride must be nonnegative"), 400
    if data.get("source_spans"):
        # Each original source/segment is a separate timeline, even when its
        # timestamps happen to coincide with another station's timestamps.
        segments = []
        skipped = 0
        for span in data["source_spans"]:
            start, end = span["start_idx"], span["end_idx"]
            local = {"values": data["values"][start:end],
                     "labels": span["timestamps"]}
            local_body = dict(body)
            local_body["require_complete"] = True
            local_body["overlay_values"] = [
                dict(ov, values=ov["values"][start:end]) for ov in data.get("overlays", [])]
            response = _split_single_timeline(local, local_body)
            if isinstance(response, tuple):
                payload = response[0].get_json()
                # Empty/short individual timelines do not invalidate other stations.
                if payload.get("error", "").startswith(("No segments", "No continuous", "Valid data")):
                    skipped += payload.get("skipped_partial", 1)
                    continue
                return response
            payload = response.get_json()
            skipped += payload.get("skipped_partial", 0)
            for seg in payload["segments"]:
                seg.update(source_key=span["source_key"], source_label=span["source_label"],
                           parent_segment_key=span["parent_segment_key"],
                           dataset_key=data.get("dataset_key", ""))
                seg["key"] = f'{span["source_key"]}/{span["parent_segment_key"]}/{seg["start_idx"]}:{seg["end_idx"]}'
                seg["label"] = f'{span["source_label"]} · {seg["label"]}'
                seg["index"] = len(segments)
                seg["concat_start_idx"] = start + seg["start_idx"]
                seg["concat_end_idx"] = start + seg["end_idx"]
                segments.append(seg)
        if not segments:
            return jsonify(error="No segments found within individual source boundaries."), 400
        lengths = [s["length"] for s in segments]
        return jsonify(segments=segments, count=len(segments), seg_len=int(np.median(lengths)),
                       boundary_minutes=int(body.get("boundary_minutes", 0)),
                       stride=int(body.get("stride", 0)), variable_length=len(set(lengths)) > 1,
                       length_stats={"min": min(lengths), "max": max(lengths), "median": int(np.median(lengths))},
                       skipped_partial=skipped, source_preserved=True)
    return _split_single_timeline(data, body)


def _split_single_timeline(data, body):
    seg_len = int(body.get("seg_len", cfg("segment.default_length")))
    boundary_minutes = int(body.get("boundary_minutes", 0))
    stride = int(body.get("stride", 0)) or seg_len  # default: no overlap
    overlay_values = body.get("overlay_values", [])

    values = data["values"]
    labels = data["labels"]
    n = len(values)
    require_complete = body.get("require_complete", False)
    interval = sampling_interval(labels) if require_complete else None

    # NaN/null positions
    gap_positions = set()
    for i in range(n):
        if isinstance(values, np.ndarray):
            if not np.isfinite(values[i]):
                gap_positions.add(i)
        elif values[i] is None or not np.isfinite(values[i]):
            gap_positions.add(i)

    # ---- Timestamp boundary split ----
    if boundary_minutes > 0:
        ref_start = None
        ts_parsed = []
        for lbl in labels:
            try:
                t = pd.Timestamp(lbl)
                ts_parsed.append(t)
                if ref_start is None and not pd.isna(t):
                    ref_start = t.normalize()
            except Exception:
                ts_parsed.append(None)

        if ref_start is None:
            return jsonify(
                error="Cannot parse timestamps for boundary split."), 400

        logger.debug(f"Split ref_start: {ref_start}, total labels: {len(labels)}, parsed timestamps: {len(ts_parsed)}")
        valid_ts = [t for t in ts_parsed if t is not None and not pd.isna(t)]
        if valid_ts:
            logger.debug(f"First 5 valid timestamps: {valid_ts[:5]}")

        groups: dict = OrderedDict()
        for i in range(n):
            if i in gap_positions:
                continue
            t = ts_parsed[i] if i < len(ts_parsed) else None
            if t is None or pd.isna(t):
                continue
            bkey = _get_boundary_key(t, boundary_minutes, ref_start)
            if bkey not in groups:
                groups[bkey] = []
            groups[bkey].append(i)

        logger.debug(f"Split boundary_minutes={boundary_minutes}, total_points={n}, gap_points={len(gap_positions)}, groups={len(groups)}")
        if len(groups) <= 10:
            for bkey, indices in list(groups.items())[:10]:
                logger.debug(f"Group {bkey}: {len(indices)} points, indices range {min(indices)}-{max(indices)}")

        # Estimate data interval
        _intervals = []
        for k in range(min(len(ts_parsed) - 1, 20)):
            t0, t1 = ts_parsed[k], ts_parsed[k + 1]
            if (t0 is not None and t1 is not None
                    and not pd.isna(t0) and not pd.isna(t1)):
                diff = (t1 - t0).total_seconds() / 60
                if 0 < diff < 525960:
                    _intervals.append(diff)
        _data_interval = float(np.median(_intervals)) if _intervals else 0
        _expected_pts = (boundary_minutes / _data_interval
                         if _data_interval > 0 else 0)

        logger.debug(f"Split detected data interval: {_data_interval} min from {len(_intervals)} valid pairs")
        if _intervals:
            logger.debug(f"First 5 intervals: {_intervals[:5]}")
        logger.debug(f"Expected points per boundary: {_expected_pts}")

        segments = []
        _skipped_partial = 0
        for bkey, indices in groups.items():
            if len(indices) < 1:
                continue
            seg_vals = [values[i] for i in indices]
            seg_labels_list = [labels[i] for i in indices]
            valid_vals = [
                v for v in seg_vals
                if v is not None and not (isinstance(v, float) and np.isnan(v))
            ]
            if len(valid_vals) == 0:
                continue

            first_ts = ts_parsed[indices[0]]
            if require_complete:
                boundary_end = (bkey + pd.DateOffset(years=1) if boundary_minutes == 525600
                                else bkey + pd.Timedelta(minutes=boundary_minutes))
                if len(indices) == 1 and interval == boundary_end - bkey:
                    is_complete = first_ts == bkey and np.isfinite(seg_vals[0])
                else:
                    is_complete = complete_window(
                        pd.DatetimeIndex([ts_parsed[i] for i in indices]),
                        seg_vals, bkey, boundary_end, interval)
                if not is_complete:
                    _skipped_partial += 1
                    continue
            if not require_complete and first_ts is not None and not pd.isna(first_ts):
                gap_from_boundary = (first_ts - bkey).total_seconds() / 60
                tolerance = _data_interval * 1.5 if _data_interval > 0 else 5
                if gap_from_boundary > tolerance:
                    _skipped_partial += 1
                    continue
            if not require_complete and _expected_pts > 0 and len(valid_vals) < _expected_pts * 0.8:
                _skipped_partial += 1
                continue

            seg = {
                "start_idx": indices[0], "end_idx": indices[-1],
                "index": len(segments),
                "label": str(bkey),
                "values": _safe_list(seg_vals),
                "labels": seg_labels_list,
                "timestamps": seg_labels_list,
                "start": seg_labels_list[0] if seg_labels_list else "",
                "end": seg_labels_list[-1] if seg_labels_list else "",
                "length": len(seg_vals),
            }
            if overlay_values:
                seg_overlays = []
                for ov in overlay_values:
                    ov_seg_vals = [ov["values"][i] for i in indices
                                   if i < len(ov["values"])]
                    seg_overlays.append(
                        {"name": ov["name"], "col_name": ov.get("col_name", ""), "values": ov_seg_vals})
                seg["overlays"] = seg_overlays
            segments.append(seg)

        if not segments:
            return jsonify(
                error="No segments found for given boundary.", skipped_partial=_skipped_partial), 400

        lengths = [len(s["values"]) for s in segments]
        return jsonify(
            count=len(segments),
            segments=segments,
            seg_len=int(np.median(lengths)),
            boundary_minutes=boundary_minutes,
            variable_length=True,
            length_stats={
                "min": int(min(lengths)),
                "max": int(max(lengths)),
                "median": int(np.median(lengths)),
            },
            skipped_partial=_skipped_partial,
        )

    # ---- Fixed point count split ----
    min_seg_len = cfg("segment.min_length")
    if seg_len < min_seg_len:
        return jsonify(
            error=f"Segment length must be >= {min_seg_len}"), 400

    valid_count = sum(
        1 for v in values
        if not (v is None or (isinstance(v, float) and np.isnan(v)))
    )
    if valid_count < seg_len:
        return jsonify(
            error=f"Valid data ({valid_count}pts) shorter than segment "
                  f"length ({seg_len}pts).",
            valid_count=valid_count,
        ), 400

    segments = []
    idx = 0
    seg_index = 0
    while idx < n:
        if idx in gap_positions:
            idx += 1
            continue
        if idx + seg_len > n:
            break
        window_gaps = [g for g in gap_positions if idx <= g < idx + seg_len]
        if window_gaps:
            idx = max(window_gaps) + 1
            continue

        seg_vals = values[idx: idx + seg_len]
        seg_vals = _safe_list(seg_vals)
        seg_labels_list = labels[idx: idx + seg_len]
        if require_complete and interval is not None:
            actual = pd.to_datetime(seg_labels_list, errors="coerce")
            if (actual.isna().any() or not all(b - a == interval for a, b in zip(actual[:-1], actual[1:]))):
                idx += stride
                continue
        seg_label = (str(seg_labels_list[0])
                     if seg_labels_list else str(seg_index))
        seg = {
            "start_idx": idx, "end_idx": idx + seg_len - 1,
            "index": seg_index,
            "label": seg_label,
            "values": seg_vals,
            "labels": seg_labels_list,
            "timestamps": seg_labels_list,
            "start": seg_labels_list[0] if seg_labels_list else "",
            "end": seg_labels_list[-1] if seg_labels_list else "",
            "length": len(seg_vals),
        }
        if overlay_values:
            seg_overlays = []
            for ov in overlay_values:
                ov_vals = (ov["values"][idx: idx + seg_len]
                           if idx + seg_len <= len(ov["values"]) else [])
                seg_overlays.append({"name": ov["name"], "col_name": ov.get("col_name", ""), "values": ov_vals})
            seg["overlays"] = seg_overlays
        segments.append(seg)
        seg_index += 1
        idx += stride

    if not segments:
        return jsonify(
            error=f"No continuous segments of length {seg_len}pts found.",
            valid_count=valid_count,
        ), 400

    return jsonify(
        count=len(segments),
        segments=segments,
        seg_len=seg_len,
        stride=stride,
    )
