"""No downloads, paid calls, or real project state. Run with unittest."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import device_setup as ds
import llm
from engines import egress as eg
from engines.base import EngineSpec, BaseEngine


def device():
    return {'os': 'Windows', 'arch': 'AMD64', 'cpu': {'name': 'Intel Core i7-13700F', 'threads': 24, 'cores': 16},
            'gpus': [{'index': 0, 'name': 'NVIDIA GeForce RTX 4060', 'vramGb': 8, 'freeVramGb': 7.8}],
            'ramGb': 16, 'freeRamGb': 11, 'freeDiskGb': 100}


class DeploymentTests(unittest.TestCase):
    def test_baseline_three_profiles(self):
        plans = ds.recommend(device())['profiles']
        self.assertTrue(all(p['eligible'] for p in plans))
        self.assertEqual([p['tag'] for p in plans], ['qwen3.5:4b-q4_K_M', 'qwen3.5:4b-q8_0', 'qwen3.5:9b-q4_K_M'])

    def test_low_hardware_cloud_all_profiles(self):
        for key, value in [('ramGb', 8), ('arch', 'ARM64'), ('gpus', []), ('cpu', {'name': 'Intel i5-10500', 'threads': 12})]:
            d = device(); d[key] = value
            self.assertFalse(any(p['eligible'] for p in ds.recommend(d)['profiles']))

    def test_free_memory_profile_specific(self):
        d = device(); d['gpus'][0]['freeVramGb'] = 5
        self.assertEqual([p['eligible'] for p in ds.recommend(d)['profiles']], [True, False, False])
        d = device(); d['freeRamGb'] = 7
        self.assertEqual([p['eligible'] for p in ds.recommend(d)['profiles']], [True, False, False])

    def test_unknown_cpu_and_no_gpu_sum(self):
        d = device(); d['cpu'] = {'name': 'Unknown 64-core processor', 'threads': 128}
        self.assertFalse(ds.recommend(d)['baselineMet'])
        d = device(); d['gpus'] = [{'index': n, 'name': 'RTX 4060', 'vramGb': 4, 'freeVramGb': 4} for n in (0, 1)]
        self.assertFalse(ds.recommend(d)['baselineMet'])

    def test_select_eligible_gpu_not_busy_first(self):
        d = device(); d['gpus'][0]['freeVramGb'] = 1
        second = copy.deepcopy(d['gpus'][0]); second.update(index=1, freeVramGb=7.8); d['gpus'].append(second)
        self.assertEqual(ds.recommend(d)['profiles'][0]['gpuIndex'], 1)

    def test_preflight_does_not_download(self):
        d = device(); d['freeDiskGb'] = 2
        with patch.object(ds, 'inspect_device', return_value=d), patch.object(ds, 'ensure_runtime') as download:
            with self.assertRaises(RuntimeError): ds.install('speed')
            download.assert_not_called()

    def test_env_preserves_keys_and_failure_does_not_switch(self):
        with tempfile.TemporaryDirectory() as td, patch.object(ds, 'ROOT', Path(td)), patch.object(ds, 'RUNTIME', Path(td) / '.firekb-runtime'):
            config = Path(td) / '00_配置'; config.mkdir()
            file = config / '.env'; before = '# preserved\nDEEPSEEK_API_KEY=placeholder\nOTHER=keep\nDEEPSEEK_BASE_URL=https://example.com/v1\n'
            file.write_text(before)
            with patch.object(ds, 'inspect_device', return_value=device()), patch.object(ds, 'ensure_runtime', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError): ds.install('speed')
            self.assertEqual(file.read_text(), before)
            self.assertFalse((ds.RUNTIME / 'setup.lock').exists())
            ds.set_env({'DEEPSEEK_BASE_URL': ds.HOST + '/v1'})
            self.assertIn('OTHER=keep', file.read_text())
            self.assertIn('DEEPSEEK_API_KEY=placeholder', file.read_text())
            self.assertTrue(list(config.glob('.env.setup-*.bak')))

    def test_full_install_success_with_mock_runtime(self):
        with tempfile.TemporaryDirectory() as td, patch.object(ds, 'ROOT', Path(td)), patch.object(ds, 'RUNTIME', Path(td) / '.firekb-runtime'):
            def api(route, body=None, timeout=30):
                if route == '/api/generate': return {'response': '就绪'}
                if route == '/api/ps': return {'models': [{'name': 'firekb-speed:latest', 'size_vram': 4000000000}]}
                return {}
            class Response(io.BytesIO):
                pass
            opener = unittest.mock.Mock(); opener.open.return_value = Response(b'{"status":"success"}\n')
            with patch.object(ds, 'inspect_device', return_value=device()), patch.object(ds, 'ensure_runtime', return_value=Path('mock-ollama.exe')), patch.object(ds, 'ensure_server'), patch.object(ds, 'run'), patch.object(ds, 'local_api', side_effect=api), patch.object(ds.urllib.request, 'build_opener', return_value=opener):
                ds.install('speed')
            cfg = (Path(td) / '00_配置' / '.env').read_text()
            self.assertIn('DEEPSEEK_MODEL=firekb-speed', cfg)
            self.assertIn('FIREKB_PROVIDER=ollama', cfg)
            self.assertTrue((Path(td) / '00_配置' / '.deployment.json').exists())

    def test_bad_archive_never_executed(self):
        with tempfile.TemporaryDirectory() as td, patch.object(ds, 'RUNTIME', Path(td)):
            folder = Path(td) / 'ollama'; folder.mkdir()
            archive = folder / (ds.VERSION + '.zip'); archive.write_bytes(b'corrupt')
            with patch.object(ds.urllib.request, 'urlopen') as download:
                with self.assertRaises(RuntimeError): ds.ensure_runtime()
                download.assert_not_called()
            self.assertFalse((folder / ds.VERSION / 'ollama.exe').exists())

    def test_warmup_failure_keeps_cloud_config(self):
        with tempfile.TemporaryDirectory() as td, patch.object(ds, 'ROOT', Path(td)), patch.object(ds, 'RUNTIME', Path(td) / '.firekb-runtime'):
            config = Path(td) / '00_配置'; config.mkdir()
            file = config / '.env'; file.write_text('DEEPSEEK_BASE_URL=https://example.com/v1')
            opener = unittest.mock.Mock(); opener.open.return_value = io.BytesIO(b'{"status":"success"}\n')
            with patch.object(ds, 'inspect_device', return_value=device()), patch.object(ds, 'ensure_runtime', return_value=Path('mock.exe')), patch.object(ds, 'ensure_server'), patch.object(ds, 'run'), patch.object(ds, 'local_api', return_value={}), patch.object(ds.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(RuntimeError): ds.install('speed')
            self.assertEqual(file.read_text(), 'DEEPSEEK_BASE_URL=https://example.com/v1')


class ConsentTests(unittest.TestCase):
    def test_approval_revoke_recipient_category_purpose(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for category in ('personal', 'audio', 'transcript', 'page_text'):
                with self.assertRaises(PermissionError): eg.require_recipient(root, category, 'https://api.example/v1', ds.PURPOSE)
            eg.grant_recipient(root, ['personal'], 'https://api.example/v1/', ds.PURPOSE, 'manual')
            eg.require_recipient(root, 'personal', 'https://api.example:443/v1', ds.PURPOSE)
            for category, recipient, purpose in [('audio', 'https://api.example/v1', ds.PURPOSE), ('personal', 'https://other.example/v1', ds.PURPOSE), ('personal', 'https://api.example/v1', 'other')]:
                with self.assertRaises(PermissionError): eg.require_recipient(root, category, recipient, purpose)
            eg.revoke(root)
            with self.assertRaises(PermissionError): eg.require_recipient(root, 'personal', 'https://api.example/v1', ds.PURPOSE)

    def test_legacy_personal_not_granted(self):
        class Cloud(BaseEngine):
            spec = EngineSpec(kind='asr', name='cloud', egress='audio')
        with tempfile.TemporaryDirectory() as td:
            eg.consent_file(td).write_text(json.dumps({'granted': {'asr:cloud': {'egress': 'audio'}}}))
            with self.assertRaises(SystemExit): eg.require_consent({'asr': Cloud()}, td)
            eg.grant(td, {'asr': Cloud()}, 'manual')
            eg.require_consent({'asr': Cloud()}, td)

    def test_recipient_validation_and_localhost(self):
        for url in ('https://user:pass@example.com', 'https://example.com?k=1', 'file:///tmp/x', 'https://example.com#frag'):
            with self.assertRaises(ValueError): eg.normalize_recipient(url)
        self.assertTrue(eg.is_local_recipient('http://127.0.0.1:11435/v1'))
        self.assertFalse(eg.is_local_recipient('https://localhost.evil.example/v1'))

    def test_no_network_without_approval(self):
        with tempfile.TemporaryDirectory() as td, patch.object(llm, 'ROOT', Path(td)), patch.object(llm, 'load_env', return_value={'DEEPSEEK_API_KEY': 'test-placeholder', 'DEEPSEEK_BASE_URL': 'https://api.example/v1'}), patch.object(llm.urllib.request, 'build_opener') as network:
            with self.assertRaises(PermissionError): llm.chat([{'role': 'user', 'content': 'private'}], retries=1)
            network.assert_not_called()

    def test_approved_cloud_no_provider_extension(self):
        with tempfile.TemporaryDirectory() as td, patch.object(llm, 'ROOT', Path(td)), patch.object(llm, 'load_env', return_value={'DEEPSEEK_API_KEY': 'test-placeholder', 'DEEPSEEK_BASE_URL': 'https://api.example/v1', 'FIREKB_PROVIDER': 'custom'}):
            eg.grant_recipient(Path(td) / '00_配置', ['personal'], 'https://api.example/v1', ds.PURPOSE, 'manual')
            opener = unittest.mock.Mock(); opener.open.return_value = io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
            with patch.object(llm.urllib.request, 'build_opener', return_value=opener):
                self.assertEqual(llm.chat([{'role': 'user', 'content': 'private'}], disable_thinking=True)['content'], 'ok')
            body = json.loads(opener.open.call_args.args[0].data)
            self.assertNotIn('thinking', body)

    def test_local_no_key_and_oversize_no_network(self):
        cfg = {'DEEPSEEK_BASE_URL': 'http://127.0.0.1:11435/v1', 'FIREKB_PROVIDER': 'ollama'}
        with patch.object(llm, 'load_env', return_value=cfg), patch.object(llm.urllib.request, 'build_opener') as opener:
            self.assertTrue(llm.has_valid_key(cfg))
            with self.assertRaises(RuntimeError): llm.chat([{'role': 'user', 'content': 'x' * 10000}])
            opener.assert_not_called()

    def test_local_wire_dummy_key_and_reasoning_control(self):
        cfg = {'DEEPSEEK_BASE_URL': ds.HOST + '/v1', 'FIREKB_PROVIDER': 'ollama', 'DEEPSEEK_API_KEY': 'dummy-cloud-secret-must-stay-local'}
        opener = unittest.mock.Mock(); opener.open.return_value = io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
        with patch.object(llm, 'load_env', return_value=cfg), patch.object(llm.urllib.request, 'build_opener', return_value=opener):
            llm.chat([{'role':'user','content':'hello'}], disable_thinking=True)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer ollama')
        self.assertEqual(json.loads(request.data)['reasoning_effort'], 'none')

    def test_cli_single_run_authorization_not_persistent(self):
        class Cloud(BaseEngine):
            spec = EngineSpec(kind='asr', name='cloud', egress='audio')
        with tempfile.TemporaryDirectory() as td:
            eg.require_consent({'asr': Cloud()}, td, allow_flag=True)
            with self.assertRaises(SystemExit): eg.require_consent({'asr': Cloud()}, td)
            self.assertFalse(eg.load_consent(td).get('granted'))

    def test_unknown_category_fails_even_with_cli_flag(self):
        class Cloud(BaseEngine):
            spec = EngineSpec(kind='parser', name='cloud', egress='unknown')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit): eg.require_consent({'parser': Cloud()}, td, allow_flag=True)


if __name__ == '__main__': unittest.main(argv=[__file__])
