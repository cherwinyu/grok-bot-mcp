"""Version readers must inspect literals without executing package code."""
from pathlib import Path
from importlib import metadata
import shutil
import subprocess
import sys
import tomllib
import unittest
from unittest.mock import patch

from scripts.source_version import source_version
from tests.support import temporary_directory


class SourceVersionTest(unittest.TestCase):
    def test_missing_source_uses_installed_metadata(self):
        with temporary_directory() as name:
            self.assertEqual(source_version(Path(name)), metadata.version('grokbot-mcp-bridge'))

    def test_no_version_source_reports_unavailable(self):
        with temporary_directory() as name, patch('scripts.source_version.metadata.version',
                                                 side_effect=metadata.PackageNotFoundError):
            with self.assertRaisesRegex(ValueError, 'source_version_unavailable'):
                source_version(Path(name))

    def test_bootstrap_version_check_in_copied_sandbox(self):
        repo = Path(__file__).resolve().parents[1]
        script = (repo / 'scripts/setup.ps1').read_text(encoding='utf-8')
        check = script.split("$versionCheck = @'\n", 1)[1].split("\n'@", 1)[0]
        installed = metadata.version('grokbot-mcp-bridge')
        with temporary_directory() as name:
            root = Path(name)
            (root / 'scripts').mkdir()
            shutil.copyfile(repo / 'scripts/source_version.py', root / 'scripts/source_version.py')
            shutil.copyfile(repo / 'pyproject.toml', root / 'pyproject.toml')

            def probe(expected):
                result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', check, str(root)],
                                        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertNotIn('installation_mismatch', result.stdout + result.stderr)

            self.assertFalse((root / 'src').exists())
            probe(0)
            source = root / 'src/grokbot_mcp/__init__.py'
            source.parent.mkdir(parents=True)
            source.write_text(f'__version__ = {installed!r}\n', encoding='utf-8')
            self.assertEqual(source_version(root), installed)
            probe(0)
            source.write_text("__version__ = '0.0.0'\n", encoding='utf-8')
            probe(1)
            source.write_text('__version__ = calculate()\n', encoding='utf-8')
            probe(2)

    def test_asset_verifier_requires_source_identity(self):
        from scripts.verify_beta_assets import CandidateError, verify
        with temporary_directory() as name:
            with self.assertRaisesRegex(CandidateError, 'source_tree_missing'):
                verify(Path(name), Path(name))

    def test_native_worker_copy_does_not_import_source_helper(self):
        repo = Path(__file__).resolve().parents[1]
        with temporary_directory() as name:
            script = Path(name) / 'native_deployment_acceptance.py'
            shutil.copyfile(repo / 'scripts/native_deployment_acceptance.py', script)
            code = (
                "import runpy,sys; ns=runpy.run_path(sys.argv[1]); "
                "ns['main'].__globals__['worker']=lambda args: 0; "
                "sys.argv=['acceptance','--worker']; sys.exit(ns['main']())"
            )
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(script)],
                                    cwd=name, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_literal_is_read_without_execution(self):
        with patch.object(Path, "read_text", return_value="raise RuntimeError('must not execute')\n__version__ = '1.2.3'\n"):
            self.assertEqual(source_version(Path("fictional")), "1.2.3")

    def test_computed_or_invalid_versions_are_rejected(self):
        for source in ("__version__ = calculate()", "__version__ = 123", "unrelated = '1.2.3'"):
            with self.subTest(source=source), patch.object(Path, "read_text", return_value=source):
                with self.assertRaises(ValueError):
                    source_version(Path("fictional"))

    def test_build_configuration_uses_static_package_attribute(self):
        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertNotIn("version", config["project"])
        self.assertIn("version", config["project"]["dynamic"])
        self.assertEqual(config["tool"]["setuptools"]["dynamic"]["version"], {"attr": "grokbot_mcp.__version__"})
