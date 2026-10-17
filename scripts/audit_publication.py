"""Bounded, read-only local Git/privacy inventory; NOT permission to publish.

No fetch, upload, secret testing, history rewrite or automatic finding dismissal.
Detailed locators stay in a newly chosen external evidence directory. Stdout is
aggregate-only. Reachable history does not cover other refs or GitHub surfaces.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

RULES = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "credential_shape": re.compile(r"\b(?:gh[pousr]_|github_pat_|sk-|sk_|crsr_|whsec_)[A-Za-z0-9_\-]{16,}\b"),
    "authorization": re.compile(r"Bearer [A-Za-z0-9._\-]{16,}"),
    "home_path": re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/]|/(?:home|Users)/[A-Za-z0-9_.-]+/)"),
    "email_address": re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
    "conversation_url": re.compile(r"https:\/\/(?:chatgpt\.com|grok\.com)/(?:c|share)/[A-Za-z0-9-]+"),
}


class AuditError(ValueError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(path):
    for p in (path, *path.parents):
        try:
            info = p.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise AuditError('linked_path')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise AuditError('linked_path')


def _git(repo, *args, limit=8*1024*1024):
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ALLOW_PROTOCOL='',
               GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0',GIT_NO_REPLACE_OBJECTS='1',GIT_NO_LAZY_FETCH='1')
    command=['git','-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false','-C',str(repo),*args]
    with tempfile.TemporaryFile() as out:
        try:
            result=subprocess.run(command,env=env,stdout=out,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
                                  timeout=20,shell=False)
        except (OSError,subprocess.SubprocessError):
            raise AuditError('git_unavailable_or_timeout') from None
        if result.returncode:
            raise AuditError('git_read_failed')
        if out.tell()>limit:
            raise AuditError('git_output_limit')
        out.seek(0);return out.read(limit+1)


@dataclass
class Audit:
    max_bytes: int = 64*1024*1024
    max_file: int = 2*1024*1024
    max_objects: int = 6000
    bytes_read: int = 0
    objects_read: int = 0
    findings: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    inventory: list = field(default_factory=list)

    def gap(self, code, locator):
        if len(self.gaps) < self.max_objects:
            self.gaps.append({'code':code,'locator':locator})

    def inspect(self, locator, raw, depth=0):
        if self.objects_read >= self.max_objects or self.bytes_read+len(raw)>self.max_bytes:
            self.gap('total_limit',locator);return
        self.objects_read+=1;self.bytes_read+=len(raw)
        self.inventory.append({'locator':locator,'sha256':sha(raw),'bytes':len(raw)})
        if len(raw)>self.max_file:
            self.gap('file_limit',locator);return
        if raw.startswith(b'PK\x03\x04'):
            if depth>=3:
                self.gap('archive_depth_limit',locator);return
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    members=archive.infolist()
                    if len(members)>self.max_objects-self.objects_read or sum(m.file_size for m in members)>self.max_bytes-self.bytes_read:
                        self.gap('archive_expansion_limit',locator);return
                    seen=set()
                    for item in members:
                        path=PurePosixPath(item.filename)
                        name=locator+'!'+item.filename
                        if item.filename in seen:
                            self.gap('duplicate_archive_member',name);continue
                        seen.add(item.filename)
                        if (path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename
                                or stat.S_ISLNK(item.external_attr>>16) or item.flag_bits&1):
                            self.gap('unsafe_archive_member',name);continue
                        if item.is_dir():continue
                        if item.file_size>self.max_file:
                            self.gap('file_limit',name);continue
                        self.inspect(name,archive.open(item).read(self.max_file+1),depth+1)
            except (ValueError,OSError,zipfile.BadZipFile,RuntimeError):
                self.gap('unreadable_archive',locator)
            return
        if raw.startswith(b'version https://git-lfs.github.com/spec/v1'):
            self.gap('lfs_content_not_read',locator);return
        try:
            text=raw.decode('utf-8')
        except UnicodeError:
            self.gap('non_text_not_scanned',locator);return
        if '\x00' in text:
            self.gap('binary_not_scanned',locator);return
        for code,pattern in RULES.items():
            if pattern.search(text):
                # Never include the matched secret, context, or a low-entropy secret hash.
                if len(self.findings)<self.max_objects:
                    self.findings.append({'code':code,'locator':locator,'file_sha256':sha(raw)})
                else:self.gap('finding_limit',locator)

    def summary(self):
        return {'schema_version':1,'objects_read':self.objects_read,'bytes_read':self.bytes_read,
                'finding_count':len(self.findings),'gap_count':len(self.gaps),
                'finding_categories':dict(Counter(x['code'] for x in self.findings)),
                'gap_categories':dict(Counter(x['code'] for x in self.gaps)),
                'scan_complete':not self.gaps,'no_rule_matches':not self.findings,
                'privacy_cleared':False,'publication_authorized':False,
                'scope':'selected local Git objects/archives only; not a platform-wide privacy clearance'}


def tree_names(raw):
    """Parse SHA-1 tree records; names are scanned, object ids are not text."""
    offset=0; names=[]
    while offset<len(raw):
        end=raw.find(b'\0',offset)
        if end<0 or end+21>len(raw):raise AuditError('invalid_tree')
        header=raw[offset:end]
        if b' ' not in header:raise AuditError('invalid_tree')
        mode,name=header.split(b' ',1)
        if mode not in {b'40000',b'100644',b'100755',b'120000',b'160000'} or not name:
            raise AuditError('invalid_tree')
        names.append(name);offset=end+21
    return b'\n'.join(names)


def inspect_repository(repo, revision, history=False, all_refs=False):
    if all_refs and not history:
        raise AuditError('all_refs_requires_history')
    safe_path(repo)
    safe_path(repo/'.git')
    if not (repo/'.git').is_dir():raise AuditError('independent_repository_required')
    if not re.fullmatch(r'[0-9a-f]{40}',revision):
        raise AuditError('exact_commit_required')
    actual=_git(repo,'rev-parse','--verify',revision+'^{commit}').decode().strip()
    if actual != revision:raise AuditError('commit_mismatch')
    tree=_git(repo,'rev-parse',revision+'^{tree}').decode().strip()
    audit=Audit()
    local_ref_targets=[]
    if all_refs:
        # Read only the refs actually available in the checked-out repository.
        # This does not prove that the GitHub server/platform ref set is complete.
        raw_refs=_git(repo,'for-each-ref','--format=%(objectname) %(refname)',
                      'refs/heads','refs/remotes','refs/tags')
        local_ref_names=[]
        for line in raw_refs.splitlines():
            parts=line.split(b' ',1)
            if (len(parts)!=2 or not re.fullmatch(rb'[0-9a-f]{40}',parts[0])
                    or not parts[1].startswith((b'refs/heads/',b'refs/remotes/',b'refs/tags/'))):
                raise AuditError('invalid_local_reference')
            local_ref_targets.append(parts[0].decode('ascii'))
            local_ref_names.append(parts[1])
        if not local_ref_names:
            audit.gap('local_ref_inventory_empty',revision)
        else:
            audit.inspect('local-ref-names',b'\n'.join(local_ref_names))
    shallow=_git(repo,'rev-parse','--is-shallow-repository').decode().strip()
    if shallow!='false':audit.gap('shallow_history',revision)
    if history:
        command=('rev-list','--objects','--no-object-names','--all',revision) if all_refs else (
            'rev-list','--objects','--no-object-names',revision)
        ids=list(dict.fromkeys(_git(repo,*command).decode().splitlines()+local_ref_targets))
    else:
        ids=[]
        for entry in _git(repo,'ls-tree','-r','-z',revision).split(b'\0'):
            if not entry:continue
            metadata,name=entry.split(b'\t',1);mode,kind,oid=metadata.split()
            if mode!=b'100644' or kind!=b'blob':
                audit.gap('nonregular_git_entry',oid.decode());continue
            audit.inspect('tree-path:'+oid.decode(), name)
            ids.append(oid.decode())
        ids=list(dict.fromkeys(ids))
    if len(ids)>audit.max_objects:
        audit.gap('object_count_limit',revision);ids=ids[:audit.max_objects]
    for oid in ids:
        if not re.fullmatch(r'[0-9a-f]{40}',oid):raise AuditError('invalid_object_id')
        kind=_git(repo,'cat-file','-t',oid).decode().strip()
        try:
            size=int(_git(repo,'cat-file','-s',oid).decode())
            if size>audit.max_file or audit.bytes_read+size>audit.max_bytes:
                audit.gap('file_or_total_limit',oid);continue
            raw=_git(repo,'cat-file',kind,oid,limit=audit.max_file)
            expected=hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            if expected!=oid:raise AuditError('git_object_mismatch')
            if kind=='tree':
                audit.inspect('git-tree-names:'+oid,tree_names(raw))
            else:
                audit.inspect('git:'+oid,raw)
        except AuditError:
            audit.gap('git_object_unreadable',oid)
    summary={**audit.summary(),'source_commit':revision,'source_tree':tree,'history_requested':history,
             'enumerated_objects':len(ids),'other_refs_and_platform_surfaces_scanned':False,
             'all_available_local_refs_included':all_refs,'local_ref_count':len(local_ref_targets),
             'complete_remote_ref_inventory_verified':False}
    return audit,summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--history',action='store_true')
    parser.add_argument('--all-refs',action='store_true',help='With --history, scan locally available refs; not GitHub platform completeness.')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--private-candidate',action='store_true',help='Allow findings/gaps in private CI while retaining INCOMPLETE clearance.')
    args=parser.parse_args(argv)
    try:
        repo=args.repository.absolute();out=args.output.absolute()
        safe_path(out)
        if out.exists() or not out.parent.is_dir() or out.resolve().is_relative_to(repo.resolve()):
            raise AuditError('new_external_evidence_directory_required')
        audit,summary=inspect_repository(repo,args.revision,args.history,args.all_refs)
        out.mkdir()
        for name,value in (('summary.json',summary),('private-findings.json',audit.findings),
                           ('private-gaps.json',audit.gaps),('private-inventory.json',audit.inventory)):
            with (out/name).open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2)
        print(json.dumps(summary,indent=2))
        return 0 if args.private_candidate or summary['scan_complete'] and summary['no_rule_matches'] else 2
    except (AuditError,OSError,ValueError):
        print(json.dumps({'ok':False,'code':'audit_refused','privacy_cleared':False,'publication_authorized':False}))
        return 3


if __name__=='__main__':
    raise SystemExit(main())
