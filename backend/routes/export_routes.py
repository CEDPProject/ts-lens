"""Download search-result CSV with consistent quoting and UTF-8 encoding."""
from __future__ import annotations

import csv
import io
import math

from flask import Blueprint, request, jsonify
from backend.app_config import cfg

bp = Blueprint("export", __name__)


@bp.route("/api/export/<sid>", methods=["POST"])
def export_results(sid):
    body = request.get_json(force=True)
    results = body.get("results", []) if isinstance(body, dict) else None
    if not isinstance(results, list):
        return jsonify(error="results must be an array"), 400
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["rank", "start_idx", "end_idx", "length", "similarity", "distance"])
    for i, row in enumerate(results, 1):
        try:
            values = [row[key] for key in ("start", "end", "length", "similarity", "distance")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return jsonify(error=f"Invalid numeric result at rank {i}"), 400
        writer.writerow([i, *values])
    text = ("\ufeff" if cfg("csv.export_bom") else "") + output.getvalue()
    return text, 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename=pattern_search_results.csv",
    }
