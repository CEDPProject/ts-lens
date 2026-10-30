# -*- coding: utf-8 -*-
"""Sliding-window search routes -- pattern search, segment compare, boundaries."""
from __future__ import annotations

import logging
import math
import numpy as np
from flask import Blueprint, request, jsonify

from backend.app_config import cfg_search
from backend.session_store import DATA_STORE
from backend.helpers import (
    _safe_list, z_normalize, compute_distance,
    make_nan_handler,
    distance_to_similarity, resample_array, _resample,
    auto_downsample,
    _VALID_METHODS, _MAX_COMPARE_LEN,
)
from backend.preprocessing import find_calendar_boundaries
from backend.source_provenance import window_source, span_for_window
from backend.db import (
    get_db, get_dataset, get_dataset_sources,
    get_ts_column_all_sources, get_segments, get_all_segment_values,
)

bp = Blueprint("search", __name__)
logger = logging.getLogger(__name__)


@bp.errorhandler(ValueError)
def invalid_search_input(error):
    return jsonify(error=str(error)), 400


def _validate_inline_segments(segments):
    if not isinstance(segments, list):
        raise ValueError("segments must be an array")
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("values"), list):
            raise ValueError("Each segment requires a values array")
        if np.asarray(segment["values"], dtype=float).ndim != 1:
            raise ValueError("Segment values must be one-dimensional")
        overlays = segment.get("overlays", [])
        if not isinstance(overlays, list):
            raise ValueError("overlays must be an array")
        for overlay in overlays:
            if not isinstance(overlay, dict) or not isinstance(overlay.get("values"), list):
                raise ValueError("Each overlay requires a values array")
            if np.asarray(overlay["values"], dtype=float).ndim != 1:
                raise ValueError("Overlay values must be one-dimensional")


