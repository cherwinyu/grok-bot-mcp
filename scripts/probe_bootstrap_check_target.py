"""Windows-only regression probe. Uses a fake .cmd interpreter, not real credentials.

This probe was PREPARED but NOT RUN in the Linux review environment. Run with:
  python probe_bootstrap_check_target.py --source <candidate-source>
It expects -Check/-Target to be rejected before the legacy helper is dispatched.
No actual Python package is executed by the fake interpreter; no credential API is used.
"""
from pathlib import Path
import argparse, json, os, shutil, subprocess, sys, tempfile

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    if sys.platform!='win32':
        print(json.dumps({'status':'NOT_RUN','reason':'native_windows_required'}))
        return 2
    script=args.source.resolve()/'scripts/setup.ps1'
    if not script.is_file():
        print(json.dumps({'status':'NOT_RUN','reason':'candidate_script_missing'}));return 2
    shell=shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        print(json.dumps({'status':'NOT_RUN','reason':'powershell_missing'}));return 2
    with tempfile.TemporaryDirectory(prefix='fictional-bootstrap-review-') as name:
        tmp=Path(name); marker=tmp/'helper-selected.txt'; fake=tmp/'probe-python.cmd'
        fake.write_text('@echo off\nsetlocal\necho %* | findstr /L /C:"grokbot_mcp.init_credentials" >nul\n'
                        'if not errorlevel 1 echo SELECTED>"%~dp0helper-selected.txt"\nexit /b 0\n',encoding='ascii')
        env={**os.environ,'LOCALAPPDATA':str(tmp),'GROKBOT_MCP_REGISTRY':str(tmp/'missing-registry.json')}
        command=[shell,'-NoProfile','-NonInteractive','-File',str(script),
                 '-Python',str(fake),'-InstallDir',str(tmp/'never-install'),
                 '-Target','fictional-review-target']
        control=subprocess.run(command,cwd=tmp,env=env,stdin=subprocess.DEVNULL,
                               capture_output=True,timeout=30)
        control_ok=marker.exists() and control.returncode==0
        marker.unlink(missing_ok=True)
        if not control_ok:
            print(json.dumps({'status':'INCONCLUSIVE','reason':'legacy_control_did_not_reach_fake_helper',
                              'real_credentials_used':False}))
            return 2
        proc=subprocess.run([*command,'-Check'],cwd=tmp,env=env,stdin=subprocess.DEVNULL,
                            capture_output=True,timeout=30)
        selected=marker.exists()
        # A fixed implementation must explicitly reject the incompatible flags.
        # A parser/install/policy failure is NOT a passing behavioral gate.
        rejection=(proc.returncode!=0 and b'invalid_arguments' in proc.stdout.lower())
        status='FAIL' if selected else ('PASS' if rejection else 'INCONCLUSIVE')
        result={'status':status,'legacy_control_selected_helper':True,
                'legacy_helper_selected_with_check':selected,'process_exit_code':proc.returncode,
                'real_credentials_used':False,'real_webhook_sent':False}
        print(json.dumps(result))
        return 1 if selected else (0 if rejection else 2)
if __name__=='__main__':raise SystemExit(main())
