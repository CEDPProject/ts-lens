"""Regressions found by running the optional model paths (VerbalTS, ChatTS, MOMENT) on a GPU."""
from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import Mock, patch

import numpy as np

from app import create_app
from backend import llm_service, moment_service, runtime_state, series_generator


class MomentClusterProfileTest(unittest.TestCase):
    def test_profile_is_json_serializable_without_moment(self):
        # Discovery 프로파일링: 통계 경로가 numpy float32 를 그대로 실어 JSON 변환에서 500 이 났다.
        client = create_app().test_client()
        centroids = [list(np.sin(np.linspace(0, 6, 24))), list(np.linspace(0, 1, 24))]
        with patch.object(moment_service, "load_moment_model", side_effect=ImportError("no momentfm")), \
                patch.object(moment_service, "get_embeddings", side_effect=ImportError("no momentfm")):
            response = client.post("/api/moment/analyze_clusters",
                                   json={"centroids": centroids, "sizes": [3, 2], "total": 5})
        self.assertEqual(response.status_code, 200, response.get_json())
        stats = response.get_json()["results"][0]["stats"]
        self.assertIsInstance(stats["trend"], float)
        self.assertIsInstance(stats["volatility"], float)


@unittest.skipUnless(importlib.util.find_spec("torch"), "Optional torch runtime not installed")
class MomentEmbeddingShapeTest(unittest.TestCase):
    def test_pooled_and_per_patch_outputs_keep_model_width(self):
        # momentfm 은 이미 평균 낸 (batch, d_model) 을 준다. 한 번 더 평균하면 스칼라가 된다.
        import torch
        from backend.clustering import moment_embed
        for shape in ((1, 512), (1, 64, 512)):
            model = Mock(return_value=Mock(embeddings=torch.ones(shape)))
            vector = moment_embed._extract_embedding(np.arange(24, dtype=float), model, "cpu")
            self.assertEqual(vector.shape, (512,), shape)


class ChattsMultivariateTest(unittest.TestCase):
    def test_chatts_receives_every_series(self):
        # 다변량 해석이 ChatTS 에 시계열 없이 문장만 보내 서버가 400 으로 거절했다.
        captured = {}

        def fake_chatts(ts_arrays, prompt, pcfg, temperature=None):
            captured["arrays"], captured["prompt"] = ts_arrays, prompt
            return "OT drops sharply while HUFL stays flat."

        columns = [{"col_name": "OT", "values": list(np.linspace(1, 0, 24))},
                   {"col_name": "HUFL", "values": [1.0] * 24}]
        with patch.object(runtime_state, "force_cpu", return_value=False), \
                patch.object(llm_service, "_call_chatts", side_effect=fake_chatts):
            result = llm_service.describe_segment_multi(columns, model_key="chatts-14b")
        self.assertEqual(len(captured["arrays"]), 2)
        self.assertEqual(captured["prompt"].count("<ts><ts/>"), 2)
        self.assertEqual(result["situation"], "OT drops sharply while HUFL stays flat.")
        self.assertEqual(result["field_sources"]["situation"], "llm")
        self.assertEqual(result["engine"], "chatts")


class VerbalTSRejectionTest(unittest.TestCase):
    def test_input_rejected_by_server_is_a_client_error(self):
        # VerbalTS 서버의 400(영어 아닌 문장)이 앱에서 500 으로 바뀌어 나갔다.
        response = Mock(status_code=400)
        response.json.return_value = {"error": "English input only"}
        with patch.object(series_generator.requests, "post", return_value=response):
            with self.assertRaisesRegex(ValueError, "English input only"):
                series_generator.generate("갑자기 하락", 24, {"base_url": "http://test", "timeout": 5})


if __name__ == "__main__":
    unittest.main()
