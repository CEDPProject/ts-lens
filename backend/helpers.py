# -*- coding: utf-8 -*-
"""
TS Pattern Finder - Utility/helper functions.
Pure computation: normalization, distance, NaN handling, resampling.
"""
from __future__ import annotations

import math as _math

import numpy as np
from scipy.signal import resample as scipy_resample

from .app_config import cfg, cfg_search

# ── Constants from config ─────────────────────────────────────
_VALID_METHODS = {"euclidean", "cosine", "dtw", "shape"}
_MAX_COMPARE_LEN = cfg_search("comparison.max_compare_len")
_DTW_BAND_RATIO = cfg_search("dtw.band_ratio")
_DTW_MIN_BAND = cfg_search("dtw.min_band")
_DTW_MAX_COMPARE_LEN = int(cfg_search("dtw.max_compare_len"))

_MIN_STD = float(cfg_search("normalize.min_std"))
_DEFAULT_NAN_MODE = cfg_search("nan.mode")
_DEFAULT_NAN_RATIO = cfg_search("nan.max_ratio")


# ── Safe conversion ──────────────────────────────────────────
def _safe_list(arr):
    """numpy array -> Python list with NaN replaced by None."""
    if isinstance(arr, np.ndarray):
        return [None if not np.isfinite(v) else v for v in arr.tolist()]
    return [None if isinstance(v, float) and not _math.isfinite(v) else v for v in arr]


def _safe_stats(arr):
    """numpy array -> NaN-free stats dict."""
    if isinstance(arr, np.ndarray):
        clean = arr[np.isfinite(arr)]
    else:
        clean = np.array([v for v in arr if v is not None and _math.isfinite(v)])
    n = len(clean)
    return {
        "count": n,
        "mean": round(float(np.mean(clean)), 2) if n else 0,
        "std": round(float(np.std(clean)), 2) if n else 0,
        "min": round(float(np.min(clean)), 2) if n else 0,
        "max": round(float(np.max(clean)), 2) if n else 0,
    }


# ── Normalization / Distance ─────────────────────────────────
def z_normalize(arr: np.ndarray) -> np.ndarray:
    std = arr.std()
    if std < _MIN_STD:
        return np.zeros_like(arr)
    return (arr - arr.mean()) / std


def compute_distance(query: np.ndarray, candidate: np.ndarray, method: str) -> float:
    # Ensure same length for euclidean/cosine (resample shorter to match)
    if method in ("euclidean", "cosine", "shape") and len(query) != len(candidate):
        if len(candidate) != len(query):
            candidate = _resample(candidate, len(query))
    if method == "euclidean":
        return np.sqrt(np.mean((query - candidate) ** 2))
    elif method == "cosine":
        nq = np.linalg.norm(query)
        nc = np.linalg.norm(candidate)
        # Two normalized flat series are the same shape.  Treat only the
        # one-flat/one-nonflat case as maximally different.
        if nq < _MIN_STD and nc < _MIN_STD:
            return 0.0
        if nq < _MIN_STD or nc < _MIN_STD:
            return 1.0
        return 1.0 - np.dot(query, candidate) / (nq * nc)
    elif method == "dtw":
        if max(len(query), len(candidate)) > _DTW_MAX_COMPARE_LEN:
            query = _resample(query, _DTW_MAX_COMPARE_LEN)
            candidate = _resample(candidate, _DTW_MAX_COMPARE_LEN)
        try:
            from dtaidistance import dtw as dtw_lib
            d = dtw_lib.distance_fast(query.astype(np.double), candidate.astype(np.double))
            return d / max(len(query), len(candidate))
        except ImportError:
            return _dtw_fallback(query, candidate)
    elif method == "shape":
        return _shape_feature_distance(query, candidate)
    raise ValueError(f"Unsupported distance method: {method}")


_SHAPE_SCALE_FLOOR = cfg_search("shape.scale_floor")
_SHAPE_PEAK_NOISE_RATIO = cfg_search("shape.peak_noise_ratio")


