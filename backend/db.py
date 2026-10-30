# -*- coding: utf-8 -*-
"""DB 스키마 및 헬퍼.

계층 구조:
  datasets → dataset_columns (컬럼 정의)
           → sources → ts_data (실제 값)
           → segments (세그먼트 구간)
"""
from __future__ import annotations

import json
import logging
import math
import threading
import sqlite3
import uuid
from pathlib import Path

from backend.runtime_paths import DB_PATH, DATA_DIR, stored_path, validate_component

SCHEMA = """
-- 데이터셋: 최상위 그룹
CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT UNIQUE NOT NULL,
    name        TEXT DEFAULT '',
    description TEXT DEFAULT '',
    type        TEXT DEFAULT 'single',  -- 'single' | 'collection'
    metadata    TEXT DEFAULT '{}',      -- JSON: location, characteristics, etc.
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 데이터셋의 컬럼 정의 (스키마)
CREATE TABLE IF NOT EXISTS dataset_columns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    col_name    TEXT NOT NULL,
    label       TEXT DEFAULT '',
    unit        TEXT DEFAULT '',
    description TEXT DEFAULT '',
    val_min     REAL DEFAULT NULL,
    val_max     REAL DEFAULT NULL,
    UNIQUE(dataset_id, col_name)
);

-- 소스: 데이터셋 내의 개별 시계열
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    key         TEXT UNIQUE NOT NULL,
    time_col    TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 시계열 데이터 (EAV)
CREATE TABLE IF NOT EXISTS ts_data (
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    timestamp   TEXT NOT NULL,
    col_name    TEXT NOT NULL,
    value       REAL,
    PRIMARY KEY(source_id, timestamp, col_name)
);

CREATE INDEX IF NOT EXISTS idx_ts_source_col
    ON ts_data(source_id, col_name, timestamp);

-- 세그먼트 (segment 모드)
CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    col_name    TEXT NOT NULL,
    seg_index   INTEGER NOT NULL,
    seg_label   TEXT DEFAULT '',
    start_ts    TEXT NOT NULL,
    end_ts      TEXT NOT NULL,
    point_count INTEGER NOT NULL,
    UNIQUE(source_id, col_name, seg_index)
);

CREATE INDEX IF NOT EXISTS idx_seg_source_col
    ON segments(source_id, col_name);

-- 원본 CSV 파일 저장 추적
CREATE TABLE IF NOT EXISTS stored_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    rel_path    TEXT NOT NULL,       -- DATA_DIR 기준 상대경로
    file_size   INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 전처리 설정 (데이터셋 단위, 최신 1개만 유지)
CREATE TABLE IF NOT EXISTS preprocess_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    config      TEXT NOT NULL DEFAULT '{}',   -- JSON: 전처리 옵션
    summary     TEXT NOT NULL DEFAULT '{}',   -- JSON: 전처리 결과 요약
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset_id)
);

-- 전처리된 시계열 데이터 (ts_data와 동일 구조)
CREATE TABLE IF NOT EXISTS ts_data_preprocessed (
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    timestamp   TEXT NOT NULL,
    col_name    TEXT NOT NULL,
    value       REAL,
    PRIMARY KEY(source_id, timestamp, col_name)
);

CREATE INDEX IF NOT EXISTS idx_ts_pp_source_col
    ON ts_data_preprocessed(source_id, col_name, timestamp);

-- ─── 패턴 라이브러리 (Labeling Loop) ────────────────────────
-- DGS 파이프라인의 독립 확장 모듈
-- Accept된 패턴을 저장하여 재검색/자동탐지에 활용

-- 패턴 정의 (자연어 설명 + 생성된 shape)
CREATE TABLE IF NOT EXISTS pattern_defs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,                    -- 패턴 이름 (e.g. "인버터 이상")
    description TEXT DEFAULT '',                  -- 자연어 설명 원문
    shape       TEXT DEFAULT '[]',                -- JSON: z-score 정규화 배열
    dataset_key TEXT DEFAULT '',                  -- 연관 데이터셋 (빈 문자열=범용)
    col_name    TEXT DEFAULT '',                  -- 연관 컬럼
    method      TEXT DEFAULT 'euclidean',         -- 검색에 사용한 거리 함수
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 패턴 인스턴스 (Accept된 실제 구간)
CREATE TABLE IF NOT EXISTS pattern_instances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id  INTEGER NOT NULL REFERENCES pattern_defs(id) ON DELETE CASCADE,
    source_key  TEXT NOT NULL,                    -- 출처 소스
    col_name    TEXT NOT NULL,
    start_ts    TEXT NOT NULL,                    -- 구간 시작
    end_ts      TEXT NOT NULL,                    -- 구간 끝
    similarity  REAL DEFAULT 0.0,                 -- 검색 시 유사도 점수
    status      TEXT DEFAULT 'accepted',          -- accepted | rejected | pending
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pattern_inst_pid
    ON pattern_instances(pattern_id);
CREATE INDEX IF NOT EXISTS idx_pattern_inst_status
    ON pattern_instances(status);

-- 데이터셋 캐시 (자동 감지 메타데이터 저장)
CREATE TABLE IF NOT EXISTS dataset_cache (
    dataset_id  INTEGER PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
    cache_data  TEXT NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Basket (장바구니) ────────────────────────────────────────
-- 검색/분류/클러스터링 결과에서 세그먼트를 수집하고 레이블을 부여
CREATE TABLE IF NOT EXISTS basket_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key TEXT NOT NULL,
    source_key  TEXT NOT NULL,
    col_name    TEXT NOT NULL,
    start_ts    TEXT NOT NULL,
    end_ts      TEXT NOT NULL,
    point_count INTEGER DEFAULT 0,
    values_json TEXT DEFAULT '',
    series_json TEXT DEFAULT '',
    similarity  REAL DEFAULT NULL,
    distance    REAL DEFAULT NULL,
    search_method TEXT DEFAULT '',
    normalize   INTEGER DEFAULT NULL,
    columns_json TEXT DEFAULT '',
    label       TEXT DEFAULT '',
    status      TEXT DEFAULT 'draft',     -- draft | confirmed
    context     TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_key, col_name, start_ts, end_ts)
);

CREATE INDEX IF NOT EXISTS idx_basket_status
    ON basket_items(status);
CREATE INDEX IF NOT EXISTS idx_basket_dataset
    ON basket_items(dataset_key);

-- ─── Ground Truth Labels ─────────────────────────────────────
-- 벤치마크 데이터셋의 정답 레이블 (anomaly/changepoint 윈도우)
CREATE TABLE IF NOT EXISTS ground_truth_labels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    source_id   INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    label_type  TEXT NOT NULL DEFAULT 'anomaly',  -- 'anomaly' | 'changepoint' | 'event'
    start_ts    TEXT NOT NULL,
    end_ts      TEXT NOT NULL,
    description TEXT DEFAULT '',
    metadata    TEXT DEFAULT '{}',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gt_source
    ON ground_truth_labels(source_id, start_ts, end_ts);
"""


