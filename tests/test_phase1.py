"""Infrastructure tests; no restaurant logic or live model required."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from bistro.config import load_settings
from bistro import paths
import common
import setup_environment as setup
import launch_application as launch

class ConfigurationTests(unittest.TestCase):
    def settings(self, **values):
        with patch('bistro.config.read_preferences', return_value=None), patch('bistro.config.load_dotenv'), patch.dict(os.environ, values, clear=True):
            return load_settings()

    def test_defaults(self):
        s = self.settings()
        self.assertEqual(s.model, 'qwen2.5:7b')
        self.assertEqual(s.port, 8000)

    def test_reject_remote_service(self):
        for url in ('https://example.com', 'http://127.0.0.1/path', 'http://user:pass@localhost:11434'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.settings(BISTRO_OLLAMA_URL=url)

    def test_invalid_settings(self):
        for values in ({'BISTRO_PORT':'70000'}, {'BISTRO_HOST':'0.0.0.0'},
                       {'BISTRO_MODEL':'qwen2.5'}, {'BISTRO_TAX_RATE':'NaN'},
                       {'BISTRO_SERVICE_RATE':'-1'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.settings(**values)

    def test_paths_independent_of_cwd(self):
        before = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='bistro space ') as folder:
            try:
                os.chdir(folder)
                self.assertEqual(paths.ROOT, ROOT)
                self.assertEqual(paths.DATABASE, ROOT / 'data/restaurant.db')
            finally:
                os.chdir(before)

class PrerequisiteTests(unittest.TestCase):
    def test_exact_model(self):
        self.assertFalse(common.has_model({'models':[{'name':'qwen2.5:3b'}]}, 'qwen2.5:7b'))
        self.assertTrue(common.has_model({'models':[{'name':'qwen2.5:7b'}]}, 'qwen2.5:7b'))

    def test_reuse_ollama_without_starting(self):
        with patch('common.ollama_executable', return_value='ollama'), patch('common.local_json', return_value={'models':[]}), patch('common.subprocess.Popen') as spawn:
            tags, owned = common.ensure_ollama(load_settings())
            self.assertIsNone(owned)
            spawn.assert_not_called()

    def test_check_never_starts_service(self):
        with patch('common.ollama_executable', return_value='ollama'), patch('common.local_json', side_effect=OSError), patch('common.subprocess.Popen') as spawn:
            with self.assertRaises(RuntimeError):
                common.ensure_ollama(load_settings(), start=False)
            spawn.assert_not_called()

    def test_setup_failure_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'requirements.txt').write_text('')
            (root/'data').mkdir(); database=root/'data/restaurant.db'; database.write_bytes(b'existing data')
            marker=root/'runtime/setup.json'; marker.parent.mkdir(); marker.write_text('{}')
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'MARKER', marker), patch.object(setup, 'require_venv'), patch.object(setup.subprocess, 'run'), patch.object(setup, 'confirm', return_value=True), patch('configure_ai.configure', return_value=False), patch('bistro.database.initialize_database', side_effect=RuntimeError('database failure')), patch('sys.argv', ['setup','--inside']):
                with self.assertRaises(RuntimeError):
                    setup.main()
            self.assertEqual(database.read_bytes(), b'existing data')
            self.assertFalse(marker.exists())

    def test_incomplete_setup_blocks_launch(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(launch, 'require_venv'), patch.object(launch, 'MARKER', Path(folder)/'missing'):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                launch.check_setup()

    def test_stop_does_not_terminate_finished_process(self):
        from unittest.mock import Mock
        process=Mock(); process.poll.return_value=0
        common.stop_owned(process)
        process.terminate.assert_not_called()


class ApplicationIntegrationTests(unittest.TestCase):
    def test_real_server_readiness_dashboard_and_cleanup(self):
        import socket
        import urllib.request
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        opened = []
        def browser(url):
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=3) as response:
                self.assertIn(b'Phase 1', response.read())
            with opener.open(url+'/static/css/dashboard.css', timeout=3) as response:
                self.assertIn(b'@media', response.read())
            opened.append(url)
            raise KeyboardInterrupt
        with patch.object(launch, 'check_setup'), patch.object(launch, 'ensure_ollama', return_value=({'models':[{'name':'qwen2.5:7b'}]}, None)), patch.dict(os.environ, {'BISTRO_PORT':str(port)}), patch('sys.argv',['launcher']), patch.object(launch.webbrowser, 'open', side_effect=browser):
            self.assertEqual(launch.main(), 0)
        self.assertEqual(len(opened), 1)
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(('127.0.0.1',port)), 0)

if __name__ == '__main__':
    unittest.main()
