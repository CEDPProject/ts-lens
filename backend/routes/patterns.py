# -*- coding: utf-8 -*-
"""Pattern library routes -- thin HTTP adapter for LabelerAgent."""
from __future__ import annotations

from flask import Blueprint, request, jsonify, Response

from backend.app_config import cfg_search
from backend.agents.labeler import LabelerAgent

bp = Blueprint("patterns", __name__)


@bp.route("/api/patterns", methods=["GET"])
def api_patterns_list():
    dataset_key = request.args.get("dataset_key", None)
    patterns = LabelerAgent.list_patterns(dataset_key)
    return jsonify(patterns=patterns)


@bp.route("/api/patterns", methods=["POST"])
def api_patterns_create():
    body = request.get_json(force=True)
    name = body.get("name", "").strip()
    if not name:
        return jsonify(error="name required"), 400
    pid = LabelerAgent.create_pattern(
        name=name,
        description=body.get("description", ""),
        shape=body.get("shape", []),
        dataset_key=body.get("dataset_key", ""),
        col_name=body.get("col_name", ""),
        method=body.get("method", cfg_search("default_method")),
    )
    return jsonify(ok=True, pattern_id=pid)


@bp.route("/api/patterns/<int:pattern_id>", methods=["GET"])
def api_patterns_get(pattern_id):
    pdef = LabelerAgent.get_pattern(pattern_id)
    if pdef is None:
        return jsonify(error="pattern not found"), 404
    return jsonify(pattern=pdef)


@bp.route("/api/patterns/<int:pattern_id>", methods=["PUT"])
def api_patterns_update(pattern_id):
    body = request.get_json(force=True)
    updates = {}
    for k in ("name", "description", "dataset_key", "col_name", "method", "shape"):
        if k in body:
            updates[k] = body[k]
    ok = LabelerAgent.update_pattern(pattern_id, **updates)
    if not ok:
        return jsonify(error="update failed"), 400
    return jsonify(ok=True)


@bp.route("/api/patterns/<int:pattern_id>", methods=["DELETE"])
def api_patterns_delete(pattern_id):
    ok = LabelerAgent.delete_pattern(pattern_id)
    if not ok:
        return jsonify(error="not found"), 404
    return jsonify(ok=True)


@bp.route("/api/patterns/<int:pattern_id>/instances", methods=["POST"])
def api_patterns_add_instance(pattern_id):
    body = request.get_json(force=True)
    source_key = body.get("source_key", "")
    col_name = body.get("col_name", "")
    start_ts = body.get("start_ts", "")
    end_ts = body.get("end_ts", "")
    if not all([source_key, col_name, start_ts, end_ts]):
        return jsonify(error="source_key, col_name, start_ts, end_ts required"), 400

    status = body.get("status", "accepted")
    if status == "rejected":
        iid = LabelerAgent.reject_result(
            pattern_id, source_key, col_name, start_ts, end_ts,
            body.get("similarity", 0.0))
    else:
        iid = LabelerAgent.accept_result(
            pattern_id, source_key, col_name, start_ts, end_ts,
            body.get("similarity", 0.0))
    return jsonify(ok=True, instance_id=iid)


@bp.route("/api/patterns/instances/<int:instance_id>/status", methods=["PUT"])
def api_patterns_update_instance(instance_id):
    body = request.get_json(force=True)
    status = body.get("status", "")
    if status not in ("accepted", "rejected", "pending"):
        return jsonify(error="status: accepted/rejected/pending"), 400
    ok = LabelerAgent.update_instance_status(instance_id, status)
    if not ok:
        return jsonify(error="not found"), 404
    return jsonify(ok=True)


@bp.route("/api/patterns/<int:pattern_id>/refine", methods=["POST"])
def api_patterns_refine(pattern_id):
    result = LabelerAgent.refine_pattern(pattern_id)
    if "error" in result:
        return jsonify(error=result["error"]), 400
    return jsonify(ok=True, **result)


@bp.route("/api/patterns/export", methods=["GET"])
def api_patterns_export():
    dataset_key = request.args.get("dataset_key", None)
    csv_text = LabelerAgent.export_patterns_csv(dataset_key)
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=pattern_library.csv"},
    )
