# -*- coding: utf-8 -*-
"""Upload routes -- CSV 미리보기, DB 적재.

읽기·정규화는 backend.csv_ingest 가 맡는다. 여기서는 요청을 받아 넘기고,
무엇을 어떻게 읽었는지(수용 보고서)를 응답과 데이터셋 메타에 남긴다.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
import uuid
from collections import Counter
from pathlib import Path

import pandas as pd
from flask import Blueprint, request, jsonify

from backend.app_config import cfg
from backend.runtime_paths import validate_component, stored_path
from backend.db import DATA_DIR, get_stored_files
from backend.session_store import DATA_STORE
from backend.csv_utils import detect_index_type, guess_time_column, insert_df_data
from backend.csv_ingest import (
    CsvIngestError, IngestReport, read_table, prepare_for_import,
)
from backend.dataset_service import build_dataset_cache
from backend.db import (
    get_db, init_db,
    get_dataset, delete_dataset,
    get_dataset_sources, get_dataset_columns,
    insert_dataset, insert_dataset_column,
    insert_source, save_uploaded_file,
)

bp = Blueprint("upload", __name__)


def _opts(form):
    """사용자가 화면에서 덮어쓴 읽기 선택. 비어 있으면 자동 감지."""
    enc = (form.get("encoding") or "").strip() or None
    sep = form.get("delimiter")
    if sep is not None:
        sep = sep.replace("\\t", "\t")
        sep = sep or None
    return enc, sep


def _ingest_error(e: CsvIngestError, filename: str = ""):
    payload = {"error": (f"[{filename}] " if filename else "") + e.message}
    if e.detail:
        payload["detail"] = e.detail
    return jsonify(**payload), 400


def _interval_label(td: str) -> str:
    try:
        import pandas as pd
        from backend.helpers import format_interval
        return format_interval(pd.Timedelta(td).total_seconds()) if td else ""
    except (ValueError, TypeError):
        return td or ""


def _num_or_none(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


@bp.route("/api/upload", methods=["POST"])
def upload_csv():
    """CSV 미리보기 -- 열 정보와 '어떻게 읽었는지' 를 돌려준다."""
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file provided"), 400

    encoding, delimiter = _opts(request.form)
    try:
        df, report = read_table(f.read(), encoding=encoding, delimiter=delimiter)
    except CsvIngestError as e:
        return _ingest_error(e, f.filename or "")

    min_rows = cfg("csv.min_rows")
    if len(df) < min_rows:
        return jsonify(error=f"행이 {min_rows}개보다 적습니다 (읽은 행: {len(df)})"), 400

    sid = str(uuid.uuid4())[:cfg("csv.session_id_length")]
    DATA_STORE[sid] = {"df": df, "filename": f.filename}

    n_sample = cfg("csv.preview_rows")
    cols = []
    for c in df.columns:
        dtype = "numeric" if pd.api.types.is_numeric_dtype(df[c]) else "text"
        sample = df[c].dropna().head(n_sample).tolist()
        cols.append({"name": c, "dtype": dtype, "sample": [str(s) for s in sample]})

    suggested_time_col, suggested_index_type = guess_time_column(df)

    # 저장 전에 중복을 미리 알려준다 -- 정책을 고를 기회를 주기 위해서다.
    # 시각으로 읽히는 행만 센다. 못 읽는 행은 어차피 저장 때 빠지므로
    # 그것들끼리 같은 문자열이라고 중복으로 세면 헛경보가 된다.
    dup_preview = {}
    if suggested_time_col:
        key = df[suggested_time_col]
        if suggested_index_type == "datetime":
            parsed = pd.to_datetime(key, errors="coerce",
                                    dayfirst=cfg("csv.time.dayfirst"))
            key = parsed.loc[parsed.notna()]
        dupes = key.duplicated(keep=False)
        if dupes.any():
            counts = key.loc[dupes].value_counts()
            dup_preview = {
                "timestamps": int(len(counts)),
                "rows": int(dupes.sum()),
                "examples": [{"timestamp": str(t), "count": int(c)}
                             for t, c in counts.head(cfg("csv.duplicate_report_limit")).items()],
            }

    return jsonify(
        sid=sid,
        filename=f.filename,
        rows=len(df),
        columns=cols,
        suggested_time_col=suggested_time_col,
        suggested_index_type=suggested_index_type,
        read_as=report.as_dict(),
        duplicates=dup_preview,
    )


@bp.route("/api/import", methods=["POST"])
def import_to_db():
    """CSV -> DB 적재 (데이터셋 계층).

    multipart/form-data:
      file: 파일 하나            files: 폴더 업로드(여러 개)
      mode: "single" | "collection" | "merge"
      dataset_key, dataset_name, dataset_desc, time_col,
      columns: JSON ["col1","col2"],
      labels: JSON {"col": ["label","unit"]},
      col_meta: JSON {"col": {"description", "val_min", "val_max"}}  (선택)
      replace: "true"/"false",
      metadata: JSON {},
      encoding, delimiter, duplicate_policy: 자동 감지를 덮어쓰는 선택
    """
    import_mode = request.form.get("mode", "single")
    if import_mode not in ("single", "collection", "merge"):
        return jsonify(error="Invalid import mode"), 400
    encoding, delimiter = _opts(request.form)
    dup_policy = (request.form.get("duplicate_policy") or "").strip() \
        or cfg("csv.duplicate_policy")

    # --- 파일 읽기 ---
    file_dfs = []          # [(filename, df, report)]
    file_raw = []

    if import_mode in ("merge", "collection"):
        files = request.files.getlist("files")
        if not files:
            return jsonify(error="No files provided"), 400
        sources = [(mf.filename, mf.read()) for mf in files]
    else:
        f = request.files.get("file")
        if not f:
            return jsonify(error="No file provided"), 400
        sources = [(f.filename, f.read())]

    for fname, raw in sources:
        file_raw.append((fname, raw))
        try:
            df, report = read_table(raw, encoding=encoding, delimiter=delimiter)
        except CsvIngestError as e:
            return _ingest_error(e, fname)
        file_dfs.append((fname, df, report))

    if not file_dfs:
        return jsonify(error="No files provided"), 400

    # 폴더 업로드는 열 구성이 같아야 이어붙이거나 나란히 놓을 수 있다.
    # 기준은 '첫 파일' 이 아니라 '가장 많은 구성' 이다 -- 브라우저가 보내는
    # 순서는 알 수 없어서, 첫 파일이 하필 이상한 파일이면 정상 파일들을
    # 틀렸다고 지목하게 된다.
    if len(file_dfs) > 1:
        shapes = Counter(tuple(d.columns) for _, d, _ in file_dfs)
        base_cols = set(shapes.most_common(1)[0][0])
        odd = [fn for fn, d, _ in file_dfs if set(d.columns) != base_cols]
        if odd:
            shown = ", ".join(odd[:3])
            more = f" 외 {len(odd) - 3}개" if len(odd) > 3 else ""
            return jsonify(
                error=f"열 구성이 다른 파일이 있습니다: {shown}{more}. "
                      f"나머지 {len(file_dfs) - len(odd)}개는 "
                      f"[{', '.join(sorted(base_cols))}] 입니다.",
                detail={"expected_columns": sorted(base_cols),
                        "files": odd[:10]}), 400

    # 병합하면 파일별 기록이 하나로 접히므로, 접기 전에 따로 챙겨 둔다.
    # 메타의 '어떤 파일을 어떻게 읽었나' 는 이 목록이 단일 소스다.
    read_reports = [(fn, r) for fn, _, r in file_dfs]

    if import_mode == "merge":
        merged = pd.concat([d for _, d, _ in file_dfs], ignore_index=True)
        merged_report = _merge_reports([r for _, _, r in file_dfs])
        file_dfs = [(file_dfs[0][0], merged, merged_report)]

    sample_df = file_dfs[0][1]

    # --- 메타 ---
    dataset_key = request.form.get("dataset_key", "").strip()
    if not dataset_key:
        dataset_key = (Path(file_dfs[0][0]).stem.lower()
                       .replace(" ", "_").replace("-", "_"))
    dataset_name = request.form.get("dataset_name", "").strip() or dataset_key
    dataset_desc = request.form.get("dataset_desc", "").strip()

    time_col = request.form.get("time_col", "").strip()
    if not time_col or time_col not in sample_df.columns:
        return jsonify(error=f"시각 열 '{time_col}' 을 파일에서 찾지 못했습니다",
                       detail={"columns": list(sample_df.columns)}), 400

    index_type = request.form.get("index_type", "").strip()
    if not index_type:
        index_type = detect_index_type(sample_df, time_col)

    try:
        col_list = json.loads(request.form.get("columns", "[]"))
    except (ValueError, TypeError):
        return jsonify(error="columns must be valid JSON"), 400
    if not isinstance(col_list, list) or not all(isinstance(c, str) for c in col_list):
        return jsonify(error="columns must be an array of column names"), 400
    if not col_list:
        col_list = [c for c in sample_df.columns
                    if c != time_col and pd.api.types.is_numeric_dtype(sample_df[c])]
    if not col_list:
        return jsonify(error="가져올 숫자 열이 없습니다"), 400

    try:
        labels_dict = json.loads(request.form.get("labels", "{}"))
        col_meta = json.loads(request.form.get("col_meta", "{}") or "{}")
    except (ValueError, TypeError):
        return jsonify(error="labels must be valid JSON"), 400
    try:
        metadata = json.loads(request.form.get("metadata", "{}"))
    except (ValueError, TypeError):
        return jsonify(error="metadata must be valid JSON"), 400

    replace = request.form.get("replace", "false") == "true"

    try:
        validate_component(dataset_key)
        for fname, _ in file_raw:
            validate_component(fname)
        if len({fname for fname, _ in file_raw}) != len(file_raw):
            raise ValueError("같은 파일 이름이 중복됩니다. 이름을 구분해 주세요.")
        if import_mode == "collection" and len({Path(fname).stem for fname, _ in file_raw}) != len(file_raw):
            raise ValueError("소스 이름이 중복됩니다. 확장자를 제외한 파일 이름을 구분해 주세요.")
        if not isinstance(col_meta, dict):
            raise ValueError("col_meta는 JSON 객체여야 합니다.")
        if not isinstance(metadata, dict) or not isinstance(labels_dict, dict):
            raise ValueError("metadata와 labels는 JSON 객체여야 합니다.")
        if not isinstance(col_list, list) or not all(isinstance(c, str) for c in col_list):
            raise ValueError("columns는 열 이름 배열이어야 합니다.")
        if time_col in col_list or len(set(col_list)) != len(col_list):
            raise ValueError("값 열은 시각 열과 분리하고 중복 없이 선택하세요.")
        if index_type not in ("datetime", "numeric", "text"):
            raise ValueError("Invalid index type")
        if dup_policy not in ("error", "first", "last", "mean", "shift"):
            raise ValueError("Invalid duplicate policy")
    except ValueError as e:
        return jsonify(error=str(e)), 400

    # --- 저장 전에 전부 정리한다: 한 파일이라도 걸리면 DB 를 건드리지 않는다 ---
    prepared = []
    for fname, fdf, report in file_dfs:
        try:
            out = prepare_for_import(fdf, time_col, index_type, col_list,
                                     dup_policy, report)
        except CsvIngestError as e:
            return _ingest_error(e, fname if len(file_dfs) > 1 else "")
        prepared.append((fname, out, report))

    # --- DB ---
    conn = get_db()
    init_db(conn)

    old_files = []
    new_files = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing_ds = get_dataset(conn, dataset_key)
        if existing_ds:
            if replace:
                old_files = get_stored_files(conn, existing_ds["id"])
                delete_dataset(conn, dataset_key, commit=False, delete_files=False,
                               keep_user_items=True)
            else:
                return jsonify(
                    error=f"데이터셋 '{dataset_key}' 가 이미 있습니다. "
                          f"덮어쓰기를 선택하세요."), 409

        ds_type = "collection" if import_mode == "collection" else "single"
        metadata["ingest"] = _ingest_metadata(prepared, read_reports,
                                             import_mode, dup_policy)

        ds_id = insert_dataset(conn, dataset_key, dataset_name, dataset_desc,
                               ds_type, json.dumps(metadata, ensure_ascii=False))
        conn.execute("UPDATE datasets SET index_type = ? WHERE id = ?",
                     (index_type, ds_id))

        for col in col_list:
            if col in labels_dict and isinstance(labels_dict[col], list):
                label = labels_dict[col][0] if labels_dict[col] else col
                unit = labels_dict[col][1] if len(labels_dict[col]) > 1 else ""
            else:
                label, unit = col, ""
            cm = col_meta.get(col) if isinstance(col_meta.get(col), dict) else {}
            insert_dataset_column(conn, ds_id, col, label, unit,
                                  description=str(cm.get("description") or ""),
                                  val_min=_num_or_none(cm.get("val_min")),
                                  val_max=_num_or_none(cm.get("val_max")))

        total_rows = 0
        source_ids = []
        seg_length = 0
        seg_lengths = []

        if import_mode == "collection":
            for fname, fdf, _ in prepared:
                stem = Path(fname).stem
                src_id = insert_source(conn, ds_id, f"{dataset_key}/{stem}",
                                       time_col, description=stem)
                source_ids.append(src_id)
                count = insert_df_data(conn, src_id, fdf, time_col, col_list,
                                       index_type=index_type, commit=False)
                total_rows += count
                seg_lengths.append(count)

            seg_length = seg_lengths[0] if seg_lengths else 0
            all_same = all(sl == seg_length for sl in seg_lengths)
            if not dataset_desc:
                if all_same:
                    auto_desc = (f"{len(prepared)} segments, {seg_length}pt each, "
                                 f"{len(col_list)} columns")
                else:
                    auto_desc = (f"{len(prepared)} segments, "
                                 f"{min(seg_lengths)}~{max(seg_lengths)}pt, "
                                 f"{len(col_list)} columns")
                conn.execute("UPDATE datasets SET description = ? WHERE id = ?",
                             (auto_desc, ds_id))
        else:
            fname, fdf, _ = prepared[0]
            src_id = insert_source(conn, ds_id, dataset_key, time_col)
            source_ids.append(src_id)
            total_rows = insert_df_data(conn, src_id, fdf, time_col, col_list,
                                        index_type=index_type, commit=False)

        for fname, raw_bytes in file_raw:
            new_files.append(save_uploaded_file(conn, ds_id, dataset_key, fname, raw_bytes))

        conn.commit()
    except Exception:
        conn.rollback()
        for rel_path in new_files:
            try:
                stored_path(DATA_DIR, rel_path).unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("Failed to clean rolled-back upload")
        raise
    finally:
        conn.close()

    # Old bytes are removed only after the replacement has committed.
    for item in old_files:
        try:
            stored_path(DATA_DIR, item["rel_path"]).unlink(missing_ok=True)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Failed to clean replaced original")
    conn = get_db()
    try:
        sources_rows = get_dataset_sources(conn, ds_id)
        cols = get_dataset_columns(conn, ds_id)
        build_dataset_cache(conn, ds_id, sources_rows, cols)
    except Exception:
        logging.getLogger(__name__).exception("Import saved; cache will be rebuilt on load")
    finally:
        conn.close()

    reports = [r for _, _, r in prepared]
    resp = {
        "dataset_key": dataset_key,
        "dataset_type": ds_type,
        "index_type": index_type,
        "rows_imported": total_rows,
        "source_count": len(source_ids),
        "columns": col_list,
        "ingest": metadata["ingest"],
        "warnings": sorted({w for r in reports for w in r.warnings}),
    }
    if ds_type == "collection":
        resp["seg_length"] = seg_length
    return jsonify(**resp)


def _merge_reports(reports: list) -> IngestReport:
    """여러 파일을 이어붙일 때, 읽기 조건이 갈리면 그 사실을 남긴다."""
    base = deepcopy(reports[0])
    encs = {r.encoding for r in reports}
    seps = {r.delimiter for r in reports}
    if len(encs) > 1:
        base.warn(f"파일마다 인코딩이 달랐습니다: {', '.join(sorted(encs))}")
    if len(seps) > 1:
        base.warn(f"파일마다 구분자가 달랐습니다: {len(seps)}종")
    for r in reports[1:]:
        for w in r.warnings:
            base.warn(w)
        base.numeric_recovered = sorted(set(base.numeric_recovered) | set(r.numeric_recovered))
    return base


def _ingest_metadata(prepared: list, read_reports: list,
                     import_mode: str, dup_policy: str) -> dict:
    """데이터셋 메타에 남길 '어떻게 읽었는가'. 나중에 재현·감사에 쓴다.

    prepared 는 저장 단위(병합이면 1개), read_reports 는 원본 파일 단위다.
    인코딩·구분자는 원본 파일 단위로 남겨야 병합해도 사실이 보존된다.
    """
    reports = [r for _, _, r in prepared]
    files = []
    for fname, r in read_reports:
        d = r.as_dict()
        d["filename"] = fname
        files.append(d)
    return {
        "mode": import_mode,
        "duplicate_policy": dup_policy,
        "file_count": len(read_reports),
        "rows_read": sum(r.rows_read for _, r in read_reports),
        "rows_stored": sum(r.rows_stored for r in reports),
        "rows_dropped_bad_time": sum(r.rows_dropped_bad_time for r in reports),
        "duplicates_found": sum(r.duplicates_found for r in reports),
        "values_coerced": sum(r.values_coerced for r in reports),
        "encodings": sorted({r.encoding for _, r in read_reports}),
        "delimiters": sorted({r.as_dict()["delimiter"] for _, r in read_reports}),
        "time_col": reports[0].time_col,
        "index_type": reports[0].index_type,
        "time_format": reports[0].time_format,
        "index_offset": reports[0].index_offset,
        "time_start": min((r.time_start for r in reports if r.time_start), default=""),
        "time_end": max((r.time_end for r in reports if r.time_end), default=""),
        "sampling_interval": reports[0].sampling_interval,
        # 화면용 — Timedelta 문자열("0 days 01:00:00")은 읽기 어렵다
        "sampling_interval_label": _interval_label(reports[0].sampling_interval),
        "files": files,
    }
