from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import numpy as np

from app import create_app
from backend import llm_service, moment_service
from backend.llm_analysis import _authoritative_interpretation, _compute_segment_features


class PatternGenerationContractTest(unittest.TestCase):
    def setUp(self):
        # These contracts exercise the legacy LLM/rule path independently of
        # whichever model the user has selected in the persisted UI settings.
        cfg = copy.deepcopy(llm_service.cfg_text_ts_raw())
        cfg["task_active_models"]["text_to_ts"] = "exaone-ko-vllm"
        config_patch = patch.object(llm_service, "cfg_text_ts_raw", return_value=cfg)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def test_explicit_shape_is_deterministic_without_network(self):
        with patch.object(llm_service, "call_llm", side_effect=AssertionError("network called")):
            result = llm_service.generate_pattern_shape("갑자기 상승", length=48)

        self.assertEqual(result["engine"], "deterministic")
        self.assertIsNone(result["model"])
        self.assertFalse(result["llm_used"])
        self.assertIsNone(result["fallback_reason"])
        self.assertEqual(len(result["values"]), 48)

    def _llm_first_config(self):
        cfg = copy.deepcopy(llm_service.cfg_text_ts_raw())
        cfg["deterministic_first"] = False
        return cfg

    def test_call_failure_fallback_keeps_provenance(self):
        with patch.object(llm_service, "cfg_text_ts_raw", return_value=self._llm_first_config()), \
             patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", side_effect=RuntimeError("offline")):
            result = llm_service.generate_pattern_shape("갑자기 상승", length=24)

        self.assertEqual(result["engine"], "deterministic")
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["fallback_reason"], "llm_call_failed")

    def test_parse_failure_fallback_keeps_provenance(self):
        with patch.object(llm_service, "cfg_text_ts_raw", return_value=self._llm_first_config()), \
             patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", return_value="not-json"):
            result = llm_service.generate_pattern_shape("sudden drop", length=24)

        self.assertEqual(result["engine"], "deterministic")
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["fallback_reason"], "llm_parse_failed")

    def test_llm_success_reports_actual_model(self):
        raw = '{"description":"complex","values":[0,1,0,-1,0]}'
        with patch.object(llm_service, "cfg_text_ts_raw", return_value=self._llm_first_config()), \
             patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", return_value=raw):
            result = llm_service.generate_pattern_shape("complex shape", length=5)

        self.assertEqual(result["engine"], "vllm")
        self.assertEqual(result["model"], "model-x")
        self.assertTrue(result["llm_used"])
        self.assertIsNone(result["fallback_reason"])

    def test_multi_stage_text_is_not_collapsed_to_one_rule(self):
        raw = '{"description":"multi-stage","values":[0,0,1,-1,0,0.5,1]}'
        with patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", return_value=raw):
            result = llm_service.generate_pattern_shape(
                "초반에는 안정적이다가 중간에 두 번 진동하고 마지막에 완만하게 회복",
                length=7,
            )

        self.assertEqual(result["engine"], "vllm")
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["description"], "multi-stage")

    def test_multi_stage_failure_is_not_misreported_as_simple_fallback(self):
        with patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                llm_service.generate_pattern_shape(
                    "초반에는 안정적이다가 중간에 진동하고 마지막에 회복",
                    length=24,
                )


class ModelAvailabilityContractTest(unittest.TestCase):
    def test_reachability_is_part_of_available(self):
        with patch.object(llm_service, "_provider_reachable", return_value=(False, "offline")), \
             patch.object(llm_service, "_is_cpu_only_env", return_value=True):
            models = llm_service.list_models(task=llm_service.TASK_TEXT_TO_TS)

        exaone = next(model for model in models if model["key"] == "exaone-ko-vllm")
        self.assertTrue(exaone["configured"])
        self.assertFalse(exaone["reachable"])
        self.assertFalse(exaone["available"])
        self.assertEqual(exaone["reachability_reason"], "offline")


class LLMRouteInputContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        app.config.update(TESTING=True)
        cls.client = app.test_client()

    def test_single_description_rejects_non_array_values_as_400(self):
        response = self.client.post(
            "/api/describe_segment",
            json={"col_name": "x", "values": "abcd"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON array", response.get_json()["error"])

    def test_multi_description_rejects_non_numeric_values_as_400(self):
        response = self.client.post(
            "/api/describe_segment_multi",
            json={"columns": [{"col_name": "x", "values": ["a", "b"]}]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("finite numbers", response.get_json()["error"])

    def test_generate_pattern_rejects_non_object_body_as_400(self):
        response = self.client.post("/api/generate_pattern", json=["rise"])
        self.assertEqual(response.status_code, 400)

    def test_generate_pattern_rejects_ambiguous_boolean_as_400(self):
        response = self.client.post(
            "/api/generate_pattern",
            json={"description": "rise", "expand": "false"},
        )
        self.assertEqual(response.status_code, 400)

    def test_description_rejects_unknown_time_unit_as_400(self):
        response = self.client.post(
            "/api/describe_segment",
            json={"values": [1, 2, 3], "unit": "not-a-unit"},
        )
        self.assertEqual(response.status_code, 400)

    def test_static_asset_version_is_stable_within_process(self):
        first = self.client.get("/finder").get_data(as_text=True)
        second = self.client.get("/finder").get_data(as_text=True)
        marker = "/static/css/style.css?v="
        first_version = first.split(marker, 1)[1].split('"', 1)[0]
        second_version = second.split(marker, 1)[1].split('"', 1)[0]
        self.assertEqual(first_version, second_version)


class DescriptionAuthorityContractTest(unittest.TestCase):
    def test_computed_feature_labels_are_bilingual(self):
        interpreted = _authoritative_interpretation({
            "trend_direction": "Flat, R²=0.00",
            "range_relative_volatility": "20.0%",
            "peak_position": "max at 4/12 (33%), min at 8/12 (67%)",
            "segment_means": "first_third=1.0, mid=2.0, last_third=3.0",
            "sudden_changes": "2회 (max_jump=1.20)",
        })

        ko_text = " ".join(item["ko"] for item in interpreted["features"])
        en_text = " ".join(item["en"] for item in interpreted["features"])
        self.assertIn("최대 위치", ko_text)
        self.assertIn("초반 1/3", ko_text)
        self.assertNotIn("max at", ko_text)
        self.assertNotIn("회", en_text)

    def test_single_description_keeps_numeric_fields_computed(self):
        raw = '''{"shape":{"ko":"직선","en":"linear"},
                  "trend":{"ko":"과장","en":"overstated"},
                  "volatility":{"ko":"매우 높음","en":"very high"},
                  "features":[],"summary":{"ko":"두드러진 차이","en":"significant"}}'''
        values = [0.0, 0.1, 0.2, 0.5, 1.2, 2.1, 1.4, 0.6, 0.1, -0.4, -0.8, -0.3]
        with patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", return_value=raw):
            result = llm_service.describe_segment(values, model_key="model-x")

        self.assertEqual(result["field_sources"]["trend"], "computed")
        self.assertEqual(result["field_sources"]["volatility"], "computed")
        self.assertIn("std/range", result["volatility"]["en"])
        self.assertNotIn("significant", result["summary"]["en"])

    def test_multi_description_computes_correlations(self):
        raw = '{"columns":[{"shape":"up"},{"shape":"up"}],"correlations":["guessed"]}'
        columns = [
            {"col_name": "a", "values": [0, 1, 2, 3, 4, 5, 6, 7]},
            {"col_name": "b", "values": [0, 2, 4, 6, 8, 10, 12, 14]},
        ]
        with patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", return_value=raw):
            result = llm_service.describe_segment_multi(columns, model_key="model-x")

        self.assertEqual(result["field_sources"]["correlations"], "computed")
        self.assertAlmostEqual(result["correlations"][0]["pearson_r"], 1.0)
        self.assertEqual(result["columns"][0]["field_sources"]["trend"], "computed")

    def test_multi_description_keeps_llm_columns_aligned_after_invalid_input(self):
        raw = '{"columns":[{"col_name":"valid","shape":"up"}]}'
        columns = [
            {"col_name": "empty", "values": []},
            {"col_name": "valid", "values": [0, 1, 2, 3]},
        ]
        with patch.object(llm_service, "_resolve_model", return_value=("vllm", "model-x", {})), \
             patch.object(llm_service, "call_llm", return_value=raw):
            result = llm_service.describe_segment_multi(columns, model_key="model-x")

        self.assertEqual(len(result["columns"]), 1)
        self.assertEqual(result["columns"][0]["col_name"], "valid")
        self.assertEqual(result["columns"][0]["shape"], "up")

    def test_multi_description_rejects_all_invalid_columns_without_llm(self):
        columns = [
            {"col_name": "empty", "values": []},
            {"col_name": "nan", "values": [float("nan")]},
        ]
        with patch.object(llm_service, "call_llm", side_effect=AssertionError("LLM called")):
            with self.assertRaisesRegex(ValueError, "finite numeric"):
                llm_service.describe_segment_multi(columns, model_key="model-x")


class MomentShapeContractTest(unittest.TestCase):
    def test_shape_and_volatility_are_offset_invariant(self):
        base = np.array([0, 1, 3, 1, 0, -1, 0, 1], dtype=np.float64)
        shifted = base + 1_000
        a = moment_service._classify_pattern(base, None)
        b = moment_service._classify_pattern(shifted, None)

        self.assertEqual(a["shape"], b["shape"])
        self.assertEqual(a["stats"]["volatility"], b["stats"]["volatility"])
        self.assertEqual(a["stats"]["peaks"], b["stats"]["peaks"])
        self.assertEqual(a["stats"]["valleys"], b["stats"]["valleys"])

    def test_llm_numeric_features_are_offset_invariant(self):
        base = np.array([0, 1, 3, 1, 0, -1, 0, 1], dtype=np.float64)
        shifted = base + 1_000
        a = _compute_segment_features(base)
        b = _compute_segment_features(shifted)

        self.assertNotIn("coefficient_of_variation", a)
        self.assertEqual(a["range_relative_volatility"], b["range_relative_volatility"])
        self.assertEqual(a["trend_direction"], b["trend_direction"])


if __name__ == "__main__":
    unittest.main()