def _extract_shape_features(arr: np.ndarray) -> np.ndarray:
    """Extract shape features: [std, slope, peak_count, peak_prominence, symmetry]."""
    n = len(arr)
    std = float(arr.std())
    slope = float(np.polyfit(np.arange(n), arr, 1)[0]) if n >= 2 else 0.0
    mid = n // 2
    first_half = arr[:mid].mean() if mid > 0 else 0.0
    second_half = arr[mid:].mean() if mid < n else 0.0
    # 앞뒤 절반의 평균 차이. 예전에는 두 평균의 합으로 나눴는데, z-정규화한
    # 대칭 도형은 두 평균이 모두 0 근처라 0/0 꼴이 되어 잡음만으로 1 과 0 을
    # 오갔다. 계열의 진폭(std)으로 나눠 안정시킨다.
    amp = float(arr.std()) or 1.0
    symmetry = 1.0 - min(1.0, abs(float(first_half - second_half)) / amp)
    # 방향이 바뀐 횟수. 잡음으로 생긴 미세한 흔들림까지 세면 값이 크게 튀어
    # (같은 모양에 잡음만 얹어도 배로 뛴다) 거리 계산을 지배해 버린다.
    # 진폭에 견줘 의미 있는 변화만 센다.
    diff = np.diff(arr)
    thr = _SHAPE_PEAK_NOISE_RATIO * float(arr.std())
    signs = np.sign(np.where(np.abs(diff) < thr, 0.0, diff))
    signs = signs[signs != 0]
    sign_changes = int(np.sum(np.diff(signs) != 0)) if signs.size > 1 else 0
    peak_count = float(sign_changes) / max(1, n - 2)
    argmax = int(np.argmax(arr))
    argmin = int(np.argmin(arr))
    peak_pos = float(argmax) / max(1, n - 1)
    dip_pos = float(argmin) / max(1, n - 1)
    peak_prominence = float(arr.max() - arr.min())
    # 봉우리·골이 "가운데에 얼마나 가까운가" (1=한가운데, 0=양 끝).
    # 예전에는 center_mass = |peak_pos-0.5| + |dip_pos-0.5| 하나로 합쳐서,
    # 종 모양(봉우리 가운데)과 V 자(골 가운데)가 똑같이 0.5 였다. 여섯 특징이
    # 전부 같아져 두 모양의 검색 결과가 동일하게 나왔다.
    # 위치 자체(peak_pos)를 그대로 쓰면 이번엔 끝단에서 불안정하다 — 종 모양의
    # 최솟값은 왼쪽 끝이나 오른쪽 끝이라 잡음에 0 과 1 을 오간다. 가운데와의
    # 거리로 바꾸면 어느 끝이든 같은 값이 되어 흔들리지 않는다.
    peak_centrality = 1.0 - 2.0 * abs(peak_pos - 0.5)
    dip_centrality = 1.0 - 2.0 * abs(dip_pos - 0.5)
    return np.array([std, slope, peak_count, peak_prominence, symmetry,
                     peak_centrality, dip_centrality], dtype=np.float64)


def _shape_feature_distance(query: np.ndarray, candidate: np.ndarray) -> float:
    """Distance based on shape features rather than point-to-point values."""
    fq = _extract_shape_features(query)
    fc = _extract_shape_features(candidate)
    # 상대 차이로 재되 분모에 바닥을 둔다. 바닥이 없으면 기울기처럼 0 에 가까운
    # 특징이 1e-17 대 1e-4 같은 무의미한 차이로도 최대값(1)을 찍어 거리를
    # 지배한다 — 같은 모양에 잡음만 얹어도 뒤집힌 모양만큼 멀어졌다.
    scale = np.maximum(np.abs(fq) + np.abs(fc), _SHAPE_SCALE_FLOOR)
    return float(np.sqrt(np.mean(((fq - fc) / scale) ** 2)))



def _dtw_fallback(a: np.ndarray, b: np.ndarray) -> float:
    n, m = len(a), len(b)
    band = max(_DTW_MIN_BAND, int(max(n, m) * _DTW_BAND_RATIO))
    INF = 1e18
    dp = np.full((n + 1, m + 1), INF)
    dp[0, 0] = 0.0
    for i in range(1, n + 1):
        j_lo = max(1, i - band)
        j_hi = min(m, i + band)
        for j in range(j_lo, j_hi + 1):
            cost = (a[i - 1] - b[j - 1]) ** 2
            dp[i, j] = cost + min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])
    return float(np.sqrt(dp[n, m] / max(n, m)))


