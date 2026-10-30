"""Minimal login: accounts live in a server-side YAML file, roles are admin/user.

There is no account-management UI on purpose. The operator writes
`config/local/users.yaml` (git-ignored, mode 600) with `tools/set_password.py`,
which stores scrypt hashes — never plain passwords.

    users:
      - name: admin
        role: admin
        password_hash: "scrypt:32768:8:1$..."

**When the file is missing or empty the app runs open, exactly as before.** This
keeps single-user installs and the test suite unchanged; login is enforced the
moment the first account exists.

Roles: `admin` may do everything. `user` may explore, search, describe and
curate the basket, but not change data or server settings — the admin-only
paths are listed in ADMIN_RULES (method + path pattern), so route modules stay
untouched.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

import yaml
from flask import jsonify, redirect, request, session
from werkzeug.security import check_password_hash

from backend.app_config import cfg
from backend.runtime_paths import APP_DIR

logger = logging.getLogger(__name__)

ROLES = ("admin", "user")

# 로그인 없이 열어 두는 경로 — 로그인 화면 자체와 정적 자원뿐이다
PUBLIC_PATHS = (
    re.compile(r"^/login$"),
    re.compile(r"^/api/login$"),
    re.compile(r"^/api/logout$"),
    re.compile(r"^/api/auth/me$"),
    re.compile(r"^/static/"),
    re.compile(r"^/set_lang/"),
    re.compile(r"^/favicon\.ico$"),
    re.compile(r"^/apple-touch-icon\.png$"),
)

# 관리자만 — 데이터와 서버 설정을 바꾸는 요청. 탐색·검색·설명·바구니는 사용자도 된다
ADMIN_RULES = (
    ("*", re.compile(r"^/data/models(?:/|$)")),
    ("DELETE", re.compile(r"^/api/db/")),
    ("POST", re.compile(r"^/api/(upload|import|import_labels)$")),
    ("POST", re.compile(r"^/api/benchmark/")),
    ("POST", re.compile(r"^/api/preprocess/(execute|delete)$")),
    ("POST", re.compile(r"^/api/column_limits/")),
    ("POST", re.compile(r"^/api/db/datasets/[^/]+/refresh_cache$")),
    ("POST", re.compile(r"^/api/llm_models/activate$")),
    ("POST", re.compile(r"^/api/presets/(reset|builtin/)")),
    ("DELETE", re.compile(r"^/api/presets/")),
    ("*", re.compile(r"^/api/external_llm")),
)


def users_path() -> Path:
    value = os.environ.get("TS_PATTERN_USERS_FILE") or cfg("auth.users_file")
    path = Path(value).expanduser()
    return path if path.is_absolute() else APP_DIR / path


_cache: dict = {"mtime": None, "path": None, "users": {}}
_lock = threading.Lock()


def load_users() -> dict[str, dict]:
    """name -> {role, password_hash}. Re-read when the file changes; empty when absent."""
    path = users_path()
    with _lock:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            _cache.update(mtime=None, path=path, users={})
            return {}
        if _cache["mtime"] == mtime and _cache["path"] == path:
            return _cache["users"]
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.error("users file unreadable (%s): %s — 로그인 불가 상태로 둡니다", path, e)
            data = {}
        users = {}
        for row in (data.get("users") or []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            role = str(row.get("role") or "user").strip().lower()
            pw = str(row.get("password_hash") or "").strip()
            if not name or role not in ROLES or not pw:
                logger.warning("users file: skipping malformed account %r", row.get("name"))
                continue
            users[name] = {"role": role, "password_hash": pw}
        _cache.update(mtime=mtime, path=path, users=users)
        return users


def auth_enabled() -> bool:
    return bool(load_users())


def current_user() -> dict | None:
    """Logged-in account, or None. Open mode reports an implicit admin so templates
    can hide nothing — the app behaves exactly as it did without login."""
    users = load_users()
    if not users:
        return {"name": "", "role": "admin", "open_mode": True}
    name = session.get("user")
    if name and name in users:
        return {"name": name, "role": users[name]["role"], "open_mode": False}
    return None


def verify(name: str, password: str) -> dict | None:
    users = load_users()
    row = users.get(name)
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    return {"name": name, "role": row["role"]}


def _is_public(path: str) -> bool:
    return any(p.match(path) for p in PUBLIC_PATHS)


def _needs_admin(method: str, path: str) -> bool:
    return any((m == "*" or m == method) and p.match(path) for m, p in ADMIN_RULES)


def guard():
    """before_request hook. Returns a response to short-circuit, or None."""
    if not auth_enabled() or _is_public(request.path):
        return None
    user = current_user()
    if user is None:
        if request.path.startswith("/api/"):
            return jsonify(error="로그인이 필요합니다", login_required=True), 401
        nxt = request.full_path if request.query_string else request.path
        return redirect(f"/login?next={nxt}")
    if user["role"] != "admin" and _needs_admin(request.method, request.path):
        return jsonify(error="관리자만 할 수 있는 작업입니다", admin_required=True), 403
    return None


def login(name: str, password: str) -> dict | None:
    user = verify(name, password)
    if user is None:
        time.sleep(0.4)  # 무차별 대입을 조금 느리게
        return None
    session.clear()
    session["user"] = user["name"]
    session.permanent = True
    return user


def logout():
    session.clear()
