import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from backend import external_llm


class ExternalLLMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "external_llm.json"
        self._env = patch.dict(os.environ, {"TS_PATTERN_EXTERNAL_LLM_FILE": str(self.path)})
        self._env.start(); external_llm._cache.update(mtime=None, data=None)
        self.client = create_app().test_client()

    def tearDown(self):
        self._env.stop(); self.tmp.cleanup(); external_llm._cache.update(mtime=None, data=None)

    def test_default_is_off_and_not_injected(self):
        s = self.client.get("/api/external_llm").json["settings"]
        self.assertFalse(s["enabled"]); self.assertFalse(s["api_key_set"])
        self.assertNotIn(external_llm.MODEL_KEY, external_llm.inject({"models": {}, "providers": {}})["models"])

    def test_save_masks_key_and_injects_model(self):
        body = {"enabled": True, "provider": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-ant-abcdefgh1234",
                "use_for": {"text_to_ts": True, "ts_to_text": False, "meta_inference": True}}
        r = self.client.put("/api/external_llm", json=body); self.assertEqual(r.status_code, 200, r.json)
        s = r.json["settings"]
        self.assertTrue(s["api_key_set"]); self.assertEqual(s["api_key_hint"], "…1234"); self.assertNotIn("api_key", s)
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), "0o600")
        merged = external_llm.inject({"models": {}, "providers": {}})
        self.assertEqual(merged["models"][external_llm.MODEL_KEY]["supports"], ["text_to_ts"])
        self.assertEqual(merged["providers"]["anthropic"]["api_key"], "sk-ant-abcdefgh1234")
        self.assertTrue(external_llm.meta_inference_enabled())
        # 키 칸을 비우고 다시 저장해도(=__keep__) 키가 유지된다
        r = self.client.put("/api/external_llm", json=dict(body, api_key=external_llm.KEEP_SECRET, label="회사 Claude"))
        self.assertEqual(r.status_code, 200); self.assertTrue(r.json["settings"]["api_key_set"])
        # 모델 목록에 나타난다
        keys = [m["key"] for m in self.client.get("/api/llm_models?task=text_to_ts").json["models"]]
        self.assertIn(external_llm.MODEL_KEY, keys)
        # 끄면 사라진다
        self.client.put("/api/external_llm", json=dict(body, enabled=False, api_key=external_llm.KEEP_SECRET))
        keys = [m["key"] for m in self.client.get("/api/llm_models?task=text_to_ts").json["models"]]
        self.assertNotIn(external_llm.MODEL_KEY, keys)

    def test_validation(self):
        r = self.client.put("/api/external_llm", json={"enabled": True, "provider": "anthropic", "model": "", "api_key": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/external_llm", json={"enabled": True, "provider": "openai_compat", "model": "m", "api_key": "x", "base_url": "api.openai.com"})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/external_llm", json={"enabled": True, "provider": "nope", "model": "m", "api_key": "x"})
        self.assertEqual(r.status_code, 400)

    def test_test_endpoint_uses_mocked_call(self):
        self.client.put("/api/external_llm", json={"enabled": True, "provider": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-test-00001234", "use_for": {"ts_to_text": True}})
        with patch("backend.llm_providers.call_external", return_value="ready") as m:
            r = self.client.post("/api/external_llm/test", json={})
        self.assertEqual(r.json["ok"], True); self.assertEqual(r.json["reply"], "ready"); m.assert_called_once()

    def test_delete(self):
        self.client.put("/api/external_llm", json={"enabled": True, "provider": "anthropic", "model": "m", "api_key": "sk-test-00001234", "use_for": {"ts_to_text": True}})
        r = self.client.delete("/api/external_llm"); self.assertFalse(r.json["settings"]["api_key_set"]); self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()


class MetaInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "external_llm.json"
        self._env = patch.dict(os.environ, {"TS_PATTERN_EXTERNAL_LLM_FILE": str(self.path)})
        self._env.start(); external_llm._cache.update(mtime=None, data=None)
        self.client = create_app().test_client()

    def tearDown(self):
        self._env.stop(); self.tmp.cleanup(); external_llm._cache.update(mtime=None, data=None)

    def test_disabled_returns_400(self):
        r = self.client.post("/api/external_llm/infer_meta", json={"sid": "x"})
        self.assertEqual(r.status_code, 400)

    def test_infer_fills_from_preview_session(self):
        import io
        self.client.put("/api/external_llm", json={"enabled": True, "provider": "anthropic", "model": "claude-opus-5-5",
                                                   "api_key": "sk-test-00001234", "use_for": {"meta_inference": True}})
        csv = "datetime_utc,measured_mw,capacity_mw\n" + "".join(f"2016-01-01 {h:02d}:00:00,{100+h},712.2\n" for h in range(24))
        up = self.client.post("/api/upload", data={"file": (io.BytesIO(csv.encode()), "onshore_flanders_dso.csv")},
                              content_type="multipart/form-data").json
        fake = ('{"dataset_name": "Flanders onshore wind", "description": "Hourly wind generation", "domain_tags": ["energy"],'
                ' "sampling": "1 hour", "columns": [{"name": "measured_mw", "label": "Measured", "unit": "MW", "description": "x",'
                ' "val_min": 0, "val_max": 800, "confidence": "high"}, {"name": "ghost", "label": "no"}], "caveats": [], "confidence": "high", "basis": "column names"}')
        with patch("backend.llm_providers.call_external", return_value=fake) as m:
            r = self.client.post("/api/external_llm/infer_meta", json={"sid": up["sid"], "lang": "en"})
        self.assertEqual(r.status_code, 200, r.json)
        sg = r.json["suggestion"]
        self.assertEqual(sg["dataset_name"], "Flanders onshore wind")
        self.assertEqual([c["name"] for c in sg["columns"]], ["measured_mw"])  # 없는 칸(ghost)은 걸러진다
        sent = m.call_args.args[1][0]["content"]
        self.assertIn("onshore_flanders_dso.csv", sent); self.assertIn("measured_mw", sent); self.assertIn("median_step", sent)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"TS_PATTERN_EXTERNAL_LLM_FILE": str(Path(self.tmp.name) / "e.json")})
        self._env.start(); external_llm._cache.update(mtime=None, data=None)
        self._usage = patch.object(external_llm, "usage_path", return_value=Path(self.tmp.name) / "usage.json"); self._usage.start()

    def tearDown(self):
        self._usage.stop(); self._env.stop(); self.tmp.cleanup(); external_llm._cache.update(mtime=None, data=None)

    def test_estimate_and_record_and_block(self):
        self.assertAlmostEqual(external_llm.estimate_usd("claude-opus-5-5", 1_000_000, 100_000), 4.0 + 2.0)
        self.assertAlmostEqual(external_llm.estimate_usd("unknown-model", 1_000_000, 0), 5.0)  # default 단가
        external_llm.save({"enabled": True, "provider": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-test-00001234",
                           "use_for": {"ts_to_text": True}, "monthly_warn_usd": 1, "monthly_limit_usd": 2})
        external_llm.check_budget()  # 0 달러 — 통과
        external_llm.record_usage("anthropic", "claude-opus-5-5", 300_000, 0)   # $1.2 → 경고 구간, 통과
        external_llm.check_budget()
        self.assertTrue(external_llm.public_view()["usage"]["warn"])
        external_llm.record_usage("anthropic", "claude-opus-5-5", 300_000, 0)   # $2.4 ≥ 한도
        with self.assertRaises(RuntimeError):
            external_llm.check_budget()
        v = external_llm.public_view()["usage"]; self.assertTrue(v["blocked"]); self.assertEqual(v["calls"], 2)
        # 한도 0 = 제한 없음
        external_llm.save({"enabled": True, "provider": "anthropic", "model": "claude-opus-5-5", "api_key": external_llm.KEEP_SECRET,
                           "use_for": {"ts_to_text": True}, "monthly_warn_usd": 1, "monthly_limit_usd": 0})
        external_llm.check_budget()

    def test_budget_validation(self):
        with self.assertRaises(ValueError):
            external_llm.save({"enabled": False, "provider": "anthropic", "monthly_limit_usd": "abc"})
        with self.assertRaises(ValueError):
            external_llm.save({"enabled": False, "provider": "anthropic", "monthly_warn_usd": -1})
