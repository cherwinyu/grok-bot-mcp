"""Publication checks are consistency checks, not permission to publish."""
from __future__ import annotations
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path

from tests.support import temporary_directory
import unittest
import zipfile
from scripts.audit_publication import Audit, AuditError
from scripts.check_release_readiness import assess, REQUIRED


class PublicationScannerTest(unittest.TestCase):
    def test_no_match_does_not_clear_privacy(self):
        a=Audit();a.inspect('fictional',b'plain text')
        self.assertTrue(a.summary()['scan_complete']);self.assertTrue(a.summary()['no_rule_matches'])
        self.assertFalse(a.summary()['privacy_cleared']);self.assertFalse(a.summary()['publication_authorized'])
    def test_detects_secret_without_echo(self):
        value='ghp_'+'A'*30
        a=Audit();a.inspect('fictional',value.encode())
        self.assertEqual(a.findings[0]['code'],'credential_shape')
        self.assertNotIn(value,json.dumps(a.findings));self.assertNotIn(value,json.dumps(a.summary()))
    def test_nonutf_is_gap_not_clean(self):
        a=Audit();a.inspect('binary',b'\xff\xfe')
        self.assertFalse(a.summary()['scan_complete'])
    def test_null_is_binary_gap(self):
        a=Audit();a.inspect('binary',b'a\0b')
        self.assertEqual(a.gaps[0]['code'],'binary_not_scanned')
    def test_file_limit(self):
        a=Audit(max_file=2);a.inspect('big',b'hello')
        self.assertEqual(a.gaps[0]['code'],'file_limit')
    def test_total_limit(self):
        a=Audit(max_bytes=2);a.inspect('big',b'hello')
        self.assertEqual(a.gaps[0]['code'],'total_limit')
    def test_object_limit(self):
        a=Audit(max_objects=1);a.inspect('one',b'a');a.inspect('two',b'b')
        self.assertEqual(a.gaps[0]['code'],'total_limit')
    def archive(self, names):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for name,value in names:z.writestr(name,value)
        return out.getvalue()
    def test_nested_archive_is_actually_scanned(self):
        token='crsr_'+'B'*30
        a=Audit();a.inspect('outer.zip',self.archive([('inner.zip',self.archive([('token.txt',token)]))]))
        self.assertEqual(a.summary()['finding_count'],1)
        self.assertNotIn(token,json.dumps(a.findings))
    def test_unsafe_archive_member_is_not_extracted(self):
        a=Audit();a.inspect('outer.zip',self.archive([('../escape','data')]))
        self.assertEqual(a.gaps[0]['code'],'unsafe_archive_member')
    def test_archive_expansion_limit(self):
        a=Audit(max_bytes=1000);a.inspect('outer.zip',self.archive([('big','x'*2000)]))
        self.assertEqual(a.gaps[0]['code'],'archive_expansion_limit')
    def test_archive_depth_limit(self):
        raw=b'plain'
        for _ in range(5):raw=self.archive([('nested.zip',raw)])
        a=Audit();a.inspect('outer.zip',raw)
        self.assertTrue(any(x['code']=='archive_depth_limit' for x in a.gaps))
    def test_lfs_is_unread_not_absence_of_secret(self):
        a=Audit();a.inspect('lfs',b'version https://git-lfs.github.com/spec/v1\noid sha256:placeholder')
        self.assertFalse(a.summary()['scan_complete'])
    def test_summary_omits_private_locator(self):
        a=Audit();a.inspect('PRIVATE_LOCATOR',b'\xff')
        self.assertNotIn('PRIVATE_LOCATOR',json.dumps(a.summary()))
    def test_corrupt_archive_is_gap(self):
        a=Audit();a.inspect('broken',b'PK\x03\x04not-a-zip')
        self.assertEqual(a.gaps[0]['code'],'unreadable_archive')


class ReleaseGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=temporary_directory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.commit='a'*40
    def index(self):
        return {'schema_version':1,'source_commit':self.commit,'evidence':{k:{'status':'NOT_RUN','file':None,'sha256':None} for k in REQUIRED}}
    def test_no_evidence_never_means_ready(self):
        report=assess(self.index(),self.root,self.commit)
        self.assertFalse(report['index_complete']);self.assertFalse(report['publication_authorized'])
        self.assertEqual(set(report['blocking_gates']),set(REQUIRED))
    def test_hash_and_commit_are_checked(self):
        value=self.index();raw=json.dumps({'source_commit':self.commit}).encode();(self.root/'native.json').write_bytes(raw)
        value['evidence']['native_tests']={'status':'PASS','file':'native.json','sha256':hashlib.sha256(raw).hexdigest()}
        report=assess(value,self.root,self.commit)
        self.assertEqual(report['evidence_states']['native_tests'],'PASS');self.assertFalse(report['index_complete'])
    def test_stale_source_evidence_refused(self):
        value=self.index();raw=json.dumps({'source_commit':'b'*40}).encode();(self.root/'native.json').write_bytes(raw)
        value['evidence']['native_tests']={'status':'PASS','file':'native.json','sha256':hashlib.sha256(raw).hexdigest()}
        with self.assertRaises(AuditError):assess(value,self.root,self.commit)
    def test_modified_evidence_refused(self):
        value=self.index();(self.root/'native.json').write_bytes(b'{}')
        value['evidence']['native_tests']={'status':'PASS','file':'native.json','sha256':'a'*64}
        with self.assertRaises(AuditError):assess(value,self.root,self.commit)
    def test_arbitrary_evidence_path_refused(self):
        value=self.index();value['evidence']['native_tests']={'status':'PASS','file':'../escape.json','sha256':'a'*64}
        with self.assertRaises(AuditError):assess(value,self.root,self.commit)
    def test_boolean_schema_refused(self):
        value=self.index();value['schema_version']=True
        with self.assertRaises(AuditError):assess(value,self.root,self.commit)
    def test_unknown_or_missing_gate_refused(self):
        value=self.index();value['evidence'].pop('routine_acceptance')
        with self.assertRaises(AuditError):assess(value,self.root,self.commit)
    def test_all_operator_reported_passes_still_do_not_authorize_publication(self):
        value=self.index();raw=json.dumps({'source_commit':self.commit,'claim':'operator-supplied, not independently attested'}).encode()
        (self.root/'all.json').write_bytes(raw)
        for key in REQUIRED:value['evidence'][key]={'status':'PASS','file':'all.json','sha256':hashlib.sha256(raw).hexdigest()}
        report=assess(value,self.root,self.commit)
        self.assertTrue(report['index_complete']);self.assertFalse(report['public_release_ready'])
        self.assertFalse(report['publication_authorized']);self.assertFalse(report['human_qa_accepted'])
    def test_nonpassing_gate_cannot_smuggle_evidence_path(self):
        value=self.index();value['evidence']['routine_acceptance']['file']='private.json'
        with self.assertRaises(AuditError):assess(value,self.root,self.commit)


class PublicationBoundaryTest(unittest.TestCase):
    def test_git_tree_names_do_not_scan_binary_hashes(self):
        from scripts.audit_publication import tree_names
        raw=b'100644 sample.txt\0'+b'\xff'*20+b'40000 directory\0'+b'\0'*20
        self.assertEqual(tree_names(raw),b'sample.txt\ndirectory')
    def test_truncated_tree_refused(self):
        from scripts.audit_publication import tree_names
        with self.assertRaises(AuditError):tree_names(b'100644 x\0short')
    def test_tree_filename_can_contain_a_finding(self):
        from scripts.audit_publication import tree_names
        name=('ghp_'+'Z'*30).encode()
        a=Audit();a.inspect('names',tree_names(b'100644 '+name+b'\0'+b'x'*20))
        self.assertEqual(a.summary()['finding_categories'],{'credential_shape':1})
        self.assertNotIn(name.decode(),json.dumps(a.findings))
    def test_reviewed_negative_urls_are_exact_and_scoped(self):
        from scripts.scan_release import FIXTURE_URLS, reviewed_url_fixture
        for url in FIXTURE_URLS:
            self.assertTrue(reviewed_url_fixture('tests/test_p0_hardening.py',url))
            self.assertFalse(reviewed_url_fixture('tests/arbitrary.py',url))
            self.assertFalse(reviewed_url_fixture('tests/test_p0_hardening.py',url+'?extra=unknown'))
    def test_fixture_exception_does_not_exempt_secret_or_private_marker(self):
        from scripts.scan_release import _inspect
        name='tests/test_p0_hardening.py'
        token='Bearer '+'X'*24
        self.assertTrue(any(x.startswith('secret-shape') for x in _inspect(name,'x = '+repr(token))))
        self.assertTrue(any(x.startswith('private-marker') for x in _inspect(name,"x='fictional-marker'",('fictional-marker',))))
    def test_release_index_duplicate_fields_rejected(self):
        from scripts.check_release_readiness import read_object
        with self.assertRaises(AuditError):read_object(b'{"source_commit":"a","source_commit":"b"}')
    def test_release_index_nonfinite_rejected(self):
        from scripts.check_release_readiness import read_object
        for raw in (b'{"x":NaN}',b'{"x":1e999}'):
            with self.subTest(raw=raw),self.assertRaises(AuditError):read_object(raw)
    def test_release_index_bom_rejected(self):
        from scripts.check_release_readiness import read_object
        with self.assertRaises(AuditError):read_object(b'\xef\xbb\xbf{}')
