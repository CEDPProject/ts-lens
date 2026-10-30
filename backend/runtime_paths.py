"""Deployment paths. Relative environment paths are anchored to the app directory."""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def runtime_path(name: str, default: Path) -> Path:
    value = Path(os.environ.get(name) or default).expanduser()
    return (value if value.is_absolute() else APP_DIR / value).resolve()


DB_PATH = runtime_path("TS_PATTERN_DB_PATH", APP_DIR / "data.db")
DATA_DIR = runtime_path("TS_PATTERN_DATA_DIR", APP_DIR.parent / "data")


def stored_path(root: Path, relative: str) -> Path:
    """Reject absolute paths, traversal and symlink escapes before file I/O."""
    rel = Path(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts or "\\" in relative:
        raise ValueError("Invalid stored file path")
    base = root.resolve()
    target = (base / rel).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError("Stored file path escapes data directory")
    return target


def validate_component(value: str) -> str:
    """Allow Unicode names, but never directory or control characters."""
    if not value or value in (".", "..") or any(c in value for c in "/\\") or any(ord(c) < 32 for c in value):
        raise ValueError("데이터셋 키와 파일 이름에는 경로나 제어문자를 사용할 수 없습니다.")
    return value
