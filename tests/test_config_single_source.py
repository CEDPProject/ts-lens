"""설정은 yaml 단일 소스여야 한다.

막는 회귀: cfg("키", 기본값) 처럼 호출부에 기본값을 두면 yaml 과 코드가
서로 다른 값을 갖고 갈라진다. 실제로 preset_shapes.classify_min_similarity 가
yaml 0.5 · 코드 0.4 로, preprocessing.default_interpolation 이
yaml adaptive · 코드 linear 로 어긋나 있었다.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CFG_FUNCS = {"cfg": "config/app.yaml",
             "cfg_search": "config/search.yaml",
             "cfg_prompts": "config/prompts.yaml"}

PY_FILES = sorted(list((ROOT / "backend").rglob("*.py"))) + [ROOT / "app.py"]


def _calls():
    """(파일, 줄, 함수, 키, 인자수) 목록."""
    for p in PY_FILES:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func.id if isinstance(node.func, ast.Name) else None
            if fn not in CFG_FUNCS or not node.args:
                continue
            key = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
            if key is None:
                continue
            yield p.relative_to(ROOT), node.lineno, fn, key, len(node.args)


def _dig(data, dotpath):
    cur = data
    for k in dotpath.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None, False
        cur = cur[k]
    return cur, True


class ConfigSingleSource(unittest.TestCase):
    def test_no_hardcoded_defaults_at_call_sites(self):
        offenders = [f"{f}:{ln}  {fn}(\"{key}\", ...)"
                     for f, ln, fn, key, n in _calls() if n > 1]
        self.assertEqual(offenders, [],
                         "설정 기본값은 yaml 에만 둔다:\n  " + "\n  ".join(offenders))

    def test_no_hardcoded_defaults_behind_wrappers(self):
        """cfg 를 감싼 함수(_col_det · _get_config 등) 뒤에 숨은 기본값도 막는다.

        실제로 column_detection 점수 9개와 clustering 값 10개가 감싼 함수의
        default 인자로 복사돼 있었다. 호출부만 보면 드러나지 않는다.
        """
        import re
        # cfg(f"...{key}") 로 감싸는 함수의 이름을 찾아낸다
        wrapper_names = set()
        for p in PY_FILES:
            src = p.read_text(encoding="utf-8")
            for m in re.finditer(r'def\s+(\w+)\s*\([^)]*\)[^:]*:\s*(?:"""(?:.|\n)*?"""\s*)?'
                                 r'(?:.|\n){0,300}?cfg\(\s*f"', src):
                wrapper_names.add(m.group(1))
        offenders = []
        for name in sorted(wrapper_names):
            pat = re.compile(re.escape(name) + r'\(\s*"[\w.]+"\s*,\s*[^)]+\)')
            for p in PY_FILES:
                for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                    if pat.search(line):
                        offenders.append(f"{p.relative_to(ROOT)}:{i}  {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "감싼 함수에도 기본값을 두지 않는다:\n  " + "\n  ".join(offenders))

    def test_every_used_key_exists_in_yaml(self):
        loaded = {}
        missing = []
        for f, ln, fn, key, _n in _calls():
            path = CFG_FUNCS[fn]
            if path not in loaded:
                fp = ROOT / path
                loaded[path] = yaml.safe_load(fp.read_text(encoding="utf-8")) if fp.exists() else {}
            _v, ok = _dig(loaded[path] or {}, key)
            if not ok:
                missing.append(f"{f}:{ln}  {key}  ({path} 에 없음)")
        self.assertEqual(missing, [],
                         "코드가 쓰는 키가 yaml 에 없다:\n  " + "\n  ".join(missing))

    def test_tuning_constants_come_from_yaml(self):
        from backend import helpers, session_store, llm_service
        from backend.routes import segment_search_routes as ssr
        for name, value in [("shape.scale_floor", helpers._SHAPE_SCALE_FLOOR),
                            ("shape.peak_noise_ratio", helpers._SHAPE_PEAK_NOISE_RATIO),
                            ("session.max_entries", session_store._MAX_ENTRIES),
                            ("llm.reachability_ttl_sec", llm_service._REACH_TTL_SEC),
                            ("cache.cluster_max_entries", ssr._MAX_CLUSTER_CACHE)]:
            self.assertIsNotNone(value, f"{name} 이 yaml 에서 읽히지 않는다")


class FrontendUsesConfig(unittest.TestCase):
    """화면 코드에도 길이·간격 숫자를 직접 쓰지 않는다.

    막는 회귀: JS 와 템플릿에 24 · 48 · 144 가 흩어져 있어, yaml 을 고쳐도
    화면은 옛 값을 그대로 썼다. 이제 window.APP_CONFIG 로만 받는다.
    """

    JS_DIR = ROOT / "web" / "static" / "js"
    # 길이·해상도 기본값으로 쓰이던 숫자들
    MAGIC = (24, 48, 144)

    def test_no_magic_length_fallbacks_in_js(self):
        import re
        offenders = []
        pat = re.compile(r"\|\|\s*(\d+)\b")
        self.assertTrue(self.JS_DIR.is_dir(), "JavaScript source folder is missing")
        for p in sorted(self.JS_DIR.rglob("*.js")):
            for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                if "acfg(" in line or "APP_CONFIG" in line:
                    continue
                for m in pat.finditer(line):
                    if int(m.group(1)) in self.MAGIC:
                        offenders.append(f"{p.relative_to(ROOT)}:{i}  {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "길이 기본값은 app.yaml 에서 받는다:\n  " + "\n  ".join(offenders))

    def test_number_inputs_come_from_config(self):
        import re
        html = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
        offenders = []
        for m in re.finditer(r'<input[^>]*type="number"[^>]*>', html):
            tag = m.group(0)
            for attr in ("value", "min"):
                am = re.search(attr + r'="(\d+)"', tag)
                if am and int(am.group(1)) in self.MAGIC:
                    offenders.append(tag[:100])
        self.assertEqual(offenders, [],
                         "입력칸 기본값은 app_config 에서 받는다:\n  " + "\n  ".join(offenders))


class ProviderConfigFromYaml(unittest.TestCase):
    """LLM 프로바이더 설정도 yaml 이 단일 소스다.

    막는 회귀: base_url · timeout · temperature · max_tokens · device · 모델 id 가
    코드에 기본값으로 복사돼 있었다. yaml 을 고쳐도 코드 값이 쓰이거나,
    두 값이 서로 달라진다 (device 가 yaml cuda:1 · 코드 cuda:0 이었다).
    """

    LLM_FILES = ["backend/llm_providers.py", "backend/llm_service.py",
                 "backend/series_generator.py"]

    def test_no_hardcoded_provider_defaults(self):
        import re
        # 빈 문자열 기본값은 "없으면 오류" 를 위한 필수값 검사라 허용한다.
        # (정규식 안에서 걸러내면 되짚기 때문에 새어 나가므로 여기서 판정한다)
        pat = re.compile(r'(?:pcfg|config)\.get\(\s*"[a-z_]+"\s*,\s*([^),]+)\)')
        offenders = []
        for rel in self.LLM_FILES:
            for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").split("\n"), 1):
                for m in pat.finditer(line):
                    if m.group(1).strip() in ('""', "''"):
                        continue
                    offenders.append(f"{rel}:{i}  {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "프로바이더 설정은 text_ts.yaml 에서만 온다:\n  " + "\n  ".join(offenders))

    def test_no_hardcoded_urls_in_backend(self):
        import re
        offenders = []
        for p in sorted((ROOT / "backend").rglob("*.py")):
            for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r'"https?://', line):
                    offenders.append(f"{p.relative_to(ROOT)}:{i}  {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "주소는 yaml 에 둔다:\n  " + "\n  ".join(offenders))


class ConfigAuditTool(unittest.TestCase):
    """tools/config_audit.py 가 실제로 문제를 잡는지, 그리고 지금은 깨끗한지.

    도구 자체가 조용히 망가지면(오탐·미탐) 점검이 있으나 마나가 된다.
    """

    def _collect(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import importlib
        mod = importlib.import_module("config_audit")
        importlib.reload(mod)
        return mod.collect()

    def test_repository_is_clean(self):
        missing, unused, defaults = self._collect()
        self.assertEqual(missing, [], "코드가 읽는 키가 yaml 에 없다")
        self.assertEqual(defaults, [], "하드코딩 기본값이 남아 있다")
        self.assertEqual(unused, [], "아무도 읽지 않는 설정이 있다")

    def test_tool_detects_an_unused_key(self):
        """일부러 넣은 미사용 설정을 잡아내는지 확인한다."""
        path = ROOT / "config" / "app.yaml"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\n_probe_unused_section:\n  never_read: 1\n",
                            encoding="utf-8")
            _missing, unused, _defaults = self._collect()
            self.assertIn("config/app.yaml  _probe_unused_section.never_read", unused)
        finally:
            path.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
