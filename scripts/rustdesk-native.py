import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / 'deployment/rustdesk'
PRIVATE = ROOT / 'temp/rustdesk-native'
ARCHIVE_SHA256 = '0565c41affe6c3f409b0bddfaf5a24ccbf3f64f5f8e3fec250b69a0d5f6bdbcf'


def ssh_helper():
    spec = importlib.util.spec_from_file_location('rustdesk_ssh', ROOT / 'scripts/tencent-ssh.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stage_payload():
    archive = (PRIVATE / 'rustdesk-server-linux-amd64-1.1.16.zip').read_bytes()
    if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError('ARCHIVE_HASH_MISMATCH')
    names = ['rustdesk.slice', 'rustdesk-hbbs.service', 'rustdesk-hbbr.service', 'rustdesk-firewall.service', 'journald.conf', 'firewall.nft', 'manage.py', 'validate.py', 'README.md']
    files = {name: base64.b64encode((DEPLOYMENT / name).read_bytes()).decode() for name in names}
    files['LICENSE'] = base64.b64encode((PRIVATE / 'LICENSE').read_bytes()).decode()
    return json.dumps({'archive': base64.b64encode(archive).decode(), 'files': files}).encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stage', 'check', 'validate', 'activate', 'status'])
    args = parser.parse_args()
    helper = ssh_helper()
    data = None
    if args.action == 'stage':
        source = (DEPLOYMENT / 'manage.py').read_text(encoding='utf-8')
        remote = 'python3 -B -c ' + shlex.quote(source) + ' stage'
        data = stage_payload()
    elif args.action == 'validate':
        remote = 'python3 -B /opt/rustdesk/validate.py'
    else:
        remote = 'python3 -B /opt/rustdesk/manage.py ' + args.action
    result = subprocess.run(helper.ssh_argv(helper.settings()) + [remote], input=data, capture_output=True, timeout=180)
    if result.stdout:
        sys.stdout.write(result.stdout.decode('utf-8', errors='replace'))
    if result.returncode:
        sys.stderr.write(result.stderr.decode('utf-8', errors='replace')[:1500])
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('RUSTDESK_REMOTE_OPERATION_FAILED', file=sys.stderr)
        raise SystemExit(1)