_db_initialized = False
_init_lock = threading.Lock()
logger = logging.getLogger(__name__)

def get_db(path: Path | None = None) -> sqlite3.Connection:
    global _db_initialized
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with _init_lock:
            if not _db_initialized or path is not None:
                init_db(conn)
                if path is None:
                    _db_initialized = True
        # Legacy routes open multiple connections and may return early. Keep
        # ownership until request teardown, including streamed responses.
        from flask import g, has_app_context
        if has_app_context():
            g.setdefault("tslens_db_connections", []).append(conn)
        return conn
    except BaseException:
        conn.close()
        raise


def close_request_connections(error=None):
    from flask import g
    for conn in g.pop("tslens_db_connections", []):
        conn.close()  # Also rolls back unfinished writes; close is idempotent.


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Migration: add metadata column if missing
    try:
        conn.execute("SELECT metadata FROM datasets LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE datasets ADD COLUMN metadata TEXT DEFAULT '{}'")
    # Migration: add val_min/val_max columns if missing
    try:
        conn.execute("SELECT val_min, val_max FROM dataset_columns LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE dataset_columns ADD COLUMN val_min REAL DEFAULT NULL")
        conn.execute("ALTER TABLE dataset_columns ADD COLUMN val_max REAL DEFAULT NULL")
    # Migration: add description column if missing
    try:
        conn.execute("SELECT description FROM dataset_columns LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE dataset_columns ADD COLUMN description TEXT DEFAULT ''")
    # Migration: add index_type column if missing (datetime | numeric | text)
    try:
        conn.execute("SELECT index_type FROM datasets LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE datasets ADD COLUMN index_type TEXT DEFAULT 'datetime'")
    # Migration: basket dedup unique index
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_basket_dedup'").fetchone()
        if not row:
            duplicate = conn.execute(
                "SELECT 1 FROM basket_items GROUP BY source_key, start_ts, end_ts, col_name "
                "HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            if duplicate:
                raise RuntimeError(
                    "Legacy Basket contains duplicate intervals. Existing labels were preserved; "
                    "review duplicates on a backup before creating the unique index.")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_basket_dedup "
                "ON basket_items(source_key, start_ts, end_ts, col_name)"
            )
    except sqlite3.OperationalError:
        pass
    # Migration: add point_count column to basket_items
    try:
        conn.execute("SELECT point_count FROM basket_items LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE basket_items ADD COLUMN point_count INTEGER DEFAULT 0")
    # Migration: add values_json column to basket_items
    try:
        conn.execute("SELECT values_json FROM basket_items LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE basket_items ADD COLUMN values_json TEXT DEFAULT ''")
    # Migration: preserve the complete multi-column search snapshot and score
    # provenance.  Additive columns keep every existing basket id/label/status.
    for column, ddl in (
        ("series_json", "TEXT DEFAULT ''"),
        ("similarity", "REAL DEFAULT NULL"),
        ("distance", "REAL DEFAULT NULL"),
        ("search_method", "TEXT DEFAULT ''"),
        ("normalize", "INTEGER DEFAULT NULL"),
        ("columns_json", "TEXT DEFAULT ''"),
    ):
        try:
            conn.execute(f"SELECT {column} FROM basket_items LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE basket_items ADD COLUMN {column} {ddl}")
    conn.commit()


# ─── 데이터셋 ─────────────────────────────────────────────────

