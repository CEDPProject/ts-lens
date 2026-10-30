# -*- coding: utf-8 -*-
"""
Modular clustering system for time series segments.

Usage:
    from backend.clustering import get_algorithm, ALGORITHMS

    # List available algorithms
    print(ALGORITHMS.keys())  # ['kmeans', 'kshape', 'moment', ...]

    # Get and use an algorithm
    algo = get_algorithm("kmeans", k=5)
    result = algo.fit_predict(X)

    # Or with auto-K
    algo = get_algorithm("kmeans")  # k=None -> auto
    result = algo.fit_predict(X, k_min=2, k_max=10)

Adding new algorithms:
    1. Create a new file (e.g., kshape.py)
    2. Implement BaseClusterAlgorithm
    3. Register in ALGORITHMS dict below
"""
from __future__ import annotations

from .base import BaseClusterAlgorithm, ClusterResult, ClusterAlgorithm, compute_silhouette
from .kmeans import KMeansCluster
from .kshape import KShapeCluster
from .moment_embed import MomentEmbedCluster
from .auto_k import (
    AutoKStrategy,
    SilhouetteStrategy,
    MultiIndexStrategy,
    ElbowStrategy,
    AUTO_K_STRATEGIES,
    get_auto_k_strategy,
)
from .features import (
    FeatureExtractor,
    RawFeatures,
    ShapeFeatures,
    PresetFeatures,
    FEATURE_EXTRACTORS,
    get_feature_extractor,
)


# ─────────────────────────────────────────────────────────────────
# Algorithm Registry
# ─────────────────────────────────────────────────────────────────

ALGORITHMS: dict[str, type[BaseClusterAlgorithm]] = {
    "kmeans": KMeansCluster,
    "kshape": KShapeCluster,
    "moment": MomentEmbedCluster,
}


def get_algorithm(name: str, **kwargs) -> BaseClusterAlgorithm:
    """
    Get a clustering algorithm by name.

    Args:
        name: Algorithm name ("kmeans", "kshape", "moment", etc.)
        **kwargs: Algorithm-specific parameters

    Returns:
        Configured algorithm instance

    Example:
        algo = get_algorithm("kmeans", k=5)
        algo = get_algorithm("kshape", k=5)
    """
    if name not in ALGORITHMS:
        raise ValueError(
            f"Unknown algorithm: {name}. Available: {list(ALGORITHMS.keys())}"
        )
    return ALGORITHMS[name](**kwargs)


def list_algorithms() -> list[dict]:
    """List all available algorithms with their default params."""
    result = []
    for name, cls in ALGORITHMS.items():
        instance = cls()
        result.append({
            "name": name,
            "supports_auto_k": instance.supports_auto_k,
            "default_params": instance.default_params,
        })
    return result


# ─────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────

__all__ = [
    # Base classes
    "BaseClusterAlgorithm",
    "ClusterAlgorithm",
    "ClusterResult",
    "compute_silhouette",
    # Algorithms
    "KMeansCluster",
    "KShapeCluster",
    "MomentEmbedCluster",
    "ALGORITHMS",
    "get_algorithm",
    "list_algorithms",
    # Auto-K
    "AutoKStrategy",
    "SilhouetteStrategy",
    "MultiIndexStrategy",
    "ElbowStrategy",
    "AUTO_K_STRATEGIES",
    "get_auto_k_strategy",
    # Features
    "FeatureExtractor",
    "RawFeatures",
    "ShapeFeatures",
    "PresetFeatures",
    "FEATURE_EXTRACTORS",
    "get_feature_extractor",
]