# ── NaN handling ─────────────────────────────────────────────
def _find_nan_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Find runs of consecutive NaN. Returns list of (start, length)."""
    runs = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            start = i
            while i < n and mask[i]:
                i += 1
            runs.append((start, i - start))
        else:
            i += 1
    return runs


def interpolate_nan(arr: np.ndarray,
                    max_nan_ratio: float = _DEFAULT_NAN_RATIO,
                    max_consecutive: int = 0,
                    method: str = "adaptive"):
    """Fill NaN using specified method. Returns None if NaN ratio exceeds max.

    Args:
        max_nan_ratio: Skip entire array if total NaN ratio exceeds this.
        max_consecutive: If > 0, only interpolate NaN runs shorter than this.
                         Longer runs are left as NaN.
        method: adaptive, linear, cubic, spline, nearest, zero, previous, moment
                adaptive = short gaps (<= 10pts) use linear, long gaps use MOMENT
    """
    mask = np.isnan(arr)
    nan_count = mask.sum()
    if nan_count == 0:
        return arr
    if nan_count / len(arr) > max_nan_ratio:
        return None
    good = np.where(~mask)[0]
    if len(good) < 2:
        return None

    # Determine which NaN indices to actually fill
    if max_consecutive > 0:
        runs = _find_nan_runs(mask)
        fill_mask = np.zeros(len(arr), dtype=bool)
        for start, length in runs:
            if length <= max_consecutive:
                fill_mask[start:start + length] = True
        # If no runs qualify, nothing to fill
        if not fill_mask.any():
            return arr
    else:
        fill_mask = mask

    result = arr.copy()
    bad = np.where(fill_mask)[0]

    if method == "adaptive":
        # Adaptive: short gaps → linear, medium gaps → MOMENT, very long → skip
        ADAPTIVE_THRESHOLD = cfg("preprocessing.adaptive_threshold")
        MAX_GAP_SIZE = cfg("preprocessing.max_gap_size")
        runs = _find_nan_runs(fill_mask)
        short_mask = np.zeros(len(arr), dtype=bool)
        medium_mask = np.zeros(len(arr), dtype=bool)
        # very long gaps (> MAX_GAP_SIZE) are left as NaN
        for start, length in runs:
            if length <= ADAPTIVE_THRESHOLD:
                short_mask[start:start + length] = True
            elif length <= MAX_GAP_SIZE:
                medium_mask[start:start + length] = True
            # else: very long gap, leave as NaN

        # Fill short gaps with linear
        if short_mask.any():
            short_bad = np.where(short_mask)[0]
            result[short_bad] = np.interp(short_bad, good, arr[good])

        # Fill medium gaps with MOMENT
        if medium_mask.any():
            from .moment_imputer import moment_impute
            # Create temp array with short gaps already filled
            temp = result.copy()
            temp[medium_mask] = np.nan
            moment_result = moment_impute(temp)
            if moment_result is not None:
                result[medium_mask] = moment_result[medium_mask]
            else:
                # Fallback to linear if MOMENT fails
                medium_bad = np.where(medium_mask)[0]
                result[medium_bad] = np.interp(medium_bad, good, arr[good])

    elif method == "zero":
        result[fill_mask] = 0.0
    elif method == "previous":
        for idx in bad:
            prev = good[good < idx]
            result[idx] = result[prev[-1]] if len(prev) > 0 else result[good[0]]
    elif method == "nearest":
        from scipy.interpolate import interp1d
        f = interp1d(good, arr[good], kind="nearest",
                     fill_value="extrapolate", bounds_error=False)
        result[bad] = f(bad)
    elif method == "cubic":
        if len(good) >= 4:
            from scipy.interpolate import CubicSpline
            cs = CubicSpline(good, arr[good], extrapolate=True)
            result[bad] = cs(bad)
        else:
            result[bad] = np.interp(bad, good, arr[good])
    elif method == "spline":
        if len(good) >= 4:
            from scipy.interpolate import UnivariateSpline
            s = UnivariateSpline(good, arr[good], s=len(good) * 0.1, ext=0)
            result[bad] = s(bad)
        else:
            result[bad] = np.interp(bad, good, arr[good])
    elif method == "moment":
        from .moment_imputer import moment_impute
        return moment_impute(arr)
    else:  # linear (default fallback)
        result[bad] = np.interp(bad, good, arr[good])

    return result


def handle_nan(arr: np.ndarray,
               nan_mode: str = _DEFAULT_NAN_MODE,
               max_nan_ratio: float = _DEFAULT_NAN_RATIO,
               method: str = "linear"):
    """Common NaN handler. skip: None if any NaN / interpolate: fill.

    When nan_mode='interpolate', tries to fill using the given method.
    Returns None if NaN ratio exceeds max (auto-skip).
    """
    arr = np.asarray(arr, dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        return None
    if not np.isfinite(arr).all():
        arr = arr.copy()
        arr[~np.isfinite(arr)] = np.nan
    if nan_mode == "skip":
        return None if np.isnan(arr).any() else arr
    return interpolate_nan(arr, max_nan_ratio=max_nan_ratio, method=method)


def make_nan_handler(nan_mode: str = "interpolate",
                     nan_max_ratio: float = 0.5,
                     nan_method: str = "linear"):
    """Return a partial NaN handler with pre-bound parameters.

    Usage:
        _hn = make_nan_handler(nan_mode, nan_max_ratio, nan_method)
        cleaned = _hn(arr)
    """
    def _handler(arr: np.ndarray):
        return handle_nan(arr, nan_mode, nan_max_ratio, method=nan_method)
    return _handler


def distance_to_similarity(dist: float, method: str) -> float:
    """Convert distance to similarity score."""
    if method == "cosine":
        return max(0.0, 1.0 - dist)
    if method == "shape":
        return max(0.0, 1.0 - dist)
    return 1.0 / (1.0 + dist)


# ── Resampling ───────────────────────────────────────────────
def resample_array(arr: np.ndarray, target_len: int) -> np.ndarray:
    """FFT-based resampling (high quality, for query patterns)."""
    if len(arr) == target_len:
        return arr
    return scipy_resample(arr, target_len)


def _resample(arr: np.ndarray, target_len: int) -> np.ndarray:
    """Linear interpolation resampling (fast, for search comparison)."""
    n = len(arr)
    if n == target_len:
        return arr
    x_old = np.linspace(0, 1, n)
    x_new = np.linspace(0, 1, target_len)
    return np.interp(x_new, x_old, arr)


def auto_downsample(arr: np.ndarray, max_len: int
                    ) -> tuple[np.ndarray, int, bool]:
    """Downsample array if it exceeds max_len.

    Returns:
        (downsampled_arr, compare_len, was_downsampled)
    """
    if len(arr) > max_len:
        return _resample(arr, max_len), max_len, True
    return arr, len(arr), False


# ── Interval formatting ──────────────────────────────────────
def format_interval(total_seconds: float) -> str:
    """Format seconds into human-readable interval string.

    Uses integer format when value is exact, float otherwise.
    Examples: "30sec", "5min", "1h", "1.5h", "1d", "2.5d"
    """
    if total_seconds < 60:
        return f"{int(total_seconds)}sec"
    elif total_seconds < 3600:
        m = total_seconds / 60
        return f"{int(m)}min" if m == int(m) else f"{m:.1f}min"
    elif total_seconds < 86400:
        h = total_seconds / 3600
        return f"{int(h)}h" if h == int(h) else f"{h:.1f}h"
    else:
        d = total_seconds / 86400
        return f"{int(d)}d" if d == int(d) else f"{d:.1f}d"


def format_interval_minutes(minutes: float) -> str:
    """Format sampling interval in minutes to human-readable string.

    Convenience wrapper around format_interval for minute-based inputs.
    """
    return format_interval(minutes * 60)


def parse_interval_seconds(interval_str: str | None) -> float:
    """Parse interval string (e.g. '1sec', '5min', '1h', '1.5d') back to seconds.

    Returns 3600 (1 hour) as default if parsing fails, for backward compatibility.
    """
    if not interval_str:
        return 3600.0
    import re
    m = re.match(r'^([\d.]+)\s*(sec|min|h|d)', interval_str)
    if not m:
        return 3600.0
    val = float(m.group(1))
    unit = m.group(2)
    if unit == 'sec':
        return val
    elif unit == 'min':
        return val * 60
    elif unit == 'h':
        return val * 3600
    elif unit == 'd':
        return val * 86400
    return 3600.0
