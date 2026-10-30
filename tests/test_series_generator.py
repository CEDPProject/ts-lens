import unittest
from unittest.mock import Mock, patch

from backend import llm_service, series_generator
from tools.models.serve_verbalts import create_app


class VerbalTSContractTest(unittest.TestCase):
    def test_selected_generator_bypasses_rules_and_llm(self):
        expected = {"engine": "verbalts", "values": [0, 1, 2, 3, 4]}
        with patch.object(series_generator, "generate", return_value=expected) as generate, \
             patch.object(llm_service, "call_llm", side_effect=AssertionError("LLM called")), \
             patch.object(llm_service, "_deterministic_shape_from_text", side_effect=AssertionError("rule called")):
            actual = llm_service.generate_pattern_shape("gradual ramp up", 5, model_key="verbalts-synth")
        self.assertEqual(actual, expected)
        generate.assert_called_once()

    def test_failure_does_not_fall_back_to_rules(self):
        with patch.object(series_generator, "generate", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                llm_service.generate_pattern_shape("gradual ramp up", 5, model_key="verbalts-synth")

    def test_expansion_does_not_invoke_language_model(self):
        with patch.object(series_generator, "generate", return_value={"values": [0]*5, "engine": "verbalts"}), \
             patch.object(llm_service, "generate_paraphrases", side_effect=AssertionError("LLM called")):
            result = llm_service.generate_pattern_shape_ensemble("rising", 5, model_key="verbalts-synth")
        self.assertEqual(len(result["queries"]), 1)
        self.assertEqual(result["paraphrases"], [])

    def test_numeric_response_is_validated(self):
        for values in ([0, 1], [0, 1, 2, 3, float("nan")], [0, 1, 2, 3, True]):
            response = Mock(status_code=200)
            response.json.return_value = {"engine": "verbalts", "values": values}
            with patch.object(series_generator.requests, "post", return_value=response):
                with self.assertRaises(RuntimeError):
                    # base_url 은 config/model/text_ts.yaml 이 단일 소스다.
                    # 여기서 넘기지 않으면 주소가 없다는 이유로 먼저 끊겨,
                    # 정작 검사하려던 "숫자 응답 검증"을 지나치게 된다.
                    series_generator.generate(
                        "rising", 5,
                        {"base_url": "http://test", "timeout": 5})

    def test_unready_model_is_not_available(self):
        response = Mock(status_code=200)
        response.json.return_value = {"available": False, "engine": "verbalts"}
        with patch("requests.get", return_value=response), patch.object(llm_service, "_reach_cache", {}):
            available, _ = llm_service._provider_reachable(
                "verbalts", {"base_url": "http://test", "health_timeout": 1.5})
        self.assertFalse(available)

    def test_server_rejects_unsupported_input_before_inference(self):
        try:
            __import__("torch")
        except ImportError:
            self.skipTest("Optional torch runtime not installed")
        model = Mock()
        client = create_app(model, "cpu").test_client()
        for body in ([], {"description": "상승"}, {"description": "rise", "length": True},
                     {"description": "rise", "seed": -1}):
            self.assertEqual(client.post("/generate", json=body).status_code, 400)
        model.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
