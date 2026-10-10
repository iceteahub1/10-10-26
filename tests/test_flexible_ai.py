"""Mocked Ollama/Windows downloads; real temporary SQLite ordering tests below."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import configure_ai as ai
import download_policy as downloads
import launch_application as launch
from bistro import ai_preferences as prefs
from bistro.config import load_settings
from bistro.model_check import check_model
from bistro.ollama_client import ToolsUnsupported, OllamaError

def tags(*models):
    return {'models': [{'name': model} for model in models]}

class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        with patch('bistro.config.read_preferences', return_value=None):
            self.settings = load_settings()
        self.settings = replace(self.settings, ai_enabled=False)

    def configure(self, models, choices, checks=None, download=False, **extra):
        save = Mock()
        with patch.object(ai, 'ensure_ollama', return_value=(tags(*models), None)), patch.object(ai, 'check_model', side_effect=checks or [(True, 'native', 'passed')]) as probe, patch.object(ai, 'download_model', return_value=download) as pull:
            result = ai.configure(self.settings, change=True, choose=Mock(side_effect=choices), ask=Mock(return_value=False), save=save, **extra)
        return result, save, probe, pull

    def test_only_3b(self):
        result, save, probe, pull = self.configure(['qwen2.5:3b'], ['1'])
        self.assertTrue(result)
        save.assert_called_once_with(True, 'qwen2.5:3b', 'native')
        pull.assert_not_called()

    def test_only_7b(self):
        result, save, _, pull = self.configure(['qwen2.5:7b'], ['1'])
        self.assertTrue(result)
        save.assert_called_once_with(True, 'qwen2.5:7b', 'native')
        pull.assert_not_called()

    def test_both_choose_lightweight(self):
        _, save, _, pull = self.configure(['qwen2.5:3b', 'qwen2.5:7b'], ['2'])
        save.assert_called_once_with(True, 'qwen2.5:3b', 'native')
        pull.assert_not_called()

    def test_no_models_continue_without_ai(self):
        result, save, probe, pull = self.configure([], ['0'])
        self.assertFalse(result)
        save.assert_called_once_with(False, self.settings.model, 'native')
        probe.assert_not_called()
        pull.assert_not_called()

    def test_decline_download_then_non_ai(self):
        result, save, probe, pull = self.configure([], ['1', '0'])
        self.assertFalse(result)
        self.assertFalse(save.call_args.args[0])
        pull.assert_called_once()
        probe.assert_not_called()

    def test_incompatible_choose_another(self):
        _, save, _, _ = self.configure(['qwen2.5:7b', 'qwen2.5:3b'], ['1', '2'], checks=[(False,'native','invalid calls'), (True,'structured','passed')])
        save.assert_called_once_with(True, 'qwen2.5:3b', 'structured')

    def test_other_qwen_sizes_and_invalid_tags(self):
        self.assertEqual(ai.installed_models(tags('qwen2.5:14b', 'llama3:8b', 'qwen2.5:../escape')), ['qwen2.5:14b'])
        result, save, _, _ = self.configure(['qwen2.5:14b'], ['1'])
        self.assertTrue(result)
        self.assertEqual(save.call_args.args[1], 'qwen2.5:14b')

    def test_ollama_missing_is_optional_and_remembered(self):
        save = Mock()
        with patch.object(ai,'ensure_ollama',side_effect=RuntimeError('missing')), patch.object(ai, 'read_preferences', return_value=None):
            self.assertFalse(ai.configure(self.settings, change=True, save=save))
        save.assert_called_once_with(False, self.settings.model, 'native')

    def test_missing_saved_model_no_silent_substitution(self):
        settings = replace(self.settings, ai_enabled=True)
        save = Mock()
        with patch.object(ai, 'ensure_ollama', return_value=(tags('qwen2.5:3b'), None)), patch.object(ai,'check_model') as probe:
            ai.configure(settings, choose=lambda _: '0', save=save)
        probe.assert_not_called()
        save.assert_called_once_with(False, 'qwen2.5:7b', 'native')

    def test_saved_model_reused_after_successful_probe(self):
        settings = replace(self.settings, ai_enabled=True, model='qwen2.5:3b')
        save = Mock()
        choose = Mock()
        with patch.object(ai,'ensure_ollama',return_value=(tags('qwen2.5:3b','qwen2.5:7b'),None)), patch.object(ai,'read_preferences',return_value={'enabled':True,'model':settings.model,'transport':'native'}), patch.object(ai,'check_model',return_value=(True,'native','passed')):
            self.assertTrue(ai.configure(settings,ask=lambda _:True,choose=choose,save=save))
        choose.assert_not_called()
        save.assert_called_once_with(True,'qwen2.5:3b','native')

    def test_failed_optional_pull_allows_non_ai_choice(self):
        save = Mock()
        with patch.object(ai,'ensure_ollama',return_value=(tags(),None)), patch.object(ai,'download_model',side_effect=OSError('download interrupted')), patch.object(ai,'check_model') as probe:
            self.assertFalse(ai.configure(self.settings,change=True,choose=Mock(side_effect=['1','0']),save=save))
        probe.assert_not_called()
        self.assertFalse(save.call_args.args[0])

    def test_disabled_preference_skips_service_on_repeat_setup(self):
        with patch.object(ai,'ensure_ollama') as service:
            self.assertFalse(ai.configure(self.settings))
            service.assert_not_called()

    def test_launch_missing_selected_model_succeeds_without_switch(self):
        settings = replace(self.settings, ai_enabled=True)
        with patch.object(launch, 'check_setup'), patch('bistro.config.load_settings', return_value=settings), patch.object(launch,'ensure_ollama',return_value=(tags('qwen2.5:3b'), None)), patch('sys.argv',['run','--check']), patch.object(ai, 'save_preferences') as save:
            self.assertEqual(launch.main(),0)
            save.assert_not_called()

    def test_non_ai_launch_never_requires_ollama(self):
        with patch.object(launch, 'check_setup'), patch('bistro.config.load_settings', return_value=self.settings), patch.object(launch,'ensure_ollama') as service, patch('sys.argv',['run','--check']):
            self.assertEqual(launch.main(),0)
            service.assert_not_called()

class CompatibilityTests(unittest.TestCase):
    def client(self, structured=False):
        calls = [{'name':'browse_menu','arguments':{}}, {'name':'get_menu_by_category','arguments':{'category':'Mains'}}, {'name':'check_stock','arguments':{'item_id':1}}]
        replies = [{'content':json.dumps({'calls':[c]})} if structured else {'content':'', 'tool_calls':[{'function':c}]} for c in calls]
        replies.append({'content':json.dumps({'reply':'Thank you!', 'facts':['Test menu is available.']})})
        return Mock(chat=Mock(side_effect=([ToolsUnsupported('no tools')] if structured else []) + replies))

    def test_native_compatible_never_dispatches(self):
        with patch('bistro.tool_schemas.dispatch') as dispatch, patch('bistro.database.connection') as database:
            ok, mode, _ = check_model('http://localhost:11434','qwen2.5:3b',self.client())
            self.assertTrue(ok)
            self.assertEqual(mode,'native')
            dispatch.assert_not_called()
            database.assert_not_called()

    def test_structured_fallback_compatible(self):
        self.assertEqual(check_model('http://localhost:11434','qwen2.5:3b',self.client(True))[:2], (True,'structured'))

    def test_incompatible_malformed_wrong_tool_wrong_types_and_timeout(self):
        for response in [{'content':'hello'}, {'content':'{}'}, {'tool_calls':[{'function':{'name':'cancel_order','arguments':{}}}]}, {'content':json.dumps({'calls':[{'name':'check_stock','arguments':{'item_id':'1'}}]})}]:
            with self.subTest(response=response):
                self.assertFalse(check_model('http://localhost:11434','qwen2.5:3b',Mock(chat=Mock(return_value=response)))[0])
        self.assertFalse(check_model('http://localhost:11434','qwen2.5:3b',Mock(chat=Mock(side_effect=OllamaError('timed out'))))[0])

    def test_ungrounded_final_reply_rejected(self):
        client = self.client()
        replies = list(client.chat.side_effect)
        replies[-1] = {'content':json.dumps({'reply':'Order created!', 'facts':[]})}
        client.chat.side_effect = replies
        self.assertFalse(check_model('http://localhost:11434','qwen2.5:7b', client)[0])

class DownloadTests(unittest.TestCase):
    def test_pypi_compatible_wheel_hash_and_total_size(self):
        with tempfile.TemporaryDirectory() as folder:
            requirements = Path(folder)/'requirements.txt'
            requirements.write_text('example==1.0\n')
            wheel = {'filename':'example-1.0-py3-none-any.whl', 'packagetype':'bdist_wheel',
                     'url':'https://files.pythonhosted.org/example.whl', 'size':1000, 'digests':{'sha256':'a'*64}}
            with patch.object(downloads,'remote_json',return_value={'urls':[wheel]}):
                size, manifest = downloads.dependency_plan(requirements)
                self.assertEqual(size,1000)
                self.assertIn('--hash=sha256:' + 'a'*64,manifest)
            wheel.pop('size')
            with patch.object(downloads,'remote_json',return_value={'urls':[wheel]}):
                self.assertIsNone(downloads.dependency_plan(requirements)[0])
            wheel['url']='https://evil.example/example.whl'
            with patch.object(downloads,'remote_json',return_value={'urls':[wheel]}), self.assertRaises(ValueError):
                downloads.dependency_plan(requirements)

    def test_required_small_libraries_install_without_prompt(self):
        import setup_environment as setup
        with patch.object(downloads,'dependency_plan',return_value=(99_999_999,'verified')), patch.object(downloads.shutil,'disk_usage',return_value=Mock(free=10**12)), patch.object(setup,'confirm') as ask, patch.object(setup.subprocess,'run') as install, patch.object(setup,'verify_libraries'):
            setup.install_dependencies()
            ask.assert_not_called()
            self.assertIn('--require-hashes',install.call_args.args[0])

    def test_unknown_required_libraries_request_approval(self):
        import setup_environment as setup
        with patch.object(downloads,'dependency_plan',return_value=(None,'verified')), patch.object(setup,'confirm',return_value=False) as ask, patch.object(setup.subprocess,'run') as install:
            with self.assertRaisesRegex(RuntimeError,'declined'):
                setup.install_dependencies()
            ask.assert_called_once()
            install.assert_not_called()

    def approval(self, size, optional=False, privileged=False, answer=True):
        ask = Mock(return_value=answer)
        with patch.object(downloads.shutil,'disk_usage',return_value=Mock(free=10**12)):
            result = downloads.approve_download('test',size,200_000_000,optional=optional,privileged=privileged,confirm=ask,location=Path('.'))
        return result, ask

    def test_below_100mb_automatic(self):
        result, ask = self.approval(99_999_999)
        self.assertTrue(result)
        ask.assert_not_called()

    def test_exactly_100mb_requires_permission(self):
        result, ask = self.approval(100_000_000, answer=False)
        self.assertFalse(result)
        ask.assert_called_once()

    def test_above_100mb_requires_permission(self):
        self.approval(100_000_001)[1].assert_called_once()

    def test_unknown_size_requires_permission(self):
        self.approval(None)[1].assert_called_once()

    def test_small_optional_and_privileged_require_permission(self):
        self.approval(1, optional=True)[1].assert_called_once()
        self.approval(1, privileged=True)[1].assert_called_once()

    def test_insufficient_disk_never_downloads(self):
        ask = Mock()
        with patch.object(downloads.shutil,'disk_usage',return_value=Mock(free=1)):
            self.assertFalse(downloads.approve_download('test',100,200,optional=True,confirm=ask,location=Path('.')))
        ask.assert_not_called()

    def test_untrusted_sources_and_redirects_rejected(self):
        for url in ['http://pypi.org/file','https://evil.example/file','https://user@pypi.org/file']:
            with self.assertRaises(ValueError):
                downloads.verify_url(url)
        with self.assertRaises(ValueError):
            downloads.TrustedRedirect().redirect_request(None,None,302,'',{},'https://evil.example')

    def test_model_decline_never_pulls(self):
        with patch.object(ai,'model_download_size',return_value=4_700_000_000), patch.object(downloads.shutil,'disk_usage',return_value=Mock(free=10**12)), patch.object(ai.subprocess,'run') as run:
            with patch('bistro.config.read_preferences',return_value=None):
                self.assertFalse(ai.download_model(load_settings(),'qwen2.5:7b',ask=lambda _:False))
            run.assert_not_called()

    def test_unknown_model_size_approval_still_required(self):
        ask = Mock(return_value=False)
        with patch.object(ai,'model_download_size',side_effect=OSError('offline')), patch.object(ai.subprocess,'run') as run:
            with patch('bistro.config.read_preferences',return_value=None):
                self.assertFalse(ai.download_model(load_settings(),'qwen2.5:3b',ask=ask))
            ask.assert_called_once()
            run.assert_not_called()

class PreferenceAndOrderingTests(unittest.TestCase):
    def test_terminal_launcher_non_ai_and_missing_model(self):
        import launch_ordering as terminal
        with patch.object(terminal,'check_setup'), patch('sys.argv',['ordering']), patch.object(terminal,'ensure_ollama') as service, patch.object(terminal.subprocess,'call',return_value=0) as start:
            self.assertEqual(terminal.main(),0)
            service.assert_not_called()
            self.assertNotIn('--ai',start.call_args.args[0])
        with patch('bistro.config.read_preferences',return_value={'enabled':True,'model':'qwen2.5:7b','transport':'native'}), patch.object(terminal,'check_setup'), patch('sys.argv',['ai_chat','--ai']), patch.object(terminal,'ensure_ollama',return_value=(tags('qwen2.5:3b'),None)), patch.object(terminal.subprocess,'call') as start:
            with self.assertRaisesRegex(RuntimeError,'selected model qwen2.5:7b is missing'):
                terminal.main()
            start.assert_not_called()

    def test_save_reuse_and_enable_later(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs,'PREFERENCES',Path(folder)/'ai.json'), patch('bistro.config.load_dotenv'), patch.dict(os.environ, {'BISTRO_MODEL':'qwen2.5:7b'},clear=True):
            prefs.save_preferences(False,'qwen2.5:3b')
            self.assertFalse(load_settings().ai_enabled)
            settings = load_settings()
            with patch.object(ai,'ensure_ollama',return_value=(tags('qwen2.5:3b'),None)), patch.object(ai,'check_model',return_value=(True,'structured','passed')):
                self.assertTrue(ai.configure(settings,change=True,choose=lambda _: '1'))
            self.assertEqual(load_settings().model,'qwen2.5:3b')
            self.assertTrue(load_settings().ai_enabled)
            self.assertEqual(load_settings().ai_transport,'structured')
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_bad_preferences_fail_clearly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'ai.json'
            path.write_text('{"enabled":true,"model":"llama3:8b"}')
            with self.assertRaisesRegex(ValueError,'configure_ai.bat'):
                prefs.read_preferences(path)

    def test_real_sqlite_non_ai_ordering(self):
        from virtual_waiter_prototype import main
        with tempfile.TemporaryDirectory() as folder, patch('bistro.config.read_preferences',return_value={'enabled':False,'model':'qwen2.5:3b','transport':'native'}), patch('bistro.ollama_client.OllamaClient') as client:
            lines = iter(['{"action":"establish_session","arguments":{"table_no":3}}','{"action":"add_item","arguments":{"item_name":"Chicken Adobo","qty":1,"instructions":""}}','quit'])
            output=[]
            self.assertEqual(main(['--db',str(Path(folder)/'restaurant.db')],get_input=lambda _: next(lines),send_output=output.append),0)
            self.assertTrue(any('Chicken Adobo' in line for line in output))
            client.assert_not_called()

    def test_disabled_ai_never_simulates_response(self):
        from virtual_waiter_prototype import main
        with tempfile.TemporaryDirectory() as folder, patch('bistro.config.read_preferences',return_value={'enabled':False,'model':'qwen2.5:3b','transport':'native'}), patch('bistro.ollama_client.OllamaClient') as client:
            output=[]
            self.assertEqual(main(['--ai','--db',str(Path(folder)/'restaurant.db')],send_output=output.append),1)
            self.assertIn('AI chat unavailable',output[-1])
            client.assert_not_called()

if __name__ == '__main__':
    unittest.main()
