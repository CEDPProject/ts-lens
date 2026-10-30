# -*- coding: utf-8 -*-
"""
Segment Cluster -- clustering-based segment grouping.

Uses modular clustering system from backend.clustering.
Supports K-Means, k-Shape, MOMENT Embedding, and extensible algorithm registry.
"""
from __future__ import annotations

import numpy as np

from .helpers import (
    z_normalize, distance_to_similarity,
    _resample, handle_nan, _safe_list, _DEFAULT_NAN_MODE, _DEFAULT_NAN_RATIO,
)
from .segment_search import (
    _sources_to_segments, _MIN_SEGMENT_LEN,
    representative_segment_length,
)
from .clustering import (
    get_algorithm, get_feature_extractor, ClusterResult,
)


# ── Clustering Entry Point ───────────────────────────────────────


def _find_medoid(members: list[np.ndarray]) -> tuple[np.ndarray, int]:
    """Find the medoid (member closest to cluster center).

    Returns:
        (medoid array, index of medoid in members list)
    """
    if len(members) == 1:
        return members[0], 0

    # Compute mean as reference center
    center = np.mean(members, axis=0)

    # Find member closest to center
    min_dist = float('inf')
    medoid_idx = 0
    for i, m in enumerate(members):
        dist = np.linalg.norm(m - center)
        if dist < min_dist:
            min_dist = dist
            medoid_idx = i

    return members[medoid_idx], medoid_idx


def _segment_identity(segment):
    return (segment.get("source_key", ""), segment.get("key") or segment.get("label", ""))


def _profile_index(candidates):
    identities, ranges = {}, {}
    for item in candidates:
        identity = _segment_identity(item)
        identities[identity] = None if identity in identities else item
        start, end = item.get("start_ts"), item.get("end_ts")
        if identity[0] and start and end:
            key = (identity[0], start, end)
            ranges[key] = None if key in ranges else item
    return identities, ranges


def _match_profile(segment, index):
    """Resolve a source and interval in O(1); ambiguous or absent means missing."""
    identities, ranges = index
    identity = _segment_identity(segment)
    if identity in identities:
        return identities[identity]
    return ranges.get((identity[0], segment.get("start_ts"), segment.get("end_ts")))


