"""Regression contracts for Basket provenance and multi-column similarity."""

import sqlite3
import unittest

import numpy as np

from app import create_app
from backend.db import add_basket_items, get_basket_items, init_db
from backend.helpers import compute_distance, distance_to_similarity, z_normalize
from backend.routes.basket_routes import _compact_rows
from backend.segment_search import match_segments, segment_search_multi_source
from backend.session_store import DATA_STORE


class BasketSimilarityContracts(unittest.TestCase):
    def test_duplicate_capture_preserves_curation(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        item = {
            "dataset_key": "dataset", "source_key": "source", "col_name": "a",
            "start_ts": "2026-01-01", "end_ts": "2026-01-02",
            "point_count": 3, "values": [1, 2, 3],
            "series": [
                {"col_name": "a", "values": [1, 2, 3]},
                {"col_name": "b", "values": [3, 2, 1]},
            ],
            "columns": ["a", "b"], "similarity": 0.8,
            "distance": 0.25, "search_method": "euclidean", "normalize": True,
        }
        add_basket_items(conn, [item])
        basket_id = conn.execute("SELECT id FROM basket_items").fetchone()["id"]
        conn.execute(
            "UPDATE basket_items SET status='confirmed', label='keep', note='memo' WHERE id=?",
            (basket_id,),
        )
        conn.commit()

        item["similarity"] = 0.9
        result = add_basket_items(conn, [item])
        row = conn.execute(
            "SELECT id, status, label, note, similarity FROM basket_items"
        ).fetchone()
        parsed = get_basket_items(conn, include_values=True)[0]

        self.assertEqual(result["updated"], 1)
        self.assertEqual(row["id"], basket_id)
        self.assertEqual((row["status"], row["label"], row["note"]),
                         ("confirmed", "keep", "memo"))
        self.assertEqual(row["similarity"], 0.9)
        self.assertEqual([s["col_name"] for s in parsed["series"]], ["a", "b"])

    def test_identical_flat_cosine_is_full_similarity(self):
        flat = z_normalize(np.array([5.0, 5.0, 5.0]))
        distance = compute_distance(flat, flat, "cosine")
        self.assertEqual(distance_to_similarity(distance, "cosine"), 1.0)

    def test_compact_chart_preserves_narrow_extrema(self):
        values = [0.0] * 1000
        values[411] = 99.0
        values[612] = -77.0
        rows = [{"timestamp": i, "col_name": "a", "value": value}
                for i, value in enumerate(values)]
        compact = _compact_rows(rows, max_points=100)["series"][0]
        self.assertLessEqual(len(compact["values"]), 100)
        self.assertEqual(compact["values"][0], 0.0)
        self.assertEqual(compact["values"][-1], 0.0)
        self.assertIn(99.0, compact["values"])
        self.assertIn(-77.0, compact["values"])

    def test_overlay_segments_match_source_not_repeated_label(self):
        segments = [
            {"source_key": "A", "key": "day", "label": "same", "values": [0, 1, 2]},
            {"source_key": "B", "key": "day", "label": "same", "values": [0, 1, 2]},
        ]
        # Deliberately reverse overlay order. Index-based matching would swap
        # these sources and incorrectly rank B first.
        overlays = [[
            {"source_key": "B", "key": "day", "label": "same", "values": [2, 1, 0]},
            {"source_key": "A", "key": "day", "label": "same", "values": [0, 1, 2]},
        ]]
        query = z_normalize(np.array([0.0, 1.0, 2.0]))
        results = match_segments(
            segments, query, normalize=True, overlay_segments=overlays,
            overlay_query_shapes=[query], top_k=2,
        )
        self.assertEqual(results[0]["source_key"], "A")
        self.assertEqual(results[0]["overlay_values"], [[0.0, 1.0, 2.0]])

    def test_missing_overlay_source_is_reported(self):
        primary = [
            {"source_key": "A", "timestamps": ["0", "1", "2"], "values": [0, 1, 2]},
            {"source_key": "B", "timestamps": ["0", "1", "2"], "values": [0, 1, 2]},
        ]
        overlay = [[
            {"source_key": "A", "timestamps": ["0", "1", "2"], "values": [0, 1, 2]},
        ]]
        query = z_normalize(np.array([0.0, 1.0, 2.0]))
        result = segment_search_multi_source(
            primary, query, pre_segmented=True,
            overlay_sources_list=overlay, overlay_query_shapes=[query],
        )
        self.assertEqual(result["segments_excluded_missing_overlay"], 1)
        self.assertEqual({row["source_key"] for row in result["results"]}, {"A"})


class SearchRouteContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def test_inline_classify_returns_per_column_tabs_and_missing_list(self):
        segments = []
        for i in range(3):
            values = list(range(24))
            segments.append({
                "key": f"s{i}", "source_key": "src",
                "timestamps": [str(j) for j in range(24)], "values": values,
                "overlays": [{"col_name": "b", "values": values[::-1]}],
            })
        response = self.client.post("/api/segment_search/classify", json={
            "dataset_key": "inline", "col_name": "a", "unit": "day",
            "method": "euclidean", "overlay_cols": ["b", "missing"],
            "inline_segments": segments,
        })
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["columns_classified"], ["a", "b"])
        self.assertEqual(data["missing_columns"], ["missing"])
        self.assertIn("b", data["column_results"])

    def test_invalid_numeric_search_option_returns_400(self):
        sid = "invalid-numeric-contract"
        DATA_STORE[sid] = {
            "values": np.arange(20, dtype=float),
            "labels": [str(i) for i in range(20)], "overlays": [],
        }
        try:
            response = self.client.post(f"/api/search/{sid}", json={
                "query_start": 0, "query_end": 4, "top_k": "many",
            })
            self.assertEqual(response.status_code, 400)
        finally:
            DATA_STORE.pop(sid, None)

    def test_bulk_data_rejects_non_array_and_non_integer_ids(self):
        self.assertEqual(self.client.post(
            "/api/basket/bulk-data", json={"ids": "1,2"}).status_code, 400)
        self.assertEqual(self.client.post(
            "/api/basket/bulk-data", json={"ids": ["bad"]}).status_code, 400)

    def test_basket_column_discovery_is_metadata_only_contract(self):
        response = self.client.get("/api/basket/columns")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.get_json().get("columns"), list)


if __name__ == "__main__":
    unittest.main()
