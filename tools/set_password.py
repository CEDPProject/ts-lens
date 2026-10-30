"""Add or update a login account in config/local/users.yaml.

    python tools/set_password.py admin --role admin       # prompts for the password
    python tools/set_password.py viewer --role user
    python tools/set_password.py --list

Only the scrypt hash is written; the file is created with mode 600. The app
reloads the file on change — no restart needed. Delete a line from the file
to remove an account; delete the file to turn login off.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.auth import ROLES, users_path  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?", help="계정 이름")
    parser.add_argument("--role", choices=ROLES, default="user")
    parser.add_argument("--list", action="store_true", help="계정 목록만 보기")
    args = parser.parse_args()

    path = users_path()
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = [r for r in (data.get("users") or []) if isinstance(r, dict)]

    if args.list or not args.name:
        if not rows:
            print(f"계정 없음 ({path}) — 로그인은 꺼져 있습니다")
        for r in rows:
            print(f"{r.get('name')}\t{r.get('role')}")
        return

    pw = getpass.getpass(f"'{args.name}' 비밀번호: ")
    if len(pw) < 8:
        sys.exit("비밀번호는 8자 이상이어야 합니다")
    if getpass.getpass("다시 입력: ") != pw:
        sys.exit("두 입력이 다릅니다")

    rows = [r for r in rows if r.get("name") != args.name]
    rows.append({"name": args.name, "role": args.role, "password_hash": generate_password_hash(pw)})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump({"users": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    print(f"저장: {path} ({args.name}, {args.role}) — 계정 {len(rows)}개")


if __name__ == "__main__":
    main()
