# -*- coding: utf-8 -*-
"""
Segment Search -- segment-level pattern matching.

Split time series by time unit (hour/day/week/...) and find segments
most similar to a query shape (preset or LLM-generated).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .app_config import cfg, cfg_search, cfg_time_units, cfg_preset_catalog
from .helpers import (
    z_normalize, compute_distance, distance_to_similarity,
    _resample, handle_nan, _safe_list, _DEFAULT_NAN_MODE, _DEFAULT_NAN_RATIO,
)


# ── Config-driven constants ───────────────────────────────────

def _get_time_units() -> dict:
    return cfg_time_units()

_PRESET_RESOLUTION = cfg("preset_shapes.generation_resolution")
_PRESET_PREVIEW_LEN = cfg("preset_shapes.preview_length")
_MIN_SEGMENT_LEN = cfg("segment.min_length")
_DEFAULT_SAMPLING_MIN = cfg("detection.default_sampling_minutes")


def time_unit_to_length(unit: str, sampling_interval_minutes: float) -> int:
    """Convert time unit + sampling interval -> number of points per segment."""
    units = _get_time_units()
    info = units.get(unit)
    if info is None:
        raise ValueError(f"Unknown time unit: {unit}")
    return max(_MIN_SEGMENT_LEN, round(info["minutes"] / sampling_interval_minutes))


def detect_sampling_minutes(timestamps: list[str],
                            sample_n: int | None = None) -> float:
    """Detect median sampling interval in minutes from timestamp strings."""
    if sample_n is None:
        sample_n = cfg("detection.sampling_sample_size")
    ts = pd.to_datetime(timestamps[:sample_n], errors="coerce").dropna()
    if len(ts) < 2:
        return _DEFAULT_SAMPLING_MIN
    diffs = ts[1:] - ts[:-1]
    pos = diffs[diffs > pd.Timedelta(0)]
    if len(pos) == 0:
        return _DEFAULT_SAMPLING_MIN
    return pos.median().total_seconds() / 60.0


# ── Preset shapes ─────────────────────────────────────────────

def _gen_preset(name: str, n: int | None = None) -> np.ndarray:
    """Generate a preset shape of length n (before z-normalization)."""
    if n is None:
        n = _PRESET_RESOLUTION
    t = np.linspace(0, 1, n)

    if name == "rise":
        v = t
    elif name == "fall":
        v = 1.0 - t
    elif name == "flat":
        return np.zeros(n)
    elif name == "bell":
        v = np.exp(-((t - 0.5) ** 2) / (2 * 0.04))
    elif name == "v_shape":
        v = np.abs(2 * t - 1)
    elif name == "spike":
        v = np.zeros(n)
        center = n // 2
        width = max(1, n // 12)
        for i in range(max(0, center - width), min(n, center + width + 1)):
            v[i] = 1.0 - abs(i - center) / (width + 1)
    elif name == "dip":
        v = np.ones(n)
        center = n // 2
        width = max(1, n // 12)
        for i in range(max(0, center - width), min(n, center + width + 1)):
            v[i] = abs(i - center) / (width + 1)
    else:
        raise ValueError(f"Unknown preset: {name}")

    return v


def get_preset_shape(name: str, length: int, dataset_id: str | None = None) -> np.ndarray:
    """Get a preset shape, resampled to target length, z-normalized."""
    # Handle custom presets (prefixed with "custom_")
    if name.startswith("custom_"):
        from .app_config import cfg_custom_presets
        custom_key = name[7:]  # Remove "custom_" prefix
        custom = cfg_custom_presets(dataset_id)
        if custom_key in custom:
            values = custom[custom_key].get("values", [])
            if values:
                raw = np.array(values, dtype=np.float64)
                if len(raw) != length:
                    raw = _resample(raw, length)
                # Sketch/manual API callers may save raw amplitudes. Normalize
                # after resampling, just as for built-in query shapes.
                return z_normalize(raw)
        raise ValueError(f"Unknown custom preset: {name}")

    # Built-in presets
    raw = _gen_preset(name)
    if length != _PRESET_RESOLUTION:
        raw = _resample(raw, length)
    std = raw.std()
    if std < 1e-10:
        return raw  # flat -> zeros
    return z_normalize(raw)



def representative_segment_length(all_segments: list[dict]) -> int:
    """구간들의 대표 길이.

    예전에는 첫 구간의 길이를 그대로 썼다. 자료가 하루/한 주의 중간에서 시작하면
    첫 구간이 잘린 조각이라, 나머지 온전한 구간이 모두 그 길이로 눌려 버렸다
    (예: 시간 간격 자료의 하루가 24 → 6 point). 온전한 구간이 다수이므로
    최빈값을 쓰고, 동률이면 큰 쪽을 택한다.
    """
    from collections import Counter
    lens = [len(s.get("values") or []) for s in all_segments if s.get("values")]
    if not lens:
        return 0
    counts = Counter(lens)
    top = max(counts.values())
    return max(l for l, c in counts.items() if c == top)

def list_presets(dataset_id: str | None = None) -> list[dict]:
    """Return preset catalog for UI (built-in + custom for dataset)."""
    from .app_config import cfg_custom_presets, load_hidden_presets

    catalog = cfg_preset_catalog()
    custom = cfg_custom_presets(dataset_id)
    hidden = load_hidden_presets(dataset_id) if dataset_id else set()
    result = []

    # Built-in presets first
    for key, info in catalog.items():
        preview = get_preset_shape(key, _PRESET_PREVIEW_LEN)
        result.append({
            "key": key,
            "label": info.get("label", key),
            "icon": info.get("icon", ""),
            "preview": _safe_list(preview),
            "custom": False,
            "deletable": True,
            "hidden": key in hidden,
        })

    # Custom presets (after separator) - use get_preset_shape for consistency
    for key, info in custom.items():
        full_key = "custom_" + key
        try:
            preview = get_preset_shape(full_key, _PRESET_PREVIEW_LEN, dataset_id)
        except ValueError:
            # Fallback if shape generation fails
            values = info.get("values", [])
            preview = _resample(np.array(values), _PRESET_PREVIEW_LEN) if values else np.zeros(_PRESET_PREVIEW_LEN)
        result.append({
            "key": full_key,
            "label": info.get("label", key),
            "icon": info.get("icon", "auto_graph"),
            "preview": _safe_list(preview),
            "custom": True,
            "deletable": True,
            "source": info.get("source", "manual"),
            "created": info.get("created", ""),
            "metadata": info.get("metadata", {}),
        })

    return result


# ── Segment splitting ─────────────────────────────────────────

def split_into_segments(
    timestamps: list[str],
    values: list[float],
    unit: str,
    source_key: str = "",
    require_complete: bool = False,
) -> list[dict]:
    """Split a continuous time series into segments by calendar boundary."""
    ts = pd.to_datetime(timestamps, errors="coerce")
    vals = np.array(values, dtype=np.float64)
    from backend.source_provenance import sampling_interval, complete_window
    interval = sampling_interval(timestamps) if require_complete else None

    groups: dict[str, dict] = {}
    for i, t in enumerate(ts):
        if pd.isna(t):
            continue
        bkey = _calendar_key(t, unit)
        if bkey not in groups:
            groups[bkey] = {"indices": []}
        groups[bkey]["indices"].append(i)

    segments = []
    for bkey in sorted(groups.keys()):
        idxs = groups[bkey]["indices"]
        seg_ts = [timestamps[i] for i in idxs]
        seg_vals = vals[idxs].tolist()
        if require_complete:
            start = pd.Timestamp(bkey.rstrip("w"))
            duration = {"hour": pd.Timedelta(hours=1), "6hour": pd.Timedelta(hours=6),
                        "day": pd.Timedelta(days=1), "week": pd.Timedelta(days=7),
                        "month": pd.DateOffset(months=1), "year": pd.DateOffset(years=1)}.get(unit)
            if duration is None or not complete_window(ts[idxs], seg_vals, start, start + duration, interval):
                continue
        segments.append({
            "key": f"{source_key}/{bkey}" if source_key else bkey,
            "label": bkey,
            "source_key": source_key,
            "start_ts": seg_ts[0],
            "end_ts": seg_ts[-1],
            "timestamps": seg_ts,
            "values": seg_vals,
            "point_count": len(seg_vals),
        })

    return segments


def _sources_to_segments(
    sources_data: list[dict],
    unit: str | None = None,
) -> list[dict]:
    """Convert source dicts to segment dicts.

    unit=None  → pre-segmented: each source becomes one segment as-is.
    unit=<str> → split each source by calendar boundary.
    """
    all_segments: list[dict] = []
    if unit is None:
        for src in sources_data:
            src_key = src.get("source_key", "")
            match_key = src.get("segment_key") or src_key
            ts_list = src.get("timestamps", [])
            val_list = src.get("values", [])
            if not val_list:
                continue
            all_segments.append({
                "key": match_key,
                "label": src.get("label") or match_key,
                "source_label": src.get("source_label", ""),
                "source_key": src_key,
                "start_ts": ts_list[0] if ts_list else "",
                "end_ts": ts_list[-1] if ts_list else "",
                "timestamps": ts_list,
                "values": val_list,
                "point_count": len(val_list),
            })
    else:
        for src in sources_data:
            ts_list = src.get("timestamps", [])
            val_list = src.get("values", [])
            if not ts_list or not val_list:
                continue
            segs = split_into_segments(
                ts_list, val_list, unit, src.get("source_key", ""))
            all_segments.extend(segs)
    return all_segments


def _calendar_key(t: pd.Timestamp, unit: str) -> str:
    """Generate a human-readable calendar key for grouping."""
    if unit == "hour":
        return t.strftime("%Y-%m-%d %H:00")
    elif unit == "6hour":
        h6 = (t.hour // 6) * 6
        return t.strftime(f"%Y-%m-%d {h6:02d}:00")
    elif unit == "day":
        return t.strftime("%Y-%m-%d")
    elif unit == "week":
        monday = t - pd.Timedelta(days=t.weekday())
        return monday.strftime("%Y-%m-%d") + "w"
    elif unit == "month":
        return t.strftime("%Y-%m")
    elif unit == "year":
        return t.strftime("%Y")
    else:
        return t.strftime("%Y-%m-%d")


# ── Core matching engine ──────────────────────────────────────

def match_segments(
    segments: list[dict],
    query_shape: np.ndarray,
    method: str = "euclidean",
    top_k: int = 10,
    threshold: float = 0.0,
    normalize: bool = True,
    nan_mode: str = _DEFAULT_NAN_MODE,
    nan_max_ratio: float = _DEFAULT_NAN_RATIO,
    min_points_ratio: float = 0.5,
    overlay_segments: list[list[dict]] | None = None,
    overlay_query_shapes: list[np.ndarray] | None = None,
    _dtw_prefilter: bool = True,
) -> list[dict]:
    """Compare each segment against query_shape, return top-K matches.

    Multi-column support:
      overlay_segments: list of segment lists, one per overlay column
      overlay_query_shapes: query shape per overlay column
    """
    query_len = len(query_shape)
    min_points = max(_MIN_SEGMENT_LEN, int(query_len * min_points_ratio))

    # Prepare overlay lookup: seg key -> [np.array, ...] per overlay col
    has_overlay = (overlay_segments and overlay_query_shapes
                   and len(overlay_segments) == len(overlay_query_shapes))
    n_cols = 1 + (len(overlay_query_shapes) if has_overlay else 0)
    w = np.ones(n_cols) / n_cols

    # Pure-Python DTW is expensive. For larger jobs, shortlist by the same
    # normalized multi-column Euclidean distance, then apply DTW to the
    # shortlist. Every returned score remains an actual DTW score.
    prefilter_min = int(cfg_search("dtw.prefilter_min_comparisons"))
    if method == "dtw" and _dtw_prefilter and len(segments) * n_cols > prefilter_min:
        multiplier = int(cfg_search("dtw.prefilter_multiplier"))
        candidate_limit = min(len(segments), max(30, top_k * multiplier))
        shortlist = match_segments(
            segments, query_shape, method="euclidean", top_k=candidate_limit,
            threshold=0.0, normalize=normalize, nan_mode=nan_mode,
            nan_max_ratio=nan_max_ratio, min_points_ratio=min_points_ratio,
            overlay_segments=overlay_segments,
            overlay_query_shapes=overlay_query_shapes,
            _dtw_prefilter=False,
        )
        shortlisted = {
            (item.get("source_key", ""), item.get("key") or item.get("label", ""))
            for item in shortlist
        }
        indices = [
            idx for idx, seg in enumerate(segments)
            if (seg.get("source_key", ""), seg.get("key") or seg.get("label", ""))
            in shortlisted
        ]
        subset_segments = [segments[idx] for idx in indices]
        subset_overlays = None
        if has_overlay:
            subset_overlays = [
                [ov_segments[idx] for idx in indices if idx < len(ov_segments)]
                for ov_segments in overlay_segments
            ]
        exact = match_segments(
            subset_segments, query_shape, method="dtw", top_k=top_k,
            threshold=threshold, normalize=normalize, nan_mode=nan_mode,
            nan_max_ratio=nan_max_ratio, min_points_ratio=min_points_ratio,
            overlay_segments=subset_overlays,
            overlay_query_shapes=overlay_query_shapes,
            _dtw_prefilter=False,
        )
        for item in exact:
            item["dtw_prefiltered"] = True
            item["dtw_candidates"] = candidate_limit
        return exact

    # Build overlay seg lookup keyed by (seg index)
    ov_seg_map: dict[int, list[np.ndarray]] = {}
    # Match by source + segment identity. Calendar labels repeat across sources,
    # so a label-only map can attach one station's overlay to another station.
    _ov_by_identity: list[dict[tuple[str, str], np.ndarray]] = []
    if has_overlay:
        for ov_idx, ov_segs in enumerate(overlay_segments):
            identity_map: dict[tuple[str, str], np.ndarray] = {}
            for si, ov_seg in enumerate(ov_segs):
                raw_ov = np.array(ov_seg["values"], dtype=np.float64)
                if si not in ov_seg_map:
                    ov_seg_map[si] = [None] * len(overlay_segments)
                ov_seg_map[si][ov_idx] = raw_ov
                identity = (
                    ov_seg.get("source_key", ""),
                    ov_seg.get("key") or ov_seg.get("label", ""),
                )
                if identity[1]:
                    identity_map[identity] = raw_ov
            _ov_by_identity.append(identity_map)

    results = []

    for si, seg in enumerate(segments):
        raw = np.array(seg["values"], dtype=np.float64)
        if len(raw) < min_points:
            continue

        cleaned = handle_nan(raw, nan_mode, nan_max_ratio)
        if cleaned is None:
            continue
        if len(cleaned) != query_len:
            cleaned = _resample(cleaned, query_len)
        if normalize:
            cleaned = z_normalize(cleaned)

        dist_primary = compute_distance(query_shape, cleaned, method)

        if has_overlay:
            dists = [dist_primary]
            skip = False
            seg_identity = (
                seg.get("source_key", ""),
                seg.get("key") or seg.get("label", ""),
            )
            for ov_idx in range(len(overlay_segments)):
                # Source-aware identity matching first, index fallback.
                ov_raw = None
                if seg_identity[1] and ov_idx < len(_ov_by_identity):
                    ov_raw = _ov_by_identity[ov_idx].get(seg_identity)
                if ov_raw is None and not seg_identity[1] and si in ov_seg_map:
                    ov_raw = ov_seg_map[si][ov_idx] if ov_seg_map[si][ov_idx] is not None else None
                if ov_raw is None or len(ov_raw) < min_points:
                    skip = True
                    break
                ov_cleaned = handle_nan(ov_raw, nan_mode, nan_max_ratio)
                if ov_cleaned is None:
                    skip = True
                    break
                if len(ov_cleaned) != query_len:
                    ov_cleaned = _resample(ov_cleaned, query_len)
                if normalize:
                    ov_cleaned = z_normalize(ov_cleaned)
                dists.append(compute_distance(
                    overlay_query_shapes[ov_idx], ov_cleaned, method))
            if skip:
                continue
            dist = float(np.dot(w, dists))
        else:
            dist = dist_primary

        sim = distance_to_similarity(dist, method)

        if threshold > 0 and sim < threshold:
            continue

        result_item = {
            "key": seg.get("key", ""),
            "label": seg.get("label", ""),
            "source_key": seg.get("source_key", ""),
            "source_label": seg.get("source_label", ""),
            "start_ts": seg.get("start_ts", ""),
            "end_ts": seg.get("end_ts", ""),
            "point_count": seg.get("point_count", len(raw)),
            "similarity": round(float(sim), 6),
            "distance": round(float(dist), 6),
            "values": _safe_list(raw),
            "timestamps": seg.get("timestamps", []),
        }
        # Include overlay column values + per-column similarities
        if has_overlay:
            ov_vals = []
            seg_identity = (
                seg.get("source_key", ""),
                seg.get("key") or seg.get("label", ""),
            )
            for ov_idx in range(len(overlay_segments)):
                ov_raw = None
                if seg_identity[1] and ov_idx < len(_ov_by_identity):
                    ov_raw = _ov_by_identity[ov_idx].get(seg_identity)
                if ov_raw is None and not seg_identity[1] and si in ov_seg_map:
                    ov_raw = ov_seg_map[si][ov_idx]
                ov_vals.append(_safe_list(ov_raw) if ov_raw is not None else [])
            result_item["overlay_values"] = ov_vals
            if len(dists) > 1:
                result_item["col_similarities"] = [
                    round(float(distance_to_similarity(d, method)), 6)
                    for d in dists
                ]
        results.append(result_item)

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


# ── High-level pipelines ─────────────────────────────────────

def _build_summary(results: list[dict]) -> dict:
    sims = [r["similarity"] for r in results]
    if not sims:
        return {}
    return {
        "count": len(results),
        "best": round(max(sims), 4),
        "worst": round(min(sims), 4),
        "avg": round(sum(sims) / len(sims), 4),
    }


def segment_search_pipeline(
    all_timestamps: list[str],
    all_values: list[float],
    query_shape: np.ndarray,
    unit: str = "day",
    source_key: str = "",
    method: str = "euclidean",
    top_k: int = 10,
    threshold: float = 0.0,
    normalize: bool = True,
    nan_mode: str = _DEFAULT_NAN_MODE,
    nan_max_ratio: float = _DEFAULT_NAN_RATIO,
) -> dict:
    """Full pipeline: split -> match -> return results with metadata."""
    units = _get_time_units()
    segments = split_into_segments(all_timestamps, all_values, unit, source_key)
    results = match_segments(
        segments, query_shape,
        method=method, top_k=top_k, threshold=threshold,
        normalize=normalize, nan_mode=nan_mode, nan_max_ratio=nan_max_ratio,
    )
    return {
        "segments_total": len(segments),
        "query_length": len(query_shape),
        "unit": unit,
        "unit_label": units.get(unit, {}).get("label", unit),
        "method": method,
        "results": results,
        "summary": _build_summary(results),
        "dtw_prefiltered": bool(results and results[0].get("dtw_prefiltered")),
        "dtw_candidates": (results[0].get("dtw_candidates") if results else None),
    }


def segment_search_multi_source(
    sources_data: list[dict],
    query_shape: np.ndarray,
    unit: str = "day",
    method: str = "euclidean",
    top_k: int = 10,
    overlay_sources_list: list[list[dict]] | None = None,
    overlay_query_shapes: list[np.ndarray] | None = None,
    pre_segmented: bool = False,
    **kwargs,
) -> dict:
    """Search across multiple sources (collection mode).

    overlay_sources_list: one list of source-dicts per overlay column.
    pre_segmented: if True, treat each source as one segment (no re-splitting).
    """
    units = _get_time_units()
    effective_unit = None if pre_segmented else unit
    all_segments = _sources_to_segments(sources_data, effective_unit)

    # Build overlay segments aligned with primary segments
    overlay_segments = None
    if overlay_sources_list and overlay_query_shapes:
        overlay_segments = [
            _sources_to_segments(ov_sources, effective_unit)
            for ov_sources in overlay_sources_list
        ]

    excluded_missing_overlay = 0
    if overlay_segments:
        def _identity(seg):
            return (seg.get("source_key", ""),
                    seg.get("key") or seg.get("label", ""))
        eligible = {_identity(seg) for seg in all_segments}
        for ov_segments in overlay_segments:
            eligible &= {_identity(seg) for seg in ov_segments}
        excluded_missing_overlay = sum(
            1 for seg in all_segments if _identity(seg) not in eligible
        )

    results = match_segments(
        all_segments, query_shape,
        method=method, top_k=top_k,
        overlay_segments=overlay_segments,
        overlay_query_shapes=overlay_query_shapes,
        **kwargs,
    )

    return {
        "segments_total": len(all_segments),
        "sources_count": len(sources_data),
        "query_length": len(query_shape),
        "unit": unit,
        "unit_label": units.get(unit, {}).get("label", unit),
        "method": method,
        "results": results,
        "summary": _build_summary(results),
        "dtw_prefiltered": bool(results and results[0].get("dtw_prefiltered")),
        "dtw_candidates": (results[0].get("dtw_candidates") if results else None),
        "segments_excluded_missing_overlay": excluded_missing_overlay,
    }


# ── Preset-based auto-classification ─────────────────────────

def classify_segments_by_presets(
    sources_data: list[dict],
    unit: str = "day",
    method: str = "euclidean",
    nan_mode: str = _DEFAULT_NAN_MODE,
    nan_max_ratio: float = _DEFAULT_NAN_RATIO,
    min_similarity: float | None = None,
    pre_segmented: bool = False,
    dataset_id: str | None = None,
) -> dict:
    """Classify all segments by their nearest preset shape.

    For each segment, compute similarity to all presets (built-in + custom)
    and assign the segment to the preset with the highest similarity.
    Segments below *min_similarity* are grouped as "unclassified".

    Returns dict with groups keyed by preset name, each containing
    a list of matched segments with similarity scores.
    """
    from .app_config import cfg_custom_presets, load_hidden_presets

    if min_similarity is None:
        min_similarity = cfg("preset_shapes.classify_min_similarity")

    # Get all presets: built-in + custom (dataset-specific), excluding hidden
    catalog = cfg_preset_catalog()
    custom = cfg_custom_presets(dataset_id)
    hidden = load_hidden_presets(dataset_id) if dataset_id else set()

    preset_names = [k for k in catalog.keys() if k not in hidden]
    # Add custom presets with "custom_" prefix
    for k in custom.keys():
        preset_names.append("custom_" + k)

    # 후보가 하나도 없으면 모든 세그먼트가 Unclassified 로 떨어진다.
    # 그런 결과는 정보가 없으므로 내장 도형 전체로 되돌려 분류한다.
    if not preset_names:
        preset_names = list(catalog.keys())

    effective_unit = None if pre_segmented else unit
    all_segments = _sources_to_segments(sources_data, effective_unit)

    if not all_segments:
        return {"groups": {}, "segments_total": 0, "unclassified": 0,
                "reason": "no_segments"}

    # 구간의 대표 길이 (첫 구간은 잘린 조각일 수 있어 최빈값을 쓴다)
    first_len = representative_segment_length(all_segments)
    seg_length = max(_MIN_SEGMENT_LEN, first_len)

    if first_len < _MIN_SEGMENT_LEN:
        # 구간이 1~2 point 로 잘리면 전부 Unclassified 로 떨어진다.
        # 결과만 보면 원인을 알 수 없으므로 이유를 함께 돌려준다.
        return {"groups": {}, "segments_total": len(all_segments),
                "unclassified": len(all_segments),
                "reason": "segment_too_short",
                "segment_length": first_len,
                "min_segment_length": _MIN_SEGMENT_LEN}

    # Generate all preset shapes at the right length
    preset_shapes = {}
    for name in preset_names:
        preset_shapes[name] = get_preset_shape(name, seg_length, dataset_id)

    # Classify each segment
    groups: dict[str, list[dict]] = {name: [] for name in preset_names}
    group_normed: dict[str, list[np.ndarray]] = {name: [] for name in preset_names}
    unclassified_segs: list[dict] = []
    unclassified = 0

    for seg in all_segments:
        raw = np.array(seg["values"], dtype=np.float64)
        if len(raw) < _MIN_SEGMENT_LEN:
            unclassified += 1
            continue

        cleaned = handle_nan(raw, nan_mode, nan_max_ratio)
        if cleaned is None:
            unclassified += 1
            continue

        if len(cleaned) != seg_length:
            cleaned = _resample(cleaned, seg_length)

        normed = z_normalize(cleaned)

        best_preset = None
        best_sim = -1.0
        sims_all = {}

        for pname, pshape in preset_shapes.items():
            dist = compute_distance(pshape, normed, method)
            sim = distance_to_similarity(dist, method)
            sims_all[pname] = round(float(sim), 4)
            if sim > best_sim:
                best_sim = sim
                best_preset = pname

        if best_preset is None or best_sim < min_similarity:
            unclassified += 1
            unclassified_segs.append({
                "key": seg.get("key", ""),
                "label": seg.get("label", ""),
                "source_key": seg.get("source_key", ""),
                "start_ts": seg.get("start_ts", ""),
                "end_ts": seg.get("end_ts", ""),
                "point_count": seg.get("point_count", len(raw)),
                "similarity": round(float(best_sim), 4) if best_preset else 0.0,
                "best_preset": best_preset or "",
                "values": _safe_list(raw),
            })
            continue

        groups[best_preset].append({
            "key": seg.get("key", ""),
            "label": seg.get("label", ""),
            "source_key": seg.get("source_key", ""),
            "start_ts": seg.get("start_ts", ""),
            "end_ts": seg.get("end_ts", ""),
            "point_count": seg.get("point_count", len(raw)),
            "similarity": round(float(best_sim), 4),
            "values": _safe_list(raw),
        })
        group_normed[best_preset].append(normed)

    # Sort each group by similarity descending
    for name in groups:
        groups[name].sort(key=lambda x: x["similarity"], reverse=True)

    # Build summary per group
    group_summary = {}
    for name in preset_names:
        segs = groups[name]
        # Get label from catalog or custom
        if name.startswith("custom_"):
            custom_key = name[7:]
            label = custom.get(custom_key, {}).get("label", custom_key)
        else:
            label = catalog.get(name, {}).get("label", name)

        if segs:
            sims = [s["similarity"] for s in segs]
            group_summary[name] = {
                "count": len(segs),
                "avg_sim": round(sum(sims) / len(sims), 4),
                "best_sim": round(max(sims), 4),
                "label": label,
            }
        else:
            group_summary[name] = {
                "count": 0, "avg_sim": 0, "best_sim": 0,
                "label": label,
            }

    # Add unclassified group summary
    if unclassified_segs:
        unclassified_segs.sort(key=lambda x: x["similarity"], reverse=True)
        uc_sims = [s["similarity"] for s in unclassified_segs]
        group_summary["_unclassified"] = {
            "count": len(unclassified_segs),
            "avg_sim": round(sum(uc_sims) / len(uc_sims), 4),
            "best_sim": round(max(uc_sims), 4) if uc_sims else 0,
            "label": "Unclassified",
        }
        groups["_unclassified"] = unclassified_segs

    # Compute medoid (representative sample) for each group
    from .segment_cluster import _find_medoid

    group_medoids: dict[str, dict] = {}
    for name in preset_names:
        normed_list = group_normed[name]
        if len(normed_list) >= 1:
            medoid_arr, medoid_idx = _find_medoid(normed_list)
            dist = compute_distance(preset_shapes[name], medoid_arr, method)
            sim = distance_to_similarity(dist, method)
            group_medoids[name] = {
                "values": _safe_list(medoid_arr),
                "medoid_idx": medoid_idx,
                "similarity_to_preset": round(float(sim), 4),
            }

    return {
        "groups": groups,
        "group_summary": group_summary,
        "group_medoids": group_medoids,
        "segments_total": len(all_segments),
        "unclassified": unclassified,
        "min_similarity": min_similarity,
    }
