# -*- coding: utf-8 -*-
"""MOMENT 시계열 Foundation Model 서비스.

MOMENT를 사용한 시계열 패턴 분석:
- 임베딩 추출
- 패턴 유사도 계산
- 클러스터 해석
"""
from __future__ import annotations

import threading
from functools import wraps

import logging
import numpy as np
try:
    import torch
except ImportError:
    torch = None
from typing import List, Dict, Any, Optional

from backend.app_config import cfg_foundation

logger = logging.getLogger(__name__)

# Global model cache
_moment_model = None
_moment_device = None


def _get_device() -> str:
    """Resolve device from foundation.yaml (auto -> cuda if present, else cpu)."""
    from backend.runtime_state import force_cpu
    if force_cpu():
        return "cpu"
    pref = cfg_foundation("moment.device")
    if pref and pref != "auto":
        return str(pref)
    return "cuda" if torch.cuda.is_available() else "cpu"


_load_lock = threading.RLock()


def _serialized_load(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _load_lock:
            return func(*args, **kwargs)
    return wrapped


@_serialized_load

def load_moment_model(device: Optional[str] = None):
    """Load MOMENT model (lazy loading)."""
    from backend.runtime_state import require_optional_models
    require_optional_models()
    global _moment_model, _moment_device

    if _moment_model is not None:
        return _moment_model

    try:
        from momentfm import MOMENTPipeline

        _moment_device = device or _get_device()
        model_name = cfg_foundation("moment.model")
        logger.info(f"Loading MOMENT model {model_name} on {_moment_device}...")

        loaded_model = MOMENTPipeline.from_pretrained(
            model_name,
            model_kwargs={"task_name": "embedding"},
        )
        loaded_model.init()

        # Move to device
        if "cuda" in _moment_device:
            loaded_model = loaded_model.to(_moment_device)

        loaded_model.eval()
        _moment_model = loaded_model
        logger.info("MOMENT model loaded successfully")
        return _moment_model

    except Exception as e:
        logger.error(f"Failed to load MOMENT: {e}")
        raise


def get_embeddings(time_series_list: List[np.ndarray]) -> np.ndarray:
    """Get embeddings for multiple time series.

    Args:
        time_series_list: List of 1D numpy arrays (each is a time series)

    Returns:
        2D numpy array of shape (n_series, embedding_dim)
    """
    model = load_moment_model()

    embeddings = []
    for ts in time_series_list:
        # MOMENT expects (batch, n_channels, seq_len)
        ts_tensor = torch.tensor(ts, dtype=torch.float32).unsqueeze(0).unsqueeze(0)

        if _moment_device and "cuda" in _moment_device:
            ts_tensor = ts_tensor.to(_moment_device)

        with torch.no_grad():
            output = model.embed(x_enc=ts_tensor)
            emb = output.embeddings.cpu().numpy().flatten()
            embeddings.append(emb)

    return np.array(embeddings)


def analyze_pattern(values: List[float]) -> Dict[str, Any]:
    """Analyze a single time series pattern using MOMENT.

    Returns pattern characteristics based on embedding analysis.
    """
    if len(values) < 8:
        return {
            "shape": "데이터 부족",
            "description": "MOMENT 분석에는 최소 8개 데이터 포인트가 필요합니다.",
            "confidence": 0,
        }

    ts = np.array(values, dtype=np.float32)

    try:
        # Normalize
        ts_norm = (ts - ts.mean()) / (ts.std() + 1e-8)

        model = load_moment_model()

        # MOMENT expects (batch, n_channels, seq_len)
        ts_tensor = torch.tensor(ts_norm, dtype=torch.float32).unsqueeze(0).unsqueeze(0)

        if _moment_device and "cuda" in _moment_device:
            ts_tensor = ts_tensor.to(_moment_device)

        with torch.no_grad():
            output = model.embed(x_enc=ts_tensor)
            embedding = output.embeddings.cpu().numpy().flatten()

        # 패턴 특성 분석 (임베딩 기반 + 통계 기반 결합)
        pattern_info = _classify_pattern(ts, embedding)
        pattern_info["model"] = "moment"
        return pattern_info

    except Exception as e:
        # MOMENT를 못 쓰는 환경(미설치 포함)에서도 해석은 계속되어야 한다.
        # 실패 문자열을 결과처럼 돌려주면 하류(LLM 평가)가 그 문자열을 패턴으로
        # 읽고 전부 의미 없음으로 판정해 포트폴리오가 비어버린다.
        logger.warning(f"MOMENT unavailable, using statistical analysis: {e}")
        pattern_info = _classify_pattern(ts, None)
        pattern_info["model"] = "statistical"
        pattern_info["model_note"] = f"MOMENT unavailable ({str(e)[:60]})"
        return pattern_info


def analyze_clusters(centroids: List[List[float]]) -> List[Dict[str, Any]]:
    """Analyze multiple cluster centroids at once.

    Returns list of pattern analyses for each centroid.
    Also computes inter-cluster similarity.
    """
    results = []
    valid_centroids = []
    valid_indices = []

    # First pass: analyze each centroid
    for i, centroid in enumerate(centroids):
        if len(centroid) >= 8:
            valid_centroids.append(np.array(centroid, dtype=np.float32))
            valid_indices.append(i)
            result = analyze_pattern(centroid)
        else:
            result = {
                "shape": "데이터 부족",
                "description": "패턴 길이가 너무 짧습니다.",
                "confidence": 0,
            }
        results.append(result)

    # Second pass: compute similarity matrix if we have valid centroids.
    # Prefer MOMENT embeddings; fall back to the raw centroids themselves so the
    # similarity column still exists when the model is unavailable.
    if len(valid_centroids) >= 2:
        try:
            try:
                embeddings = get_embeddings(valid_centroids)
            except Exception as emb_err:
                logger.warning(f"Embedding unavailable, similarity on raw shapes: {emb_err}")
                min_len = min(len(c) for c in valid_centroids)
                embeddings = np.array([c[:min_len] for c in valid_centroids], dtype=np.float32)

            # Normalize embeddings
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings_norm = embeddings / (norms + 1e-8)

            # Cosine similarity matrix
            sim_matrix = embeddings_norm @ embeddings_norm.T

            # Add similarity info to results
            for idx, orig_idx in enumerate(valid_indices):
                # Find most similar other cluster
                sim_row = sim_matrix[idx].copy()
                sim_row[idx] = -1  # Exclude self
                most_similar_idx = np.argmax(sim_row)
                similarity = sim_row[most_similar_idx]

                results[orig_idx]["similar_to"] = valid_indices[most_similar_idx]
                results[orig_idx]["similarity"] = float(similarity)

        except Exception as e:
            logger.warning(f"Similarity computation failed: {e}")

    return results


def _classify_pattern(ts: np.ndarray, embedding: Optional[np.ndarray]) -> Dict[str, Any]:
    """Classify pattern from statistical features, refined by embedding if present.

    embedding is optional: without MOMENT the classifier runs on statistics alone.
    """

    n = len(ts)
    ts_min, ts_max = ts.min(), ts.max()
    ts_range = ts_max - ts_min
    ts_mean = ts.mean()
    ts_std = ts.std()

    # Trend analysis
    start_val = ts[:max(1, n//10)].mean()
    end_val = ts[-max(1, n//10):].mean()
    mid_val = ts[n//3:2*n//3].mean()

    trend = (end_val - start_val) / (ts_range + 1e-8)

    # 군집 중심은 보통 z-score라 평균이 0에 가깝다. std/mean(CV)을 쓰면
    # 8e9% 같은 무의미한 값이 되어 LLM과 랭킹을 오염시킨다. 범위 대비 표준편차는
    # 평행 이동에 안전하고 정규화된 형태끼리도 비교할 수 있다.
    volatility = ts_std / (ts_range + 1e-8)

    # Peak/Valley detection
    peaks = _count_peaks(ts)
    valleys = _count_valleys(ts)

    # Embedding statistics (for additional features)
    emb_entropy = _embedding_entropy(embedding) if embedding is not None else 0.0

    # Classification logic
    confidence = 70  # Base confidence

    if ts_range < 1e-8:
        shape = "Flat"
        description = "Stable pattern with minimal change."
        confidence = 85
    elif trend > 0.6:
        shape = "Upward"
        description = f"Overall upward trend (+{abs(trend)*100:.0f}%)."
        confidence = 80
    elif trend < -0.6:
        shape = "Downward"
        description = f"Overall downward trend (-{abs(trend)*100:.0f}%)."
        confidence = 80
    elif mid_val > max(start_val, end_val) + 0.15 * ts_range:
        shape = "Peak"
        description = "Mountain-shaped pattern with a high point in the middle."
        confidence = 75
    elif mid_val < min(start_val, end_val) - 0.15 * ts_range:
        shape = "Valley"
        description = "V-shaped pattern with a low point in the middle."
        confidence = 75
    elif peaks >= 2 and valleys >= 2:
        if volatility > 0.3:
            shape = "Oscillating"
            description = f"Periodic oscillation pattern ({peaks} peaks detected)."
            confidence = 70
        else:
            shape = "Wave"
            description = f"Gentle wave pattern ({peaks} peaks)."
            confidence = 65
    elif volatility > 0.45:
        shape = "Volatile"
        description = f"Irregular pattern with high range-relative volatility ({volatility:.1%})."
        confidence = 60
    elif emb_entropy > 0.7:
        shape = "Complex"
        description = "Complex pattern with mixed characteristics."
        confidence = 55
    else:
        shape = "Normal"
        description = "General pattern with no distinctive features."
        confidence = 50

    # 형태 대분류(Peak/Valley/...)만으로는 클러스터가 서로 구별되지 않는다.
    # 수준(level)과 봉우리/골의 위치를 함께 실어, 같은 대분류 안에서도
    # "얼마나 깊은지 / 하루 중 언제인지" 로 갈라볼 수 있게 한다.
    return {
        "shape": shape,
        "description": description,
        "confidence": confidence,
        "stats": {
            "trend": round(float(trend) * 100, 1),
            "volatility": round(float(volatility) * 100, 1),
            "peaks": peaks,
            "valleys": valleys,
            "level_mean": round(float(ts_mean), 2),
            "level_min": round(float(ts_min), 2),
            "level_max": round(float(ts_max), 2),
            # 0 = 구간 시작, 1 = 구간 끝 (일 단위 세그먼트면 하루 중 위치)
            "peak_at": round(float(np.argmax(ts)) / max(1, n - 1), 2),
            "valley_at": round(float(np.argmin(ts)) / max(1, n - 1), 2),
        }
    }


def _count_peaks(ts: np.ndarray, threshold: float = 0.1) -> int:
    """Count number of local maxima."""
    if len(ts) < 3:
        return 0

    ts_norm = (ts - ts.min()) / (ts.max() - ts.min() + 1e-8)
    peaks = 0

    for i in range(1, len(ts) - 1):
        if ts_norm[i] > ts_norm[i-1] and ts_norm[i] > ts_norm[i+1]:
            if ts_norm[i] > threshold:
                peaks += 1

    return peaks


def _count_valleys(ts: np.ndarray, threshold: float = 0.1) -> int:
    """Count number of local minima."""
    if len(ts) < 3:
        return 0

    ts_norm = (ts - ts.min()) / (ts.max() - ts.min() + 1e-8)
    valleys = 0

    for i in range(1, len(ts) - 1):
        if ts_norm[i] < ts_norm[i-1] and ts_norm[i] < ts_norm[i+1]:
            if ts_norm[i] < (1 - threshold):
                valleys += 1

    return valleys


def _embedding_entropy(embedding: np.ndarray) -> float:
    """Compute normalized entropy of embedding (complexity measure)."""
    # Normalize to probability-like distribution
    emb_abs = np.abs(embedding)
    emb_sum = emb_abs.sum() + 1e-8
    probs = emb_abs / emb_sum

    # Entropy
    entropy = -np.sum(probs * np.log(probs + 1e-8))

    # Normalize by max entropy
    max_entropy = np.log(len(embedding))

    return entropy / max_entropy if max_entropy > 0 else 0
