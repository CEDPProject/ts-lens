# -*- coding: utf-8 -*-
"""
Auto-K selection strategies for clustering.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional
import logging
import warnings
import numpy as np

logger = logging.getLogger(__name__)


def _get_config(key: str):
    """군집 설정은 config/app.yaml 의 clustering 이 단일 소스다."""
    from ..app_config import cfg
    return cfg(f"clustering.{key}")


# Progress callback type
ProgressCallback = Optional[Callable[[str], None]]


@dataclass
class AutoKResult:
    """Result from auto-K selection."""
    best_k: int
    scores: dict[int, float]  # k -> combined score
    details: dict[int, dict]  # k -> per-metric scores


class AutoKStrategy(ABC):
    """Base class for auto-K selection strategies."""

    name: str = "base"

    @abstractmethod
    def find_best_k(
        self,
        X: np.ndarray,
        k_min: int,
        k_max: int,
        fit_fn: Callable[[int], np.ndarray],
        progress_cb: ProgressCallback = None,
    ) -> AutoKResult:
        """
        Find optimal K.

        Args:
            X: Feature matrix
            k_min: Minimum K to try
            k_max: Maximum K to try
            fit_fn: Function that takes k and returns labels
            progress_cb: Optional callback for progress updates

        Returns:
            AutoKResult with best_k and scoring details
        """
        pass


class SilhouetteStrategy(AutoKStrategy):
    """Simple silhouette-based K selection."""

    name = "silhouette"

    def find_best_k(self, X, k_min, k_max, fit_fn, progress_cb=None) -> AutoKResult:
        from sklearn.metrics import silhouette_score

        scores = {}
        details = {}
        best_k = k_min
        best_score = -1.0

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for k in range(k_min, k_max + 1):
                if progress_cb:
                    progress_cb(f"Silhouette: k={k}/{k_max}")
                labels = fit_fn(k)
                if len(set(labels)) < 2:
                    continue
                try:
                    sil = silhouette_score(X, labels)
                    scores[k] = sil
                    details[k] = {"silhouette": sil}
                    if progress_cb:
                        progress_cb(f"  k={k}: score={sil:.3f}" + (" *best" if sil > best_score else ""))
                    if sil > best_score:
                        best_score = sil
                        best_k = k
                except Exception as e:
                    # Swallowing every k here silently collapses auto-K to the
                    # fallback; log so a systematic failure is visible.
                    logger.debug("auto-K scoring failed at k=%s: %s", k, e)
                    continue

        if progress_cb:
            progress_cb(f"Selected k={best_k} (score={best_score:.3f})")
        return AutoKResult(best_k=best_k, scores=scores, details=details)


class MultiIndexStrategy(AutoKStrategy):
    """
    Multi-index voting: combines Silhouette coefficient, Calinski-Harabasz index, Davies-Bouldin index.
    Each index is normalized to [0,1] and summed.
    """

    name = "multi_index"

    def __init__(self, weights: tuple[float, float, float] | None = None):
        """
        Args:
            weights: (silhouette_weight, calinski_weight, davies_bouldin_weight)
        """
        if weights is None:
            cfg_weights = _get_config("auto_k.weights")
            weights = tuple(cfg_weights)
        self.weights = weights

    def find_best_k(self, X, k_min, k_max, fit_fn, progress_cb=None) -> AutoKResult:
        from sklearn.metrics import (
            silhouette_score,
            calinski_harabasz_score,
            davies_bouldin_score,
        )

        raw_scores: dict[int, dict] = {}

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for k in range(k_min, k_max + 1):
                if progress_cb:
                    progress_cb(f"Multi-Index: k={k}/{k_max}")
                labels = fit_fn(k)
                if len(set(labels)) < 2:
                    continue
                try:
                    sil = silhouette_score(X, labels)
                    ch = calinski_harabasz_score(X, labels)
                    db = davies_bouldin_score(X, labels)
                    raw_scores[k] = {"sil": sil, "ch": ch, "db": db}
                    if progress_cb:
                        progress_cb(f"  k={k}: Sil={sil:.2f} CH={ch:.0f} DB={db:.2f}")
                except Exception as e:
                    # Swallowing every k here silently collapses auto-K to the
                    # fallback; log so a systematic failure is visible.
                    logger.debug("auto-K scoring failed at k=%s: %s", k, e)
                    continue

        if not raw_scores:
            return AutoKResult(best_k=k_min, scores={}, details={})

        # Normalize and combine
        ks = list(raw_scores.keys())
        sils = [raw_scores[k]["sil"] for k in ks]
        chs = [raw_scores[k]["ch"] for k in ks]
        dbs = [raw_scores[k]["db"] for k in ks]

        sil_n = self._normalize(sils)
        ch_n = self._normalize(chs)
        db_n = self._normalize(dbs, invert=True)

        w_sil, w_ch, w_db = self.weights
        scores = {}
        details = {}

        for i, k in enumerate(ks):
            combined = w_sil * sil_n[i] + w_ch * ch_n[i] + w_db * db_n[i]
            scores[k] = combined
            details[k] = {
                "silhouette": round(sils[i], 4),
                "calinski_harabasz": round(chs[i], 4),
                "davies_bouldin": round(dbs[i], 4),
                "sil_norm": round(sil_n[i], 4),
                "ch_norm": round(ch_n[i], 4),
                "db_norm": round(db_n[i], 4),
                "combined": round(combined, 4),
            }

        best_k = max(scores, key=scores.get)
        if progress_cb:
            progress_cb(f"Selected k={best_k} (combined={scores[best_k]:.3f})")
        return AutoKResult(best_k=best_k, scores=scores, details=details)

    @staticmethod
    def _normalize(vals: list[float], invert: bool = False) -> list[float]:
        mn, mx = min(vals), max(vals)
        if mx - mn < 1e-9:
            return [0.5] * len(vals)
        normed = [(v - mn) / (mx - mn) for v in vals]
        if invert:
            normed = [1.0 - n for n in normed]
        return normed


class ElbowStrategy(AutoKStrategy):
    """
    Elbow method with automatic knee detection.
    Uses inertia (within-cluster sum of squares) and finds the elbow point.
    """

    name = "elbow"

    def find_best_k(self, X, k_min, k_max, fit_fn, progress_cb=None) -> AutoKResult:
        from sklearn.cluster import KMeans

        random_state = _get_config("kmeans.random_state")
        inertias = {}

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for k in range(k_min, k_max + 1):
                if progress_cb:
                    progress_cb(f"Elbow: k={k}/{k_max}")
                km = KMeans(n_clusters=k, n_init=3, random_state=random_state)
                km.fit(X)
                inertias[k] = km.inertia_
                if progress_cb:
                    progress_cb(f"  k={k}: inertia={km.inertia_:.0f}")

        if not inertias:
            return AutoKResult(best_k=k_min, scores={}, details={})

        # Find elbow using second derivative (max curvature)
        ks = sorted(inertias.keys())
        vals = [inertias[k] for k in ks]

        if len(ks) < 3:
            best_k = ks[0]
        else:
            # Compute second derivative approximation
            second_deriv = []
            for i in range(1, len(vals) - 1):
                d2 = vals[i - 1] - 2 * vals[i] + vals[i + 1]
                second_deriv.append(d2)
            # Elbow is where second derivative is maximum
            elbow_idx = second_deriv.index(max(second_deriv)) + 1
            best_k = ks[elbow_idx]

        scores = {k: -inertias[k] for k in ks}  # Negative because lower is better
        details = {k: {"inertia": round(inertias[k], 4)} for k in ks}

        if progress_cb:
            progress_cb(f"Elbow detected at k={best_k}")
        return AutoKResult(best_k=best_k, scores=scores, details=details)


class ShapeSilhouetteStrategy(AutoKStrategy):
    """
    Shape-aware silhouette using DTW distance.

    Computes pairwise DTW distance matrix and uses silhouette_score
    with precomputed distances. This properly evaluates cluster quality
    based on time series shape similarity rather than Euclidean distance.
    """

    name = "shape_silhouette"

    def __init__(self, max_samples: int = 200):
        """
        Args:
            max_samples: Max samples for DTW computation (O(n^2) complexity)
        """
        self.max_samples = max_samples

    def find_best_k(self, X, k_min, k_max, fit_fn, progress_cb=None) -> AutoKResult:
        from sklearn.metrics import silhouette_score

        n_samples = len(X)

        # Subsample if too large (DTW is O(n^2 * m) where m is sequence length)
        if n_samples > self.max_samples:
            if progress_cb:
                progress_cb(f"Shape-DTW: subsampling {n_samples} -> {self.max_samples}")
            rng = np.random.RandomState(42)
            idx = rng.choice(n_samples, self.max_samples, replace=False)
            X_sub = X[idx]
        else:
            X_sub = X
            idx = np.arange(n_samples)

        # Compute pairwise DTW distance matrix
        if progress_cb:
            progress_cb(f"Computing DTW distance matrix ({len(X_sub)}x{len(X_sub)})...")
        dist_matrix = self._compute_dtw_matrix(X_sub, progress_cb)

        scores = {}
        details = {}
        best_k = k_min
        best_score = -1.0

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for k in range(k_min, k_max + 1):
                if progress_cb:
                    progress_cb(f"Shape-DTW: k={k}/{k_max}")
                # Get labels for full data, then subset
                full_labels = fit_fn(k)
                labels = full_labels[idx] if n_samples > self.max_samples else full_labels

                if len(set(labels)) < 2:
                    continue
                try:
                    # Use precomputed distance matrix
                    sil = silhouette_score(dist_matrix, labels, metric='precomputed')
                    scores[k] = sil
                    details[k] = {"shape_silhouette": round(sil, 4)}
                    if progress_cb:
                        progress_cb(f"  k={k}: shape_sil={sil:.3f}" + (" *best" if sil > best_score else ""))
                    if sil > best_score:
                        best_score = sil
                        best_k = k
                except Exception as e:
                    # Swallowing every k here silently collapses auto-K to the
                    # fallback; log so a systematic failure is visible.
                    logger.debug("auto-K scoring failed at k=%s: %s", k, e)
                    continue

        if progress_cb:
            progress_cb(f"Selected k={best_k} (shape_sil={best_score:.3f})")
        return AutoKResult(best_k=best_k, scores=scores, details=details)

    def _compute_dtw_matrix(self, X: np.ndarray, progress_cb=None) -> np.ndarray:
        """Compute pairwise DTW distance matrix."""
        n = len(X)
        dist_matrix = np.zeros((n, n))
        total_pairs = n * (n - 1) // 2
        count = 0

        for i in range(n):
            for j in range(i + 1, n):
                d = self._dtw_distance(X[i], X[j])
                dist_matrix[i, j] = d
                dist_matrix[j, i] = d
                count += 1
            if progress_cb and i % 20 == 0:
                pct = int(count / total_pairs * 100)
                progress_cb(f"  DTW: {pct}% ({count}/{total_pairs} pairs)")

        return dist_matrix

    @staticmethod
    def _dtw_distance(s1: np.ndarray, s2: np.ndarray) -> float:
        """
        Compute DTW distance between two sequences.
        Uses fast implementation with Sakoe-Chiba band constraint.
        """
        n, m = len(s1), len(s2)

        # Sakoe-Chiba band width (10% of length)
        w = max(abs(n - m), int(max(n, m) * 0.1))

        # Initialize DTW matrix with infinity
        dtw = np.full((n + 1, m + 1), np.inf)
        dtw[0, 0] = 0.0

        for i in range(1, n + 1):
            j_start = max(1, i - w)
            j_end = min(m, i + w) + 1
            for j in range(j_start, j_end):
                cost = (s1[i-1] - s2[j-1]) ** 2
                dtw[i, j] = cost + min(dtw[i-1, j], dtw[i, j-1], dtw[i-1, j-1])

        return np.sqrt(dtw[n, m])


# Registry of available strategies
AUTO_K_STRATEGIES: dict[str, type[AutoKStrategy]] = {
    "silhouette": SilhouetteStrategy,
    "multi_index": MultiIndexStrategy,
    "elbow": ElbowStrategy,
    "shape_silhouette": ShapeSilhouetteStrategy,
}


def get_auto_k_strategy(name: str, **kwargs) -> AutoKStrategy:
    """Get an auto-K strategy by name."""
    if name not in AUTO_K_STRATEGIES:
        raise ValueError(f"Unknown auto-K strategy: {name}. Available: {list(AUTO_K_STRATEGIES.keys())}")
    return AUTO_K_STRATEGIES[name](**kwargs)
