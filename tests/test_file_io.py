"""Temporary-store acceptance tests: originals, transactions and file boundaries."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from backend import db
from backend.routes import upload, datasets
from backend.runtime_paths import stored_path
from backend.csv_ingest import read_table

CSV = b"time,value\n" + b"".join(f"2026-01-{i:02d},{i}\n".encode() for i in range(1, 13))


class FileIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "data.db"
        for module in (db, upload, datasets):
            patcher = patch.object(module, "DATA_DIR", self.root / "files")
            patcher.start()
            self.addCleanup(patcher.stop)
        def connect():
            return db.get_db(self.path)
        for module in (upload, datasets):
            patcher = patch.object(module, "get_db", connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def post(self, raw=CSV, filename="한글.csv", **fields):
        return self.client.post("/api/import", data={
            "file": (io.BytesIO(raw), filename), "dataset_key": "sample",
            "time_col": "time", "columns": '["value"]', **fields},
            content_type="multipart/form-data")

    def test_original_round_trip_and_conflict(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["rows_imported"], 12)
        response = self.client.get("/api/db/datasets/sample/files/한글.csv/download")
        self.assertEqual(response.data, CSV)
        response.close()
        self.assertEqual(self.post().status_code, 409)

    def test_failed_replacement_preserves_database_and_original(self):
        self.assertEqual(self.post().status_code, 200)
        with patch.object(upload, "save_uploaded_file", side_effect=OSError("disk full")):
            self.assertEqual(self.post(replace="true").status_code, 500)
        conn = db.get_db(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT count(*) FROM ts_data").fetchone()[0], 12)
        rows = db.get_stored_files(conn, db.get_dataset(conn, "sample")["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(stored_path(db.DATA_DIR, rows[0]["rel_path"]).read_bytes(), CSV)

    def test_successful_replacement_removes_only_old_original(self):
        self.assertEqual(self.post().status_code, 200)
        old = list(db.DATA_DIR.rglob("*.csv"))
        replacement = CSV.replace(b",12", b",99")
        self.assertEqual(self.post(raw=replacement, replace="true").status_code, 200)
        self.assertFalse(old[0].exists())
        self.assertEqual(len(list(db.DATA_DIR.rglob("*.csv"))), 1)
        response = self.client.get("/api/db/datasets/sample/files/한글.csv/download")
        self.assertEqual(response.data, replacement)
        response.close()

    def test_traversal_and_unregistered_download_are_rejected(self):
        for fields in ({"dataset_key": "../escape"}, {"filename": "../escape.csv"}):
            self.assertEqual(self.post(**fields).status_code, 400)
        db.DATA_DIR.mkdir(exist_ok=True)
        (db.DATA_DIR / "secret.csv").write_bytes(b"not registered")
        self.assertEqual(self.client.get("/api/db/datasets/sample/files/../secret.csv/download").status_code, 404)
        (db.DATA_DIR / "escape").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            stored_path(db.DATA_DIR, "escape/outside.csv")

    def test_input_validation(self):
        for fields in ({"mode": "bad"}, {"metadata": "[]"}, {"columns": '"value"'},
                       {"duplicate_policy": "bad"}, {"index_type": "bad"}):
            self.assertEqual(self.post(**fields).status_code, 400, fields)

    def test_export_numeric_contract_and_bom(self):
        response = self.client.post("/api/export/test", json={"results": [
            dict(start=0, end=12, length=12, similarity=.9, distance=.1)]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.client.post("/api/export/test", json={"results": [{}]}).status_code, 400)

    def test_encoding_fixtures(self):
        root = Path(__file__).resolve().parents[1] / "tests/fixtures/single"
        for name in ("01_basic_utf8.csv", "02_korean_cp949.csv", "03_utf8_bom.csv", "04_semicolon.csv", "05_tab.tsv", "14_utf16.csv", "19_decimal_comma.csv"):
            with self.subTest(name=name):
                frame, report = read_table((root / name).read_bytes())
                self.assertGreaterEqual(len(frame), 10)
                self.assertGreaterEqual(len(frame.columns), 2)
                self.assertTrue(report.encoding)

    def test_failed_second_original_leaves_no_partial_collection(self):
        save = upload.save_uploaded_file
        calls = 0
        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second file failed")
            return save(*args)
        with patch.object(upload, "save_uploaded_file", side_effect=fail_second):
            response = self.client.post("/api/import", data={
                "mode": "collection", "dataset_key": "batch", "time_col": "time",
                "columns": '["value"]', "files": [(io.BytesIO(CSV), "a.csv"), (io.BytesIO(CSV), "b.csv")]})
        self.assertEqual(response.status_code, 500)
        conn = db.get_db(self.path)
        try:
            self.assertEqual(db.get_all_datasets(conn), [])
        finally:
            conn.close()
        self.assertEqual(list(db.DATA_DIR.rglob("*.csv")), [])

    def test_merge_reports_keep_original_row_counts(self):
        response = self.client.post("/api/import", data={
            "mode": "merge", "dataset_key": "merge", "time_col": "time",
            "columns": '["value"]', "duplicate_policy": "first",
            "files": [(io.BytesIO(CSV), "a.csv"), (io.BytesIO(CSV), "b.csv")]})
        self.assertEqual(response.status_code, 200, response.json)
        ingest = response.json["ingest"]
        self.assertEqual(ingest["rows_read"], 24)
        self.assertEqual(ingest["rows_stored"], 12)
        self.assertEqual(len(ingest["files"]), 2)

    def test_two_explicit_databases_get_schemas(self):
        for name in ("one.db", "two.db"):
            conn = db.get_db(self.root / name)
            try:
                self.assertEqual(db.get_all_datasets(conn), [])
            finally:
                conn.close()

    def test_yaml_write_failure_preserves_file_and_cache(self):
        from backend import app_config
        path = self.root / "model.yaml"
        path.write_text("old: true\n")
        with patch.object(app_config.os, "replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                app_config._write_yaml(path, {"new": True})
        self.assertEqual(path.read_text(), "old: true\n")
        self.assertEqual(list(self.root.glob(".model.yaml.*")), [])


if __name__ == "__main__":
    unittest.main()
