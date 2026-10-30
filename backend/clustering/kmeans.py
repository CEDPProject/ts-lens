# -*- coding: utf-8 -*-
"""
K-Means clustering implementation.
"""
from __future__ import annotations
import warnings
import numpy as np

from .base import BaseClusterAlgorithm, ClusterResult, compute_silhouette
from .auto_k import get_auto_k_strategy


def _get_config(key: str):
    """군집 설정은 config/app.yaml 의 clustering 이 단일 소스다."""
    from ..app_config import cfg
    return cfg(f"clustering.kmeans.{key}")


class KMeansCluster(BaseClusterAlgorithm):
    """K-Means clustering with optional auto-K selection."""

    name = "kmeans"

    def __init__(
        self,
        k: int | None = None,
        k_min: int | None = None,
        k_max: int | None = None,
        n_init: int | None = None,
        random_state: int | None = None,
        auto_k_strategy: str | None = None,
        max_subsample: int | None = None,
    ):
        self.k = k
        self.k_min = k_min if k_min is not None else _get_config("k_min")
        self.k_max = k_max if k_max is not None else _get_config("k_max")
        self.n_init = n_init if n_init is not None else _get_config("n_init")
        self.random_state = random_state if random_state is not None else _get_config("random_state")
        self.auto_k_strategy = auto_k_strategy if auto_k_strategy is not None else _get_config("auto_k_strategy")
        self.max_subsample = max_subsample if max_subsample is not None else _get_config("max_subsample")

    @property
    def supports_auto_k(self) -> bool:
        return True

    @property
    def default_params(self) -> dict:
        return {
            "k": None,
            "k_min": self.k_min,
            "k_max": self.k_max,
            "auto_k_strategy": self.auto_k_strategy,
        }

    def fit_predict(self, X: np.ndarray, progress_cb=None, **kwargs) -> ClusterResult:
        from sklearn.cluster import KMeans

        n_samples = len(X)
        k = kwargs.get("k", self.k)
        k_min = kwargs.get("k_min", self.k_min)
        k_max = kwargs.get("k_max", self.k_max)

        is_auto_k = k is None
        actual_max = min(k_max, n_samples - 1)

        if is_auto_k:
            if progress_cb:
                progress_cb(f"Auto-K: searching k in [{k_min}, {actual_max}]")
            k = self._auto_select_k(X, k_min, actual_max, progress_cb)
        else:
            k = max(2, min(k, n_samples - 1))

        if progress_cb:
            progress_cb(f"Running K-Means with k={k}...")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            km = KMeans(n_clusters=k, n_init=self.n_init, random_state=self.random_state)
            labels = km.fit_predict(X)

        score = compute_silhouette(X, labels)

        if progress_cb:
            progress_cb(f"Clustering complete: k={k}, silhouette={score:.3f}")

        return ClusterResult(
            labels=labels,
            k=k,
            score=score,
            centers=km.cluster_centers_,
            is_auto_k=is_auto_k,
            extra={"inertia": km.inertia_},
        )

    def _auto_select_k(self, X: np.ndarray, k_min: int, k_max: int, progress_cb=None) -> int:
        """Auto-select K using the configured strategy."""
        X_trial = X
        if len(X) > self.max_subsample:
            if progress_cb:
                progress_cb(f"Subsampling {len(X)} -> {self.max_subsample} segments")
            rng = np.random.RandomState(self.random_state)
            idx = rng.choice(len(X), self.max_subsample, replace=False)
            X_trial = X[idx]

        def fit_fn(k: int) -> np.ndarray:
            from sklearn.cluster import KMeans
            km = KMeans(n_clusters=k, n_init=3, random_state=self.random_state)
            return km.fit_predict(X_trial)

        strategy = get_auto_k_strategy(self.auto_k_strategy)
        result = strategy.find_best_k(X_trial, k_min, k_max, fit_fn, progress_cb)
        return result.best_k
