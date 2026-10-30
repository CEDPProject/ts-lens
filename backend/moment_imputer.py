# -*- coding: utf-8 -*-
"""MOMENT Foundation Model imputer -- zero-shot NaN filling.

Uses CMU's MOMENT (ICML'24) pretrained time series model for
context-aware imputation via masked reconstruction.
No training required -- works zero-shot on any univariate time series.
"""
from __future__ import annotations

import threading
from functools import wraps

import warnings

import numpy as np
try:
    import torch
except ImportError:
    torch = None

from .app_config import cfg_foundation

# Inference-only use — silence torch.utils.checkpoint noise from MOMENT's encoder.
warnings.filterwarnings("ignore", message=".*use_reentrant.*")
warnings.filterwarnings("ignore", message=".*None of the inputs have requires_grad.*")

_model = None
_device = None
WINDOW = 512  # MOMENT fixed sequence length (64 patches x 8 timesteps)


_load_lock = threading.RLock()


def _serialized_load(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _load_lock:
            return func(*args, **kwargs)
    return wrapped


@_serialized_load

def _load_model():
    """Lazy-load MOMENT model (singleton). Called once on first use."""
    from backend.runtime_state import require_optional_models
    require_optional_models()
    global _model, _device
    if _model is not None:
        return

    from momentfm import MOMENTPipeline

    model_name = cfg_foundation("moment.model")
    from backend.runtime_state import force_cpu
    device_pref = "cpu" if force_cpu() else cfg_foundation("moment.device")
    if device_pref == "auto":
        _device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        _device = device_pref

    loaded_model = MOMENTPipeline.from_pretrained(
        model_name,
        model_kwargs={"task_name": "reconstruction"},
    )
    loaded_model.init()
    loaded_model = loaded_model.to(_device).float().eval()
    _model = loaded_model


def _impute_chunk(arr_chunk: np.ndarray, nan_mask: np.ndarray) -> np.ndarray:
    """Run MOMENT reconstruction on a single chunk (<= 512pt).

    Args:
        arr_chunk: values with NaN replaced by 0 (length <= WINDOW)
        nan_mask: boolean mask where True = NaN position (same length)

    Returns:
        Reconstructed values for the chunk (same length as input).
    """
    n = len(arr_chunk)
    padded = np.zeros(WINDOW, dtype=np.float64)
    padded[:n] = arr_chunk

    input_mask = np.zeros(WINDOW, dtype=np.float64)
    input_mask[:n] = 1.0  # 1 = valid position, 0 = padding

    # MOMENT mask convention: 1 = valid (use for stats), 0 = to reconstruct
    # So we INVERT nan_mask: NaN positions get 0, valid positions get 1
    recon_mask = np.ones(WINDOW, dtype=np.int64)
    recon_mask[:n] = (~nan_mask).astype(np.int64)

    x = torch.tensor(padded, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
    im = torch.tensor(input_mask, dtype=torch.float32).unsqueeze(0)
    m = torch.tensor(recon_mask, dtype=torch.long).unsqueeze(0)

    with torch.no_grad():
        out = _model(
            x_enc=x.to(_device),
            input_mask=im.to(_device),
            mask=m.to(_device),
        )
    return out.reconstruction.cpu().numpy()[0, 0, :n]


def moment_impute(arr: np.ndarray) -> np.ndarray:
    """Fill NaN values in arr using MOMENT foundation model.

    For sequences <= 512pt: single pass with padding.
    For longer sequences: sliding window with 50% overlap, averaged.

    Args:
        arr: 1D numpy array with NaN values to fill.

    Returns:
        Array with NaN positions filled by MOMENT reconstruction.
        Non-NaN positions are preserved exactly.
    """
    _load_model()

    mask = np.isnan(arr)
    if not mask.any():
        return arr

    result = arr.copy()
    filled = np.nan_to_num(arr, nan=0.0)
    n = len(arr)

    if n <= WINDOW:
        recon = _impute_chunk(filled, mask)
        result[mask] = recon[mask]
    else:
        # Sliding window with 50% overlap
        step = WINDOW // 2
        accum = np.zeros(n, dtype=np.float64)
        counts = np.zeros(n, dtype=np.float64)

        for start in range(0, n, step):
            end = min(start + WINDOW, n)
            chunk_len = end - start

            chunk_filled = filled[start:end]
            chunk_mask = mask[start:end]

            recon = _impute_chunk(chunk_filled, chunk_mask)
            accum[start:end] += recon[:chunk_len]
            counts[start:end] += 1.0

            if end >= n:
                break

        averaged = accum / np.maximum(counts, 1.0)
        result[mask] = averaged[mask]

    return result
