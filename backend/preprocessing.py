# -*- coding: utf-8 -*-
"""
TS Pattern Finder - Preprocessing & calendar boundary functions.
Gap interpolation, outlier clamping, value range limiting, diagnostics.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .app_config import cfg
from .helpers import interpolate_nan, format_interval

# ── Config values ─────────────────────────────────────────────
_GAP_MULTIPLIER = cfg("preprocessing.gap_detection_multiplier")
_MAX_INSERT_PTS = cfg("preprocessing.max_insertable_points")
_OUTLIER_IQR_K = cfg("preprocessing.outlier_iqr_k")
_PCT_BOUNDS = cfg("preprocessing.outlier_percentile_bounds")
_NAN_OVERRIDE_RATIO = cfg("preprocessing.nan_override_ratio")

_SECONDS_PER_DAY = 86400
_MINUTES_PER_DAY = 1440


# ── Sampling interval detection ──────────────────────────────
def _detect_interval_from_labels(labels: list) -> pd.Timedelta | None:
    """Estimate sampling interval (median) from timestamp labels."""
    if len(labels) < 2:
        return None
    try:
        ts = pd.to_datetime(labels[:200])
    except Exception:
        return None
    diffs = ts[1:] - ts[:-1]
    pos = diffs[diffs > pd.Timedelta(0)]
    if len(pos) == 0:
        return None
    return pos.median()


# ── Gap interpolation ────────────────────────────────────────
def _fill_time_gaps(labels: list, values: np.ndarray,
                    expected_interval: pd.Timedelta | None = None,
                    interp_method: str = "linear",
                    interpolate: bool = True,
                    ) -> tuple[list, np.ndarray, dict]:
    """Detect missing timestamps and fill gaps.

    Inserts NaN placeholders for missing timestamps.
    If interpolate=True, fills them using interpolate_nan.
    If interpolate=False, leaves NaN for a later interpolation step.
    """
    info = {"gaps_found": 0, "points_inserted": 0, "filled_ranges": []}
    if len(labels) < 2:
        return labels, values, info

    try:
        ts = pd.to_datetime(labels)
    except Exception:
        return labels, values, info

    if expected_interval is None:
        expected_interval = _detect_interval_from_labels(labels)
    if expected_interval is None or expected_interval <= pd.Timedelta(0):
        return labels, values, info

    threshold = expected_interval * _GAP_MULTIPLIER
    new_ts_list = [ts[0]]
    new_val_list = [float(values[0])]

    for i in range(1, len(ts)):
        gap = ts[i] - ts[i - 1]
        if gap > threshold:
            n_insert = int(round(gap / expected_interval)) - 1
            if 0 < n_insert < _MAX_INSERT_PTS:
                info["gaps_found"] += 1
                info["points_inserted"] += n_insert
                info["filled_ranges"].append(
                    (str(ts[i - 1]), str(ts[i]))
                )
                for j in range(1, n_insert + 1):
                    interp_ts = ts[i - 1] + expected_interval * j
                    new_ts_list.append(interp_ts)
                    new_val_list.append(np.nan)  # placeholder
        new_ts_list.append(ts[i])
        new_val_list.append(float(values[i]))

    new_labels = [t.strftime("%Y-%m-%d %H:%M:%S") if hasattr(t, 'strftime') else str(t)
                  for t in new_ts_list]
    new_values = np.array(new_val_list, dtype=np.float64)

    # Fill inserted NaN placeholders using chosen method
    if info["points_inserted"] > 0 and interpolate:
        filled = interpolate_nan(new_values, max_nan_ratio=1.0,
                                 method=interp_method)
        if filled is not None:
            new_values = filled

    return new_labels, new_values, info


# ── Outlier clamping ─────────────────────────────────────────
def _clamp_outliers(values: np.ndarray, method: str = "iqr",
                    k: float | None = None) -> tuple[np.ndarray, dict]:
    """Clamp outliers to boundary values."""
    clean = values[~np.isnan(values)]
    if len(clean) < 4:
        return values, {"clamped_count": 0, "lower_bound": None, "upper_bound": None}

    if method == "iqr":
        if k is None:
            k = _OUTLIER_IQR_K
        q1, q3 = np.percentile(clean, [25, 75])
        iqr = q3 - q1
        lower = q1 - k * iqr
        upper = q3 + k * iqr
    elif method == "percentile":
        pct = min(max(k or 1.0, _PCT_BOUNDS[0]), _PCT_BOUNDS[1])
        lower = np.percentile(clean, pct)
        upper = np.percentile(clean, 100.0 - pct)
    else:  # sigma
        if k is None:
            k = _OUTLIER_IQR_K
        mu, sigma = np.mean(clean), np.std(clean)
        lower = mu - k * sigma
        upper = mu + k * sigma

    result = values.copy()
    mask_low = result < lower
    mask_high = result > upper
    result[mask_low] = lower
    result[mask_high] = upper
    clamped = int(mask_low.sum() + mask_high.sum())

    return result, {
        "clamped_count": clamped,
        "lower_bound": round(float(lower), 4),
        "upper_bound": round(float(upper), 4),
    }


# ── Value range limiting ─────────────────────────────────────
def _apply_value_range(values: np.ndarray, val_min, val_max,
                       mode: str = "clamp") -> tuple[np.ndarray, dict]:
    """Limit values to a range. mode='clamp' or 'nan'."""
    result = values.copy()
    count = 0
    if val_min is not None:
        mask = result < val_min
        c = int(mask.sum())
        result[mask] = np.nan if mode == "nan" else val_min
        count += c
    if val_max is not None:
        mask = result > val_max
        c = int(mask.sum())
        result[mask] = np.nan if mode == "nan" else val_max
        count += c

    # NaN interpolation is deferred to pipeline step 4
    return result, {"range_replaced": count,
                    "val_min": val_min, "val_max": val_max, "mode": mode}


# ── Preprocessing pipeline ───────────────────────────────────
def _preprocess_timeseries(labels: list, values: np.ndarray,
                           options: dict) -> tuple[list, np.ndarray, dict]:
    """Unified preprocessing function.

    Pipeline order (3 steps):
      Step 1 -- 극단값 처리 (택1: value_range OR clamp_outliers)
        a) Value range limiting (domain-specific min/max bounds)
        b) Outlier clamping     (statistical IQR/percentile/sigma)
      Step 2 -- 시간 갭 채움 (insert NaN placeholders for missing timestamps)
      Step 3 -- NaN 보간     (fill ALL remaining NaN)
    """
    info = {}
    interp_method = options.get("interpolation_method", "linear")

    # ── Step 1: 극단값 처리 (택1) ──
    # value_range and clamp_outliers are mutually exclusive.
    # If both are somehow True, value_range takes priority.

    if options.get("value_range", False):
        v_min = options.get("val_min")
        v_max = options.get("val_max")
        r_mode = options.get("range_mode", "clamp")
        if v_min is not None or v_max is not None:
            values, range_info = _apply_value_range(values, v_min, v_max, r_mode)
            info["range"] = range_info
    elif options.get("clamp_outliers", False):
        method = options.get("clamp_method", "iqr")
        k = float(options.get("clamp_k", _OUTLIER_IQR_K))
        values, clamp_info = _clamp_outliers(values, method=method, k=k)
        info["clamp"] = clamp_info

    # ── Step 2: 시간 갭 채움 ──

    if options.get("fill_gaps", False):
        labels, values, gap_info = _fill_time_gaps(
            labels, values, interpolate=False)
        info["gap"] = gap_info

    # ── Step 3: NaN 보간 (optional) ──

    if options.get("interpolate_nan", True):
        nan_count = int(np.isnan(values).sum()) if len(values) > 0 else 0
        if nan_count > 0:
            interpolated = interpolate_nan(values, max_nan_ratio=_NAN_OVERRIDE_RATIO,
                                           method=interp_method)
            if interpolated is not None:
                filled = nan_count - int(np.isnan(interpolated).sum())
                values = interpolated
                info["nan"] = {"interpolated": filled, "skipped": nan_count - filled,
                               "method": interp_method}

    return labels, values, info


# ── Data quality diagnostics ─────────────────────────────────
def _diagnose_timeseries(labels: list, values: np.ndarray) -> dict:
    """Read-only data quality diagnostics."""
    result = {
        "interval": None, "gaps_found": 0, "points_missing": 0,
        "gap_ratio": 0.0, "outliers_iqr": 0, "outlier_ratio": 0.0,
        "nan_count": 0, "total_points": len(values),
        "expected_points": len(values),
    }

    result["nan_count"] = int(np.isnan(values).sum()) if len(values) > 0 else 0

    if len(labels) < 2:
        return result

    interval = _detect_interval_from_labels(labels)
    if interval is None or interval <= pd.Timedelta(0):
        return result

    total_sec = interval.total_seconds()
    result["interval"] = format_interval(total_sec)

    try:
        ts = pd.to_datetime(labels)
    except Exception:
        return result

    threshold = interval * _GAP_MULTIPLIER
    gaps = 0
    missing = 0
    for i in range(1, len(ts)):
        gap = ts[i] - ts[i - 1]
        if gap > threshold:
            gaps += 1
            missing += int(round(gap / interval)) - 1

    result["gaps_found"] = gaps
    result["points_missing"] = missing
    result["expected_points"] = len(values) + missing
    if result["expected_points"] > 0:
        result["gap_ratio"] = round(missing / result["expected_points"], 4)

    clean = values[~np.isnan(values)]
    if len(clean) > 4:
        q1, q3 = np.percentile(clean, [25, 75])
        iqr = q3 - q1
        lower = q1 - _OUTLIER_IQR_K * iqr
        upper = q3 + _OUTLIER_IQR_K * iqr
        outlier_count = int(((clean < lower) | (clean > upper)).sum())
        result["outliers_iqr"] = outlier_count
        if len(clean) > 0:
            result["outlier_ratio"] = round(outlier_count / len(clean), 4)

    return result


# ── Calendar boundary functions ──────────────────────────────
def _get_boundary_key(ts: pd.Timestamp, boundary_minutes: int,
                      ref_start: pd.Timestamp | None = None) -> pd.Timestamp:
    """Map timestamp to calendar boundary (floor)."""
    if boundary_minutes == 1:
        return ts.floor('min')
    if boundary_minutes == 60:
        return ts.floor('h')
    if boundary_minutes == 360:
        return ts.floor('6h')
    if boundary_minutes == _MINUTES_PER_DAY:
        return ts.normalize()
    if boundary_minutes == 10080:
        return ts.normalize() - pd.Timedelta(days=ts.dayofweek)
    if boundary_minutes == 525600:
        return pd.Timestamp(year=ts.year, month=1, day=1)
    if ref_start is None:
        ref_start = ts.normalize()
    days = boundary_minutes / _MINUTES_PER_DAY
    elapsed_days = (ts - ref_start).total_seconds() / _SECONDS_PER_DAY
    group_idx = int(elapsed_days // days)
    return ref_start + pd.Timedelta(days=group_idx * days)


def _parse_timestamps(labels: list):
    """Parse labels to DatetimeIndex. Returns None on failure."""
    try:
        ts = pd.to_datetime(pd.Series(labels), errors="coerce")
    except Exception:
        return None
    if ts.isna().sum() > len(ts) * 0.5:
        return None
    return ts


def find_calendar_boundaries(labels: list, boundary_minutes: int,
                             fixed_len: int = 0) -> list[int] | None:
    """Return start indices aligned to calendar boundaries."""
    ts = _parse_timestamps(labels)
    if ts is None:
        return None

    N = len(labels)
    starts = []

    for i, t in enumerate(ts):
        if pd.isna(t):
            continue
        is_boundary = _is_calendar_boundary(t, boundary_minutes)
        if is_boundary:
            if fixed_len > 0 and i + fixed_len > N:
                continue
            starts.append(i)

    return starts if starts else None


def _is_calendar_boundary(t: pd.Timestamp, boundary_minutes: int) -> bool:
    """Check if timestamp is a calendar boundary."""
    if boundary_minutes == 1:
        return t.second == 0
    elif boundary_minutes == 60:
        return t.minute == 0 and t.second == 0
    elif boundary_minutes == 360:
        return t.hour % 6 == 0 and t.minute == 0 and t.second == 0
    elif boundary_minutes == _MINUTES_PER_DAY:
        return t.hour == 0 and t.minute == 0 and t.second == 0
    elif boundary_minutes == 10080:
        return (t.weekday() == 0 and t.hour == 0
                and t.minute == 0 and t.second == 0)
    elif boundary_minutes == 525600:
        return (t.month == 1 and t.day == 1
                and t.hour == 0 and t.minute == 0)
    else:
        if t.hour == 0 and t.minute == 0 and t.second == 0:
            days = boundary_minutes / _MINUTES_PER_DAY
            return (t.dayofyear - 1) % days < 1
        return False


def _describe_period(pts: float, interval_seconds: float = 3600.0) -> tuple[str, str]:
    """Convert period (in points) to human-readable (label, tooltip).

    Args:
        pts: period in number of data points
        interval_seconds: sampling interval in seconds (default 3600 = hourly)
    """
    # Convert points to hours
    hours = pts * interval_seconds / 3600.0

    known = [
        (5, 8, "6h"),
        (10, 14, "12h"),
        (22, 26, "1d (24h)"),
        (44, 52, "2d (48h)"),
        (70, 76, "3d (72h)"),
        (115, 125, "5d"),
        (165, 172, "1w (168h)"),
        (330, 340, "2w"),
        (500, 520, "3w"),
        (700, 750, "1mo"),
        (1400, 1500, "2mo"),
        (2100, 2200, "3mo"),
        (4300, 4400, "6mo"),
        (8700, 8800, "1y"),
    ]
    for lo, hi, label in known:
        if lo <= hours <= hi:
            tooltip = f"{pts:.1f}pt x {interval_seconds}s = {hours:.1f}h -> '{label}'"
            return label, tooltip

    if hours < 1:
        mins = hours * 60
        label = f"{mins:.0f}min"
    elif hours < 24:
        label = f"{hours:.0f}h"
    elif hours < 168:
        label = f"{hours / 24:.1f}d ({hours:.0f}h)"
    elif hours < 720:
        label = f"{hours / 168:.1f}w ({hours:.0f}h)"
    else:
        label = f"{hours / 720:.1f}mo ({hours:.0f}h)"
    if hours >= 1:
        time_str = f"{hours:.1f}h"
    else:
        time_str = f"{hours * 60:.1f}min"
    tooltip = f"{pts:.1f}pt x {interval_seconds}s = {time_str} -- auto-converted"
    return label, tooltip
