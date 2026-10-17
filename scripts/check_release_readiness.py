"""Check evidence completeness for a selected candidate; never authorizes publication.

Accepts a small, operator-reviewed evidence index. A boolean in a report is not
proof of remote access, an OS sandbox, service permission or a human approval.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
if __package__:
    from .audit_publication import safe_path, AuditError
else:
    from audit_publication import safe_path, AuditError

REQUIRED = ('native_tests','installed_package','source_scan','archive_scan','protocol_review',
            'privacy_scope_review','client_acceptance','routine_acceptance','security_review')
KINDS = {'PASS','FAIL','NOT_RUN','BLOCKED','INCOMPLETE'}


def read_object(raw):
    if len(raw)>1024*1024 or raw.startswith(b'\xef\xbb\xbf'):raise AuditError('json_limit_or_encoding')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise AuditError('duplicate_field')
            result[key]=value
        return result
    def nonfinite(value):raise AuditError('nonfinite')
    def finite_float(value):
        result=float(value)
        if not math.isfinite(result):raise AuditError('nonfinite')
        return result
    try:
        return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=nonfinite,parse_float=finite_float)
    except (UnicodeError,ValueError,RecursionError):raise AuditError('json_invalid') from None


def assess(index, directory: Path, commit: str):
    if (type(index) is not dict or set(index)!={'schema_version','source_commit','evidence'}
            or type(index['schema_version']) is not int or index['schema_version']!=1
            or index['source_commit']!=commit or not re.fullmatch('[0-9a-f]{40}',commit)
            or type(index['evidence']) is not dict or set(index['evidence'])!=set(REQUIRED)):
        raise AuditError('index_invalid')
    states={}; hashes={}
    for key in REQUIRED:
        item=index['evidence'][key]
        if (type(item) is not dict or set(item)!={'status','file','sha256'}
                or type(item['status']) is not str or item['status'] not in KINDS):
            raise AuditError('entry_invalid')
        status=item['status'];name=item['file'];expected=item['sha256']
        if status=='PASS':
            if (type(name) is not str or not re.fullmatch('[a-zA-Z0-9_.-]+\\.json',name)
                    or name in {'.','..'} or type(expected) is not str or not re.fullmatch('[0-9a-f]{64}',expected)):
                raise AuditError('entry_invalid')
            path=directory/name;safe_path(path)
            if path.stat().st_size>1024*1024:raise AuditError('evidence_limit')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=expected:raise AuditError('evidence_hash_mismatch')
            # All evidence must name the exact final source, not an earlier green run.
            body=read_object(raw)
            if type(body) is not dict or body.get('source_commit')!=commit:
                raise AuditError('evidence_commit_mismatch')
            hashes[key]=expected
        elif name is not None or expected is not None:
            raise AuditError('nonpass_evidence_must_be_unset')
        states[key]=status
    all_pass=all(value=='PASS' for value in states.values())
    return {'schema_version':1,'source_commit':commit,'evidence_states':states,
            'evidence_hashes':hashes,'index_complete':all_pass,
            'blocking_gates':[key for key,value in states.items() if value!='PASS'],
            'public_release_ready':False,'publication_authorized':False,'human_qa_accepted':False,
            'scope':'index consistency only; independent human release/rights/service approval still required'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--commit',required=True)
    args=parser.parse_args(argv)
    try:
        safe_path(args.index)
        if args.index.stat().st_size>1024*1024:raise AuditError('index_limit')
        report=assess(read_object(args.index.read_bytes()),args.index.parent,args.commit)
        print(json.dumps(report,indent=2));return 0 if report['index_complete'] else 2
    except (OSError,ValueError,AuditError):
        print(json.dumps({'ok':False,'code':'release_index_refused','publication_authorized':False}));return 3


if __name__=='__main__':
    raise SystemExit(main())
