"""Hardware-independent startup, subprocess ownership and CPU API contracts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import create_app
from backend import runtime_state, llm_service
from backend.startup import ModelServers, load_settings


class StartupTests(unittest.TestCase):
    def setUp(self):
        previous = deepcopy(runtime_state.STATE)
        self.addCleanup(lambda: runtime_state.STATE.update(previous))
        self.settings = load_settings()
        occupied = patch('backend.startup.endpoint_occupied', return_value=False)
        occupied.start()
        self.addCleanup(occupied.stop)

    def test_cpu_never_probes_or_starts_model_servers(self):
        with (patch('backend.startup.gpu_available', side_effect=AssertionError('GPU probe')),
             patch('backend.startup.ready', side_effect=AssertionError('network')),
             patch('backend.startup.subprocess.Popen', side_effect=AssertionError('spawn'))):
            result = ModelServers(self.settings).prepare('off')
        self.assertEqual(set(result['models'].values()), {'cpu-basic'})

    def test_no_gpu_auto_starts_only_cpu_capable_models(self):
        with patch('backend.startup.gpu_available', return_value=False), patch('backend.startup.ready', return_value=True):
            result = ModelServers(self.settings).prepare('auto')
        self.assertEqual(result['models']['text_to_ts'], 'verbalts-synth')
        self.assertEqual(result['models']['ts_to_text'], 'local-cpu-llm')
        vllm = next(s for s in result['services'] if s['name'] == 'vllm')
        self.assertEqual(vllm['status'], 'skipped')
        self.assertIn('GPU', vllm['reason'])

    def test_missing_weights_on_gpu_uses_cpu(self):
        with patch('backend.startup.gpu_available', return_value=True), patch('backend.startup.ready', return_value=False), patch('backend.startup.prerequisites', return_value='모델 파일 없음'), patch('backend.startup.subprocess.Popen') as spawn:
            result = ModelServers(self.settings).prepare('auto')
        self.assertEqual(set(result['models'].values()), {'cpu-basic'})
        spawn.assert_not_called()

    def test_existing_servers_reused_and_not_owned(self):
        manager = ModelServers(self.settings)
        with patch('backend.startup.gpu_available', return_value=True), patch('backend.startup.ready', return_value=True), patch('backend.startup.subprocess.Popen') as spawn:
            result = manager.prepare('auto')
        self.assertEqual(result['models']['text_to_ts'], 'verbalts-synth')
        self.assertEqual(manager.owned, [])
        spawn.assert_not_called()
        manager.close()

    def test_external_on_cpu_can_use_existing_server(self):
        with patch('backend.startup.gpu_available', return_value=False), patch('backend.startup.ready', return_value=True), patch('backend.startup.subprocess.Popen') as spawn:
            result = ModelServers(self.settings).prepare('external')
        self.assertEqual(result['mode'], 'external')
        spawn.assert_not_called()

    def test_check_never_starts_a_ready_to_launch_model(self):
        with patch('backend.startup.gpu_available', return_value=True), patch('backend.startup.ready', return_value=False), patch('backend.startup.prerequisites', return_value=''), patch('backend.startup.subprocess.Popen') as spawn:
            result = ModelServers(self.settings).prepare('auto', check=True)
        self.assertTrue(all(s['status'] == 'startable' for s in result['services']))
        spawn.assert_not_called()

    def test_occupied_wrong_model_port_does_not_start_duplicate(self):
        with patch('backend.startup.gpu_available', return_value=True), patch('backend.startup.ready', return_value=False), patch('backend.startup.endpoint_occupied', return_value=True), patch('backend.startup.subprocess.Popen') as spawn:
            report = ModelServers(self.settings).prepare('auto')
        spawn.assert_not_called()
        self.assertTrue(all('포트' in row['reason'] for row in report['services']))

    def test_started_child_is_owned_and_stopped(self):
        self.settings['services'] = {'verbalts': self.settings['services']['verbalts']}
        manager = ModelServers(self.settings)
        process = Mock(pid=12345)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp, patch('backend.startup.APP_DIR', Path(tmp)), patch('backend.startup.gpu_available', return_value=True), patch('backend.startup.ready', side_effect=[False, True]), patch('backend.startup.prerequisites', return_value=''), patch('backend.startup.subprocess.Popen', return_value=process), patch('backend.startup.os.killpg') as stop:
            result = manager.prepare('auto')
            self.assertTrue(result['services'][0]['owned'])
            manager.close()
            stop.assert_any_call(process.pid, __import__("signal").SIGTERM)
            stop.assert_any_call(process.pid, __import__("signal").SIGKILL)

    def test_failed_child_falls_back_and_is_reaped(self):
        self.settings['services'] = {'verbalts': self.settings['services']['verbalts']}
        process = Mock(pid=12345)
        process.poll.return_value = 1
        manager = ModelServers(self.settings)
        with tempfile.TemporaryDirectory() as tmp, patch('backend.startup.APP_DIR', Path(tmp)), patch('backend.startup.gpu_available', return_value=True), patch('backend.startup.ready', return_value=False), patch('backend.startup.prerequisites', return_value=''), patch('backend.startup.subprocess.Popen', return_value=process), patch('backend.startup.os.killpg'):
            result = manager.prepare('auto')
        self.assertEqual(set(result['models'].values()), {'cpu-basic'})
        self.assertEqual(manager.owned, [])


class CPUContracts(unittest.TestCase):
    def setUp(self):
        previous = deepcopy(runtime_state.STATE)
        self.addCleanup(lambda: runtime_state.STATE.update(previous))
        runtime_state.configure('off', False, {'text_to_ts':'cpu-basic', 'ts_to_text':'cpu-basic'}, [])
        self.client = create_app().test_client()

    def test_cpu_generation_and_description_without_network(self):
        with patch.object(llm_service, 'call_llm', side_effect=AssertionError('LLM called')), patch('requests.get', side_effect=AssertionError('network')):
            status = self.client.get('/api/llm_status?task=text_to_ts').json
            self.assertTrue(status['available'])
            self.assertEqual(status['model_key'], 'cpu-basic')
            result = self.client.post('/api/generate_pattern', json={'description':'갑자기 상승', 'length':24})
            self.assertEqual(result.status_code, 200, result.json)
            self.assertFalse(result.json['llm_used'])
            result = self.client.post('/api/describe_segment', json={'values':[1,2,3,4,5]})
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(result.json['engine'], 'statistics')
            self.assertFalse(result.json['llm_used'])
            result = self.client.post('/api/describe_segment_multi', json={'columns':[{'col_name':'x','values':[1,2,3]},{'col_name':'y','values':[2,4,6]}]})
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(result.json['correlations'][0]['pearson_r'], 1)

    def test_unsupported_cpu_text_is_explicit_400(self):
        response = self.client.post('/api/generate_pattern', json={'description':'초반 안정 중간 진동 마지막 회복'})
        self.assertEqual(response.status_code, 400)

    def test_explicit_gpu_model_is_blocked_in_cpu_mode(self):
        with patch('requests.get', side_effect=AssertionError('network')):
            response = self.client.post('/api/generate_pattern', json={'description':'rise', 'model_key':'verbalts-synth'})
            self.assertEqual(response.status_code, 400)
            models = self.client.get('/api/llm_models?task=text_to_ts').json['models']
            self.assertTrue(next(m for m in models if m['key']=='cpu-basic')['available'])
            self.assertFalse(next(m for m in models if m['key']=='verbalts-synth')['available'])

    def test_cpu_selection_does_not_write_yaml(self):
        with patch.object(llm_service, 'save_text_ts_config', side_effect=AssertionError('config write')):
            self.assertTrue(llm_service.set_active_model('cpu-basic', 'text_to_ts'))

    def test_optional_foundation_weights_are_blocked(self):
        from backend.moment_service import load_moment_model
        with self.assertRaisesRegex(RuntimeError, 'AI'):
            load_moment_model()


class CPULanguageServerTests(unittest.TestCase):
    def test_cpu_model_response_uses_generated_tokens(self):
        try:
            import torch
        except ImportError:
            self.skipTest("Optional torch runtime not installed")
        from types import SimpleNamespace
        from tools.models.serve_cpu_llm import create_app
        class Tokenizer:
            eos_token_id = 0
            def apply_chat_template(self, messages, **kwargs):
                return messages[-1]['content']
            def __call__(self, text, **kwargs):
                return {'input_ids': torch.tensor([[1, 2]])}
            def decode(self, tokens, **kwargs):
                self.tokens = tokens.tolist()
                return '{"shape":"rise"}'
        tokenizer = Tokenizer()
        model = Mock(config=SimpleNamespace(max_position_embeddings=4096))
        model.generate.return_value = torch.tensor([[1, 2, 3, 4]])
        client = create_app(model, tokenizer).test_client()
        response = client.post('/v1/chat/completions', json={'messages':[{'role':'user','content':'describe'}], 'max_tokens':10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tokenizer.tokens, [3,4])
        self.assertEqual(client.get('/v1/models').json['device'], 'cpu')
        self.assertFalse(model.generate.call_args.kwargs['do_sample'])
        self.assertEqual(response.json['choices'][0]['message']['content'], '{"shape":"rise"}')
        self.assertEqual(client.post('/v1/chat/completions', json=['invalid']).status_code, 400)
