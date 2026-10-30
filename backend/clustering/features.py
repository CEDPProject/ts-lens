# -*- coding: utf-8 -*-
"""
Feature extraction methods for time series clustering.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import numpy as np


class FeatureExtractor(ABC):
    """Base class for feature extraction."""

    name: str = "base"

    @abstractmethod
    def extract(self, arr: np.ndarray, **kwargs) -> np.ndarray:
        """Extract features from a normalized time series."""
        pass

    @property
    def uses_feature_space(self) -> bool:
        """
        Whether this extractor produces features in a different space than raw time series.
        If True, centroids should be computed from raw time series, not feature space.
        """
        return False


class RawFeatures(FeatureExtractor):
    """Use raw (normalized) time series as features."""

    name = "raw"

    def __init__(self, add_stats: bool = True):
        """
        Args:
            add_stats: Append mean/std to help distinguish flat patterns.
        """
        self.add_stats = add_stats

    def extract(self, arr: np.ndarray, **kwargs) -> np.ndarray:
        if self.add_stats:
            raw_mean = kwargs.get("raw_mean", arr.mean())
            raw_std = kwargs.get("raw_std", arr.std())
            stats = np.array([raw_mean * 0.1, raw_std * 0.1])
            return np.concatenate([arr, stats])
        return arr

    @property
    def uses_feature_space(self) -> bool:
        return False


class ShapeFeatures(FeatureExtractor):
    """Extract shape-based statistical features."""

    name = "shape"

    def extract(self, arr: np.ndarray, **kwargs) -> np.ndarray:
        n = len(arr)
        features = []

        # Basic stats
        features.append(float(arr.mean()))
        features.append(float(arr.std()))
        features.append(float(arr.min()))
        features.append(float(arr.max()))

        # Slope
        if n >= 2:
            slope = float(np.polyfit(np.arange(n), arr, 1)[0])
        else:
            slope = 0.0
        features.append(slope)

        # First/second half comparison
        mid = n // 2
        if mid > 0:
            first_mean = float(arr[:mid].mean())
            second_mean = float(arr[mid:].mean())
            features.append(first_mean - second_mean)
        else:
            features.append(0.0)

        # Peak position (normalized)
        peak_pos = float(np.argmax(arr)) / max(1, n - 1)
        features.append(peak_pos)

        # Valley position (normalized)
        valley_pos = float(np.argmin(arr)) / max(1, n - 1)
        features.append(valley_pos)

        # Curvature (second derivative approximation)
        if n >= 3:
            d2 = np.diff(arr, n=2)
            curvature = float(np.mean(np.abs(d2)))
        else:
            curvature = 0.0
        features.append(curvature)

        return np.array(features, dtype=np.float64)

    @property
    def uses_feature_space(self) -> bool:
        return True


class PresetFeatures(FeatureExtractor):
    """Extract features based on similarity to preset shapes (built-in + custom)."""

    name = "preset"

    def __init__(self, preset_catalog: dict | None = None, dataset_id: str | None = None):
        """
        Args:
            preset_catalog: Dict of preset name -> params. If None, will be loaded.
            dataset_id: Dataset ID for loading dataset-specific custom presets.
        """
        self._catalog = preset_catalog
        self._dataset_id = dataset_id

    @property
    def catalog(self) -> dict:
        if self._catalog is None:
            from ..app_config import cfg_preset_catalog, cfg_custom_presets
            # Combine built-in + custom presets (dataset-specific)
            self._catalog = dict(cfg_preset_catalog())
            custom = cfg_custom_presets(self._dataset_id)
            for k, v in custom.items():
                self._catalog["custom_" + k] = v
        return self._catalog

    def extract(self, arr: np.ndarray, **kwargs) -> np.ndarray:
        from ..segment_search import get_preset_shape
        from ..helpers import compute_distance, distance_to_similarity

        seg_length = len(arr)
        preset_names = list(self.catalog.keys())

        # Similarity to each preset
        preset_sims = []
        for name in preset_names:
            preset_shape = get_preset_shape(name, seg_length, self._dataset_id)
            dist = compute_distance(arr, preset_shape, "euclidean")
            sim = distance_to_similarity(dist, "euclidean")
            preset_sims.append(sim)

        # Statistical features
        n = len(arr)
        mean_val = float(arr.mean())
        std_val = float(arr.std())
        slope = float(np.polyfit(np.arange(n), arr, 1)[0]) if n >= 2 else 0.0

        # Symmetry
        mid = n // 2
        first_half = arr[:mid].mean() if mid > 0 else 0.0
        second_half = arr[mid:].mean() if mid < n else 0.0
        symmetry = 1.0 - abs(float(first_half - second_half)) / (
            abs(float(first_half)) + abs(float(second_half)) + 1e-10
        )

        stats = [mean_val * 0.01, std_val * 0.1, slope * 0.5, symmetry]
        return np.array(preset_sims + stats, dtype=np.float64)

    @property
    def uses_feature_space(self) -> bool:
        return True


# Registry
FEATURE_EXTRACTORS: dict[str, type[FeatureExtractor]] = {
    "raw": RawFeatures,
    "euclidean": RawFeatures,  # Alias
    "shape": ShapeFeatures,
    "preset": PresetFeatures,
}


def get_feature_extractor(name: str, **kwargs) -> FeatureExtractor:
    """Get a feature extractor by name."""
    if name not in FEATURE_EXTRACTORS:
        raise ValueError(
            f"Unknown feature extractor: {name}. Available: {list(FEATURE_EXTRACTORS.keys())}"
        )
    return FEATURE_EXTRACTORS[name](**kwargs)
