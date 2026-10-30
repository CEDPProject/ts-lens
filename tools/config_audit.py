#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""설정 점검 — yaml 과 코드가 어긋나는 곳을 한 번에 보여 준다.

  python3 tools/config_audit.py            사람이 읽는 보고
  python3 tools/config_audit.py --strict    문제가 있으면 종료코드 1 (CI 용)

검사하는 것
  1) 코드가 읽는데 yaml 에 없는 키          → 실행 중 None 이 된다
  2) yaml 에 있는데 아무도 읽지 않는 키      → 지워도 되는 설정
  3) 호출부·감싼 함수에 남은 하드코딩 기본값 → yaml 과 갈라진다
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# yaml 파일 -> 그 파일을 읽는 cfg 함수 이름
YAMLS = {
    "config/app.yaml": "cfg",
    "config/search.yaml": "cfg_search",
    "config/prompts.yaml": "cfg_prompts",
    "config/model/text_ts.yaml": "cfg_text_ts",
    "config/model/foundation.yaml": "cfg_foundation",
    "config/model/lexicon_ko.yaml": "cfg_lexicon_ko",
}

# 파일을 통째로 읽는 helper -> (yaml, 그 아래 전부 사용으로 본다)
WHOLE_READERS = {
    "cfg_time_units": ("config/app.yaml", "time_units"),
    "cfg_preset_catalog": ("config/app.yaml", "preset_shapes.catalog"),
    "cfg_clustering": ("config/app.yaml", "clustering"),
    "cfg_all_presets": ("config/app.yaml", "preset_shapes.catalog"),
    "cfg_custom_presets": ("config/app.yaml", "preset_shapes"),
    "cfg_text_ts_raw": ("config/model/text_ts.yaml", ""),
    "cfg_foundation_raw": ("config/model/foundation.yaml", ""),
    "cfg_lexicon_ko_raw": ("config/model/lexicon_ko.yaml", ""),
    "cfg_prompts": ("config/prompts.yaml", ""),
}


def _py_files():
    return sorted((ROOT / "backend").rglob("*.py")) + [ROOT / "app.py"]


def _ui_files():
    return sorted((ROOT / "web/templates").rglob("*.html")) + sorted((ROOT / "web/static/js").rglob("*.js"))


def _read(paths):
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in paths)


def _leaves(node, prefix=""):
    if isinstance(node, dict) and node:
        for k, v in node.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    else:
        yield prefix


def _wrappers(py_text_by_file):
    """cfg(f"접두사{key}") 로 감싸는 함수를 찾는다.

    같은 이름(_get_config)이 파일마다 다른 접두사로 정의되므로, 반드시
    "정의된 파일" 단위로 묶어야 한다. 전역으로 섞으면 있지도 않은 키를
    쓴다고 잘못 보고한다 (clustering.moment.n_init 같은 유령 키).
    """
    out: dict = {}
    pat = re.compile(r'def\s+(\w+)\s*\([^)]*\)[^:]*:(?:.|\n){0,400}?cfg\(\s*f"([\w.]*)\{')
    for p, src in py_text_by_file.items():
        for m in pat.finditer(src):
            out.setdefault(p, {})[m.group(1)] = m.group(2)
    return out


def collect():
    py_by_file = {p: p.read_text(encoding="utf-8", errors="replace") for p in _py_files()}
    py_text = "\n".join(py_by_file.values())
    ui_text = _read(_ui_files())

    direct: dict[str, set] = {}
    defaults: list[str] = []
    for p, src in py_by_file.items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.args):
                continue
            if not n.func.id.startswith("cfg"):
                continue
            if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                direct.setdefault(n.func.id, set()).add(n.args[0].value)
                if len(n.args) > 1:
                    defaults.append(f'{p.relative_to(ROOT)}:{n.lineno}  {n.func.id}("{n.args[0].value}", ...)')

    # 감싼 함수: 접두사를 붙여 실제 경로로 환원하고, 기본값이 있으면 잡아낸다
    wrappers = _wrappers(py_by_file)
    for p, defs in wrappers.items():
        for name, prefix in defs.items():
            call = re.compile(re.escape(name) + r'\(\s*"([\w.]+)"\s*(,\s*[^)]+)?\)')
            for i, line in enumerate(py_by_file[p].split("\n"), 1):
                for m in call.finditer(line):
                    direct.setdefault("cfg", set()).add(prefix + m.group(1))
                    if m.group(2):
                        defaults.append(f'{p.relative_to(ROOT)}:{i}  {name}("{m.group(1)}", ...)')

    whole = {v for k, v in WHOLE_READERS.items() if re.search(r"\b" + k + r"\s*\(", py_text)}
    ui_paths = set(re.findall(r"acfg\(\s*['\"]([\w.]+)['\"]", ui_text))
    ui_paths |= set(re.findall(r"app_config\.([\w.]+)", ui_text))
    ui_names = set(re.findall(r"(?:SEARCH_CONFIG|search_config|SCFG)[.\[]['\"]?(\w+)", ui_text))
    dict_names = set(re.findall(r'\.get\(\s*["\'](\w+)["\']', py_text))
    dict_names |= set(re.findall(r'\[\s*["\'](\w+)["\']\s*\]', py_text))

    missing, unused = [], []
    for rel, fn in YAMLS.items():
        path = ROOT / rel
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = set(_leaves(data))
        for key in sorted(direct.get(fn, set())):
            if key not in known and not any(k.startswith(key + ".") for k in known):
                missing.append(f"{rel}  ←  {fn}(\"{key}\")")
        for leaf in _leaves(data):
            parts = leaf.split(".")
            if any(".".join(parts[:i]) in direct.get(fn, set()) for i in range(1, len(parts) + 1)):
                continue
            if any(wf == rel and (wp == "" or leaf == wp or leaf.startswith(wp + ".")) for wf, wp in whole):
                continue
            if any(leaf == u or leaf.startswith(u + ".") or u.startswith(leaf + ".") for u in ui_paths):
                continue
            if parts[-1] in ui_names or parts[-1] in dict_names:
                continue
            unused.append(f"{rel}  {leaf}")
    return missing, unused, defaults


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="문제가 있으면 종료코드 1")
    args = ap.parse_args()

    missing, unused, defaults = collect()
    print("== 코드가 읽는데 yaml 에 없는 키 ==")
    print("\n".join("  " + m for m in missing) or "  없음")
    print("\n== yaml 에 있는데 읽는 곳을 못 찾은 키 ==")
    print("\n".join("  " + u for u in unused) or "  없음")
    print("\n== 남은 하드코딩 기본값 ==")
    print("\n".join("  " + d for d in defaults) or "  없음")

    problems = len(missing) + len(defaults)   # 미사용은 경고로만 본다
    print(f"\n요약 — 없는 키 {len(missing)} · 미사용 후보 {len(unused)} · 하드코딩 {len(defaults)}")
    if args.strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
