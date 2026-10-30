# -*- coding: utf-8 -*-
"""
Base classes and protocols for clustering algorithms.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
import numpy as np


@dataclass
class ClusterResult:
    """Standard result from any clustering algorithm."""
    labels: np.ndarray          # Cluster label for each sample (-1 = noise)
    k: int                      # Number of clusters found
    score: float                # Quality score (e.g., silhouette)
    centers: np.ndarray | None  # Cluster centers in feature space
    is_auto_k: bool = False     # Whether K was auto-selected
    noise_count: int = 0        # Number of noise points (for DBSCAN-like)
    extra: dict = None          # Algorithm-specific extra info

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}


@runtime_checkable
class ClusterAlgorithm(Protocol):
    """Protocol for clustering algorithms."""

    name: str

    def fit_predict(self, X: np.ndarray, **kwargs) -> ClusterResult:
        """Run clustering and return results."""
        ...

    @property
    def supports_auto_k(self) -> bool:
        """Whether this algorithm supports automatic K selection."""
        ...

    @property
    def default_params(self) -> dict:
        """Default parameters for this algorithm."""
        ...


class BaseClusterAlgorithm(ABC):
    """Base class for clustering algorithms."""

    name: str = "base"

    @abstractmethod
    def fit_predict(self, X: np.ndarray, **kwargs) -> ClusterResult:
        """Run clustering and return results."""
        pass

    @property
    def supports_auto_k(self) -> bool:
        return False

    @property
    def default_params(self) -> dict:
        return {}


def compute_silhouette(X: np.ndarray, labels: np.ndarray) -> float:
    """Compute silhouette score, handling edge cases."""
    from sklearn.metrics import silhouette_score
    unique_labels = set(labels)
    # Remove noise label if present
    cluster_labels = [lb for lb in unique_labels if lb >= 0]
    if len(cluster_labels) < 2:
        return 0.0
    # Filter out noise for scoring
    mask = labels >= 0
    if np.sum(mask) <= len(cluster_labels):
        return 0.0
    try:
        return float(silhouette_score(X[mask], labels[mask]))
    except Exception:
        return 0.0
