import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from werkzeug.security import generate_password_hash

from app import create_app
from backend import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users = Path(self.tmp.name) / "users.yaml"
        self._env = patch.dict(os.environ, {"TS_PATTERN_USERS_FILE": str(self.users)})
        self._env.start()
        auth._cache.update(mtime=None, path=None, users={})
        self.client = create_app().test_client()

    def tearDown(self):
        self._env.stop(); self.tmp.cleanup(); auth._cache.update(mtime=None, path=None, users={})

    def _write(self, rows):
        self.users.write_text(yaml.safe_dump({"users": rows}), encoding="utf-8")

    def _login(self, name, pw):
        return self.client.post("/api/login", json={"username": name, "password": pw})

    def test_open_mode_without_users_file(self):
        self.assertEqual(self.client.get("/api/auth/me").json["enabled"], False)
        self.assertEqual(self.client.get("/data").status_code, 200)
        self.assertEqual(self.client.get("/login").status_code, 302)  # 켜져 있지 않으면 홈으로

    def test_login_required_and_roles(self):
        self._write([{"name": "boss", "role": "admin", "password_hash": generate_password_hash("secret-123")},
                     {"name": "guest", "role": "user", "password_hash": generate_password_hash("guest-123")}])
        r = self.client.get("/data"); self.assertEqual(r.status_code, 302); self.assertIn("/login?next=/data", r.headers["Location"])
        self.assertEqual(self.client.get("/api/db/datasets").status_code, 401)
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self._login("boss", "wrong").status_code, 401)
        for path in ('/data/models/external', '/data/models/local'):
            self.assertEqual(self.client.get(path).status_code, 302)
        # 사용자: 읽기·검색은 되고, 데이터 변경은 403
        self.assertEqual(self._login("guest", "guest-123").status_code, 200)
        self.assertEqual(self.client.get("/api/db/datasets").status_code, 200)
        self.assertEqual(self.client.delete("/api/db/datasets/x?confirm=x").status_code, 403)
        self.assertEqual(self.client.post("/api/import", data={}).status_code, 403)
        self.assertEqual(self.client.get("/api/auth/me").json["user"]["role"], "user")
        for path in ('/data/models/external', '/data/models/local'):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/db/datasets").status_code, 401)
        # 관리자: 데이터 변경 경로 통과(없는 키라 404 — 권한 단계는 지난 것)
        self.assertEqual(self._login("boss", "secret-123").status_code, 200)
        self.assertEqual(self.client.delete("/api/db/datasets/x?confirm=x").status_code, 404)
        for path in ('/data/models/external', '/data/models/local'):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_malformed_rows_are_skipped(self):
        self._write([{"name": "bad", "role": "root", "password_hash": "x"}])
        self.assertFalse(auth.auth_enabled())


if __name__ == "__main__":
    unittest.main()
