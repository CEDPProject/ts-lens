# -*- coding: utf-8 -*-
"""Periodicity analysis routes -- single session and batch per-dataset."""
from __future__ import annotations

import logging

import numpy as np
from flask import Blueprint, jsonify

from backend.periodicity_service import analyze_periodicity_core
from backend.helpers import parse_interval_seconds
from backend.session_store import DATA_STORE
from backend.db import (
    get_db, get_dataset, get_dataset_sources, get_dataset_columns,
    has_preprocessed_data,
    get_ts_column, get_ts_column_all_sources,
    get_ts_column_preprocessed, get_ts_column_preprocessed_all_sources,
)

bp = Blueprint("periodicity", __name__)
logger = logging.getLogger(__name__)


@bp.route("/api/periodicity/<sid>")
def analyze_periodicity(sid):
    """FFT-based periodicity analysis for loaded data."""
    entry = DATA_STORE.get(sid)
    if not entry or "values" not in entry:
        return jsonify(error="Load data first"), 404
    # Get sampling interval from diagnostics
    diag = entry.get("diagnostics", {})
    interval_str = diag.get("interval")
    interval_sec = parse_interval_seconds(interval_str)

    # 컬렉션은 여러 출처를 이어 붙인 계열이다. 통째로 FFT 하면 경계에서
    # "계열 길이의 절반" 같은 가짜 장주기가 잡힌다(20개 지점 1년 자료에서
    # 121.5개월이 1위로 나오던 문제). periodicity_all 과 같은 방식으로
    # 출처별로 분석해 다수가 동의하는 주기를 쓴다.
    if entry.get("dataset_type") == "collection" and not entry.get("single_source"):
        per_source = _collection_periodicity(
            entry.get("dataset_key"), entry.get("primary_col"), interval_sec)
        if per_source is not None:
            return jsonify(**per_source)

    result = analyze_periodicity_core(entry["values"], interval_sec)
    return jsonify(**result)


def _collection_periodicity(dataset_key, col_name, interval_sec):
    """출처별로 주기를 구하고, 과반이 같은 주기를 보이면 그것을 돌려준다."""
    if not dataset_key or not col_name:
        return None
    conn = get_db()
    try:
        ds = get_dataset(conn, dataset_key)
        if ds is None:
            return None
        use_pp = has_preprocessed_data(conn, ds["id"])
        if use_pp:
            all_sd = get_ts_column_preprocessed_all_sources(conn, ds["id"], col_name)
        else:
            all_sd = get_ts_column_all_sources(conn, ds["id"], col_name)
    finally:
        conn.close()

    if not all_sd:
        return None

    votes: dict[str, list] = {}
    for sd in all_sd:
        arr = np.array(sd["values"], dtype=np.float64)
        info = analyze_periodicity_core(arr, interval_sec)
        if not info.get("has_periodicity"):
            continue
        for rank, per in enumerate(info.get("periods", [])[:3]):
            slot = votes.setdefault(per["description"], [0, 0.0, per])
            slot[0] += 1
            slot[1] += per["strength"]

    if not votes:
        return {"has_periodicity": False, "message": "No periodicity", "periods": []}

    total = len(all_sd)
    ranked = sorted(votes.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)
    periods = []
    for desc, (count, tot_strength, sample) in ranked[:5]:
        periods.append({
            "period_points": sample["period_points"],
            "strength": round(tot_strength / count, 4),
            "description": desc,
            "tooltip": f"{count}/{total} sources · {sample.get('tooltip', '')}",
        })

    top_count = ranked[0][1][0]
    has = top_count > total / 2
    msg = (f"Main period: {periods[0]['description']}"
           f" ({top_count}/{total} sources)") if has else "No agreed periodicity"
    return {"has_periodicity": has, "message": msg, "periods": periods}


@bp.route("/api/periodicity_all/<dataset_key>")
def analyze_periodicity_all(dataset_key):
    """FFT periodicity for all columns of a dataset (batch, for badges)."""
    from backend.dataset_service import get_dataset_cache, detect_sampling_interval

    conn = get_db()
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        conn.close()
        return jsonify(error="dataset not found"), 404

    sources = get_dataset_sources(conn, ds["id"])
    columns = get_dataset_columns(conn, ds["id"])
    use_pp = has_preprocessed_data(conn, ds["id"])
    ds_type = ds["type"]

    # Detect sampling interval from cache or first source
    cache = get_dataset_cache(conn, ds["id"])
    interval_str = (cache or {}).get("sampling_interval")
    if not interval_str and sources:
        interval_str = detect_sampling_interval(conn, sources[0]["id"])
    interval_sec = parse_interval_seconds(interval_str)

    results = {}
    for col_def in columns:
        col_name = col_def["col_name"]
        try:
            if ds_type == "collection":
                # Collection: analyze each source independently, then pick
                # the most common period (concat creates boundary artifacts)
                if use_pp:
                    all_sd = get_ts_column_preprocessed_all_sources(
                        conn, ds["id"], col_name)
                else:
                    all_sd = get_ts_column_all_sources(conn, ds["id"], col_name)
                period_votes = {}  # description -> (count, total_strength)
                for sd in (all_sd or []):
                    arr = np.array(sd["values"], dtype=np.float64)
                    info = analyze_periodicity_core(arr, interval_sec)
                    if info["has_periodicity"] and info["periods"]:
                        top = info["periods"][0]
                        desc = top["description"]
                        prev = period_votes.get(desc, (0, 0.0))
                        period_votes[desc] = (prev[0] + 1, prev[1] + top["strength"])
                if period_votes:
                    best = max(period_votes.items(), key=lambda x: x[1][0])
                    best_count = best[1][0]
                    total_sources = len(all_sd or [])
                    # Require majority agreement (>50% of sources share same period)
                    if total_sources > 0 and best_count > total_sources / 2:
                        results[col_name] = {
                            "has": True,
                            "period": best[0],
                            "strength": round(best[1][1] / best[1][0], 4),
                        }
                    else:
                        results[col_name] = {"has": False, "period": None, "strength": 0}
                else:
                    results[col_name] = {"has": False, "period": None, "strength": 0}
            else:
                if use_pp:
                    _, val_list = get_ts_column_preprocessed(
                        conn, sources[0]["key"], col_name)
                else:
                    _, val_list = get_ts_column(
                        conn, sources[0]["key"], col_name)
                vals = val_list
                arr = np.array(vals, dtype=np.float64) if vals else np.array([])
                info = analyze_periodicity_core(arr, interval_sec)
                # compact: only send summary per column
                top = info["periods"][0] if info["periods"] else None
                results[col_name] = {
                    "has": info["has_periodicity"],
                    "period": top["description"] if top else None,
                    "strength": top["strength"] if top else 0,
                }
        except Exception as e:
            # Reporting "no periodicity" on failure is indistinguishable from a
            # genuine negative -- log so a broken column is not silently hidden.
            logger.warning("Periodicity analysis failed for column %s: %s", col_name, e)
            results[col_name] = {"has": False, "period": None, "strength": 0}

    conn.close()
    return jsonify(columns=results)
