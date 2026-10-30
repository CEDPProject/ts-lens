# -*- coding: utf-8 -*-
"""LLM response parsing and time-series analysis utilities.

Extracted from llm_service.py -- contains _extract_json,
_compute_segment_features, _build_structured_response.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ─── Parse Response ──────────────────────────────────────────

def _extract_json(text: str, require_keys: tuple = ()) -> dict | None:
    """LLM 응답에서 JSON 객체를 꺼낸다.

    코드 울타리(```json … ```)가 있으면 그 안을, 없으면 본문에서 첫 '{' 부터 균형이 맞는
    객체를 raw_decode 로 읽는다. `require_keys` 를 주면 그 키를 하나라도 가진 객체만 받는다 —
    응답이 토큰 상한에서 잘리면 바깥 객체는 못 읽고 안쪽 {"ko","en"} 같은 조각만 읽히는데,
    그걸 결과로 삼지 않기 위해서다. 예전 정규식(\{[^{}]*\})은 중첩 JSON 을 첫 '}' 에서 잘라
    ko/en 이중 언어 응답을 전부 놓쳤다.
    """
    if not text:
        return None
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    decoder = json.JSONDecoder()
    for cand in candidates:
        pos = cand.find("{")
        while pos != -1:
            try:
                obj, _ = decoder.raw_decode(cand[pos:])
                if isinstance(obj, dict) and (not require_keys or any(k in obj for k in require_keys)):
                    return obj
            except json.JSONDecodeError:
                pass
            pos = cand.find("{", pos + 1)
    return None

def _compute_segment_features(arr) -> dict:
    """시계열 세그먼트에서 구체적인 수치 특징을 추출.

    LLM에 raw 값 대신 계산된 특징을 전달하여 명확한 해석을 유도.
    """
    import numpy as np
    from scipy import stats as sp_stats

    n = len(arr)
    result = {}

    # 1. 추세: 값의 절대 수준이 아니라 관측 범위 대비 선형 변화량을 사용한다.
    # 평균으로 나누면 z-score/영점 교차 시계열에서 비율이 폭발한다.
    if n >= 3:
        x = np.arange(n)
        slope, intercept, r_value, _, _ = sp_stats.linregress(x, arr)
        r2 = r_value ** 2
        value_range = float(np.max(arr) - np.min(arr))
        linear_span = (slope * (n - 1)) / (value_range + 1e-10) * 100
        if value_range < 1e-10 or abs(linear_span) < 10:
            trend_str = "Flat"
        elif r2 < 0.20:
            trend_str = f"No clear linear trend (span={linear_span:+.1f}% of range)"
        elif linear_span > 0:
            trend_str = f"Rising (span={linear_span:+.1f}% of range)"
        else:
            trend_str = f"Falling (span={linear_span:+.1f}% of range)"
        result["trend_direction"] = f"{trend_str}, R²={r2:.2f}"

    # 2. 변동성: 관측 범위 대비 표준편차. 평균이 0에 가까운 정규화 데이터에서도
    # 유한하고, 동일 파형의 평행 이동에도 값이 변하지 않는다.
    mean_val = np.mean(arr)
    std_val = np.std(arr)
    value_range = float(np.max(arr) - np.min(arr))
    if value_range > 1e-10:
        range_volatility = std_val / value_range * 100
        result["range_relative_volatility"] = f"{range_volatility:.1f}%"

    # 3. 분위수 분포
    q25, q50, q75 = np.percentile(arr, [25, 50, 75])
    result["quartiles"] = f"Q25={q25:.2f}, Q50(median)={q50:.2f}, Q75={q75:.2f}"

    # 4. 값이 0인 비율 (에너지 데이터에서 중요)
    zero_ratio = np.sum(arr == 0) / n * 100
    if zero_ratio > 1:
        result["zero_ratio"] = f"{zero_ratio:.1f}%"

    # 5. 급변 감지: 인접 포인트 간 차이의 상위 이상치
    if n >= 5:
        diffs = np.abs(np.diff(arr))
        diff_mean = np.mean(diffs)
        diff_max = np.max(diffs)
        spike_count = int(np.sum(diffs > diff_mean + 2 * np.std(diffs)))
        if spike_count > 0:
            result["sudden_changes"] = f"{spike_count}회 (max_jump={diff_max:.2f})"

    # 6. 주기성 탐지 (FFT)
    if n >= 16:
        try:
            fft_vals = np.abs(np.fft.rfft(arr - mean_val))
            freqs = np.fft.rfftfreq(n)
            # DC 제외, 상위 주파수 찾기
            fft_vals[0] = 0
            if len(fft_vals) > 1:
                peak_idx = np.argmax(fft_vals[1:]) + 1
                peak_power = fft_vals[peak_idx]
                total_power = np.sum(fft_vals[1:])
                if total_power > 0:
                    dominance = peak_power / total_power
                    period = round(1.0 / freqs[peak_idx]) if freqs[peak_idx] > 0 else n
                    if dominance > 0.15:
                        result["dominant_period"] = f"{period} points (strength={dominance:.0%})"
        except Exception:
            pass

    # 7. 구간 분할 요약 (앞/중간/뒤 각 1/3)
    if n >= 9:
        third = n // 3
        seg_means = [
            round(float(np.mean(arr[:third])), 2),
            round(float(np.mean(arr[third:2*third])), 2),
            round(float(np.mean(arr[2*third:])), 2),
        ]
        result["segment_means"] = f"first_third={seg_means[0]}, mid={seg_means[1]}, last_third={seg_means[2]}"

    # 8. 최대/최소 위치
    max_pos = int(np.argmax(arr))
    min_pos = int(np.argmin(arr))
    result["peak_position"] = f"max at {max_pos}/{n} ({max_pos/n*100:.0f}%), min at {min_pos}/{n} ({min_pos/n*100:.0f}%)"

    return result


def _authoritative_interpretation(analysis: dict) -> dict:
    """Build bilingual display facts without asking an LLM to judge magnitudes.

    The LLM may format or name a shape, but trend, volatility, feature tags and
    the displayed summary must remain deterministic projections of the server
    calculations.  This prevents phrases such as "significant difference"
    from appearing when no threshold established significance.
    """
    trend_raw = str(analysis.get("trend_direction", ""))
    trend_ko = trend_raw
    replacements = (
        ("No clear linear trend", "뚜렷한 선형 추세 없음"),
        ("Rising", "상승"),
        ("Falling", "하락"),
        ("Flat", "횡보"),
        ("span=", "범위 대비 변화="),
        ("of range", ""),
    )
    for source, target in replacements:
        trend_ko = trend_ko.replace(source, target)

    vol_raw = str(analysis.get("range_relative_volatility", ""))
    try:
        vol_value = float(vol_raw.replace("%", ""))
    except (TypeError, ValueError):
        vol_value = 0.0
    if vol_value > 40:
        vol_ko, vol_en = "높음", "high"
    elif vol_value > 25:
        vol_ko, vol_en = "보통", "moderate"
    else:
        vol_ko, vol_en = "낮음", "low"
    volatility = {
        "ko": f"{vol_ko} (표준편차/범위 {vol_raw or 'N/A'})",
        "en": f"{vol_en} (std/range {vol_raw or 'N/A'})",
    }

    features = []
    feature_specs = (
        ("dominant_period", "우세 주기", "Dominant period"),
        ("sudden_changes", "급변", "Sudden changes"),
        ("peak_position", "최대·최소 위치", "Peak/trough position"),
        ("segment_means", "구간별 평균", "Segment means"),
    )
    for key, ko_label, en_label in feature_specs:
        value = analysis.get(key)
        if value:
            ko_value, en_value = _localize_feature_value(key, str(value))
            features.append({"ko": f"{ko_label}: {ko_value}", "en": f"{en_label}: {en_value}"})

    trend = {"ko": trend_ko or "계산 정보 없음", "en": trend_raw or "Not available"}
    ko_parts = [trend["ko"], volatility["ko"]]
    en_parts = [trend["en"], volatility["en"]]
    if features:
        ko_parts.append(features[0]["ko"])
        en_parts.append(features[0]["en"])

    return {
        "trend": trend,
        "volatility": volatility,
        "features": features,
        "summary": {"ko": " · ".join(ko_parts), "en": " · ".join(en_parts)},
    }


def _localize_feature_value(key: str, value: str) -> tuple[str, str]:
    """Translate labels embedded in deterministic feature strings.

    Numeric values remain identical in both languages; only presentation
    tokens are localized.  Keeping this conversion deterministic avoids asking
    an LLM to rewrite factual measurements.
    """
    ko_value = value
    en_value = value
    if key == "dominant_period":
        ko_value = ko_value.replace(" points", "포인트").replace("strength=", "강도=")
    elif key == "sudden_changes":
        ko_value = ko_value.replace("max_jump=", "최대 점프=")
        en_value = en_value.replace("회", " event(s)")
    elif key == "peak_position":
        ko_value = (ko_value.replace("max at", "최대 위치")
                    .replace("min at", "최소 위치"))
    elif key == "segment_means":
        ko_value = (ko_value.replace("first_third=", "초반 1/3=")
                    .replace("mid=", "중간 1/3=")
                    .replace("last_third=", "후반 1/3="))
    return ko_value, en_value


# ─── 구조화된 응답 생성 ──────────────────────────────────────

def _build_structured_response(chatts_text: str, analysis: dict) -> str:
    """ChatTS 자유 텍스트와 사전 분석 수치를 결합하여 JSON 문자열 반환."""

    # 사전 분석에서 구조화된 필드 추출
    trend = analysis.get("trend_direction", "N/A")
    range_volatility = analysis.get("range_relative_volatility", "")
    period = analysis.get("dominant_period", "")
    changes = analysis.get("sudden_changes", "")
    seg_means = analysis.get("segment_means", "")

    # range-relative volatility level
    try:
        vol_val = float(range_volatility.replace("%", "")) if range_volatility else 0
    except (ValueError, AttributeError):
        vol_val = 0
    if vol_val > 40:
        vol_label = f"High (std/range {range_volatility})"
    elif vol_val > 25:
        vol_label = f"Medium (std/range {range_volatility})"
    else:
        vol_label = f"Low (std/range {range_volatility})"

    # features tags
    features = []
    if period:
        features.append(f"Period {period}")
    if changes:
        features.append(f"Sudden changes {changes}")
    if seg_means:
        features.append(f"Segment means {seg_means}")

    # trend parsing (English only)
    trend_label = trend

    # shape: 추세 방향은 계산값(trend_direction)이 정본이다. ChatTS 문장은 국소 사건
    # ("initial increasing")만으로도 Rising 이라 하므로 방향 낱말에서 추세를 읽지 않는다.
    text_lower = chatts_text.lower()
    trend_lower = str(trend).lower()
    if trend_lower.startswith("rising"):
        shape_label = "Rising"
    elif trend_lower.startswith("falling"):
        shape_label = "Declining"
    else:
        shape_label = "Flat" if trend_lower.startswith("flat") else "Irregular"
        # 방향이 없을 때만 모양 낱말로 좁힌다 (앞에 있는 것이 우선)
        shape_keywords = {
            ("cycl", "period", "oscillat", "season", "sinusoid", "sawtooth"): "Cyclic",
            ("spike", "burst"): "Spike",
            ("step", "shift", "plateau"): "Step",
            ("v-shape", "recovery", "dip", "trough"): "V-shape",
            ("bell", "gaussian"): "Bell",
            ("noise", "random", "erratic"): "Noisy",
        }
        for keywords, label in shape_keywords.items():
            if any(kw in text_lower for kw in keywords):
                shape_label = label
                break

    result = {
        "shape": shape_label,
        "trend": trend_label,
        "volatility": vol_label,
        "features": features if features else ["General"],
        # ChatTS 서술 전체. 자르면 문장이 끊긴 채 화면에 나간다.
        "summary": chatts_text.strip(),
    }
    return json.dumps(result, ensure_ascii=False)
