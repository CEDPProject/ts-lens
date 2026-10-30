# -*- coding: utf-8 -*-
"""Custom preset management routes."""
from __future__ import annotations

import logging
from datetime import datetime
from flask import Blueprint, request, jsonify

from backend.app_config import (
    cfg_custom_presets, save_custom_preset, delete_custom_preset,
    cfg_preset_catalog, load_hidden_presets, hide_preset, unhide_preset,
)

bp = Blueprint("presets", __name__)
logger = logging.getLogger(__name__)


@bp.errorhandler(ValueError)
def invalid_preset(error):
    return jsonify(error=str(error)), 400



@bp.route("/api/presets", methods=["GET"])
def list_presets():
    """List all presets (built-in + custom for current dataset)."""
    dataset_id = request.args.get("dataset_id", "")
    builtin = cfg_preset_catalog()
    custom = cfg_custom_presets(dataset_id) if dataset_id else {}
    hidden = load_hidden_presets(dataset_id) if dataset_id else set()

    result = {
        "builtin": {
            name: {
                "label": info.get("label", name),
                "icon": info.get("icon", "show_chart"),
                "deletable": True,
                "hidden": name in hidden,
            }
            for name, info in builtin.items()
        },
        "custom": {
            name: {
                "label": info.get("label", name),
                "icon": info.get("icon", "auto_graph"),
                "source": info.get("source", "manual"),
                "created": info.get("created", ""),
                "values": info.get("values", []),
                "metadata": info.get("metadata", {}),
                "deletable": True,
            }
            for name, info in custom.items()
        },
        "hidden_count": len(hidden),
    }
    return jsonify(result)


@bp.route("/api/presets/custom", methods=["POST"])
def add_custom_preset():
    """Add or overwrite a custom preset from cluster centroid."""
    body = request.get_json(force=True)

    if not isinstance(body.get("dataset_id", ""), str) or not isinstance(body.get("name", ""), str):
        return jsonify(error="dataset_id and name must be strings"), 400
    dataset_id = body.get("dataset_id", "").strip()
    if not dataset_id:
        return jsonify(error="dataset_id is required"), 400

    name = body.get("name", "").strip()
    if not name:
        return jsonify(error="Name is required"), 400

    # Sanitize name for use as key
    key = name.lower().replace(" ", "_").replace("-", "_")
    key = "".join(c for c in key if c.isalnum() or c == "_")

    if not key:
        return jsonify(error="Name must contain letters or numbers"), 400

    # Build preset data
    values = body.get("values", [])
    if not isinstance(values, list) or len(values) < 3:
        return jsonify(error="Values must have at least 3 points"), 400

    # 변화가 없는 도형은 모양 정보가 없다. 저장해 두면 평평한 구간마다
    # 내장 flat 과 거리 0 으로 동점이 되고, 그 밖에는 아무것도 맞히지 못한다.
    # (실제로 값이 전부 0 인 프리셋이 저장돼 분류에서 0건을 기록했다.)
    try:
        import numpy as _np
        _arr = _np.asarray(values, dtype=float)
        if _arr.ndim != 1 or any(isinstance(v, bool) for v in values) or not _np.isfinite(_arr).all():
            return jsonify(error="Values must be finite numbers"), 400
        if float(_arr.max() - _arr.min()) < 1e-9:
            return jsonify(
                error="This shape is flat — every point has the same value, "
                      "so it carries no shape information. "
                      "Use the built-in 'Flat' preset instead."), 400
    except (TypeError, ValueError):
        return jsonify(error="Values must be numbers"), 400

    if not isinstance(body.get("metadata", {}), dict):
        return jsonify(error="metadata must be an object"), 400
    values = _arr.tolist()

    # Check if overwriting
    existing = cfg_custom_presets(dataset_id)
    is_overwrite = key in existing

    preset_data = {
        "label": name,
        "icon": body.get("icon", "auto_graph"),
        "source": body.get("source", "cluster"),
        "created": datetime.now().isoformat(),
        "values": values,
        "metadata": body.get("metadata", {}),
    }

    save_custom_preset(key, preset_data, dataset_id)
    action = "overwritten" if is_overwrite else "saved"
    logger.info(f"Custom preset {action}: {key} for dataset {dataset_id}")

    # 목록·검색 계층은 사용자 프리셋을 "custom_" 접두어로 부른다(segment_search.py).
    # 저장은 접두어 없는 키로 하되, 응답은 화면에서 쓰는 키를 그대로 돌려줘야
    # 호출한 쪽이 방금 만든 프리셋을 바로 집을 수 있다.
    return jsonify(
        ok=True,
        key="custom_" + key,
        storage_key=key,
        preset=preset_data,
        overwritten=is_overwrite,
    )