@bp.route("/api/search/<sid>", methods=["POST"])
def search_pattern(sid):
    """Sliding-window similarity search."""
    entry = DATA_STORE.get(sid)
    if not entry or "values" not in entry:
        return jsonify(error="Load data first"), 404

    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify(error="JSON object required"), 400
    q_start = body.get("query_start", 0)
    q_end = body.get("query_end", 0)
    method = body.get("method", cfg_search("default_method"))
    normalize = body.get("normalize", cfg_search("normalize.enabled"))
    top_k = body.get("top_k", cfg_search("default_top_k"))
    threshold = body.get("threshold", 0)
    stride = body.get("stride", 1)
    fixed_len = body.get("fixed_len", 0)
    exclude_query = body.get("exclude_query", True)
    align = body.get("align", False)
    nan_mode = body.get("nan_mode", "interpolate")
    nan_max_ratio = body.get("nan_max_ratio", 0.5)
    nan_method = body.get("nan_method", "linear")
    max_compare_len = body.get("max_compare_len", _MAX_COMPARE_LEN)

    if not isinstance(method, str) or method not in _VALID_METHODS:
        return jsonify(error=f"Unsupported method: {method}"), 400
    try:
        q_start = int(q_start)
        q_end = int(q_end)
        stride = max(1, int(stride))
        fixed_len = max(0, int(fixed_len))
        top_k = max(1, int(top_k))
        threshold = float(threshold)
        nan_max_ratio = float(nan_max_ratio)
        max_compare_len = max(3, min(_MAX_COMPARE_LEN, int(max_compare_len)))
    except (TypeError, ValueError, OverflowError):
        return jsonify(error="Invalid numeric search option"), 400
    if not math.isfinite(threshold) or not math.isfinite(nan_max_ratio):
        return jsonify(error="Search options must be finite"), 400
    nan_max_ratio = max(0.0, min(1.0, nan_max_ratio))

    data = entry["values"]
    labels = entry.get("labels", [])
    overlay_data = entry.get("overlays", [])
    N = len(data)

    _hn = make_nan_handler(nan_mode, nan_max_ratio, nan_method)

    # External query values (LLM-generated patterns or sketch)
    external_query = body.get("query_values", None)
    external_query_original = None  # Store original for response
    matched_ov_names = []

    if external_query is not None:
        if not isinstance(external_query, list):
            return jsonify(error="query_values must be an array"), 400
        external_query_original = list(external_query)  # Save original for response
        try:
            query_raw = np.array(external_query, dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            return jsonify(error="query_values must be numeric"), 400
        if query_raw.ndim != 1 or not np.isfinite(query_raw).all():
            return jsonify(error="query_values must be a finite numeric array"), 400
        if len(query_raw) < 3:
            return jsonify(error="Query array too short"), 400
        query_len = len(query_raw)
        search_len = fixed_len if fixed_len > 0 else query_len
        if fixed_len > 0 and query_len != fixed_len:
            query_raw = resample_array(query_raw, fixed_len)
        query = z_normalize(query_raw)
        normalize = True
        q_start = -1
        q_end = -1
        exclude_query = False
        query_nan_mask = np.zeros(len(query_raw), dtype=bool)
        # Reuse primary query for overlay columns (same as preset fallback)
        ov_queries = []
        for ov in overlay_data:
            ov_vals = np.array(ov.get("values", []), dtype=np.float64)
            if len(ov_vals) > 0:
                ov_queries.append((ov_vals, query.copy()))
                matched_ov_names.append(ov.get("name", ov.get("col_name", "")))
        n_cols = 1 + len(ov_queries)
    else:
        if q_start < 0 or q_end >= N or q_start >= q_end:
            return jsonify(error="Invalid query range"), 400
        if entry.get("source_spans") and window_source(entry, q_start, q_end) is None:
            return jsonify(error="Select a query within one original source segment."), 400

        query_original = data[q_start: q_end + 1].copy()
        query_nan_mask = np.isnan(query_original)
        query_raw = _hn(query_original.copy())
        if query_raw is None:
            return jsonify(error="Query segment has missing values"), 400
        query_len = len(query_raw)
        search_len = fixed_len if fixed_len > 0 else query_len

        if fixed_len > 0 and query_len != fixed_len:
            query_raw = resample_array(query_raw, fixed_len)
        query = z_normalize(query_raw) if normalize else query_raw.copy()

        # Overlay query preparation
        ov_queries = []
        for ov in overlay_data:
            ov_vals = np.array(ov.get("values", []), dtype=np.float64)
            if len(ov_vals) < q_end + 1:
                continue
            ov_q = ov_vals[q_start: q_end + 1].copy()
            ov_q = _hn(ov_q)
            if ov_q is None:
                continue
            if fixed_len > 0 and len(ov_q) != fixed_len:
                ov_q = resample_array(ov_q, fixed_len)
            ov_queries.append((ov_vals, z_normalize(ov_q) if normalize else ov_q.copy()))
            matched_ov_names.append(ov.get("name", ov.get("col_name", "")))

        n_cols = 1 + len(ov_queries)

    # Sliding window start indices
    if align and fixed_len > 0:
        boundary_starts = find_calendar_boundaries(labels, fixed_len)
    else:
        boundary_starts = None

    # Auto-downsampling for large queries
    _downsample_applied = False
    _compare_len = search_len
    if search_len > max_compare_len:
        _downsample_applied = True
        _compare_len = max_compare_len
        query = _resample(query, _compare_len)
        for oi in range(len(ov_queries)):
            ov_full, ov_q = ov_queries[oi]
            ov_queries[oi] = (ov_full, _resample(ov_q, _compare_len))
        ds_ratio = search_len / _compare_len
        auto_stride = max(stride, int(ds_ratio))
        stride = auto_stride

    if boundary_starts is not None:
        start_indices = boundary_starts
    elif entry.get("source_spans"):
        start_indices = [i for span in entry["source_spans"]
                         for i in range(span["start_idx"], span["end_idx"] - search_len + 1, stride)]
    else:
        start_indices = range(0, N - search_len + 1, stride)

    # Gap ranges (block windows crossing concat boundaries)
    gap_ranges = entry.get("gap_ranges", [])

    def _crosses_boundary(start_i, end_i):
        if entry.get("source_spans") and span_for_window(entry, start_i, end_i) is None:
            return True
        if not gap_ranges:
            return False
        for gr in gap_ranges:
            gap_s, gap_e = gr[0], gr[1]
            if start_i <= gap_e and end_i >= gap_s:
                return True
        return False

    # Sliding window search
    candidates = []
    _dbg_total = 0
    _dbg_skip_query = 0
    _dbg_skip_boundary = 0
    _dbg_skip_nan = 0
    _dbg_skip_overlay = 0
    comparison_count = len(start_indices) * n_cols
    dtw_prefiltered = (
        method == "dtw" and comparison_count > int(
            cfg_search("dtw.prefilter_min_comparisons")
        )
    )
    scan_method = "euclidean" if dtw_prefiltered else method

    def _distance_at(i, compare_method):
        """Calculate one window; None means unusable overlay/NaN data."""
        seg_raw = _hn(data[i: i + search_len].copy())
        if seg_raw is None:
            return None
        seg = z_normalize(seg_raw) if normalize else seg_raw.copy()
        if _downsample_applied:
            seg = _resample(seg, _compare_len)
        col_dists = [compute_distance(query, seg, compare_method)]
        for ov_full, ov_q in ov_queries:
            ov_seg = ov_full[i: i + search_len].copy()
            if len(ov_seg) != search_len:
                return None
            ov_seg = _hn(ov_seg)
            if ov_seg is None:
                return None
            ov_seg = z_normalize(ov_seg) if normalize else ov_seg.copy()
            if _downsample_applied:
                ov_seg = _resample(ov_seg, _compare_len)
            col_dists.append(compute_distance(ov_q, ov_seg, compare_method))
        dist = sum(col_dists) / n_cols
        return dist, col_dists

    for i in start_indices:
        _dbg_total += 1
        if exclude_query and not (i + search_len <= q_start or i > q_end):
            _dbg_skip_query += 1
            continue

        if _crosses_boundary(i, i + search_len - 1):
            _dbg_skip_boundary += 1
            continue

        calculated = _distance_at(i, scan_method)
        if calculated is None:
            _dbg_skip_overlay += 1
            continue
        dist, col_dists = calculated
        sim = distance_to_similarity(dist, scan_method)
        candidates.append((i, i + search_len - 1, sim, dist, col_dists))

    if dtw_prefiltered:
        # A generous Euclidean shortlist bounds fallback-DTW cost. Recompute
        # every published candidate with DTW; response metadata makes the
        # approximate candidate-generation stage explicit.
        candidates.sort(key=lambda x: x[2], reverse=True)
        multiplier = int(cfg_search("dtw.prefilter_multiplier"))
        # Keep the strongest local alternatives plus geographically diverse
        # windows. A global top-N alone can consist almost entirely of mutually
        # overlapping windows and collapse to one result after NMS.
        global_limit = min(len(candidates), 200)
        max_diverse = int(cfg_search("dtw.prefilter_max_diverse"))
        diverse_target = min(len(candidates), max_diverse,
                             max(30, top_k * multiplier))
        shortlist = list(candidates[:global_limit])
        shortlist_starts = {item[0] for item in shortlist}
        diverse_ranges = []
        for item in candidates:
            start, end = item[0], item[1]
            if any(start <= used_end and end >= used_start
                   for used_start, used_end in diverse_ranges):
                continue
            diverse_ranges.append((start, end))
            if start not in shortlist_starts:
                shortlist.append(item)
                shortlist_starts.add(start)
            if len(diverse_ranges) >= diverse_target:
                break
        shortlist_limit = len(shortlist)
        reranked = []
        for start, end, _sim, _dist, _cols in shortlist:
            calculated = _distance_at(start, "dtw")
            if calculated is None:
                continue
            dist, col_dists = calculated
            reranked.append((start, end, distance_to_similarity(dist, "dtw"),
                             dist, col_dists))
        candidates = reranked

    # Sort by similarity descending
    candidates.sort(key=lambda x: x[2], reverse=True)

    # Greedy NMS (remove overlapping regions)
    results = []
    used = set()
    for start, end, sim, dist, col_dists in candidates:
        if len(results) >= top_k:
            break
        if threshold > 0 and sim < threshold:
            break
        if any(idx in used for idx in range(start, end + 1)):
            continue
        r_entry = {
            "start": int(start),
            "end": int(end),
            "length": int(end - start + 1),
            "similarity": round(float(sim), 6),
            "distance": round(float(dist), 6),
            "values": _safe_list(data[start: end + 1]),
        }
        r_entry.update(window_source(entry, start, end) or {})
        if ov_queries:
            r_overlays = []
            for ov_full, _ov_q in ov_queries:
                r_overlays.append(_safe_list(ov_full[start: end + 1]))
            r_entry["overlay_values"] = r_overlays
            if len(col_dists) > 1:
                r_entry["col_similarities"] = [
                    round(float(distance_to_similarity(d, method)), 6)
                    for d in col_dists
                ]
        results.append(r_entry)
        used.update(range(start, end + 1))

    sims = [r["similarity"] for r in results]
    summary = {}
    if sims:
        summary = {
            "count": len(results),
            "best": round(max(sims), 4),
            "worst": round(min(sims), 4),
            "avg": round(sum(sims) / len(sims), 4),
        }

    align_info = {
        "requested": align,
        "applied": boundary_starts is not None,
        "mode": "calendar" if align else None,
        "boundaries_found": len(boundary_starts) if boundary_starts is not None else 0,
    }

    # Query overlay values
    q_overlay_values = []
    for ov_full, _ov_q in ov_queries:
        if external_query_original is not None:
            # External query: no overlay slicing possible
            q_overlay_values.append([])
        else:
            q_overlay_values.append(_safe_list(ov_full[q_start: q_end + 1]))

    ov_names = matched_ov_names

    # Use external query values if provided, otherwise slice from data
    query_values_response = (
        external_query_original
        if external_query_original is not None
        else _safe_list(data[q_start: q_end + 1])
    )

    return jsonify(
        query={
            "start": q_start,
            "end": q_end,
            "length": query_len,
            "values": query_values_response,
            "nan_indices": (np.where(query_nan_mask)[0].tolist()
                            if query_nan_mask.any() else []),
            "overlay_values": q_overlay_values,
            "is_sketch": external_query_original is not None,
        },
        results=results,
        summary=summary,
        method=method,
        normalized=normalize,
        align_info=align_info,
        columns_used=n_cols,
        overlay_cols_requested=len(overlay_data),
        overlay_cols_matched=len(ov_queries),
        dtw_prefiltered=dtw_prefiltered,
        dtw_candidates=(shortlist_limit if dtw_prefiltered else None),
        overlay_names=ov_names,
        _debug={
            "data_len": N,
            "search_len": search_len,
            "compare_len": _compare_len,
            "downsampled": _downsample_applied,
            "query_range": [q_start, q_end],
            "stride": stride,
            "total_windows": _dbg_total,
            "skip_query_overlap": _dbg_skip_query,
            "skip_boundary": _dbg_skip_boundary,
            "skip_nan": _dbg_skip_nan,
            "skip_overlay": _dbg_skip_overlay,
            "candidates_found": len(candidates),
            "results_after_nms": len(results),
            "gap_ranges_count": len(gap_ranges),
        },
    )


@bp.route("/api/search_segments/<dataset_key>/<col_name>", methods=["POST"])
def search_segments(dataset_key, col_name):
    """Segment mode: 1:1 comparison of query segment against others.

    collection: each source is an independent segment
    single (with segments table): split a single source into segments
    """
    body = request.get_json(force=True)
    query_index = body.get("query_index", 0)
    method = body.get("method", cfg_search("default_method"))
    normalize = body.get("normalize", cfg_search("normalize.enabled"))
    top_k = body.get("top_k", cfg_search("segment_top_k"))
    threshold = body.get("threshold", 0)
    overlay_cols = body.get("overlay_cols", [])
    nan_mode = body.get("nan_mode", "interpolate")
    nan_max_ratio = body.get("nan_max_ratio", 0.5)
    nan_method = body.get("nan_method", "linear")
    max_compare_len = body.get("max_compare_len", _MAX_COMPARE_LEN)
    inline_segments = body.get("segments", None)  # Frontend sends current segments

    if not isinstance(method, str) or method not in _VALID_METHODS:
        return jsonify(error=f"Unsupported method: {method}"), 400
    try:
        query_index = int(query_index)
        top_k = max(1, min(10000, int(top_k)))
        threshold = float(threshold)
        nan_max_ratio = float(nan_max_ratio)
        if not math.isfinite(threshold) or not math.isfinite(nan_max_ratio) or not 0 <= nan_max_ratio <= 1:
            raise ValueError("Invalid threshold or NaN ratio")
        max_compare_len = max(3, min(_MAX_COMPARE_LEN, int(max_compare_len)))
    except (TypeError, ValueError, OverflowError):
        return jsonify(error="Invalid numeric search option"), 400
    if not isinstance(overlay_cols, list):
        return jsonify(error="overlay_cols must be an array"), 400
    _hn = make_nan_handler(nan_mode, nan_max_ratio, nan_method)

    # Build segment list depending on dataset type
    seg_labels = []
    seg_data = []
    seg_source_keys = []
    ov_seg_map = {ov_col: {} for ov_col in overlay_cols}

    # Use inline segments if provided (avoid index mismatch from time_unit filtering)
    if inline_segments is not None:
        _validate_inline_segments(inline_segments)
    if inline_segments:
        logger.debug(f"Using inline segments: {len(inline_segments)} segments")
        for inline_index, seg in enumerate(inline_segments):
            seg_labels.append(seg.get("label", ""))
            seg_data.append((seg.get("timestamps", []), seg.get("values", [])))
            seg_source_keys.append(seg.get("source_key", ""))
            # Overlay data from inline segments
            for ov_col in overlay_cols:
                ovs = seg.get("overlays", [])
                for ov in ovs:
                    if ov.get("col_name", ov.get("name")) == ov_col:
                        ov_seg_map[ov_col][("inline", inline_index)] = ov.get("values", [])
        # Skip DB loading
        conn = None
        ds = {"type": "inline"}
    else:
        conn = get_db()
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            conn.close()
            return jsonify(error="Dataset not found"), 400

        if ds["type"] == "collection":
            all_source_data = get_ts_column_all_sources(conn, ds["id"], col_name)
            for sd in all_source_data:
                seg_labels.append(sd["description"] or sd["source_key"].split("/")[-1])
                seg_data.append((sd["timestamps"], sd["values"]))
                seg_source_keys.append(sd["source_key"])
            for ov_col in overlay_cols:
                ov_all = get_ts_column_all_sources(conn, ds["id"], ov_col)
                for sd in ov_all:
                    ov_seg_map[ov_col][sd["source_key"]] = sd["values"]
        else:
            sources = get_dataset_sources(conn, ds["id"])
            if not sources:
                conn.close()
                return jsonify(error="No sources"), 400
            source_key = sources[0]["key"]
            seg_meta = get_segments(conn, source_key, col_name)
            seg_vals = get_all_segment_values(conn, source_key, col_name, seg_meta)
            for idx_m, (meta, (ts_list, val_list)) in enumerate(zip(seg_meta, seg_vals)):
                seg_labels.append(meta["seg_label"])
                seg_data.append((ts_list, val_list))
                seg_source_keys.append(f"{source_key}/_seg_{idx_m}")
            for ov_col in overlay_cols:
                ov_meta = get_segments(conn, source_key, ov_col)
                ov_vals = get_all_segment_values(conn, source_key, ov_col, ov_meta)
                for idx_m, (_, (_, val_list)) in enumerate(zip(ov_meta, ov_vals)):
                    ov_seg_map[ov_col][f"{source_key}/_seg_{idx_m}"] = val_list

        conn.close()

    # Debug: log query_index and seg_data length
    logger.debug(f"search_segments: query_index={query_index}, seg_data length={len(seg_data)}, dataset_type={ds['type']}")
    if len(seg_data) > 0:
        logger.debug(f"First segment label: {seg_labels[0] if seg_labels else 'N/A'}")
        logger.debug(f"Last segment label: {seg_labels[-1] if seg_labels else 'N/A'}")

    if query_index < 0 or query_index >= len(seg_data):
        logger.error(f"Invalid query_index {query_index} for {len(seg_data)} segments")
        return jsonify(error=f"Invalid query index: {query_index} (have {len(seg_data)} segments)"), 400

    query_ts, query_vals = seg_data[query_index]
    query_arr = np.array(query_vals, dtype=np.float64)
    query_nan_mask = np.isnan(query_arr)
    query_arr = _hn(query_arr.copy())
    if query_arr is None:
        return jsonify(error="Query segment has missing values"), 400
    if normalize and query_arr.std() > 0:
        query_arr = z_normalize(query_arr)

    # Auto-downsampling for large segments
    query_cmp, _dbseg_compare_len, _dbseg_ds_applied = auto_downsample(
        query_arr, max_compare_len)

    # Overlay query arrays
    query_src_key = (seg_source_keys[query_index]
                     if query_index < len(seg_source_keys) else None)
    if inline_segments:
        query_src_key = ("inline", query_index)
    ov_query_arrs = []
    for ov_col in overlay_cols:
        if query_src_key and query_src_key in ov_seg_map[ov_col]:
            ov_q = np.array(ov_seg_map[ov_col][query_src_key], dtype=np.float64)
            if len(ov_q) != len(query_arr):
                continue
            ov_q = _hn(ov_q)
            if ov_q is None:
                continue
            if normalize and ov_q.std() > 0:
                ov_q = z_normalize(ov_q)
            ov_q_cmp = (_resample(ov_q, _dbseg_compare_len)
                        if _dbseg_ds_applied else ov_q)
            ov_query_arrs.append((ov_col, ov_q, ov_q_cmp))

    n_cols = 1 + len(ov_query_arrs)

    results = []
    _dbg_total = 0
    _dbg_skip_len = 0
    _dbg_skip_nan = 0
    _dbg_skip_overlay = 0
    _dbg_seg_info = []
    for i, (ts_list, val_list) in enumerate(seg_data):
        if i == query_index:
            continue
        _dbg_total += 1
        seg_arr = np.array(val_list, dtype=np.float64)
        _nan_cnt = int(np.isnan(seg_arr).sum())
        _nan_pct = (round(_nan_cnt / len(seg_arr) * 100, 1)
                    if len(seg_arr) > 0 else 0)
        _dbg_seg_info.append({
            "i": i, "len": len(seg_arr),
            "nan": _nan_cnt, "nan_pct": _nan_pct,
        })
        seg_arr = _hn(seg_arr)
        if seg_arr is None:
            _dbg_skip_nan += 1
            continue
        if normalize and seg_arr.std() > 0:
            seg_arr = z_normalize(seg_arr)
        seg_cmp = (_resample(seg_arr, _dbseg_compare_len)
                   if _dbseg_ds_applied else seg_arr)

        d0 = compute_distance(query_cmp, seg_cmp, method)
        col_dists = [d0]

        # Overlay column distances
        seg_src_key = (("inline", i) if inline_segments else
                       seg_source_keys[i] if i < len(seg_source_keys) else None)
        skip = False
        ov_vals_for_result = []
        for oi, (ov_col, ov_q_orig, ov_q_cmp) in enumerate(ov_query_arrs):
            if seg_src_key and seg_src_key in ov_seg_map[ov_col]:
                ov_raw = ov_seg_map[ov_col][seg_src_key]
                ov_s = np.array(ov_raw, dtype=np.float64)
                ov_s = _hn(ov_s)
                if ov_s is None:
                    skip = True
                    break
                if normalize and ov_s.std() > 0:
                    ov_s = z_normalize(ov_s)
                ov_s_cmp = (_resample(ov_s, _dbseg_compare_len)
                            if _dbseg_ds_applied else ov_s)
                col_dists.append(compute_distance(ov_q_cmp, ov_s_cmp, method))
                ov_vals_for_result.append(_safe_list(ov_raw))
            else:
                skip = True
                break
        if skip:
            _dbg_skip_overlay += 1
            continue

        dist = sum(col_dists) / n_cols
        sim = distance_to_similarity(dist, method)

        r_entry = {
            "index": i,
            "label": seg_labels[i],
            "similarity": round(sim, 6),
            "values": val_list,
        }
        if ov_vals_for_result:
            r_entry["overlay_values"] = ov_vals_for_result
            if len(col_dists) > 1:
                r_entry["col_similarities"] = [
                    round(float(distance_to_similarity(d, method)), 6)
                    for d in col_dists
                ]
        results.append(r_entry)

    results.sort(key=lambda x: x["similarity"], reverse=True)
    if threshold > 0:
        results = [r for r in results if r["similarity"] >= threshold]
    results = results[:top_k]

    sims = [r["similarity"] for r in results]
    return jsonify(
        query={
            "index": query_index,
            "label": seg_labels[query_index],
            "values": query_vals,
            "nan_indices": (np.where(query_nan_mask)[0].tolist()
                            if query_nan_mask.any() else []),
        },
        results=results,
        columns_used=n_cols,
        overlay_cols_requested=overlay_cols,
        overlay_cols_matched=len(ov_query_arrs),
        summary={
            "count": len(results),
            "best": round(max(sims), 4) if sims else 0,
            "avg": round(float(np.mean(sims)), 4) if sims else 0,
            "worst": round(min(sims), 4) if sims else 0,
        },
        _debug={
            "total_segments": _dbg_total,
            "query_len": len(query_arr),
            "query_nan_pct": (round(int(query_nan_mask.sum()) / len(query_arr) * 100, 1)
                              if len(query_arr) > 0 else 0),
            "max_nan_ratio": nan_max_ratio,
            "skip_length": _dbg_skip_len,
            "skip_nan": _dbg_skip_nan,
            "skip_overlay": _dbg_skip_overlay,
            "seg_info": _dbg_seg_info,
            "results_count": len(results),
            "compare_len": _dbseg_compare_len,
            "downsampled": _dbseg_ds_applied,
        },
    )


@bp.route("/api/search_split_segments/<sid>", methods=["POST"])
def search_split_segments(sid):
    """Split segment mode: compare segments sent from frontend."""
    data = DATA_STORE.get(sid)
    if data is None:
        return jsonify(error="Session not found"), 400

    body = request.get_json(force=True)
    segments = body.get("segments", [])
    query_index = body.get("query_index", 0)
    method = body.get("method", cfg_search("default_method"))
    normalize = body.get("normalize", cfg_search("normalize.enabled"))
    top_k = body.get("top_k", cfg_search("segment_top_k"))
    threshold = body.get("threshold", 0)
    nan_mode = body.get("nan_mode", "interpolate")
    nan_max_ratio = body.get("nan_max_ratio", 0.5)
    nan_method = body.get("nan_method", "linear")
    max_compare_len = body.get("max_compare_len", _MAX_COMPARE_LEN)

    if not isinstance(method, str) or method not in _VALID_METHODS:
        return jsonify(error=f"Unsupported method: {method}"), 400
    try:
        query_index = int(query_index)
        top_k = max(1, min(10000, int(top_k)))
        threshold = float(threshold)
        nan_max_ratio = float(nan_max_ratio)
        if not math.isfinite(threshold) or not math.isfinite(nan_max_ratio) or not 0 <= nan_max_ratio <= 1:
            raise ValueError("Invalid threshold or NaN ratio")
        max_compare_len = max(3, min(_MAX_COMPARE_LEN, int(max_compare_len)))
    except (TypeError, ValueError, OverflowError):
        return jsonify(error="Invalid numeric search option"), 400
    _validate_inline_segments(segments)
    _hn = make_nan_handler(nan_mode, nan_max_ratio, nan_method)

    if query_index < 0 or query_index >= len(segments):
        return jsonify(error="Invalid query index"), 400

    query_arr = np.array(segments[query_index]["values"], dtype=np.float64)
    query_nan_mask = np.isnan(query_arr)
    query_arr = _hn(query_arr.copy())
    if query_arr is None:
        return jsonify(error="Query segment has missing values"), 400
    if normalize and query_arr.std() > 0:
        query_arr = z_normalize(query_arr)

    # Auto-downsampling for large segments
    query_cmp, _seg_compare_len, _seg_ds_applied = auto_downsample(
        query_arr, max_compare_len)

    # Multi-column overlay
    query_seg = segments[query_index]
    ov_query_arrs = []
    if query_seg.get("overlays"):
        for original_index, ov in enumerate(query_seg["overlays"]):
            ov_q = np.array(ov["values"], dtype=np.float64)
            ov_q = _hn(ov_q)
            if ov_q is None or len(ov_q) != len(query_arr):
                return jsonify(error="Selected query overlay has missing or mismatched values"), 400
            if normalize and ov_q.std() > 0:
                ov_q = z_normalize(ov_q)
            ov_q_cmp = (_resample(ov_q, _seg_compare_len)
                        if _seg_ds_applied else ov_q)
            ov_query_arrs.append((ov.get("col_name", ov.get("name")), original_index, ov_q, ov_q_cmp))

    n_cols = 1 + len(ov_query_arrs)

    results = []
    for i, seg in enumerate(segments):
        if i == query_index:
            continue
        seg_arr = np.array(seg["values"], dtype=np.float64)
        seg_arr = _hn(seg_arr)
        if seg_arr is None:
            continue
        # Resample if lengths differ (boundary split may produce variable lengths)
        if len(seg_arr) != len(query_arr):
            if method == "dtw":
                pass
            else:
                seg_arr = _resample(seg_arr, len(query_arr))
        if normalize and seg_arr.std() > 0:
            seg_arr = z_normalize(seg_arr)
        seg_cmp = (_resample(seg_arr, _seg_compare_len)
                   if _seg_ds_applied else seg_arr)

        d0 = compute_distance(query_cmp, seg_cmp, method)
        dist = d0
        col_dists = [d0]
        ov_vals_for_result = []

        # Overlay column distances
        seg_overlays = seg.get("overlays", [])
        skip = False
        for name, original_index, ov_q_orig, ov_q_cmp in ov_query_arrs:
            matches = ([ov for ov in seg_overlays if ov.get("col_name", ov.get("name")) == name]
                       if name is not None else seg_overlays[original_index:original_index + 1])
            if len(matches) == 1:
                matched_overlay = matches[0]
                ov_s = np.array(matched_overlay["values"], dtype=np.float64)
                ov_s = _hn(ov_s)
                if ov_s is None:
                    skip = True
                    break
                if len(ov_s) != len(ov_q_orig):
                    if method == "dtw":
                        pass
                    else:
                        ov_s = _resample(ov_s, len(ov_q_orig))
                if normalize and ov_s.std() > 0:
                    ov_s = z_normalize(ov_s)
                ov_s_cmp = (_resample(ov_s, _seg_compare_len)
                            if _seg_ds_applied else ov_s)
                ov_dist = compute_distance(ov_q_cmp, ov_s_cmp, method)
                dist += ov_dist
                col_dists.append(ov_dist)
                ov_vals_for_result.append(_safe_list(matched_overlay["values"]))
            else:
                skip = True
                break
        if skip:
            continue

        dist /= n_cols
        sim = distance_to_similarity(dist, method)

        result_item = {
            "index": i,
            "label": seg.get("label", ""),
            "similarity": round(sim, 6),
            "distance": round(float(dist), 6),
            "values": seg["values"],
        }
        for field in ("source_key", "source_label", "parent_segment_key", "key", "timestamps", "labels", "start", "end"):
            if field in seg:
                result_item[field] = seg[field]
        if ov_vals_for_result:
            result_item["overlay_values"] = ov_vals_for_result
            result_item["col_similarities"] = [
                round(float(distance_to_similarity(d, method)), 6)
                for d in col_dists
            ]
        results.append(result_item)

    results.sort(key=lambda x: x["similarity"], reverse=True)
    if threshold > 0:
        results = [r for r in results if r["similarity"] >= threshold]
    results = results[:top_k]

    sims = [r["similarity"] for r in results]
    return jsonify(
        query={
            "index": query_index,
            "label": segments[query_index].get("label", ""),
            "values": segments[query_index]["values"],
            "nan_indices": (np.where(query_nan_mask)[0].tolist()
                            if query_nan_mask.any() else []),
        },
        results=results,
        columns_used=n_cols,
        overlay_cols_matched=len(ov_query_arrs),
        summary={
            "count": len(results),
            "best": round(max(sims), 4) if sims else 0,
            "avg": round(float(np.mean(sims)), 4) if sims else 0,
            "worst": round(min(sims), 4) if sims else 0,
        },
        _debug={
            "seg_count": len(segments),
            "seg_len": len(query_arr),
            "compare_len": _seg_compare_len,
            "downsampled": _seg_ds_applied,
        },
    )


@bp.route("/api/boundaries/<sid>")
def get_boundaries(sid):
    """Return calendar-aligned start indices.

    Query params:
      fixed_len: fixed length (point count)
      boundary_minutes: boundary type (minutes, e.g. 1440=1day)
    """
    entry = DATA_STORE.get(sid)
    if not entry or "labels" not in entry:
        return jsonify(error="Load data first"), 404

    fixed_len = request.args.get("fixed_len", 24, type=int)
    boundary_minutes = request.args.get("boundary_minutes", 0, type=int)
    labels = entry["labels"]

    if boundary_minutes <= 0:
        return jsonify(boundaries=[])

    starts = find_calendar_boundaries(labels, boundary_minutes, fixed_len)
    return jsonify(
        boundaries=starts if starts else [],
        boundary_minutes=boundary_minutes,
    )