def get_dataset(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT * FROM datasets WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def get_dataset_by_id(conn: sqlite3.Connection, dataset_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return dict(row) if row else None


def get_all_datasets(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM datasets ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def insert_dataset(conn: sqlite3.Connection, key: str, name: str = "",
                   description: str = "", ds_type: str = "single",
                   metadata: str = "{}") -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO datasets (key, name, description, type, metadata) VALUES (?, ?, ?, ?, ?)",
        (key, name, description, ds_type, metadata),
    )
    if cur.lastrowid == 0 or cur.rowcount == 0:
        row = conn.execute("SELECT id FROM datasets WHERE key = ?", (key,)).fetchone()
        return row["id"]
    return cur.lastrowid


def dataset_delete_summary(conn: sqlite3.Connection, dataset_key: str) -> dict | None:
    """삭제 확인 창에 보일 건수. 무엇이 사라지는지 지우기 전에 보여 준다."""
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        return None
    ds_id = ds["id"]
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]
    return {
        "key": dataset_key,
        "sources": one("SELECT COUNT(*) FROM sources WHERE dataset_id = ?", ds_id),
        "points": one("SELECT COUNT(*) FROM ts_data t JOIN sources s ON s.id = t.source_id "
                      "WHERE s.dataset_id = ?", ds_id),
        "preprocessed_points": one("SELECT COUNT(*) FROM ts_data_preprocessed t JOIN sources s "
                                   "ON s.id = t.source_id WHERE s.dataset_id = ?", ds_id),
        "labels": one("SELECT COUNT(*) FROM ground_truth_labels WHERE dataset_id = ?", ds_id),
        "basket_items": one("SELECT COUNT(*) FROM basket_items WHERE dataset_key = ?", dataset_key),
        "patterns": one("SELECT COUNT(*) FROM pattern_defs WHERE dataset_key = ?", dataset_key),
        "stored_files": one("SELECT COUNT(*) FROM stored_files WHERE dataset_id = ?", ds_id),
    }


def delete_dataset(conn: sqlite3.Connection, dataset_key: str, *,
                   commit: bool = True, delete_files: bool = True,
                   keep_user_items: bool = False) -> bool:
    """keep_user_items=True: 덮어쓰기 재적재용. 바구니·패턴은 키로 묶여 있어 새 적재에 이어진다."""
    ds = get_dataset(conn, dataset_key)
    if ds is None:
        return False
    if delete_files and not commit:
        raise ValueError("File deletion requires a committed dataset deletion")
    files = get_stored_files(conn, ds["id"]) if delete_files else []
    ds_id = ds["id"]
    # 소스별 데이터 삭제 — 원본·전처리·라벨 모두. 외래키 연쇄(PRAGMA)에 기대지 않는다
    sources = conn.execute("SELECT id, key FROM sources WHERE dataset_id = ?", (ds_id,)).fetchall()
    for s in sources:
        conn.execute("DELETE FROM segments WHERE source_id = ?", (s["id"],))
        conn.execute("DELETE FROM ts_data WHERE source_id = ?", (s["id"],))
        conn.execute("DELETE FROM ts_data_preprocessed WHERE source_id = ?", (s["id"],))
        conn.execute("DELETE FROM ground_truth_labels WHERE source_id = ?", (s["id"],))
        if not keep_user_items:
            conn.execute("DELETE FROM pattern_instances WHERE source_key = ?", (s["key"],))
    if not keep_user_items:
        # 외래키 없이 키 문자열로만 묶인 표 — CASCADE가 닿지 않는다
        conn.execute("DELETE FROM basket_items WHERE dataset_key = ?", (dataset_key,))
        conn.execute("DELETE FROM pattern_defs WHERE dataset_key = ?", (dataset_key,))
    conn.execute("DELETE FROM preprocess_configs WHERE dataset_id = ?", (ds_id,))
    conn.execute("DELETE FROM dataset_cache WHERE dataset_id = ?", (ds_id,))
    conn.execute("DELETE FROM sources WHERE dataset_id = ?", (ds_id,))
    conn.execute("DELETE FROM dataset_columns WHERE dataset_id = ?", (ds_id,))
    # Ground truth labels 삭제
    conn.execute("DELETE FROM ground_truth_labels WHERE dataset_id = ?", (ds_id,))
    # 원본 CSV 파일 삭제
    conn.execute("DELETE FROM stored_files WHERE dataset_id = ?", (ds_id,))
    conn.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
    if commit:
        conn.commit()
        for item in files:
            try:
                stored_path(DATA_DIR, item["rel_path"]).unlink(missing_ok=True)
            except (OSError, ValueError):
                logger.warning("Dataset deleted; original cleanup pending: %s", item["rel_path"], exc_info=True)
        if delete_files:
            try:
                stored_path(DATA_DIR, dataset_key).rmdir()  # 비었을 때만 지워진다
            except (OSError, ValueError):
                pass
    return True


# ─── 데이터셋 컬럼 ────────────────────────────────────────────

def get_dataset_columns(conn: sqlite3.Connection, dataset_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT col_name, label, unit, description, val_min, val_max "
        "FROM dataset_columns WHERE dataset_id = ? ORDER BY id",
        (dataset_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_column_info(conn: sqlite3.Connection, dataset_key: str, col_name: str) -> dict:
    row = conn.execute("""
        SELECT dc.label, dc.unit, dc.description, dc.val_min, dc.val_max
        FROM dataset_columns dc
        JOIN datasets d ON d.id = dc.dataset_id
        WHERE d.key = ? AND dc.col_name = ?
    """, (dataset_key, col_name)).fetchone()
    if row:
        return {"label": row["label"], "unit": row["unit"],
                "description": row["description"],
                "val_min": row["val_min"], "val_max": row["val_max"]}
    return {"label": col_name, "unit": "", "description": "",
            "val_min": None, "val_max": None}


def insert_dataset_column(conn: sqlite3.Connection, dataset_id: int,
                          col_name: str, label: str, unit: str = "",
                          description: str = "",
                          val_min: float | None = None,
                          val_max: float | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO dataset_columns "
        "(dataset_id, col_name, label, unit, description, val_min, val_max) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dataset_id, col_name, label, unit, description, val_min, val_max),
    )


def update_column_meta(conn: sqlite3.Connection, dataset_id: int,
                       col_name: str, description: str | None = None,
                       val_min: float | None = ...,
                       val_max: float | None = ..., *, commit: bool = True) -> None:
    """컬럼의 description, val_min, val_max를 선택적으로 업데이트."""
    sets, params = [], []
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if val_min is not ...:
        sets.append("val_min = ?")
        params.append(val_min)
    if val_max is not ...:
        sets.append("val_max = ?")
        params.append(val_max)
    if not sets:
        return
    params.extend([dataset_id, col_name])
    conn.execute(
        f"UPDATE dataset_columns SET {', '.join(sets)} "
        "WHERE dataset_id = ? AND col_name = ?",
        params,
    )
    if commit:
        conn.commit()


# ─── 소스 ─────────────────────────────────────────────────────

def get_source(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT * FROM sources WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def get_dataset_sources(conn: sqlite3.Connection, dataset_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sources WHERE dataset_id = ? ORDER BY key",
        (dataset_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_source(conn: sqlite3.Connection, dataset_id: int, key: str,
                  time_col: str, description: str = "") -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO sources (dataset_id, key, time_col, description) VALUES (?, ?, ?, ?)",
        (dataset_id, key, time_col, description),
    )
    if cur.lastrowid == 0 or cur.rowcount == 0:
        row = conn.execute("SELECT id FROM sources WHERE key = ?", (key,)).fetchone()
        return row["id"]
    return cur.lastrowid


def delete_source(conn: sqlite3.Connection, source_key: str) -> bool:
    src = get_source(conn, source_key)
    if src is None:
        return False
    conn.execute("DELETE FROM segments WHERE source_id = ?", (src["id"],))
    conn.execute("DELETE FROM ts_data WHERE source_id = ?", (src["id"],))
    conn.execute("DELETE FROM ground_truth_labels WHERE source_id = ?", (src["id"],))
    # 소스가 빠지면 데이터셋 전처리 요약이 어긋난다 — 전처리는 데이터셋 통째로 지운다
    conn.execute("DELETE FROM preprocess_configs WHERE dataset_id = ?", (src["dataset_id"],))
    conn.execute("DELETE FROM ts_data_preprocessed WHERE source_id IN "
                 "(SELECT id FROM sources WHERE dataset_id = ?)", (src["dataset_id"],))
    conn.execute("DELETE FROM basket_items WHERE source_key = ?", (source_key,))
    conn.execute("DELETE FROM pattern_instances WHERE source_key = ?", (source_key,))
    conn.execute("DELETE FROM sources WHERE id = ?", (src["id"],))
    conn.commit()
    return True


# ─── 시계열 데이터 ────────────────────────────────────────────

def insert_ts_bulk(conn: sqlite3.Connection, source_id: int,
                   timestamp: str, col_values: dict[str, float]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO ts_data (source_id, timestamp, col_name, value) VALUES (?, ?, ?, ?)",
        [(source_id, timestamp, col, val) for col, val in col_values.items()],
    )


def get_ts_column(conn: sqlite3.Connection, source_key: str, col_name: str) -> tuple[list[str], list[float]]:
    """특정 소스의 특정 컬럼 시계열 → (timestamps, values)."""
    rows = conn.execute("""
        SELECT d.timestamp, d.value
        FROM ts_data d JOIN sources s ON s.id = d.source_id
        WHERE s.key = ? AND d.col_name = ? AND d.value IS NOT NULL
        ORDER BY d.timestamp
    """, (source_key, col_name)).fetchall()
    return [r["timestamp"] for r in rows], [r["value"] for r in rows]


def get_ts_column_all_sources(conn: sqlite3.Connection, dataset_id: int,
                               col_name: str) -> list[dict]:
    """데이터셋 내 모든 소스의 특정 컬럼 데이터를 소스별로 반환.
    collection 모드에서 사용."""
    sources = get_dataset_sources(conn, dataset_id)
    result = []
    for s in sources:
        ts, vals = get_ts_column(conn, s["key"], col_name)
        if ts:
            result.append({
                "source_key": s["key"],
                "description": s["description"],
                "timestamps": ts,
                "values": vals,
            })
    return result


def count_points(conn: sqlite3.Connection, source_key: str, col_name: str) -> int:
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM ts_data d JOIN sources s ON s.id = d.source_id
        WHERE s.key = ? AND d.col_name = ? AND d.value IS NOT NULL
    """, (source_key, col_name)).fetchone()
    return row["cnt"] if row else 0


def count_dataset_points(conn: sqlite3.Connection, dataset_id: int, col_name: str) -> int:
    """데이터셋 내 모든 소스의 포인트 합계."""
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM ts_data d JOIN sources s ON s.id = d.source_id
        WHERE s.dataset_id = ? AND d.col_name = ? AND d.value IS NOT NULL
    """, (dataset_id, col_name)).fetchone()
    return row["cnt"] if row else 0


# ─── 세그먼트 ─────────────────────────────────────────────────

def insert_segment(conn: sqlite3.Connection, source_id: int, col_name: str,
                   seg_index: int, seg_label: str,
                   start_ts: str, end_ts: str, point_count: int) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO segments
           (source_id, col_name, seg_index, seg_label, start_ts, end_ts, point_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (source_id, col_name, seg_index, seg_label, start_ts, end_ts, point_count),
    )


def get_segments(conn: sqlite3.Connection, source_key: str, col_name: str) -> list[dict]:
    rows = conn.execute("""
        SELECT seg.seg_index, seg.seg_label, seg.start_ts, seg.end_ts, seg.point_count
        FROM segments seg JOIN sources s ON s.id = seg.source_id
        WHERE s.key = ? AND seg.col_name = ?
        ORDER BY seg.seg_index
    """, (source_key, col_name)).fetchall()
    return [dict(r) for r in rows]


def get_all_segment_values(conn: sqlite3.Connection, source_key: str, col_name: str,
                           segments: list[dict]) -> list[tuple[list[str], list[float]]]:
    if not segments:
        return []
    all_ts, all_vals = get_ts_column(conn, source_key, col_name)
    result = []
    for seg in segments:
        start_ts, end_ts = seg["start_ts"], seg["end_ts"]
        seg_ts, seg_vals = [], []
        for ts, val in zip(all_ts, all_vals):
            if start_ts <= ts <= end_ts:
                seg_ts.append(ts)
                seg_vals.append(val)
        result.append((seg_ts, seg_vals))
    return result


# ─── 원본 CSV 파일 저장 ──────────────────────────────────────

def insert_stored_file(conn: sqlite3.Connection, dataset_id: int,
                       filename: str, rel_path: str, file_size: int = 0) -> int:
    cur = conn.execute(
        "INSERT INTO stored_files (dataset_id, filename, rel_path, file_size) VALUES (?, ?, ?, ?)",
        (dataset_id, filename, rel_path, file_size),
    )
    return cur.lastrowid


def get_stored_files(conn: sqlite3.Connection, dataset_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, filename, rel_path, file_size, created_at "
        "FROM stored_files WHERE dataset_id = ? ORDER BY filename",
        (dataset_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def save_uploaded_file(conn: sqlite3.Connection, dataset_id: int,
                       dataset_key: str, filename: str, content: bytes) -> str:
    """Store unchanged bytes under a unique name; preserve the display filename."""
    validate_component(dataset_key)
    validate_component(filename)
    rel_path = f"{dataset_key}/{uuid.uuid4().hex}_{filename}"
    out_path = stored_path(DATA_DIR, rel_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_path.open("xb") as handle:
            handle.write(content)
        insert_stored_file(conn, dataset_id, filename, rel_path, len(content))
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return rel_path


# ─── 전처리 데이터 ──────────────────────────────────────────

def get_preprocess_config(conn: sqlite3.Connection, dataset_id: int) -> dict | None:
    """데이터셋의 전처리 설정 조회. 없으면 None."""
    row = conn.execute(
        "SELECT * FROM preprocess_configs WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    return dict(row) if row else None


def save_preprocess_config(conn: sqlite3.Connection, dataset_id: int,
                           config: str, summary: str, *, commit: bool = True) -> int:
    """전처리 설정 저장 (기존 것 대체 — 최신 1개만 유지)."""
    conn.execute("DELETE FROM preprocess_configs WHERE dataset_id = ?", (dataset_id,))
    cur = conn.execute(
        "INSERT INTO preprocess_configs (dataset_id, config, summary) VALUES (?, ?, ?)",
        (dataset_id, config, summary),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def delete_preprocess(conn: sqlite3.Connection, dataset_id: int) -> None:
    """데이터셋의 전처리 설정 + 데이터 모두 삭제."""
    conn.execute("DELETE FROM preprocess_configs WHERE dataset_id = ?", (dataset_id,))
    sources = conn.execute(
        "SELECT id FROM sources WHERE dataset_id = ?", (dataset_id,)
    ).fetchall()
    for s in sources:
        conn.execute("DELETE FROM ts_data_preprocessed WHERE source_id = ?", (s["id"],))
    conn.commit()


def clear_preprocessed_data(conn: sqlite3.Connection, dataset_id: int) -> None:
    """전처리된 ts 데이터만 삭제 (config는 유지)."""
    sources = conn.execute(
        "SELECT id FROM sources WHERE dataset_id = ?", (dataset_id,)
    ).fetchall()
    for s in sources:
        conn.execute("DELETE FROM ts_data_preprocessed WHERE source_id = ?", (s["id"],))


def insert_ts_preprocessed_bulk(conn: sqlite3.Connection, source_id: int,
                                rows: list[tuple[str, str, float]]) -> None:
    """전처리된 데이터 일괄 삽입. rows: [(timestamp, col_name, value), ...]"""
    conn.executemany(
        "INSERT OR REPLACE INTO ts_data_preprocessed "
        "(source_id, timestamp, col_name, value) VALUES (?, ?, ?, ?)",
        [(source_id, ts, col, val) for ts, col, val in rows],
    )


def get_ts_column_preprocessed(conn: sqlite3.Connection, source_key: str,
                                col_name: str) -> tuple[list[str], list[float]]:
    """전처리된 데이터에서 특정 소스·컬럼의 시계열 반환."""
    rows = conn.execute("""
        SELECT d.timestamp, d.value
        FROM ts_data_preprocessed d JOIN sources s ON s.id = d.source_id
        WHERE s.key = ? AND d.col_name = ? AND d.value IS NOT NULL
        ORDER BY d.timestamp
    """, (source_key, col_name)).fetchall()
    return [r["timestamp"] for r in rows], [r["value"] for r in rows]


def get_ts_column_preprocessed_all_sources(conn: sqlite3.Connection,
                                            dataset_id: int,
                                            col_name: str) -> list[dict]:
    """전처리된 데이터에서 모든 소스의 특정 컬럼 반환 (collection 모드)."""
    sources = get_dataset_sources(conn, dataset_id)
    result = []
    for s in sources:
        ts, vals = get_ts_column_preprocessed(conn, s["key"], col_name)
        if ts:
            result.append({
                "source_key": s["key"],
                "description": s["description"],
                "timestamps": ts,
                "values": vals,
            })
    return result


def has_preprocessed_data(conn: sqlite3.Connection, dataset_id: int) -> bool:
    """전처리 데이터 존재 여부 확인."""
    cfg = get_preprocess_config(conn, dataset_id)
    if cfg is None:
        return False
    # 실제 데이터가 있는지도 확인
    sources = conn.execute(
        "SELECT id FROM sources WHERE dataset_id = ? LIMIT 1", (dataset_id,)
    ).fetchone()
    if sources is None:
        return False
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM ts_data_preprocessed WHERE source_id = ?",
        (sources["id"],)
    ).fetchone()
    return row["cnt"] > 0


# ═══════════════════════════════════════════════════════════════
# 패턴 라이브러리 (Labeling Loop 독립 모듈)
# ═══════════════════════════════════════════════════════════════

def create_pattern_def(conn: sqlite3.Connection, name: str,
                       description: str = "", shape: str = "[]",
                       dataset_key: str = "", col_name: str = "",
                       method: str = "euclidean") -> int:
    """패턴 정의 생성. 반환: pattern_id."""
    cur = conn.execute(
        "INSERT INTO pattern_defs (name, description, shape, dataset_key, col_name, method) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, description, shape, dataset_key, col_name, method),
    )
    conn.commit()
    return cur.lastrowid


def get_pattern_defs(conn: sqlite3.Connection,
                     dataset_key: str | None = None) -> list[dict]:
    """패턴 정의 목록 조회. dataset_key 지정 시 해당 데이터셋+범용 패턴만."""
    if dataset_key:
        rows = conn.execute(
            "SELECT * FROM pattern_defs WHERE dataset_key = ? OR dataset_key = '' "
            "ORDER BY updated_at DESC",
            (dataset_key,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pattern_defs ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_pattern_def(conn: sqlite3.Connection, pattern_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pattern_defs WHERE id = ?", (pattern_id,)
    ).fetchone()
    return dict(row) if row else None


def update_pattern_def(conn: sqlite3.Connection, pattern_id: int,
                       **kwargs) -> bool:
    """패턴 정의 업데이트. name, description, shape 등."""
    allowed = {"name", "description", "shape", "dataset_key", "col_name", "method"}
    sets, params = [], []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(pattern_id)
    conn.execute(
        f"UPDATE pattern_defs SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return True


def delete_pattern_def(conn: sqlite3.Connection, pattern_id: int) -> bool:
    conn.execute("DELETE FROM pattern_instances WHERE pattern_id = ?", (pattern_id,))
    cur = conn.execute("DELETE FROM pattern_defs WHERE id = ?", (pattern_id,))
    conn.commit()
    return cur.rowcount > 0


def add_pattern_instance(conn: sqlite3.Connection, pattern_id: int,
                         source_key: str, col_name: str,
                         start_ts: str, end_ts: str,
                         similarity: float = 0.0,
                         status: str = "accepted") -> int:
    """패턴 인스턴스(Accept/Reject된 구간) 추가."""
    cur = conn.execute(
        "INSERT INTO pattern_instances "
        "(pattern_id, source_key, col_name, start_ts, end_ts, similarity, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pattern_id, source_key, col_name, start_ts, end_ts, similarity, status),
    )
    conn.commit()
    return cur.lastrowid


def get_pattern_instances(conn: sqlite3.Connection, pattern_id: int,
                          status: str | None = None) -> list[dict]:
    """패턴 인스턴스 목록 조회."""
    if status:
        rows = conn.execute(
            "SELECT * FROM pattern_instances WHERE pattern_id = ? AND status = ? "
            "ORDER BY similarity DESC",
            (pattern_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pattern_instances WHERE pattern_id = ? "
            "ORDER BY created_at DESC",
            (pattern_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_instance_status(conn: sqlite3.Connection, instance_id: int,
                           status: str) -> bool:
    """인스턴스 상태 변경 (accepted/rejected/pending)."""
    cur = conn.execute(
        "UPDATE pattern_instances SET status = ? WHERE id = ?",
        (status, instance_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_pattern_stats(conn: sqlite3.Connection, pattern_id: int) -> dict:
    """패턴의 인스턴스 통계."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) as accepted,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            AVG(CASE WHEN status = 'accepted' THEN similarity END) as avg_similarity
        FROM pattern_instances WHERE pattern_id = ?
    """, (pattern_id,)).fetchone()
    return dict(row) if row else {"total": 0, "accepted": 0, "rejected": 0,
                                   "pending": 0, "avg_similarity": None}


# ═══════════════════════════════════════════════════════════════
# Ground Truth Labels
# ═══════════════════════════════════════════════════════════════

def insert_ground_truth_label(conn: sqlite3.Connection,
                               dataset_id: int,
                               start_ts: str, end_ts: str,
                               label_type: str = "anomaly",
                               source_id: int | None = None,
                               description: str = "",
                               metadata: str = "{}") -> int:
    cur = conn.execute(
        "INSERT INTO ground_truth_labels "
        "(dataset_id, source_id, label_type, start_ts, end_ts, description, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dataset_id, source_id, label_type, start_ts, end_ts, description, metadata),
    )
    return cur.lastrowid


def get_ground_truth_labels(conn: sqlite3.Connection,
                             dataset_id: int,
                             source_id: int | None = None,
                             label_type: str | None = None) -> list[dict]:
    clauses = ["dataset_id = ?"]
    params: list = [dataset_id]
    if source_id is not None:
        clauses.append("source_id = ?")
        params.append(source_id)
    if label_type is not None:
        clauses.append("label_type = ?")
        params.append(label_type)
    rows = conn.execute(
        f"SELECT * FROM ground_truth_labels WHERE {' AND '.join(clauses)} "
        "ORDER BY start_ts", params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_labels_for_dataset_key(conn: sqlite3.Connection,
                                dataset_key: str,
                                label_type: str | None = None) -> list[dict]:
    params: list = [dataset_key]
    type_clause = ""
    if label_type:
        type_clause = " AND g.label_type = ?"
        params.append(label_type)
    rows = conn.execute(f"""
        SELECT g.*, s.key as source_key FROM ground_truth_labels g
        JOIN datasets d ON d.id = g.dataset_id
        LEFT JOIN sources s ON s.id = g.source_id
        WHERE d.key = ?{type_clause}
        ORDER BY g.start_ts
    """, params).fetchall()
    return [dict(r) for r in rows]


def get_overlapping_labels(conn: sqlite3.Connection,
                            source_id: int,
                            start_ts: str, end_ts: str) -> list[dict]:
    """Labels overlapping [start_ts, end_ts]: label.start <= end AND label.end >= start."""
    rows = conn.execute(
        "SELECT * FROM ground_truth_labels "
        "WHERE source_id = ? AND start_ts <= ? AND end_ts >= ? "
        "ORDER BY start_ts",
        (source_id, end_ts, start_ts),
    ).fetchall()
    return [dict(r) for r in rows]


def has_ground_truth(conn: sqlite3.Connection, dataset_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM ground_truth_labels WHERE dataset_id = ?",
        (dataset_id,),
    ).fetchone()
    return row["cnt"] > 0


def delete_ground_truth_labels(conn: sqlite3.Connection, dataset_id: int) -> int:
    cur = conn.execute(
        "DELETE FROM ground_truth_labels WHERE dataset_id = ?", (dataset_id,))
    conn.commit()
    return cur.rowcount


# ═══════════════════════════════════════════════════════════════
# Basket (장바구니)
# ═══════════════════════════════════════════════════════════════

def get_basket_items(conn: sqlite3.Connection,
                     status: str | None = None,
                     dataset_key: str | None = None,
                     include_values: bool = False) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if dataset_key:
        clauses.append("dataset_key = ?")
        params.append(dataset_key)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM basket_items{where} ORDER BY created_at DESC", params
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if include_values and d.get("values_json"):
            try:
                d["values"] = json.loads(d["values_json"])
            except (json.JSONDecodeError, TypeError):
                d["values"] = []
        if include_values and d.get("series_json"):
            try:
                d["series"] = json.loads(d["series_json"])
            except (json.JSONDecodeError, TypeError):
                d["series"] = []
        if d.get("columns_json"):
            try:
                d["columns"] = json.loads(d["columns_json"])
            except (json.JSONDecodeError, TypeError):
                d["columns"] = []
        d.pop("values_json", None)
        d.pop("series_json", None)
        d.pop("columns_json", None)
        result.append(d)
    return result


def _validate_basket_snapshot(item):
    if not isinstance(item, dict):
        raise ValueError("Basket item must be an object")
    for key in ("dataset_key", "source_key", "col_name", "start_ts", "end_ts"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise ValueError(f"{key} must be a nonempty string")
    for key in ("context", "search_method"):
        if key in item and not isinstance(item[key], str):
            raise ValueError(f"{key} must be a string")
    for key in ("similarity", "distance"):
        value = item.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"{key} must be a finite number")
    if "normalize" in item and item["normalize"] is not None and type(item["normalize"]) is not bool:
        raise ValueError("normalize must be a boolean")
    points = item.get("point_count", 0)
    if type(points) is not int or points < 0:
        raise ValueError("point_count must be a nonnegative integer")
    def values_ok(values):
        return isinstance(values, list) and all(
            v is None or (type(v) in (int, float) and math.isfinite(v)) for v in values)
    if "values" in item and item["values"] is not None and not values_ok(item["values"]):
        raise ValueError("values must be a numeric array (null allowed)")
    series = item.get("series") or []
    columns = item.get("columns") or []
    if not isinstance(series, list) or not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ValueError("series and columns must be arrays")
    for entry in series:
        if not isinstance(entry, dict) or not isinstance(entry.get("col_name"), str) or not values_ok(entry.get("values")):
            raise ValueError("Invalid series snapshot")
        timestamps = entry.get("timestamps") or []
        if not isinstance(timestamps, list) or (timestamps and len(timestamps) != len(entry["values"])):
            raise ValueError("Snapshot timestamps and values must have matching lengths")


def add_basket_items(conn: sqlite3.Connection,
                     items: list[dict]) -> dict:
    ids = []
    keys = []
    updated = 0
    errors = []
    for it in items:
        try:
            _validate_basket_snapshot(it)
            required = ("dataset_key", "source_key", "col_name", "start_ts", "end_ts")
            missing = [key for key in required if key not in it]
            if missing:
                raise ValueError(f"missing required fields: {', '.join(missing)}")
            values_json = ""
            if it.get("values"):
                values_json = json.dumps(it["values"])
            series_json = json.dumps(it.get("series") or [], ensure_ascii=False)
            columns_json = json.dumps(it.get("columns") or [], ensure_ascii=False)
            existing = conn.execute(
                "SELECT id FROM basket_items WHERE source_key=? AND col_name=? "
                "AND start_ts=? AND end_ts=?",
                (it["source_key"], it["col_name"], it["start_ts"], it["end_ts"]),
            ).fetchone()
            # Upsert the captured waveform/provenance while preserving curation
            # fields (id, label, status, note, created_at).
            conn.execute(
                "INSERT INTO basket_items "
                "(dataset_key, source_key, col_name, start_ts, end_ts, "
                "context, point_count, values_json, series_json, similarity, "
                "distance, search_method, normalize, columns_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_key, col_name, start_ts, end_ts) DO UPDATE SET "
                "dataset_key=excluded.dataset_key, context=excluded.context, "
                "point_count=excluded.point_count, "
                "values_json=CASE WHEN excluded.values_json!='' THEN excluded.values_json ELSE basket_items.values_json END, "
                "series_json=CASE WHEN excluded.series_json!='[]' THEN excluded.series_json ELSE basket_items.series_json END, "
                "similarity=COALESCE(excluded.similarity, basket_items.similarity), "
                "distance=COALESCE(excluded.distance, basket_items.distance), "
                "search_method=CASE WHEN excluded.search_method!='' THEN excluded.search_method ELSE basket_items.search_method END, "
                "normalize=COALESCE(excluded.normalize, basket_items.normalize), "
                "columns_json=CASE WHEN excluded.columns_json!='[]' THEN excluded.columns_json ELSE basket_items.columns_json END",
                (it["dataset_key"], it["source_key"], it["col_name"],
                 it["start_ts"], it["end_ts"], it.get("context", ""),
                 it.get("point_count", 0), values_json, series_json,
                 it.get("similarity"), it.get("distance"),
                 it.get("search_method", ""),
                 (int(bool(it["normalize"])) if it.get("normalize") is not None else None),
                 columns_json),
            )
            row = conn.execute(
                "SELECT id FROM basket_items WHERE source_key=? AND col_name=? "
                "AND start_ts=? AND end_ts=?",
                (it["source_key"], it["col_name"], it["start_ts"], it["end_ts"]),
            ).fetchone()
            if existing:
                updated += 1
            elif row:
                ids.append(row["id"])
            keys.append(
                f"{it['source_key']}|{it['start_ts']}|{it['end_ts']}|{it['col_name']}"
            )
        except Exception as exc:
            errors.append(str(exc))
    conn.commit()
    return {"ids": ids, "count": len(ids), "updated": updated,
            "keys": keys, "errors": errors}


def get_basket_keys(conn: sqlite3.Connection,
                    status: str | None = None) -> list[str]:
    if status:
        rows = conn.execute(
            "SELECT source_key || '|' || start_ts || '|' || end_ts || '|' || col_name AS k "
            "FROM basket_items WHERE status = ?", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT source_key || '|' || start_ts || '|' || end_ts || '|' || col_name AS k "
            "FROM basket_items"
        ).fetchall()
    return [r["k"] for r in rows]


def get_basket_count(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM basket_items GROUP BY status"
    ).fetchall()
    result = {"draft": 0, "confirmed": 0, "total": 0}
    for r in rows:
        s = r["status"]
        c = r["cnt"]
        if s in result:
            result[s] = c
        result["total"] += c
    return result


def update_basket_item(conn: sqlite3.Connection, item_id: int,
                       **kwargs) -> bool:
    allowed = {"label", "status", "note"}
    sets, params = [], []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    params.append(item_id)
    cur = conn.execute(
        f"UPDATE basket_items SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return cur.rowcount > 0


def bulk_update_basket(conn: sqlite3.Connection, ids: list[int],
                       label: str | None = None,
                       status: str | None = None) -> int:
    sets, params = [], []
    if label is not None:
        sets.append("label = ?")
        params.append(label)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if not sets or not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    params.extend(ids)
    cur = conn.execute(
        f"UPDATE basket_items SET {', '.join(sets)} WHERE id IN ({placeholders})",
        params,
    )
    conn.commit()
    return cur.rowcount


def get_basket_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM basket_items WHERE id = ?", (item_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_basket_item(conn: sqlite3.Connection, item_id: int) -> bool:
    cur = conn.execute("DELETE FROM basket_items WHERE id = ?", (item_id,))
    conn.commit()
    return cur.rowcount > 0


def bulk_delete_basket(conn: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"DELETE FROM basket_items WHERE id IN ({placeholders})", ids
    )
    conn.commit()
    return cur.rowcount


def bulk_delete_basket_by_keys(conn: sqlite3.Connection, keys: list[str]) -> int:
    if not keys:
        return 0
    total = 0
    for k in keys:
        parts = k.split("|", 3)
        if len(parts) != 4:
            continue
        source_key, start_ts, end_ts, col_name = parts
        cur = conn.execute(
            "DELETE FROM basket_items WHERE source_key=? AND start_ts=? AND end_ts=? AND col_name=?",
            (source_key, start_ts, end_ts, col_name),
        )
        total += cur.rowcount
    conn.commit()
    return total


def _resolve_source_id(conn: sqlite3.Connection,
                       source_key: str, dataset_key: str = "") -> int | None:
    """구간이 속한 소스 하나를 결정. 못 정하면 None.

    source_key 가 실제 소스면 그것. 아니면 dataset_key 로 fallback 하되,
    소스가 여러 개인 데이터셋(관측소별 분리 등)은 어느 소스인지 알 수 없으므로
    None 을 돌려준다 - 여러 소스를 한꺼번에 긁으면 같은 col_name 에 서로 다른
    소스 값이 섞여 그래프가 뒤엉키고 응답도 수만 행으로 불어난다.
    """
    if source_key:
        row = conn.execute(
            "SELECT id FROM sources WHERE key = ?", (source_key,)
        ).fetchone()
        if row:
            return row["id"]
    if dataset_key:
        rows = conn.execute("""
            SELECT s.id FROM sources s JOIN datasets ds ON ds.id = s.dataset_id
            WHERE ds.key = ? LIMIT 2
        """, (dataset_key,)).fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
    return None


def get_all_columns_for_segment(conn: sqlite3.Connection,
                                source_key: str,
                                start_ts: str, end_ts: str,
                                dataset_key: str = "") -> list[dict]:
    """해당 구간의 모든 컬럼 값을 long format으로 반환.
    source_key로 못 찾으면 dataset_key로 fallback.
    Returns: [{timestamp, col_name, value}, ...]"""
    source_id = _resolve_source_id(conn, source_key, dataset_key)
    if source_id is None:
        return []
    rows = conn.execute("""
        SELECT d.timestamp, d.col_name, d.value
        FROM ts_data d
        WHERE d.source_id = ? AND d.timestamp >= ? AND d.timestamp <= ?
              AND d.value IS NOT NULL
        ORDER BY d.timestamp, d.col_name
    """, (source_id, start_ts, end_ts)).fetchall()
    return [{"timestamp": r["timestamp"], "col_name": r["col_name"],
             "value": r["value"]} for r in rows]
