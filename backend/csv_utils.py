# -*- coding: utf-8 -*-
"""CSV utilities -- column detection, index type inference, batch insertion."""
from __future__ import annotations

import sqlite3

import pandas as pd

from .app_config import cfg


# ── Column detection config ──────────────────────────────────
def _col_det(key: str):
    """열 판별 점수는 config/app.yaml 의 column_detection 이 단일 소스다."""
    return cfg(f"column_detection.{key}")


def detect_index_type(df: pd.DataFrame, col: str) -> str:
    """Infer column index type: 'datetime' | 'numeric' | 'text'."""
    if pd.api.types.is_numeric_dtype(df[col]):
        return "numeric"
    sample = df[col].dropna().head(20)
    if len(sample) == 0:
        return "text"
    confidence = cfg("detection.date_parse_confidence")
    try:
        parsed = pd.to_datetime(sample, errors="coerce")
        if parsed.notna().sum() / max(len(sample), 1) >= confidence:
            return "datetime"
    except Exception:
        pass
    return "text"


def guess_time_column(df: pd.DataFrame) -> tuple[str | None, str]:
    """Guess the time/index column. Returns (col_name, index_type)."""
    TIME_EXACT = set(cfg("column_detection.time_exact_keywords"))
    TIME_SUBSTR = cfg("column_detection.time_substring_keywords")
    INDEX_EXACT = set(cfg("column_detection.index_exact_keywords"))
    INDEX_SUBSTR = cfg("column_detection.index_substring_keywords")

    # Scores from config
    s_time_exact = _col_det("time_exact_score")
    s_time_substr = _col_det("time_substring_score")
    s_idx_exact = _col_det("index_exact_score")
    s_idx_substr = _col_det("index_substring_score")
    s_non_numeric = _col_det("non_numeric_bonus")
    s_datetime = _col_det("datetime_detected_bonus")
    s_monotonic = _col_det("monotonic_bonus")
    s_first_col = _col_det("first_column_bonus")
    min_score = _col_det("min_detection_score")

    confidence = cfg("detection.date_parse_confidence")
    candidates = []

    for col in df.columns:
        score = 0
        col_lower = col.lower().strip()
        idx_type = detect_index_type(df, col)

        if col_lower in TIME_EXACT:
            score += s_time_exact
        else:
            for kw in TIME_SUBSTR:
                if kw in col_lower:
                    score += s_time_substr
                    break

        if score == 0:
            if col_lower in INDEX_EXACT:
                score += s_idx_exact
            else:
                for kw in INDEX_SUBSTR:
                    if kw in col_lower:
                        score += s_idx_substr
                        break

        if not pd.api.types.is_numeric_dtype(df[col]):
            score += s_non_numeric

        if idx_type == "datetime":
            score += s_datetime

        if idx_type == "numeric":
            sample_vals = df[col].dropna().head(20)
            if len(sample_vals) >= 3:
                diffs = sample_vals.diff().dropna()
                if (diffs > 0).all():
                    score += s_monotonic

        if col == df.columns[0]:
            score += s_first_col

        if score > 0:
            candidates.append((col, score, idx_type))

    if not candidates:
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            sample = df[col].dropna().head(10)
            try:
                parsed = pd.to_datetime(sample, errors="coerce")
                valid_ratio = parsed.notna().sum() / max(len(sample), 1)
                if valid_ratio >= confidence:
                    candidates.append((col, s_datetime, "datetime"))
            except Exception:
                pass

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        if candidates[0][1] >= min_score:
            return candidates[0][0], candidates[0][2]
    return None, "datetime"


def insert_df_data(conn: sqlite3.Connection,
                   source_id: int,
                   df: pd.DataFrame,
                   time_col: str,
                   col_list: list[str],
                   batch_size: int | None = None,
                   index_type: str = "datetime", *, commit: bool = True) -> int:
    """DataFrame -> ts_data bulk insertion. Returns row count."""
    if batch_size is None:
        batch_size = cfg("csv.batch_size")

    pad_width = 0
    if index_type == "numeric" and pd.api.types.is_numeric_dtype(df[time_col]):
        max_val = df[time_col].max()
        pad_width = len(str(int(max_val))) if pd.notna(max_val) else 0

    buf = []
    count = 0
    col_indices = {col: df.columns.get_loc(col) for col in col_list}
    time_idx = df.columns.get_loc(time_col)

    for row in df.itertuples(index=False):
        raw_ts = row[time_idx]
        if index_type == "numeric" and pad_width > 0:
            ts = str(int(raw_ts)).zfill(pad_width)
        else:
            ts = str(raw_ts)
        has_val = False
        for col, ci in col_indices.items():
            val = row[ci]
            if pd.notna(val):
                # 숫자가 아닌 칸은 건너뛴다 -- 한 칸 때문에 적재 전체가 죽지 않게
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                buf.append((source_id, ts, col, num))
                has_val = True
        if has_val:
            count += 1
        if len(buf) >= batch_size:
            conn.executemany(
                "INSERT OR REPLACE INTO ts_data "
                "(source_id, timestamp, col_name, value) VALUES (?, ?, ?, ?)",
                buf,
            )
            if commit:
                conn.commit()
            buf.clear()
    if buf:
        conn.executemany(
            "INSERT OR REPLACE INTO ts_data "
            "(source_id, timestamp, col_name, value) VALUES (?, ?, ?, ?)",
            buf,
        )
        if commit:
            conn.commit()
    return count
