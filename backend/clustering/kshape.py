# -*- coding: utf-8 -*-
"""
k-Shape clustering implementation.

k-Shape is a shape-based clustering algorithm specifically designed for
time series data. It uses Shape-Based Distance (SBD) which is scale and
shift invariant, making it more suitable for time series than K-Means.

Reference: Paparrizos & Gravano, "k-Shape: Efficient and Accurate Clustering
of Time Series", SIGMOD 2015.
"""
from __future__ import annotations
import numpy as np
import logging

logger = logging.getLogger(__name__)

from .base import BaseClusterAlgorithm, ClusterResult, compute_silhouette


def _get_config(key: str):
    """군집 설정은 config/app.yaml 의 clustering.kshape 이 단일 소스다."""
    from ..app_config import cfg
    return cfg(f"clustering.kshape.{key}")


class KShapeCluster(BaseClusterAlgorithm):
    """k-Shape clustering for time series.

    Uses Shape-Based Distance (SBD) which is:
    - Scale invariant (ignores amplitude differences)
    - Shift invariant (ignores phase shifts)
    - Optimized for time series shape similarity
    """

    name = "kshape"

    def __init__(
        self,
        k: int | None = None,
        k_min: int | None = None,
        k_max: int | None = None,
        max_iter: int | None = None,
        auto_k_strategy: str | None = None,  # ignored, k-Shape uses SBD internally
    ):
        self.k = k
        self.k_min = k_min if k_min is not None else _get_config("k_min")
        self.k_max = k_max if k_max is not None else _get_config("k_max")
        self.max_iter = max_iter if max_iter is not None else _get_config("max_iter")

    @property
    def supports_auto_k(self) -> bool:
        return True

    @property
    def default_params(self) -> dict:
        return {
            "k": self.k,
            "k_min": self.k_min,
            "k_max": self.k_max,
            "max_iter": self.max_iter,
        }

    def fit_predict(self, X: np.ndarray, progress_cb=None, **kwargs) -> ClusterResult:
        from tslearn.clustering import KShape
        from tslearn.preprocessing import TimeSeriesScalerMeanVariance

        k = kwargs.get("k", self.k)
        k_min = kwargs.get("k_min", self.k_min)
        k_max = kwargs.get("k_max", self.k_max)
        max_iter = kwargs.get("max_iter", self.max_iter)

        n_samples = X.shape[0]

        # Reshape for tslearn: (n_samples, n_timestamps, 1)
        if X.ndim == 2:
            X_ts = X.reshape(n_samples, -1, 1)
        else:
            X_ts = X

        # Normalize for k-Shape (required for SBD)
        if progress_cb:
            progress_cb("k-Shape: normalizing time series...")
        scaler = TimeSeriesScalerMeanVariance()
        X_scaled = scaler.fit_transform(X_ts)

        # Auto-K selection if k is None
        is_auto_k = k is None
        if is_auto_k:
            if progress_cb:
                progress_cb(f"k-Shape: auto-selecting k in [{k_min}, {k_max}]")
            k = self._auto_select_k(X_scaled, k_min, k_max, max_iter, progress_cb)

        # Fit k-Shape
        if progress_cb:
            progress_cb(f"k-Shape: fitting with k={k}...")
        ks = KShape(n_clusters=k, max_iter=max_iter, verbose=False, random_state=42)
        labels = ks.fit_predict(X_scaled)

        # Extract centroids (shape: (k, n_timestamps, 1) -> (k, n_timestamps))
        centers = ks.cluster_centers_.squeeze(axis=-1) if ks.cluster_centers_ is not None else None

        # Compute silhouette score on original feature space
        score = compute_silhouette(X, labels)

        if progress_cb:
            progress_cb(f"k-Shape complete: k={k}, silhouette={score:.3f}")

        return ClusterResult(
            labels=labels,
            k=k,
            score=score,
            centers=centers,
            is_auto_k=is_auto_k,
            extra={"algorithm": "kshape", "max_iter": max_iter},
        )

    def _auto_select_k(
        self,
        X: np.ndarray,
        k_min: int,
        k_max: int,
        max_iter: int,
        progress_cb=None
    ) -> int:
        """Auto-select K using SBD-based silhouette score.

        Uses Shape-Based Distance (SBD) for proper time series evaluation
        instead of Euclidean distance.
        """
        from tslearn.clustering import KShape
        from sklearn.metrics import silhouette_score

        n_samples = X.shape[0]
        k_max = min(k_max, n_samples - 1)
        k_min = max(k_min, 2)

        if k_max <= k_min:
            return k_min

        best_k = k_min
        best_score = -1

        # Subsample for speed if too many samples
        max_subsample = 150
        if n_samples > max_subsample:
            if progress_cb:
                progress_cb(f"k-Shape: subsampling {n_samples} -> {max_subsample}")
            rng = np.random.RandomState(42)
            idx = rng.choice(n_samples, max_subsample, replace=False)
            X_sub = X[idx]
        else:
            X_sub = X
            idx = None

        # Precompute SBD distance matrix for shape-aware silhouette
        if progress_cb:
            progress_cb("k-Shape: computing SBD distance matrix...")
        dist_matrix = self._compute_sbd_matrix(X_sub)

        for k in range(k_min, k_max + 1):
            if progress_cb:
                progress_cb(f"k-Shape auto-K: testing k={k}/{k_max}")
            try:
                ks = KShape(n_clusters=k, max_iter=max_iter, verbose=False, random_state=42)
                full_labels = ks.fit_predict(X)

                # Use subsampled labels if we subsampled
                labels = full_labels[idx] if idx is not None else full_labels

                # Skip if all points in one cluster
                if len(set(labels)) < 2:
                    continue

                # Use SBD distance matrix for silhouette
                score = silhouette_score(dist_matrix, labels, metric='precomputed')
                if progress_cb:
                    progress_cb(f"  k={k}: sbd_sil={score:.3f}" + (" *best" if score > best_score else ""))
                if score > best_score:
                    best_score = score
                    best_k = k
            except Exception as e:
                # Swallowing every k here silently collapses auto-K to the
                # fallback; log so a systematic failure is visible.
                logger.debug("auto-K scoring failed at k=%s: %s", k, e)
                continue

        if progress_cb:
            progress_cb(f"Selected k={best_k} (sbd_sil={best_score:.3f})")
        return best_k

    def _compute_sbd_matrix(self, X: np.ndarray) -> np.ndarray:
        """Compute pairwise SBD (Shape-Based Distance) matrix."""
        n = len(X)
        dist_matrix = np.zeros((n, n))

        for i in range(n):
            for j in range(i + 1, n):
                d = self._sbd(X[i].flatten(), X[j].flatten())
                dist_matrix[i, j] = d
                dist_matrix[j, i] = d

        return dist_matrix

    @staticmethod
    def _sbd(x: np.ndarray, y: np.ndarray) -> float:
        """
        Compute Shape-Based Distance (SBD) between two sequences.
        SBD = 1 - max(NCC) where NCC is normalized cross-correlation.
        Range: [0, 2], lower is more similar.
        """
        # Z-normalize
        x = (x - np.mean(x)) / (np.std(x) + 1e-8)
        y = (y - np.mean(y)) / (np.std(y) + 1e-8)

        # Compute normalized cross-correlation using FFT
        n = len(x)
        fft_size = 2 * n - 1

        # Pad and FFT
        x_pad = np.zeros(fft_size)
        y_pad = np.zeros(fft_size)
        x_pad[:n] = x
        y_pad[:n] = y

        fx = np.fft.fft(x_pad)
        fy = np.fft.fft(y_pad)

        # Cross-correlation via inverse FFT
        cc = np.fft.ifft(fx * np.conj(fy)).real

        # Normalize
        norm = np.sqrt(np.sum(x**2) * np.sum(y**2))
        if norm > 0:
            ncc = cc / norm
        else:
            ncc = cc

        # SBD = 1 - max(NCC)
        return 1.0 - np.max(ncc)
