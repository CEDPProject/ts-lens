# -*- coding: utf-8 -*-
"""Benchmark dataset import routes -- SKAB and NAB."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify

from backend.db import (
    get_db, get_dataset, insert_dataset, insert_source,
    insert_dataset_column, insert_ground_truth_label,
    get_ground_truth_labels,
    save_uploaded_file, DATA_DIR,
)
from backend.runtime_paths import stored_path
from backend.app_config import cfg
from backend.csv_utils import insert_df_data
from backend.dataset_service import build_dataset_cache

bp = Blueprint("benchmark_import", __name__)
logger = logging.getLogger(__name__)

# ── SKAB constants ──
SKAB_REPO = cfg("benchmark.skab.repo")
SKAB_SENSOR_COLS = [
    "Accelerometer1RMS", "Accelerometer2RMS", "Current",
    "Pressure", "Temperature", "Thermocouple",
    "Voltage", "Volume Flow RateRMS",
]
SKAB_SUBDIRS = ["anomaly-free", "valve1", "valve2", "other"]

# ── NAB constants ──
NAB_REPO = cfg("benchmark.nab.repo")
NAB_KNOWN_CAUSE_FILES = [
    "ambient_temperature_system_failure",
    "cpu_utilization_asg_misconfiguration",
    "ec2_request_latency_system_failure",
    "machine_temperature_system_failure",
    "nyc_taxi",
    "rogue_agent_key_hold",
    "rogue_agent_key_updown",
]


def _extract_windows(df: pd.DataFrame, col: str, time_col: str) -> list[dict]:
    """Convert consecutive col==1 rows into (start_ts, end_ts) windows."""
    windows = []
    in_window = False
    start = None
    prev_ts = None
    for i in range(len(df)):
        val = df.iloc[i][col]
        ts = str(df.iloc[i][time_col])
        if val == 1:
            if not in_window:
                start = ts
                in_window = True
            prev_ts = ts
        else:
            if in_window:
                windows.append({"start_ts": start, "end_ts": prev_ts})
                in_window = False
    if in_window:
        windows.append({"start_ts": start, "end_ts": prev_ts})
    return windows


def _git_clone(repo_url: str, dest: Path) -> bool:
    """Shallow clone a git repo. Returns True on success."""
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(dest)],
            check=True, capture_output=True, timeout=120,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"git clone failed: {e}")
        return False


@bp.route("/api/benchmark/import_skab", methods=["POST"])
def import_skab():
    """Import SKAB benchmark dataset."""
    DS_KEY = "skab"

    conn = get_db()
    existing = get_dataset(conn, DS_KEY)
    if existing:
        conn.close()
        return jsonify(error="SKAB already imported. Delete it first to re-import.",
                       dataset_key=DS_KEY), 409

    # Clone repo
    tmp = Path(tempfile.mkdtemp(prefix="skab_"))
    created_files = []
    committed = False
    try:
        if not _git_clone(SKAB_REPO, tmp):
            return jsonify(error="git clone failed. Is git installed?"), 500

        data_dir = tmp / "data"
        if not data_dir.is_dir():
            return jsonify(error="SKAB data/ directory not found"), 500

        # Collect CSV files
        csv_files = []
        for subdir in SKAB_SUBDIRS:
            d = data_dir / subdir
            if d.is_dir():
                for f in sorted(d.glob("*.csv")):
                    csv_files.append((subdir, f))

        if not csv_files:
            return jsonify(error="No CSV files found in SKAB"), 500

        # Serialize the final conflict check and the complete import.
        conn.execute("BEGIN IMMEDIATE")
        if get_dataset(conn, DS_KEY):
            return jsonify(error="Dataset already imported", dataset_key=DS_KEY), 409
        # Create dataset
        meta = json.dumps({"source": "SKAB", "benchmark": True,
                           "has_ground_truth": True,
                           "url": cfg("benchmark.skab.home")})
        ds_id = insert_dataset(conn, DS_KEY, "SKAB Benchmark",
                               "Skoltech Anomaly Benchmark - 8 sensor columns, "
                               "anomaly + changepoint labels",
                               ds_type="collection", metadata=meta)

        # Add index_type
        try:
            conn.execute("UPDATE datasets SET index_type = 'datetime' WHERE id = ?",
                         (ds_id,))
        except Exception:
            pass

        # Register columns (once)
        for col in SKAB_SENSOR_COLS:
            insert_dataset_column(conn, ds_id, col, col)


        total_labels = 0
        for subdir, csv_path in csv_files:
            stem = csv_path.stem
            source_key = f"{DS_KEY}/{subdir}_{stem}"
            desc = f"{subdir}/{stem}"

            # Read CSV (semicolon-delimited)
            df = pd.read_csv(csv_path, sep=";")

            # Detect time column
            time_col = "datetime" if "datetime" in df.columns else df.columns[0]

            # Insert source
            src_id = insert_source(conn, ds_id, source_key, time_col, desc)

            # Insert sensor data
            sensor_cols = [c for c in SKAB_SENSOR_COLS if c in df.columns]
            insert_df_data(conn, src_id, df, time_col, sensor_cols, commit=False)

            # Extract and insert label windows
            for label_col, label_type in [("anomaly", "anomaly"),
                                           ("changepoint", "changepoint")]:
                if label_col in df.columns:
                    windows = _extract_windows(df, label_col, time_col)
                    for w in windows:
                        insert_ground_truth_label(
                            conn, ds_id, w["start_ts"], w["end_ts"],
                            label_type=label_type, source_id=src_id,
                            description=desc,
                        )
                        total_labels += 1

            created_files.append(save_uploaded_file(
                conn, ds_id, DS_KEY, f"{subdir}_{stem}.csv", csv_path.read_bytes()))


        # Build cache
        from backend.db import get_dataset_sources, get_dataset_columns
        sources = get_dataset_sources(conn, ds_id)
        cols = get_dataset_columns(conn, ds_id)
        build_dataset_cache(conn, ds_id, sources, cols, commit=False)
        conn.commit()
        committed = True
        return jsonify(
            ok=True,
            dataset_key=DS_KEY,
            sources_imported=len(csv_files),
            labels_imported=total_labels,
        )

    finally:
        try:
            if not committed:
                conn.rollback()
                for relative in created_files:
                    try:
                        stored_path(DATA_DIR, relative).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        logger.warning("Failed to clean uncommitted benchmark file: %s", relative, exc_info=True)
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)


@bp.route("/api/benchmark/import_nab", methods=["POST"])
def import_nab():
    """Import NAB realKnownCause benchmark dataset."""
    DS_KEY = "nab_known_cause"

    conn = get_db()
    existing = get_dataset(conn, DS_KEY)
    if existing:
        conn.close()
        return jsonify(error="NAB already imported. Delete it first to re-import.",
                       dataset_key=DS_KEY), 409

    tmp = Path(tempfile.mkdtemp(prefix="nab_"))
    created_files = []
    committed = False
    try:
        if not _git_clone(NAB_REPO, tmp):
            return jsonify(error="git clone failed. Is git installed?"), 500

        cause_dir = tmp / "data" / "realKnownCause"
        if not cause_dir.is_dir():
            return jsonify(error="NAB realKnownCause directory not found"), 500

        # Parse labels
        labels_path = tmp / "labels" / "combined_windows.json"
        nab_labels = {}
        if labels_path.is_file():
            with open(labels_path, encoding="utf-8") as f:
                all_labels = json.load(f)
            # Filter for realKnownCause
            for k, v in all_labels.items():
                if "realKnownCause" in k:
                    # key like "realKnownCause/nyc_taxi.csv"
                    stem = Path(k).stem
                    nab_labels[stem] = v

        # Serialize the final conflict check and the complete import.
        conn.execute("BEGIN IMMEDIATE")
        if get_dataset(conn, DS_KEY):
            return jsonify(error="Dataset already imported", dataset_key=DS_KEY), 409
        # Create dataset
        meta = json.dumps({"source": "NAB", "benchmark": True,
                           "has_ground_truth": True,
                           "subset": "realKnownCause",
                           "url": cfg("benchmark.nab.home")})
        ds_id = insert_dataset(conn, DS_KEY, "NAB realKnownCause",
                               "Numenta Anomaly Benchmark - real data with known causes",
                               ds_type="collection", metadata=meta)
        try:
            conn.execute("UPDATE datasets SET index_type = 'datetime' WHERE id = ?",
                         (ds_id,))
        except Exception:
            pass

        # Register column
        insert_dataset_column(conn, ds_id, "value", "value")


        total_labels = 0
        imported = 0
        for stem in NAB_KNOWN_CAUSE_FILES:
            csv_path = cause_dir / f"{stem}.csv"
            if not csv_path.is_file():
                continue

            source_key = f"{DS_KEY}/{stem}"
            df = pd.read_csv(csv_path)
            time_col = "timestamp" if "timestamp" in df.columns else df.columns[0]

            src_id = insert_source(conn, ds_id, source_key, time_col, stem)
            insert_df_data(conn, src_id, df, time_col, ["value"], commit=False)

            # Insert label windows from JSON
            windows = nab_labels.get(stem, [])
            for w in windows:
                if len(w) == 2:
                    # NAB format: ["2014-01-15 10:30:00.000000", "2014-01-16 08:45:00.000000"]
                    start_ts = w[0].split(".")[0]  # trim microseconds
                    end_ts = w[1].split(".")[0]
                    insert_ground_truth_label(
                        conn, ds_id, start_ts, end_ts,
                        label_type="anomaly", source_id=src_id,
                        description=stem,
                    )
                    total_labels += 1

            created_files.append(save_uploaded_file(
                conn, ds_id, DS_KEY, f"{stem}.csv", csv_path.read_bytes()))
            imported += 1


        if imported == 0:
            return jsonify(error="No supported NAB files found"), 400

        from backend.db import get_dataset_sources, get_dataset_columns
        sources = get_dataset_sources(conn, ds_id)
        cols = get_dataset_columns(conn, ds_id)
        build_dataset_cache(conn, ds_id, sources, cols, commit=False)
        conn.commit()
        committed = True
        return jsonify(
            ok=True,
            dataset_key=DS_KEY,
            sources_imported=imported,
            labels_imported=total_labels,
        )

    finally:
        try:
            if not committed:
                conn.rollback()
                for relative in created_files:
                    try:
                        stored_path(DATA_DIR, relative).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        logger.warning("Failed to clean uncommitted benchmark file: %s", relative, exc_info=True)
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)


@bp.route("/api/benchmark/status")
def benchmark_status():
    """Check which benchmark datasets are imported."""
    conn = get_db()
    result = {}
    for ds_key in ["skab", "nab_known_cause"]:
        ds = get_dataset(conn, ds_key)
        if ds:
            labels = get_ground_truth_labels(conn, ds["id"])
            from backend.db import get_dataset_sources
            sources = get_dataset_sources(conn, ds["id"])
            result[ds_key] = {
                "imported": True,
                "source_count": len(sources),
                "label_count": len(labels),
            }
        else:
            result[ds_key] = {"imported": False}
    conn.close()
    return jsonify(benchmarks=result)
