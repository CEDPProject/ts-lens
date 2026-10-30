# -*- coding: utf-8 -*-
"""
MOMENT Embedding clustering implementation.

Uses the MOMENT foundation model to extract semantic embeddings from
time series segments, then clusters in the embedding space using K-Means.

This approach captures high-level semantic patterns that traditional
distance metrics might miss, and naturally supports multivariate time series
by encoding all channels into a unified embedding space.

Reference: Goswami et al., "MOMENT: A Family of Open Time-series Foundation
Models", ICML 2024.
"""
from __future__ import annotations
import threading
from functools import wraps

import numpy as np
import logging

logger = logging.getLogger(__name__)

from .base import BaseClusterAlgorithm, ClusterResult, compute_silhouette


def _get_config(key: str):
    """군집 설정은 config/app.yaml 의 clustering 이 단일 소스다."""
    from ..app_config import cfg
    return cfg(f"clustering.moment.{key}")


# Singleton for MOMENT embedding model
_embed_model = None
_embed_device = None
WINDOW = 512  # MOMENT's fixed sequence length


_load_lock = threading.RLock()


def _serialized_load(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _load_lock:
            return func(*args, **kwargs)
    return wrapped


@_serialized_load

def _load_embed_model():
    """Lazy-load MOMENT model for embedding (singleton)."""
    from backend.runtime_state import require_optional_models
    require_optional_models()
    global _embed_model, _embed_device
    if _embed_model is not None:
        return _embed_model, _embed_device

    try:
        import torch
        from momentfm import MOMENTPipeline
    except ImportError:
        raise ImportError(
            "MOMENT clustering requires: pip install momentfm torch"
        )

    from ..app_config import cfg_foundation
    model_name = cfg_foundation("moment.model")
    from backend.runtime_state import force_cpu
    device_pref = "cpu" if force_cpu() else cfg_foundation("moment.device")
    if device_pref == "auto":
        _embed_device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        _embed_device = device_pref

    loaded_model = MOMENTPipeline.from_pretrained(
        model_name,
        model_kwargs={"task_name": "embedding"},
    )
    loaded_model.init()
    loaded_model = loaded_model.to(_embed_device).float().eval()
    _embed_model = loaded_model

    return _embed_model, _embed_device


def _extract_embedding(segment: np.ndarray, model, device) -> np.ndarray:
    """Extract embedding from a single time series segment.

    Args:
        segment: 1D array of shape (n_timestamps,)
        model: MOMENT model
        device: torch device

    Returns:
        1D embedding vector
    """
    import torch

    n = len(segment)

    # Pad or truncate to WINDOW
    if n >= WINDOW:
        padded = segment[:WINDOW]
        input_mask = np.ones(WINDOW)
    else:
        padded = np.zeros(WINDOW, dtype=np.float64)
        padded[:n] = segment
        input_mask = np.zeros(WINDOW)
        input_mask[:n] = 1.0

    # Replace NaN with 0
    padded = np.nan_to_num(padded, nan=0.0)

    # Shape: (1, 1, 512) for univariate
    x = torch.tensor(padded, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
    im = torch.tensor(input_mask, dtype=torch.float32).unsqueeze(0)

    with torch.no_grad():
        out = model(
            x_enc=x.to(device),
            input_mask=im.to(device),
        )

    # momentfm 의 embedding 작업은 패치·채널 평균을 이미 낸 (batch, d_model) 을 준다.
    # 패치 축이 남은 (batch, n_patches, d_model) 일 때만 직접 평균한다 — 2차원에 또
    # 평균을 내면 벡터가 스칼라 하나로 줄어 군집이 "Expected 2D array" 로 실패한다.
    embeddings = out.embeddings
    if embeddings.dim() == 3:
        embeddings = embeddings.mean(dim=1)

    return embeddings.cpu().numpy()[0]


class MomentEmbedCluster(BaseClusterAlgorithm):
    """MOMENT Foundation Model-based clustering.

    Extracts semantic embeddings from time series using MOMENT,
    then clusters in the embedding space using K-Means.

    Advantages:
    - Captures high-level semantic patterns
    - Naturally handles multivariate time series
    - Transfer learning from large-scale pretraining
    """

    name = "moment"

    def __init__(
        self,
        k: int | None = None,
        k_min: int | None = None,
        k_max: int | None = None,
        auto_k_strategy: str | None = None,  # ignored, uses silhouette on embedding space
    ):
        self.k = k
        self.k_min = k_min if k_min is not None else _get_config("k_min")
        self.k_max = k_max if k_max is not None else _get_config("k_max")

    @property
    def supports_auto_k(self) -> bool:
        return True

    @property
    def default_params(self) -> dict:
        return {
            "k": self.k,
            "k_min": self.k_min,
            "k_max": self.k_max,
        }

    def fit_predict(self, X: np.ndarray, progress_cb=None, **kwargs) -> ClusterResult:
        from sklearn.cluster import KMeans

        k = kwargs.get("k", self.k)
        k_min = kwargs.get("k_min", self.k_min)
        k_max = kwargs.get("k_max", self.k_max)

        n_samples = X.shape[0]

        # Load MOMENT model
        if progress_cb:
            progress_cb("MOMENT: loading foundation model...")
        model, device = _load_embed_model()

        # Extract embeddings for each segment
        if progress_cb:
            progress_cb(f"MOMENT: extracting embeddings for {n_samples} segments...")
        embeddings = []
        for i in range(n_samples):
            if X.ndim == 1:
                seg = X
            elif X.ndim == 2:
                seg = X[i]
            else:
                seg = X[i].flatten()

            emb = _extract_embedding(seg, model, device)
            embeddings.append(emb)
            if progress_cb and i > 0 and i % 50 == 0:
                progress_cb(f"  embedded {i}/{n_samples}")

        embeddings = np.array(embeddings)
        if progress_cb:
            progress_cb(f"MOMENT: embedding complete (dim={embeddings.shape[1]})")

        # Auto-K selection if k is None
        is_auto_k = k is None
        if is_auto_k:
            if progress_cb:
                progress_cb(f"MOMENT: auto-selecting k in [{k_min}, {k_max}]")
            k = self._auto_select_k(embeddings, k_min, k_max, progress_cb)

        # Cluster in embedding space
        if progress_cb:
            progress_cb(f"MOMENT: K-Means on embeddings (k={k})...")
        kmeans = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = kmeans.fit_predict(embeddings)

        # Compute silhouette in embedding space
        score = compute_silhouette(embeddings, labels)

        if progress_cb:
            progress_cb(f"MOMENT complete: k={k}, silhouette={score:.3f}")

        # For centroids, use original feature space medoids
        # (embedding space centroids don't make sense for display)
        centers = None

        return ClusterResult(
            labels=labels,
            k=k,
            score=score,
            centers=centers,
            is_auto_k=is_auto_k,
            extra={"algorithm": "moment", "embedding_dim": embeddings.shape[1]},
        )

    def _auto_select_k(
        self,
        embeddings: np.ndarray,
        k_min: int,
        k_max: int,
        progress_cb=None
    ) -> int:
        """Auto-select K using silhouette score on embeddings."""
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score

        n_samples = embeddings.shape[0]
        k_max = min(k_max, n_samples - 1)
        k_min = max(k_min, 2)

        if k_max <= k_min:
            return k_min

        best_k = k_min
        best_score = -1

        for k in range(k_min, k_max + 1):
            if progress_cb:
                progress_cb(f"MOMENT auto-K: k={k}/{k_max}")
            try:
                kmeans = KMeans(n_clusters=k, n_init=10, random_state=42)
                labels = kmeans.fit_predict(embeddings)

                if len(set(labels)) < 2:
                    continue

                score = silhouette_score(embeddings, labels)
                if progress_cb:
                    progress_cb(f"  k={k}: sil={score:.3f}" + (" *best" if score > best_score else ""))
                if score > best_score:
                    best_score = score
                    best_k = k
            except Exception as e:
                # Swallowing every k here silently collapses auto-K to the
                # fallback; log so a systematic failure is visible.
                logger.debug("auto-K scoring failed at k=%s: %s", k, e)
                continue

        if progress_cb:
            progress_cb(f"Selected k={best_k} (sil={best_score:.3f})")

        return best_k
