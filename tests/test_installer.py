"""Installer recovery tests: real temp filesystem, simulated external prerequisites."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import setup_environment as setup
import launch_application as launch


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bistro installer space ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / '.venv'
        self.target.mkdir()
        self.python = self.target / 'python.exe'
        self.python.touch()
        (self.target / '.bistro-root').write_text(str(self.root))
        (self.root / 'data').mkdir()
        self.data = self.root / 'data' / 'restaurant.db'
        self.data.write_bytes(b'existing receipts and stock')
        self.root_patch = patch.object(setup, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def result(self, version=(3,12), bits=64, **extra):
        return Mock(returncode=0, stdout=json.dumps({'prefix':str(self.target),'version':list(version),'bits':bits,'venv':True, **extra}))

    def test_reject_python313_and_32bit_venv(self):
        for result in [self.result((3,13)), self.result(bits=32), self.result(venv=False)]:
            with patch.object(setup.subprocess, 'run', return_value=result):
                self.assertFalse(setup.validate_environment(self.target, self.python))

    def test_accept_supported_interpreters(self):
        for version in [(3,11),(3,12)]:
            with patch.object(setup.subprocess, 'run', return_value=self.result(version)) as run:
                self.assertTrue(setup.validate_environment(self.target, self.python))
                self.assertEqual(run.call_args.kwargs['timeout'],10)

    def test_copied_environment_and_missing_python(self):
        (self.target / '.bistro-root').write_text('another computer')
        with patch.object(setup.subprocess,'run') as run:
            self.assertFalse(setup.validate_environment(self.target,self.python))
            run.assert_not_called()
        self.python.unlink()
        self.assertFalse(setup.validate_environment(self.target,self.python))

    def test_broken_probe_and_timeout(self):
        for error in [OSError('cannot execute'), subprocess.TimeoutExpired('python',10)]:
            with patch.object(setup.subprocess,'run', side_effect=error):
                self.assertFalse(setup.validate_environment(self.target,self.python))
        with patch.object(setup.subprocess,'run',return_value=Mock(returncode=0,stdout='invalid')):
            self.assertFalse(setup.validate_environment(self.target,self.python))

    def test_declined_recreation_preserves_all_files(self):
        with patch.object(setup,'PYTHON',self.python), patch.object(setup,'validate_environment',return_value=False), patch.object(setup,'confirm',return_value=False), patch.object(setup.venv,'EnvBuilder') as build:
            with self.assertRaisesRegex(RuntimeError,'declined'):
                setup.prepare_environment()
            build.assert_not_called()
        self.assertTrue(self.python.exists())
        self.assertEqual(self.data.read_bytes(), b'existing receipts and stock')

    def test_approved_recreation_archives_only_venv(self):
        def create(target):
            target.mkdir()
        with patch.object(setup,'PYTHON',self.python), patch.object(setup,'validate_environment',side_effect=[False,True]), patch.object(setup,'confirm',return_value=True), patch.object(setup.venv,'EnvBuilder') as build:
            build.return_value.create.side_effect = create
            setup.prepare_environment()
        backups = list(self.root.glob('.venv-backup-*'))
        self.assertEqual(len(backups),1)
        self.assertTrue((backups[0] / 'python.exe').exists())
        self.assertEqual(self.data.read_bytes(), b'existing receipts and stock')

    def test_valid_environment_reused(self):
        with patch.object(setup,'PYTHON',self.python), patch.object(setup,'validate_environment',return_value=True), patch.object(setup.venv,'EnvBuilder') as build:
            setup.prepare_environment()
            build.assert_not_called()


class PermissionTests(unittest.TestCase):
    def test_libraries_declined_never_download(self):
        with patch.object(setup,'confirm',return_value=False), patch.object(setup.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'declined'):
                setup.install_dependencies()
            run.assert_not_called()

    def test_library_failure_readable(self):
        with patch.object(setup,'confirm',return_value=True), patch.object(setup.subprocess,'run',side_effect=subprocess.CalledProcessError(1,'pip')):
            with self.assertRaisesRegex(RuntimeError,'internet connection'):
                setup.install_dependencies()

    def test_libraries_pinned_project_only(self):
        with patch.object(setup,'confirm',return_value=True), patch.object(setup.subprocess,'run') as run:
            setup.install_dependencies()
            argv = run.call_args_list[0].args[0]
            self.assertEqual(argv[0],sys.executable)
            self.assertIn(str(ROOT / 'requirements.txt'),argv)

    def test_ollama_install_declined(self):
        with patch.object(setup,'ollama_executable',side_effect=RuntimeError('missing')), patch.object(setup,'confirm',return_value=False), patch.object(setup.webbrowser,'open') as browser:
            with self.assertRaisesRegex(RuntimeError,'declined'):
                setup.ensure_ollama_installed()
            browser.assert_not_called()

    def test_ollama_guided_official_install_and_redetection(self):
        with patch.object(setup,'ollama_executable',side_effect=[RuntimeError('missing'),'ollama.exe']), patch.object(setup,'confirm',return_value=True), patch.object(setup.webbrowser,'open',return_value=True) as browser, patch('sys.stdin.isatty',return_value=True), patch('builtins.input',return_value=''):
            self.assertEqual(setup.ensure_ollama_installed(),'ollama.exe')
            browser.assert_called_once_with('https://ollama.com/download/windows')

    def test_noninteractive_has_no_implicit_permission(self):
        with patch('sys.stdin.isatty',return_value=False), patch('builtins.input') as ask:
            self.assertFalse(setup.confirm('Download?'))
            ask.assert_not_called()

    def test_check_setup_does_not_start_services(self):
        with patch.object(launch,'check_setup'), patch.object(launch,'ensure_ollama') as ollama, patch('sys.argv',['launch','--check-setup']):
            self.assertEqual(launch.main(),0)
            ollama.assert_not_called()

    def test_model_download_failure_preserves_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            data = root / 'data' / 'restaurant.db'
            data.write_bytes(b'existing inventory and receipts')
            marker = root / 'runtime' / 'setup.json'
            marker.parent.mkdir()
            with patch.object(setup,'ROOT',root), patch.object(setup,'MARKER',marker), patch.object(setup,'require_venv'), patch.object(setup,'install_dependencies'), patch.object(setup,'ensure_ollama_installed'), patch.object(setup,'ensure_ollama',return_value=({'models':[]},None)), patch.object(setup,'confirm',return_value=True), patch.object(setup,'ollama_executable',return_value='ollama'), patch.object(setup.subprocess,'run',side_effect=subprocess.CalledProcessError(1,'pull')), patch('sys.argv',['setup','--inside']):
                with self.assertRaisesRegex(RuntimeError,'model download failed'):
                    setup.main()
            self.assertEqual(data.read_bytes(),b'existing inventory and receipts')
            self.assertFalse(marker.exists())

    def test_pull_model_flag_still_requires_permission(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / 'runtime' / 'setup.json'
            marker.parent.mkdir()
            with patch.object(setup,'ROOT',root), patch.object(setup,'MARKER',marker), patch.object(setup,'require_venv'), patch.object(setup,'install_dependencies'), patch.object(setup,'ensure_ollama_installed'), patch.object(setup,'ensure_ollama',return_value=({'models':[]},None)), patch.object(setup,'confirm',return_value=False), patch.object(setup.subprocess,'run') as run, patch('sys.argv',['setup','--inside','--pull-model']):
                with self.assertRaisesRegex(RuntimeError,'declined'):
                    setup.main()
                run.assert_not_called()

    def test_pinned_dependency_validation_is_offline(self):
        import common
        with tempfile.TemporaryDirectory() as folder:
            requirements = Path(folder) / 'requirements.txt'
            requirements.write_text('example==1.2.3\n')
            with patch('importlib.metadata.version',return_value='1.2.3'), patch.object(common.subprocess,'run') as run:
                common.verify_libraries(requirements)
                self.assertEqual(run.call_args.args[0][-1],'check')
            with patch('importlib.metadata.version',return_value='1.2.4'):
                with self.assertRaisesRegex(RuntimeError,'version mismatch'):
                    common.verify_libraries(requirements)

    def test_completed_setup_with_bad_pins_offers_reinstall(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('')
            marker = root / 'runtime' / 'setup.json'
            marker.parent.mkdir()
            marker.write_text(json.dumps({'root':str(root),'requirements':__import__('hashlib').sha256(b'').hexdigest()}))
            with patch.object(setup,'ROOT',root), patch.object(setup,'MARKER',marker), patch.object(setup,'require_venv'), patch.object(setup,'verify_libraries',side_effect=RuntimeError('version mismatch')), patch.object(setup,'install_dependencies') as install, patch.object(setup,'ensure_ollama_installed'), patch.object(setup,'ensure_ollama',return_value=({'models':[{'name':'qwen2.5:7b'}]},None)), patch('bistro.database.initialize_database'), patch('sys.argv',['setup','--inside']):
                self.assertEqual(setup.main(),0)
                install.assert_called_once()


class BootstrapTests(unittest.TestCase):
    def test_batch_never_uses_unverified_python_or_py(self):
        for file in ['setup.bat','run.bat']:
            text = (ROOT / file).read_text()
            self.assertIn('windows_bootstrap.ps1',text)
            self.assertIn('%SystemRoot%',text)
            self.assertNotIn('py -3.',text)
            self.assertNotIn('python ',text)

    @unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell.exe'), 'PowerShell unavailable; simulated bootstrap scenarios require PowerShell')
    def test_simulated_powershell_bootstrap_scenarios(self):
        executable = shutil.which('pwsh') or shutil.which('powershell.exe')
        result = subprocess.run([executable,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/test_windows_bootstrap.ps1')], capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout + result.stderr)
        self.assertIn('simulated bootstrap checks passed',result.stdout)


if __name__ == '__main__':
    unittest.main()
