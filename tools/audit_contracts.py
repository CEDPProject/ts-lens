#!/usr/bin/env python3
"""Read-only TS-Lens contract audit.

Checks Python/config syntax, KO/EN parity, translation references, local
documentation links and anchors, static frontend API references against Flask
routes, and read-only HTTP rendering for the main pages. It never calls LLMs.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def flatten(value: dict, prefix: str = "") -> set[str]:
    result: set[str] = set()
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        result |= flatten(item, path) if isinstance(item, dict) else {path}
    return result


def heading_anchors(text: str) -> set[str]:
    """Return the common GitHub-style anchors used by local Markdown links."""
    anchors: set[str] = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE):
        plain = re.sub(r"[`*_~]", "", heading).strip().lower()
        plain = re.sub(r"[^\w\-\s가-힣]", "", plain, flags=re.UNICODE)
        anchors.add(re.sub(r"\s+", "-", plain))
    return anchors


def main() -> int:
    errors: list[str] = []

    python_files = sorted(
        path for folder in ("app.py", "backend", "tools", "tests")
        for path in ([ROOT / folder] if (ROOT / folder).is_file() else (ROOT / folder).rglob("*.py"))
    )
    for path in python_files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except Exception as exc:
            errors.append(f"Python compile failed: {path.relative_to(ROOT)}: {exc}")

    ko = json.loads((ROOT / "web/i18n/ko.json").read_text())
    en = json.loads((ROOT / "web/i18n/en.json").read_text())
    ko_keys, en_keys = flatten(ko), flatten(en)
    if ko_keys != en_keys:
        errors.append(f"i18n KO-only={sorted(ko_keys-en_keys)} EN-only={sorted(en_keys-ko_keys)}")

    for path in sorted((ROOT / "config").rglob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            yaml.safe_load(handle)

    referenced: set[str] = set()
    for path in (ROOT / "web/templates").rglob("*.html"):
        text = path.read_text(errors="ignore")
        referenced |= set(re.findall(r"\{\{\s*t\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)", text))
    for path in (ROOT / "web/static/js").rglob("*.js"):
        text = path.read_text(errors="ignore")
        referenced |= set(re.findall(r"(?<![A-Za-z0-9_])(?:window\.)?_t\(\s*['\"]([^'\"]+)", text))
    missing_keys = sorted(key for key in referenced if key not in ko_keys)
    if missing_keys:
        errors.append(f"missing i18n keys={missing_keys}")

    broken_links: list[str] = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        text = path.read_text(errors="ignore")
        for href in re.findall(r"\[[^]]*\]\(([^)]+)\)", text):
            if "://" in href:
                continue
            target_ref, _, anchor = href.partition("#")
            target = (path.parent / target_ref).resolve() if target_ref else path
            if not target.exists():
                broken_links.append(f"{path.relative_to(ROOT)} -> {href}")
            elif anchor and target.suffix.lower() == ".md":
                if anchor not in heading_anchors(target.read_text(errors="ignore")):
                    broken_links.append(f"{path.relative_to(ROOT)} -> missing anchor {href}")
    if broken_links:
        errors.append(f"broken local links={broken_links}")

    js_text = "\n".join(path.read_text(errors="ignore") for path in (ROOT / "web/static/js").rglob("*.js"))
    frontend = set(re.findall(r"(?:fetch|fetchJson)\(\s*['\"]([^'\"]+)", js_text))
    route_text = "\n".join(path.read_text(errors="ignore") for path in (ROOT / "backend/routes").glob("*.py"))
    backend = set(re.findall(r"@(?:bp|\w+)\.route\(\s*['\"]([^'\"]+)", route_text))

    def route_exists(url: str) -> bool:
        clean = url.split("?", 1)[0]
        if "${" in clean:
            return True
        if clean in backend:
            return True
        return any("<" in route and clean.startswith(route.split("<", 1)[0]) for route in backend)

    missing_routes = sorted(url for url in frontend if not route_exists(url))
    if missing_routes:
        errors.append(f"frontend API routes not found={missing_routes}")

    page_status: dict[str, int] = {}
    static_urls: set[str] = set()
    try:
        from app import app
        client = app.test_client()
        for url in ("/", "/finder", "/discovery", "/labeler", "/preprocess", "/data", "/data/models/external", "/data/models/local", "/about"):
            response = client.get(url)
            page_status[url] = response.status_code
            static_urls.update(re.findall(r'(?:src|href)=["\'](/static/[^"\']+)', response.get_data(as_text=True)))
        for url in sorted(static_urls):
            response = client.get(url)
            if response.status_code != 200:
                errors.append(f"static asset HTTP {response.status_code}: {url}")
            response.close()
        bad_pages = {url: code for url, code in page_status.items() if code != 200}
        if bad_pages:
            errors.append(f"main page HTTP failures={bad_pages}")
    except Exception as exc:
        errors.append(f"main page HTTP smoke failed: {exc}")

    if errors:
        print("TS-Lens contract audit: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "TS-Lens contract audit: OK\n"
        f"- Python files compiled in memory: {len(python_files)}\n"
        f"- i18n parity: {len(ko_keys)} keys\n"
        f"- translation references: {len(referenced)}\n"
        f"- YAML files: {len(list((ROOT / 'config').rglob('*.yaml')))}\n"
        f"- frontend static API references: {len(frontend)}\n"
        f"- backend route declarations: {len(backend)}\n"
        f"- main page HTTP 200: {len(page_status)}\n"
        f"- static asset HTTP 200: {len(static_urls)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
