# -*- coding: utf-8 -*-
"""Interpreter Agent -- query generation (DGS: Describe, Generate, Search).

Responsibilities:
    - Preset -> shape generation  (D)
    - Natural language -> shape via LLM  (D+G)
    - Example (QBE) -> shape extraction
    - Segment -> text description (reverse direction)
    - LLM model management
"""
from __future__ import annotations

import json
from typing import Optional

import numpy as np

from backend import llm_service
from backend.db import (
    get_db, get_dataset, get_dataset_columns,
    get_column_info,
)
from backend.helpers import z_normalize, _resample
from backend.segment_search import get_preset_shape, list_presets


class InterpreterAgent:
    """Generates query shapes from various input modalities."""

    # ── Preset -> Shape ───────────────────────────────────────

    @staticmethod
    def generate_query_from_preset(preset_name: str, length: int,
                                   dataset_id: str | None = None) -> np.ndarray:
        """Generate a z-normalized query shape from a named preset."""
        return get_preset_shape(preset_name, length, dataset_id)

    @staticmethod
    def list_available_presets(dataset_id: str | None = None) -> list[dict]:
        """Return preset catalog with preview shapes."""
        return list_presets(dataset_id)

    # ── Natural Language -> Shape (via LLM) ───────────────────

    @staticmethod
    def _build_dataset_context(dataset_key: str, col_name: str) -> str:
        """dataset/column 메타데이터로 LLM 프롬프트용 context 문자열 빌드."""
        if not dataset_key:
            return ""
        context_parts = []
        conn = get_db()
        try:
            ds = get_dataset(conn, dataset_key)
            if not ds:
                return ""
            context_parts.append(f"Dataset: {ds['name'] or ds['key']}")
            if ds.get("description"):
                context_parts.append(f"Description: {ds['description']}")
            meta_raw = ds.get("metadata") or "{}"
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}
            if meta.get("facility"):
                context_parts.append(f"Facility: {meta['facility']}")
            if meta.get("interval"):
                context_parts.append(f"Sampling: {meta['interval']}")

            cols = get_dataset_columns(conn, ds["id"])
            if cols:
                context_parts.append("\n### Columns")
                for c in cols:
                    parts = [f"- **{c['col_name']}**"]
                    if c.get("label"):
                        parts.append(f"({c['label']})")
                    if c.get("unit"):
                        parts.append(f"[{c['unit']}]")
                    if c.get("description"):
                        parts.append(f": {c['description']}")
                    context_parts.append(" ".join(parts))

            if col_name:
                col_info = get_column_info(conn, dataset_key, col_name)
                context_parts.append(f"\n### Target column: {col_name}")
                if col_info.get("label"):
                    context_parts.append(f"Label: {col_info['label']}")
                if col_info.get("unit"):
                    context_parts.append(f"Unit: {col_info['unit']}")
        finally:
            conn.close()
        return "\n".join(context_parts)

    @staticmethod
    def generate_query_from_text(description: str, length: int = 24,
                                 dataset_key: str = "",
                                 col_name: str = "",
                                 model_key: Optional[str] = None) -> dict:
        """Convert natural language description to a shape via LLM.

        Returns dict with shape, description, raw_response, etc.
        """
        context = InterpreterAgent._build_dataset_context(dataset_key, col_name)
        return llm_service.generate_pattern_shape(
            description, length=length,
            dataset_context=context,
            model_key=model_key,
        )

    @staticmethod
    def generate_queries_from_text_ensemble(
        description: str, length: int = 24,
        dataset_key: str = "",
        col_name: str = "",
        model_key: Optional[str] = None,
        n_paraphrases: int = 3,
    ) -> dict:
        """원본 + paraphrase 들로 N+1 개의 shape을 lexical ensemble 생성.

        Returns: { "queries": [...], "paraphrases": [...] } — llm_service 참조.
        """
        context = InterpreterAgent._build_dataset_context(dataset_key, col_name)
        return llm_service.generate_pattern_shape_ensemble(
            description, length=length,
            dataset_context=context,
            model_key=model_key,
            n_paraphrases=n_paraphrases,
        )

    # ── Example (QBE) -> Shape ────────────────────────────────

    @staticmethod
    def generate_query_from_example(values: list[float],
                                    target_length: int = 0) -> np.ndarray:
        """Extract query shape from a user-drawn or selected segment."""
        arr = np.array(values, dtype=np.float64)
        if target_length > 0 and len(arr) != target_length:
            arr = _resample(arr, target_length)
        return z_normalize(arr)

    # ── Segment -> Text Description (reverse direction) ───────

    @staticmethod
    def describe_segment(values: list[float],
                         col_name: str = "", col_label: str = "",
                         col_unit: str = "", unit_label: str = "",
                         timestamps: list[str] = None,
                         dataset_key: str = "",
                         model_key: Optional[str] = None) -> dict:
        """Describe a time series segment in natural language."""
        context_parts = []
        if dataset_key:
            conn = get_db()
            ds = get_dataset(conn, dataset_key)
            if ds:
                context_parts.append(f"Dataset: {ds['name'] or ds['key']}")
                if ds.get("description"):
                    context_parts.append(f"Description: {ds['description']}")
                if col_name and not col_label:
                    ci = get_column_info(conn, dataset_key, col_name)
                    col_label = ci.get("label", "")
                    col_unit = ci.get("unit", "")
            conn.close()

        return llm_service.describe_segment(
            values=values,
            col_name=col_name,
            col_label=col_label,
            col_unit=col_unit,
            unit_label=unit_label,
            timestamps=timestamps or [],
            dataset_context="\n".join(context_parts),
            model_key=model_key,
        )

    @staticmethod
    def describe_segment_multi(columns_data: list[dict],
                               unit_label: str = "",
                               timestamps: list[str] = None,
                               dataset_key: str = "",
                               model_key: Optional[str] = None) -> dict:
        """Describe a multi-column segment in natural language."""
        context_parts = []
        if dataset_key:
            conn = get_db()
            ds = get_dataset(conn, dataset_key)
            if ds:
                context_parts.append(f"Dataset: {ds['name'] or ds['key']}")
                for col in columns_data:
                    cn = col.get("col_name", "")
                    if cn:
                        ci = get_column_info(conn, dataset_key, cn)
                        col["col_label"] = ci.get("label", "")
                        col["col_unit"] = ci.get("unit", "")
            conn.close()

        return llm_service.describe_segment_multi(
            columns_data=columns_data,
            unit_label=unit_label,
            timestamps=timestamps or [],
            dataset_context="\n".join(context_parts),
            model_key=model_key,
        )

    # ── LLM Model management ─────────────────────────────────

    @staticmethod
    def get_llm_status(model_key: Optional[str] = None,
                       task: Optional[str] = None) -> dict:
        return llm_service.get_status(model_key, task=task)

    @staticmethod
    def list_models(task: Optional[str] = None) -> list[dict]:
        return llm_service.list_models(task=task)

    @staticmethod
    def set_active_model(model_key: str,
                         task: Optional[str] = None) -> bool:
        return llm_service.set_active_model(model_key, task=task)
