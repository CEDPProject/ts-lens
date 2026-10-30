# -*- coding: utf-8 -*-
"""Periodicity analysis service -- FFT-based period detection.

Extracted from routes/data_load.py to break cross-layer dependency
(agents were importing private functions from route modules).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from backend.app_config import cfg
from backend.preprocessing import _describe_period


def analyze_periodicity_core(values: np.ndarray,
                             interval_seconds: float = 3600.0) -> dict:
    """Core FFT-based periodicity analysis.

    Args:
        values: time series values
        interval_seconds: sampling interval in seconds (default 3600 = hourly)
    """
    N = len(values)
    min_len = cfg("periodicity.min_data_length")

    if N < min_len:
        return {"has_periodicity": False, "message": "Too short", "periods": []}

    mask = np.isnan(values)
    if mask.all():
        return {"has_periodicity": False, "message": "All NaN", "periods": []}
    if mask.any():
        good = np.where(~mask)[0]
        values = np.interp(np.arange(N), good, values[good])

    # 선형 추세를 먼저 뺀다. 평균만 빼면 우상향 계열의 스펙트럼이 최저 주파수에
    # 몰려, 주기가 없는 자료에서도 "계열 길이의 절반"이 1위 주기로 잡힌다
    # (단조 증가 자료가 '2d 주기'로, ETT 온도가 '1y' 하나로만 나오던 원인).
    idx = np.arange(N, dtype=np.float64)
    try:
        slope, intercept = np.polyfit(idx, values, 1)
        v = values - (slope * idx + intercept)
    except Exception:
        v = values - np.mean(values)
    fft_vals = np.fft.rfft(v)
    power = np.abs(fft_vals) ** 2
    freqs = np.fft.rfftfreq(N)

    power[0] = 0
    if len(power) > 1:
        power[1] = 0

    min_repeats = cfg("periodicity.min_repeats")

    # 계열 안에서 min_repeats 번은 반복돼야 주기로 인정한다. 그 조건은
    # "주기 <= N/min_repeats", 곧 "bin >= min_repeats" 와 같다. 이 아래쪽
    # 저주파 대역은 추세에 해당하며 전력이 압도적으로 커서, 예전에는
    #   (1) 정규화 기준을 독차지해 하루·일주일 봉우리를 문턱 아래로 눌렀고
    #   (2) 상위 5개를 먼저 자른 뒤 걸러내 후보를 통째로 잡아먹었다
    # (시간 간격 온도 자료에서 하루 주기가 안 잡히던 원인이다.)
    # 그래서 대역을 먼저 자르고, 그 안에서 정규화한 뒤 봉우리를 찾는다.
    lo = max(2, int(min_repeats))
    if lo >= len(power):
        return {"has_periodicity": False, "message": "Too short", "periods": []}
    band = power.copy()
    band[:lo] = 0

    if band.max() <= 0:
        return {"has_periodicity": False, "message": "No signal", "periods": []}

    power_norm = band / band.max()

    peak_height = cfg("periodicity.fft_peak_height")
    peak_dist_ratio = cfg("periodicity.fft_peak_distance_ratio")
    peak_indices, _ = find_peaks(
        power_norm, height=peak_height,
        distance=max(1, int(N * peak_dist_ratio)))

    if len(peak_indices) == 0:
        return {"has_periodicity": False, "message": "No periodicity", "periods": []}

    sorted_peaks = sorted(peak_indices, key=lambda i: band[i], reverse=True)

    periods = []
    for pi in sorted_peaks:
        if len(periods) >= 5:
            break
        if freqs[pi] > 0:
            period_pts = 1.0 / freqs[pi]
            if period_pts > N / min_repeats:
                continue
            strength = float(power_norm[pi])
            desc, tooltip = _describe_period(period_pts, interval_seconds)
            periods.append({
                "period_points": round(float(period_pts), 1),
                "strength": round(strength, 4),
                "description": desc,
                "tooltip": tooltip,
            })

    strength_threshold = cfg("periodicity.strength_threshold")
    has_period = (len(periods) > 0
                  and periods[0]["strength"] >= strength_threshold)

    if has_period:
        top = periods[0]
        message = (f"Main period: {top['description']}"
                   f" (strength: {top['strength']:.0%})")
    else:
        message = "No clear periodicity found."

    return {
        "has_periodicity": has_period,
        "message": message,
        "periods": periods,
    }
