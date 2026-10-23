"""Delivery integrity checks are pure stdlib; no Windows API substitutes required."""
import base64
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

from scripts import verify_delivery as v
from scripts import scan_nested_delivery as s


def packed(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return out.getvalue()


def sample_wheel(extra=False):
    files = {"grokbot_mcp/__init__.py": b'__version__ = "0.1.4.dev4"\n',
             "grokbot_mcp_bridge-0.1.4.dev4.dist-info/METADATA": b'Metadata-Version: 2.4\nName: grokbot-mcp-bridge\nVersion: 0.1.4.dev4\n'}
    record = "grokbot_mcp_bridge-0.1.4.dev4.dist-info/RECORD"
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for name, raw in files.items():
        writer.writerow((name, 'sha256='+base64.urlsafe_b64encode(__import__('hashlib').sha256(raw).digest()).rstrip(b'=').decode(), str(len(raw))))
    writer.writerow((record, '', ''))
    files[record] = output.getvalue().encode()
    if extra:
        files['unlisted.txt'] = b'not recorded'
    return packed(files)


def sample_delivery(base):
    wheel = sample_wheel()
    init = b'__version__ = "0.1.4.dev4"\n'
    source = {'src/grokbot_mcp/__init__.py': init}
    blobs = {name: v.git_hash('blob', raw) for name, raw in source.items()}
    modes = {name: '100644' for name in source}
    tree = v.source_tree(blobs, modes)
    commit = 'tree '+tree+'\nauthor Fixture <fixture@example.invalid> 0 +0000\ncommitter Fixture <fixture@example.invalid> 0 +0000\n\nfictional commit\n'
    provenance = {'version': '0.1.4.dev4', 'source_commit_raw': commit, 'source_commit': v.git_hash('commit', commit.encode()),
                  'source_tree': tree, 'source_blobs': blobs, 'source_modes': modes, 'wheel_file': 'wheel/candidate.whl', 'wheel_sha256': v.sha(wheel)}
    files = {'source/'+name: raw for name, raw in source.items()}
    files.update({'wheel/candidate.whl': wheel, 'provenance.json': json.dumps(provenance).encode()})
    files['MANIFEST.json'] = json.dumps({'files': {name: {'size': len(raw), 'sha256': v.sha(raw)} for name, raw in files.items()}}).encode()
    path = base/'candidate.zip'
    path.write_bytes(packed(files))
    return path, files


class DeliveryIntegrityTest(unittest.TestCase):
    def test_complete_delivery_checks_tree_commit_record_and_not_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = sample_delivery(Path(tmp))
            result = v.verify(path, v.sha(path.read_bytes()))
            self.assertTrue(result['integrity_verified'])
            self.assertFalse(result['windows_tests_rerun'])
            self.assertFalse(result['qa_accepted'])
            self.assertFalse(result['remote_routine_verified'])

    def test_wrong_external_hash_rejected_even_with_optimized_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = sample_delivery(Path(tmp))
            proc = subprocess.run([sys.executable, '-O', str(Path(v.__file__)), '--bundle', str(path), '--sha256', '0'*64], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout)['code'], 'external_sha256_mismatch')

    def test_unlisted_wheel_member_cannot_hide_outside_record(self):
        with self.assertRaisesRegex(v.IntegrityError, 'record_coverage'):
            v.wheel_record(sample_wheel(extra=True))

    def test_source_tree_matches_real_git_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root/'a').write_bytes(b'a')
            (root/'a.d').mkdir()
            (root/'a.d/z.txt').write_bytes(b'z')
            (root/'b').mkdir()
            (root/'b/x.txt').write_bytes(b'X')
            subprocess.run(['git', '-C', str(root), '-c', 'core.autocrlf=false', 'add', '.'], check=True)
            blobs = {n: v.git_hash('blob', (root/n).read_bytes()) for n in ('a','a.d/z.txt','b/x.txt')}
            expected = subprocess.check_output(['git', '-C', str(root), 'write-tree'], text=True, encoding="utf-8", errors="replace").strip()
            self.assertEqual(v.source_tree(blobs, {n:'100644' for n in blobs}), expected)

    def test_provenance_tree_mismatch_rejected_after_manifest_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, files = sample_delivery(Path(tmp))
            obj = json.loads(files['provenance.json'])
            obj['source_tree'] = '0'*40
            files['provenance.json'] = json.dumps(obj).encode()
            del files['MANIFEST.json']
            files['MANIFEST.json'] = json.dumps({'files': {n:{'size':len(r),'sha256':v.sha(r)} for n,r in files.items()}}).encode()
            path.write_bytes(packed(files))
            with self.assertRaisesRegex(v.IntegrityError, 'git_tree_mismatch'):
                v.verify(path, v.sha(path.read_bytes()))

    def test_unsafe_paths_rejected(self):
        for name in ('../outside', '/absolute', 'C:/local', 'a\\b', 'a//b'):
            self.assertFalse(v.safe_name(name))


class NestedScanTest(unittest.TestCase):
    def test_nested_wheel_is_scanned_not_silently_skipped(self):
        marker = 'fictional-nested-marker'
        raw = packed({'wheel/candidate.whl': packed({'readme.txt': marker.encode()})})
        result = s.inspect(raw, 'delivery.zip', (marker,))
        self.assertEqual(result, ['private-marker delivery.zip!/wheel/candidate.whl!/readme.txt'])

    def test_clean_nested_text_wheel_and_delivery_pass(self):
        raw = packed({'wheel/candidate.whl': sample_wheel(), 'README.md': b'fictional delivery'})
        self.assertEqual(s.inspect(raw, 'delivery.zip'), [])

    def test_nested_traversal_and_duplicate_are_rejected(self):
        out = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(out, 'w') as z:
                z.writestr('../bad', 'bad')
                z.writestr('same.txt', 'one')
                z.writestr('same.txt', 'two')
        result = s.inspect(packed({'inside.whl':out.getvalue()}), 'delivery.zip')
        self.assertTrue(any(x.startswith('unsafe-path') for x in result))
        self.assertTrue(any(x.startswith('duplicate-member') for x in result))

    def test_recursive_depth_and_size_budgets_fail_closed(self):
        raw = b'text'
        for _ in range(7):
            raw = packed({'inside.zip': raw})
        self.assertTrue(any(x.startswith(('archive-limit','unreadable-archive')) for x in s.inspect(raw,'delivery.zip')))
        self.assertTrue(any(x.startswith('archive-limit') for x in s.inspect(packed({'readme.txt':b'text'}),'delivery.zip',budget=[1,2])))

    def test_invalid_nested_archive_is_not_accepted_as_text(self):
        self.assertEqual(s.inspect(b'not a zip','bad.whl'), ['unreadable-archive bad.whl'])