@bp.route("/api/presets/custom/<key>", methods=["DELETE"])
def remove_custom_preset(key: str):
    """Delete a custom preset."""
    dataset_id = request.args.get("dataset_id", "")
    if not dataset_id:
        return jsonify(error="dataset_id is required"), 400

    # Cannot delete built-in presets
    builtin = cfg_preset_catalog()
    if key in builtin:
        return jsonify(error="Cannot delete built-in preset"), 403

    if delete_custom_preset(key, dataset_id):
        logger.info(f"Custom preset deleted: {key} from dataset {dataset_id}")
        return jsonify(ok=True)
    else:
        return jsonify(error="Preset not found"), 404


@bp.route("/api/presets/builtin/<key>/hide", methods=["POST"])
def hide_builtin(key: str):
    """Hide a built-in preset for a dataset."""
    dataset_id = request.args.get("dataset_id", "")
    if not dataset_id:
        return jsonify(error="dataset_id is required"), 400

    builtin = cfg_preset_catalog()
    if key not in builtin:
        return jsonify(error="Unknown preset"), 404

    hide_preset(key, dataset_id)
    logger.info(f"Preset hidden: {key} for dataset {dataset_id}")
    return jsonify(ok=True)


@bp.route("/api/presets/builtin/<key>/unhide", methods=["POST"])
def unhide_builtin(key: str):
    """Unhide a built-in preset for a dataset."""
    dataset_id = request.args.get("dataset_id", "")
    if not dataset_id:
        return jsonify(error="dataset_id is required"), 400

    unhide_preset(key, dataset_id)
    logger.info(f"Preset unhidden: {key} for dataset {dataset_id}")
    return jsonify(ok=True)


@bp.route("/api/presets/hidden", methods=["GET"])
def list_hidden():
    """List hidden preset names for a dataset."""
    dataset_id = request.args.get("dataset_id", "")
    if not dataset_id:
        return jsonify(hidden=[])

    hidden = load_hidden_presets(dataset_id)
    return jsonify(hidden=list(hidden))


@bp.route("/api/presets/reset", methods=["POST"])
def reset_all_presets():
    """Reset presets to default: delete all custom and show every built-in.

    기본값이 "전부 숨김"이던 시절에는 여기서 hide_preset 을 돌렸는데, 그러면
    도형 목록이 비고 분류가 100% Unclassified 로 떨어졌다. 기본값과 같게 맞춘다.
    """
    dataset_id = request.args.get("dataset_id", "")
    if not dataset_id:
        return jsonify(error="dataset_id is required"), 400

    # 1. Delete all custom presets
    custom = cfg_custom_presets(dataset_id)
    custom_count = len(custom)
    for key in list(custom.keys()):
        delete_custom_preset(key, dataset_id)

    # 2. 내장 도형을 모두 되살린다 (기본값 = 전부 보임)
    catalog_keys = list(cfg_preset_catalog().keys())
    hidden = load_hidden_presets(dataset_id)
    restored = len([k for k in catalog_keys if k in hidden])
    for key in catalog_keys:
        unhide_preset(key, dataset_id)

    logger.info(f"Presets reset for {dataset_id}: {custom_count} custom deleted, "
                f"{restored} built-in restored")
    return jsonify(ok=True, custom_deleted=custom_count, restored=restored)