def cluster_segments(
    sources_data: list[dict],
    unit: str | None = "day",
    method: str = "euclidean",
    algorithm: str = "kmeans",
    k: int | None = None,
    k_min: int = 2,
    k_max: int = 10,
    auto_k_strategy: str = "elbow",
    normalize: bool = True,
    nan_mode: str = _DEFAULT_NAN_MODE,
    nan_max_ratio: float = _DEFAULT_NAN_RATIO,
    overlay_sources_list: list[list[dict]] | None = None,
    profile_sources_list: list[tuple[str, list[dict]]] | None = None,
    profile_unit: str | None = None,
    progress_cb=None,
) -> dict:
    """Cluster segments using modular clustering system.

    Args:
        method: Feature extraction method ("euclidean", "shape", "preset").
        algorithm: Clustering algorithm ("kmeans", "kshape", "moment").
        k: Number of clusters. None = auto-K.
        auto_k_strategy: Strategy for auto-K selection ("elbow", "silhouette", etc.)
        normalize: Apply z-score normalization to segments.
        progress_cb: Optional callback for progress updates.
    """
    if algorithm in {"kshape", "moment"} and overlay_sources_list:
        raise ValueError("Joint multi-column clustering currently requires kmeans")
    for name, value in (("k", k), ("k_min", k_min), ("k_max", k_max)):
        if value is not None and (type(value) is not int or value < 2):
            raise ValueError(f"{name} must be an integer of at least 2")
    if k_min > k_max:
        raise ValueError("k_min must not exceed k_max")
    empty_result = {
        "groups": {}, "group_summary": {}, "centroids": {},
        "segments_total": 0, "k": 0, "k_auto": k is None,
        "silhouette_score": 0, "algorithm": algorithm,
    }

    # -- 1. Split all sources into segments --
    if progress_cb:
        progress_cb("Splitting sources into segments...")
    all_segments = _sources_to_segments(sources_data, unit)

    if not all_segments:
        empty_result["segments_total"] = 0
        return empty_result

    first_len = representative_segment_length(all_segments)
    seg_length = max(_MIN_SEGMENT_LEN, first_len)

    # -- 2. Build additional selected-column segment maps (aligned by index) --
    has_overlay = bool(overlay_sources_list)
    ov_all_segments: list[list[dict]] = []
    if has_overlay:
        for ov_sources in overlay_sources_list:
            ov_all_segments.append(_sources_to_segments(ov_sources, unit))

    overlay_indices = [_profile_index(segments) for segments in ov_all_segments]

    # -- 3. Equal weights --
    n_cols = 1 + (len(ov_all_segments) if has_overlay else 0)
    w = np.ones(n_cols) / n_cols

    # -- 4. Get feature extractor --
    feature_extractor = get_feature_extractor(method)
    use_feature_space = feature_extractor.uses_feature_space

    # -- 5. Build feature matrix --
    feature_rows = []
    normed_rows = []  # Store normalized time series for centroid calculation
    valid_indices = []

    for si, seg in enumerate(all_segments):
        raw = np.array(seg["values"], dtype=np.float64)
        if len(raw) < _MIN_SEGMENT_LEN:
            continue
        cleaned = handle_nan(raw, nan_mode, nan_max_ratio)
        if cleaned is None:
            continue
        if len(cleaned) != seg_length:
            cleaned = _resample(cleaned, seg_length)

        seg_std = cleaned.std()
        seg_mean = cleaned.mean()

        normed = z_normalize(cleaned) if normalize else cleaned

        overlay_prepared = []
        if has_overlay:
            skip = False
            for ov_idx, ov_segs in enumerate(ov_all_segments):
                ov_seg = _match_profile(seg, overlay_indices[ov_idx])
                if ov_seg is None:
                    skip = True
                    break
                ov_raw = np.array(ov_seg["values"], dtype=np.float64)
                ov_cleaned = handle_nan(ov_raw, nan_mode, nan_max_ratio)
                if ov_cleaned is None:
                    skip = True
                    break
                if len(ov_cleaned) != seg_length:
                    ov_cleaned = _resample(ov_cleaned, seg_length)
                ov_normed = z_normalize(ov_cleaned) if normalize else ov_cleaned
                overlay_prepared.append((
                    ov_normed,
                    float(ov_cleaned.mean()),
                    float(ov_cleaned.std()),
                ))
            if skip:
                continue

        if use_feature_space:
            # Shape/preset extractors produce compact features. For joint
            # clustering, extract the same feature vector per selected column
            # and concatenate weighted column features.
            parts = [
                feature_extractor.extract(
                    normed, raw_mean=seg_mean, raw_std=seg_std
                ) * np.sqrt(w[0])
            ]
            for ov_idx, (ov_normed, ov_mean, ov_std) in enumerate(overlay_prepared):
                parts.append(
                    feature_extractor.extract(
                        ov_normed, raw_mean=ov_mean, raw_std=ov_std
                    ) * np.sqrt(w[ov_idx + 1])
                )
            feat = np.concatenate(parts)
        elif has_overlay:
            parts = [normed * np.sqrt(w[0])]
            for ov_idx, (ov_normed, _, _) in enumerate(overlay_prepared):
                parts.append(ov_normed * np.sqrt(w[ov_idx + 1]))
            level_parts = [seg_mean * 0.1, seg_std * 0.1]
            for _, ov_mean, ov_std in overlay_prepared:
                level_parts.extend([ov_mean * 0.1, ov_std * 0.1])
            feat = np.concatenate([np.concatenate(parts), np.array(level_parts)])
        else:
            # Raw features (euclidean)
            feat = feature_extractor.extract(normed, raw_mean=seg_mean, raw_std=seg_std)

        feature_rows.append(feat)
        normed_rows.append(normed)
        valid_indices.append(si)

    n_valid = len(feature_rows)
    if n_valid < 2:
        # 조용히 빈 결과를 돌려주면 화면에는 클러스터마다 "데이터 부족"만 뜨고
        # 왜 그런지 알 수 없다. 가장 흔한 원인(구간이 1~2 point 로 잘림)을 밝힌다.
        empty_result["segments_total"] = len(all_segments)
        if all_segments and first_len < _MIN_SEGMENT_LEN:
            empty_result["reason"] = "segment_too_short"
            empty_result["segment_length"] = first_len
            empty_result["min_segment_length"] = _MIN_SEGMENT_LEN
        else:
            empty_result["reason"] = "not_enough_segments"
        return empty_result

    # k-Shape and MOMENT consume waveforms, not compact shape features or
    # raw vectors with appended mean/std. Those extras invent time points.
    X = np.array(normed_rows if algorithm in {"kshape", "moment"} else feature_rows, dtype=np.float64)
    k_min = min(k_min, n_valid)
    k_max = min(k_max, n_valid)
    if k is not None:
        k = min(k, n_valid)

    if progress_cb:
        progress_cb(f"Prepared {n_valid} segments for clustering")

    # -- 6. Run clustering using modular algorithm --
    algo = get_algorithm(algorithm, k=k, k_min=k_min, k_max=k_max, auto_k_strategy=auto_k_strategy)

    if progress_cb:
        progress_cb(f"Running {algorithm} clustering...")
    result: ClusterResult = algo.fit_predict(X, progress_cb=progress_cb)
    labels = result.labels
    k_found = result.k
    sil_score = result.score
    cluster_centers = result.centers

    if progress_cb:
        progress_cb(f"Building result groups (k={k_found})...")

    # -- 7. Build result groups --
    unique_labels = sorted(set(labels))
    cluster_ids = [lb for lb in unique_labels if lb >= 0]

    groups: dict[str, list[dict]] = {}
    centroids_display = {}
    cluster_member_indices: dict[str, list[int]] = {}  # For computing centroids from actual data

    for ci in cluster_ids:
        ckey = f"cluster_{ci}"
        groups[ckey] = []
        cluster_member_indices[ckey] = []
        centroids_display[ckey] = []  # Will be computed later

    for row_idx, si in enumerate(valid_indices):
        ci = int(labels[row_idx])
        seg = all_segments[si]
        raw = np.array(seg["values"], dtype=np.float64)

        ckey = f"cluster_{ci}"
        if cluster_centers is not None and ci < len(cluster_centers):
            dist = float(np.linalg.norm(X[row_idx] - cluster_centers[ci]))
            sim = distance_to_similarity(dist, "euclidean")
        else:
            sim = 0.0

        cluster_member_indices[ckey].append(row_idx)  # Track for centroid calculation
        groups[ckey].append({
            "key": seg.get("key", ""),
            "label": seg.get("label", ""),
            "source_key": seg.get("source_key", ""),
            "start_ts": seg.get("start_ts", ""),
            "end_ts": seg.get("end_ts", ""),
            "point_count": seg.get("point_count", len(raw)),
            "similarity": round(float(sim), 4),
            "values": _safe_list(raw),
        })

    # Compute centroids using Medoid (actual cluster member closest to center)
    # Medoid is more robust to outliers and represents a real pattern
    for ckey in cluster_member_indices:
        member_idxs = cluster_member_indices[ckey]
        if member_idxs:
            member_normed = [normed_rows[idx] for idx in member_idxs]
            medoid, medoid_idx = _find_medoid(member_normed)
            centroids_display[ckey] = _safe_list(medoid)
        else:
            centroids_display[ckey] = []

    for ckey in groups:
        groups[ckey].sort(key=lambda x: x["similarity"], reverse=True)

    # Build summary
    group_summary = {}
    label_idx = 1
    for ci in cluster_ids:
        ckey = f"cluster_{ci}"
        segs = groups[ckey]
        if segs:
            sims = [s["similarity"] for s in segs]
            group_summary[ckey] = {
                "count": len(segs),
                "avg_sim": round(sum(sims) / len(sims), 4),
                "best_sim": round(max(sims), 4),
                "label": f"Cluster {label_idx}",
            }
        else:
            group_summary[ckey] = {
                "count": 0, "avg_sim": 0, "best_sim": 0,
                "label": f"Cluster {label_idx}",
            }
        label_idx += 1

    # Sort groups by count descending
    sorted_keys = sorted(groups.keys(),
                         key=lambda ck: len(groups[ck]), reverse=True)
    groups = {ck: groups[ck] for ck in sorted_keys}
    group_summary = {ck: group_summary[ck] for ck in sorted_keys}
    centroids_display = {ck: centroids_display[ck] for ck in sorted_keys}

    # -- 7. Auto-profile: compute average shapes of other columns per cluster --
    column_profiles: dict[str, dict[str, list[float]]] = {}
    if profile_sources_list:
        # Determine how to split profile columns:
        # - If unit is set, use it
        # - If unit is None but profile_unit is set (inline segments), use profile_unit
        # - If both are None (collection dataset), use sources as-is
        _prof_split_unit = unit or profile_unit

        # Pre-build cluster membership once (shared across all profile columns)
        _cluster_members: dict[str, list[int]] = {}
        for ckey in cluster_ids:
            ck = f"cluster_{ckey}"
            _cluster_members[ck] = [
                si for ri, si in enumerate(valid_indices)
                if int(labels[ri]) == ckey
            ]

        for prof_col, prof_sources in profile_sources_list:
            # Split profile column by same unit
            # Use the same identity builder as primary and joint columns.
            # Already-segmented sources retain segment_key and timestamps.
            segmented = [src for src in prof_sources if src.get("segment_key") or not src.get("timestamps")]
            continuous = [src for src in prof_sources if not src.get("segment_key") and src.get("timestamps")]
            prof_segs = (_sources_to_segments(segmented, None)
                         + _sources_to_segments(continuous, _prof_split_unit))

            profile_index = _profile_index(prof_segs)

            # For each cluster, average the profile segments + consistency
            for ck, member_indices in _cluster_members.items():
                shapes = []
                for si in member_indices:
                    ps = _match_profile(all_segments[si], profile_index)
                    if ps is None:
                        continue
                    raw = np.array(ps["values"], dtype=np.float64)
                    cleaned = handle_nan(raw, nan_mode, nan_max_ratio)
                    if cleaned is not None:
                        if len(cleaned) != seg_length:
                            cleaned = _resample(cleaned, seg_length)
                        shapes.append(z_normalize(cleaned))
                if shapes:
                    avg_shape = np.mean(shapes, axis=0)
                    # Consistency: avg cosine similarity of members to mean
                    n = len(shapes)
                    if n >= 2:
                        avg_norm = float(np.linalg.norm(avg_shape))
                        if avg_norm > 0:
                            sims = []
                            for sh in shapes:
                                sh_norm = float(np.linalg.norm(sh))
                                if sh_norm > 0:
                                    cos = float(np.dot(sh, avg_shape)) / (sh_norm * avg_norm)
                                    sims.append((cos + 1) / 2)
                            consistency = round(sum(sims) / len(sims), 4) if sims else 0.5
                        else:
                            consistency = 0.5
                        std_shape = np.std(shapes, axis=0)
                        avg_std = round(float(np.mean(std_shape)), 4)
                    else:
                        consistency = 1.0
                        avg_std = 0.0
                    if ck not in column_profiles:
                        column_profiles[ck] = {}
                    column_profiles[ck][prof_col] = {
                        "shape": _safe_list(avg_shape),
                        "consistency": consistency,
                        "std": avg_std,
                        "n": n,
                    }

    # -- 8. Column Discriminative Power: how well each column separates clusters --
    column_discriminative = {}
    if column_profiles and len(column_profiles) >= 2:
        # Get all profile columns
        all_prof_cols = set()
        for ck_profiles in column_profiles.values():
            all_prof_cols.update(ck_profiles.keys())

        # For each column, compute inter-cluster diversity (pairwise distances between centroids)
        for prof_col in sorted(all_prof_cols):
            centroids = []
            cluster_keys = []
            for ck in sorted(column_profiles.keys()):
                if prof_col in column_profiles[ck]:
                    centroids.append(np.array(column_profiles[ck][prof_col]["shape"]))
                    cluster_keys.append(ck)

            if len(centroids) >= 2:
                # Compute mean pairwise Euclidean distance (z-score normalized)
                distances = []
                for i in range(len(centroids)):
                    for j in range(i + 1, len(centroids)):
                        dist = np.linalg.norm(centroids[i] - centroids[j])
                        distances.append(dist)

                avg_dist = float(np.mean(distances)) if distances else 0.0
                max_dist = float(np.max(distances)) if distances else 0.0

                # Determine discriminative level (empirical thresholds for z-score normalized data)
                if avg_dist > 3.0:
                    level = "high"
                elif avg_dist > 1.5:
                    level = "medium"
                else:
                    level = "low"

                column_discriminative[prof_col] = {
                    "avg_distance": round(avg_dist, 3),
                    "max_distance": round(max_dist, 3),
                    "level": level,
                    "num_clusters": len(centroids)
                }

    # Compute additional metrics for benchmarking
    ch_score = 0.0
    db_score = 0.0
    if len(cluster_ids) >= 2 and n_valid >= len(cluster_ids) + 1:
        try:
            from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score
            ch_score = calinski_harabasz_score(X, labels)
            db_score = davies_bouldin_score(X, labels)
        except Exception:
            pass

    result = {
        "groups": groups,
        "group_summary": group_summary,
        "centroids": centroids_display,
        "segments_total": len(all_segments),
        "k": len(cluster_ids),
        "silhouette_score": round(sil_score, 4),
        "calinski_harabasz_score": round(ch_score, 2),
        "davies_bouldin_score": round(db_score, 4),
        "algorithm": algorithm,
        "k_auto": k is None,
        "auto_k_strategy": auto_k_strategy if k is None else None,
    }
    if column_profiles:
        result["column_profiles"] = column_profiles
        result["column_discriminative"] = column_discriminative
    return result
