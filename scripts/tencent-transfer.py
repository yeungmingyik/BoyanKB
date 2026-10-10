import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tarfile
import threading


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "temp/tencent-cloud"
CHUNK = 1024 * 1024
CONFIG_MEMBERS = {
    "LICENSE", "docker-compose.tencent.yml", "docker-compose.tencent-ip-https.yml",
    "deployment/tencent/Caddyfile", "deployment/tencent/Caddyfile.ip-https",
    "deployment/tencent/frontend-entrypoint.sh", "deployment/tencent/redis-entrypoint.sh",
    "deployment/tencent/images.lock.json", "deployment/tencent/env.example",
    "deployment/tencent/README.md", "deployment/tencent/validate-config.py",
    "deployment/tencent/validate-ip-https.py", "deployment/tencent/install-host.sh",
    "scripts/tencent-restore.py", "scripts/tencent-accounts.py",
    "scripts/tencent-acceptance.py", "scripts/acceptance-presets.py", "docs/13-tencent-deployment.md",
}
SNAPSHOT_MEMBERS = {
    "database.dump", "data-files.tar.gz", "redis-data.tar.gz", "neo4j-data.tar.gz",
    "ollama-models.tar.gz", "runtime.env", "config.yaml", "builtin_models.local.yaml",
    "source-counts.json", "plan.json", "manifest.json",
    "data-files.tar.gz.inventory.json", "redis-data.tar.gz.inventory.json",
    "neo4j-data.tar.gz.inventory.json", "ollama-models.tar.gz.inventory.json",
}
REMOTE = r'''
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import base64

BASE = Path('/opt/boyankb')
MARKER = {'schema_version': 1, 'project': 'boyankb-tencent', 'public_ip': '193.112.17.218'}

def require(condition, code):
    if not condition:
        raise RuntimeError(code)

def emit(value):
    print(json.dumps(value), flush=True)

def relative_path(value):
    require(isinstance(value, str) and len(value) <= 240, 'INVALID_DESTINATION')
    path = PurePosixPath(value)
    require(not path.is_absolute() and 1 <= len(path.parts) <= 4 and str(path) == value, 'INVALID_DESTINATION')
    require(all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', part) and part not in ('.', '..') for part in path.parts), 'INVALID_DESTINATION')
    require(not path.name.endswith(('.partial', '.partial.json', '.lock')), 'RESERVED_DESTINATION')
    return path

def private_stat(path, directory=False):
    item = path.lstat()
    require(not stat.S_ISLNK(item.st_mode) and item.st_uid == 0, 'REMOTE_OWNER_OR_LINK_REJECTED')
    require(stat.S_ISDIR(item.st_mode) if directory else stat.S_ISREG(item.st_mode), 'REMOTE_FILE_TYPE_REJECTED')
    require(stat.S_IMODE(item.st_mode) == (0o700 if directory else 0o600), 'REMOTE_PERMISSIONS_REJECTED')
    require(directory or item.st_nlink == 1, 'REMOTE_HARDLINK_REJECTED')
    return item

def exists(path):
    return os.path.lexists(path)

def read_private(path):
    before = private_stat(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        require(os.fstat(handle.fileno()).st_ino == before.st_ino, 'REMOTE_FILE_CHANGED')
        return handle.read()

def digest(path):
    before = private_stat(path)
    value = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        require(os.fstat(handle.fileno()).st_ino == before.st_ino, 'REMOTE_FILE_CHANGED')
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    require(path.lstat().st_size == before.st_size and path.lstat().st_mtime_ns == before.st_mtime_ns, 'REMOTE_FILE_CHANGED')
    return value.hexdigest()

def target_directory(relative, create):
    require(os.geteuid() == 0, 'ROOT_DEPLOYMENT_REQUIRED')
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '24.04' and os.uname().machine == 'x86_64', 'SERVER_PLATFORM_MISMATCH')
    require(BASE.resolve() == BASE and BASE.parent.resolve() == BASE.parent, 'REMOTE_BASE_LINK_REJECTED')
    private_stat(BASE, True)
    require(json.loads(read_private(BASE / '.deployment-target.json')) == MARKER, 'DEPLOYMENT_MARKER_MISMATCH')
    directory = BASE / 'incoming'
    private_stat(directory, True)
    for part in relative.parts[:-1]:
        directory /= part
        if not exists(directory):
            if not create:
                return None
            directory.mkdir(mode=0o700)
        private_stat(directory, True)
    return directory

def exclusive_file(path, payload):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

def promote(source, target):
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    require(result == 0, 'ATOMIC_PROMOTION_REJECTED')

def state(target, payload):
    partial = target.with_name(target.name + '.partial')
    metadata = target.with_name(target.name + '.partial.json')
    if exists(target):
        info = private_stat(target)
        require(info.st_size == payload['size'] and digest(target) == payload['sha256'], 'COMPLETED_FILE_CONFLICT')
        return {'status': 'complete', 'size': info.st_size, 'sha256': payload['sha256']}
    if exists(metadata):
        require(json.loads(read_private(metadata)) == payload, 'PARTIAL_METADATA_MISMATCH')
    if exists(partial):
        require(exists(metadata), 'PARTIAL_METADATA_REQUIRED')
        info = private_stat(partial)
        require(info.st_size <= payload['size'], 'PARTIAL_TOO_LARGE')
        return {'status': 'ready', 'offset': info.st_size, 'prefix_sha256': digest(partial)}
    return {'status': 'ready', 'offset': 0, 'prefix_sha256': hashlib.sha256(b'').hexdigest()}

def main():
    os.umask(0o077)
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    require(set(request) == {'mode', 'destination', 'size', 'sha256'}, 'TRANSFER_REQUEST_INVALID')
    require(request['mode'] in ('probe', 'upload'), 'TRANSFER_MODE_INVALID')
    require(type(request['size']) is int and 0 < request['size'] <= 64 * 1024**3, 'TRANSFER_SIZE_INVALID')
    require(isinstance(request['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', request['sha256']), 'TRANSFER_HASH_INVALID')
    relative = relative_path(request['destination'])
    directory = target_directory(relative, request['mode'] == 'upload')
    if directory is None:
        emit({'status': 'ready', 'offset': 0, 'prefix_sha256': hashlib.sha256(b'').hexdigest(), 'target_validated': True})
        return
    target = directory / relative.name
    payload = {key: request[key] for key in ('destination', 'size', 'sha256')}
    if request['mode'] == 'probe':
        emit({**state(target, payload), 'target_validated': True})
        return
    lock = target.with_name(target.name + '.lock')
    lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        private_stat(lock)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = state(target, payload)
        if current['status'] == 'complete':
            emit(current)
            return
        require(shutil.disk_usage(directory).free >= request['size'] - current['offset'] + 64 * 1024**2, 'REMOTE_DISK_SPACE_INSUFFICIENT')
        partial = target.with_name(target.name + '.partial')
        metadata = target.with_name(target.name + '.partial.json')
        if not exists(metadata):
            exclusive_file(metadata, json.dumps(payload, sort_keys=True).encode())
        if not exists(partial):
            exclusive_file(partial, b'')
        private_stat(partial)
        fd = os.open(partial, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            require(os.fstat(fd).st_size == current['offset'], 'PARTIAL_CHANGED')
            emit(current)
            remaining = request['size'] - current['offset']
            while remaining:
                block = sys.stdin.buffer.read(min(1024 * 1024, remaining))
                require(block, 'UPLOAD_INTERRUPTED')
                view = memoryview(block)
                while view:
                    count = os.write(fd, view)
                    view = view[count:]
                remaining -= len(block)
            require(sys.stdin.buffer.read(1) == b'', 'UPLOAD_EXCESS_DATA')
            os.fsync(fd)
        finally:
            os.close(fd)
        require(digest(partial) == request['sha256'], 'REMOTE_SHA256_MISMATCH')
        promote(partial, target)
        metadata.unlink()
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        emit({'status': 'complete', 'size': request['size'], 'sha256': request['sha256'], 'resumed_bytes': current['offset']})
    finally:
        os.close(lock_fd)

if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        emit({'status': 'error', 'code': str(error) if re.fullmatch(r'[A-Z0-9_]+', str(error)) else 'REMOTE_TRANSFER_FAILED'})
        raise SystemExit(1)
    except Exception:
        emit({'status': 'error', 'code': 'REMOTE_TRANSFER_FAILED'})
        raise SystemExit(1)
'''


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def digest(path, limit=None):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        remaining = limit
        while remaining is None or remaining:
            block = handle.read(CHUNK if remaining is None else min(CHUNK, remaining))
            if not block:
                require(remaining in (None, 0), "LOCAL_PREFIX_TOO_SHORT")
                break
            value.update(block)
            if remaining is not None:
                remaining -= len(block)
    return value.hexdigest()


def safe_local(value):
    path = Path(os.path.abspath(value))
    require(".." not in Path(value).parts and path.is_relative_to(PRIVATE), "SOURCE_OUTSIDE_PRIVATE_DEPLOYMENT")
    for item in (path, *path.parents):
        require(not item.is_symlink() and not (hasattr(item, "is_junction") and item.is_junction()), "LOCAL_LINK_REJECTED")
        if item == ROOT:
            break
    require(path.is_file() and path.stat().st_nlink == 1, "LOCAL_REGULAR_FILE_REQUIRED")
    with path.open("rb") as handle:
        require(b"PRIVATE KEY-----" not in handle.read(8192), "PRIVATE_KEY_UPLOAD_FORBIDDEN")
    return path


def load_json(path):
    path = safe_local(path)
    require(path.stat().st_size <= 32 * 1024**2, "METADATA_TOO_LARGE")
    return json.loads(path.read_bytes())


def receipt_archive(path, prefix):
    if path.name.endswith(".tar.gz"):
        archive, receipt = path, path.with_suffix(".json")
    elif path.name.endswith(".tar.json"):
        archive, receipt = path.with_suffix(".gz"), path
    else:
        raise RuntimeError("ARCHIVE_OR_RECEIPT_REQUIRED")
    require(re.fullmatch(prefix + r"[A-Za-z0-9_.-]*\.tar\.gz", archive.name), "ARCHIVE_NAME_NOT_ALLOWLISTED")
    safe_local(archive)
    record = load_json(receipt)
    require(record.get("archive") == archive.name and record.get("size") == archive.stat().st_size, "ARCHIVE_RECEIPT_MISMATCH")
    require(isinstance(record.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", record["sha256"]), "ARCHIVE_RECEIPT_HASH_INVALID")
    with archive.open("rb") as handle:
        require(handle.read(2) == b"\x1f\x8b", "GZIP_ARCHIVE_REQUIRED")
    return archive, receipt, record


def image_source(path):
    archive, receipt, record = receipt_archive(path, "images")
    locked = json.loads((ROOT / "deployment/tencent/images.lock.json").read_bytes())
    require(record.get("format") == "boyankb-tencent-images-v1" and record.get("platform") == "linux/amd64" and record.get("images") == locked["images"], "IMAGE_RECEIPT_LOCK_MISMATCH")
    return record["sha256"] if path == archive else None


def config_source(path):
    archive, receipt, record = receipt_archive(path, "config-bundle-")
    require(record.get("format") == "boyankb-tencent-config-v1" and isinstance(record.get("files"), dict), "CONFIG_RECEIPT_INVALID")
    files = record["files"]
    require(files and set(files).issubset(CONFIG_MEMBERS), "CONFIG_MEMBER_NOT_ALLOWLISTED")
    seen = set()
    with tarfile.open(archive, "r:gz") as package:
        for member in package:
            require(member.name in files and member.name not in seen and member.isfile() and not member.issym() and not member.islnk() and member.size <= 16 * 1024**2, "CONFIG_TAR_MEMBER_REJECTED")
            source = ROOT / member.name
            require(source.is_file() and not source.is_symlink(), "CONFIG_SOURCE_MISSING")
            with package.extractfile(member) as content:
                actual = hashlib.sha256(content.read()).hexdigest()
            require(actual == files[member.name] == digest(source), "CONFIG_MEMBER_HASH_MISMATCH")
            seen.add(member.name)
    require(seen == set(files), "CONFIG_TAR_MEMBERS_MISSING")
    return record["sha256"] if path == archive else None


def snapshot_source(path):
    require(path.name in SNAPSHOT_MEMBERS, "SNAPSHOT_MEMBER_NOT_ALLOWLISTED")
    manifest = load_json(path.parent / "manifest.json")
    require(manifest.get("format") == "boyankb-tencent-snapshot-v1" and manifest.get("snapshot_validated") is True and manifest.get("consistency") == "application-quiesced" and manifest.get("writers_resumed") is True, "VALIDATED_SNAPSHOT_REQUIRED")
    components = manifest.get("components")
    require(isinstance(components, dict) and set(components).issubset(SNAPSHOT_MEMBERS), "SNAPSHOT_COMPONENTS_INVALID")
    if path.name == "runtime.env":
        content = path.read_bytes()
        expected = set(re.findall(rb"^([A-Z][A-Z0-9_]*)=", (ROOT / "deployment/tencent/env.example").read_bytes(), re.M))
        keys = re.findall(rb"^([A-Z][A-Z0-9_]*)=", content, re.M)
        require(set(keys) == expected and len(keys) == len(expected) and b"PRIVATE KEY" not in content and b"CLOUDFLARE" not in content, "RUNTIME_ENV_NOT_ALLOWLISTED")
    if path.name in components:
        component = components[path.name]
        require(component.get("size") == path.stat().st_size and re.fullmatch(r"[a-f0-9]{64}", component.get("sha256", "")), "SNAPSHOT_COMPONENT_RECEIPT_MISMATCH")
        return component["sha256"]
    if path.name.endswith(".inventory.json"):
        item = next((entry for entry in components.values() if entry.get("inventory") == path.name), None)
        require(item is not None, "SNAPSHOT_INVENTORY_NOT_REFERENCED")
        normalized = json.dumps(load_json(path), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        require(hashlib.sha256(normalized).hexdigest() == item["inventory_sha256"], "SNAPSHOT_INVENTORY_HASH_MISMATCH")
    else:
        require(path.name in ("manifest.json", "plan.json"), "SNAPSHOT_COMPONENT_NOT_REFERENCED")
    return None


def source_file(value):
    path = safe_local(value)
    relative = path.relative_to(PRIVATE)
    if len(relative.parts) == 1 and path.name.startswith("images"):
        expected = image_source(path)
    elif len(relative.parts) == 1 and path.name.startswith("config-bundle-"):
        expected = config_source(path)
    elif len(relative.parts) == 3 and relative.parts[0] == "snapshots":
        expected = snapshot_source(path)
    else:
        raise RuntimeError("SOURCE_NOT_ALLOWLISTED")
    before = path.stat()
    require(0 < before.st_size <= 64 * 1024**3, "SOURCE_SIZE_INVALID")
    actual = digest(path)
    require(expected is None or expected == actual, "LOCAL_SHA256_MISMATCH")
    require(file_identity(path.stat()) == file_identity(before), "LOCAL_FILE_CHANGED")
    return path, before, actual


def file_identity(item):
    return item.st_size, item.st_mtime_ns, item.st_ino, item.st_dev


def destination(value, filename):
    scope = {}
    exec(compile(REMOTE.split("if __name__ == '__main__':", 1)[0].replace("import fcntl\n", ""), "<transfer-path-guard>", "exec"), scope)
    path = scope["relative_path"](value)
    require(path.name == filename, "DESTINATION_FILENAME_MISMATCH")
    return str(path)


def ssh_helper():
    spec = importlib.util.spec_from_file_location("tencent_ssh", ROOT / "scripts/tencent-ssh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Progress:
    def __init__(self):
        self.phase = "validating_source"
        self.offset = 0
        self.size = 0
        self.threshold = 256 * 1024**2
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.periodic, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def periodic(self):
        while not self.stopped.wait(30):
            self.report()

    def report(self):
        emit({"phase": self.phase, "sent_mib": self.offset // 1024**2, "total_mib": self.size // 1024**2})

    def advance(self, offset):
        self.offset = offset
        if self.offset >= self.threshold:
            self.report()
            self.threshold = self.offset + 256 * 1024**2

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join()


def control(process):
    line = process.stdout.readline(8193)
    require(line and len(line) <= 8192, "REMOTE_CONNECTION_CLOSED")
    response = json.loads(line)
    if response.get("status") == "error":
        code = response.get("code", "REMOTE_TRANSFER_FAILED")
        raise RuntimeError(code if re.fullmatch(r"[A-Z0-9_]+", code) else "REMOTE_TRANSFER_FAILED")
    require(response.get("status") in ("ready", "complete"), "REMOTE_PROTOCOL_INVALID")
    return response


def transfer(args):
    with Progress() as progress:
        path, identity, sha256 = source_file(args.source)
        target = destination(args.destination or path.name, path.name)
        helper = ssh_helper()
        values = helper.settings()
        request = {"mode": args.mode, "destination": target, "size": identity.st_size, "sha256": sha256}
        payload = base64.b64encode(json.dumps(request).encode()).decode()
        command = "python3 -c " + shlex.quote(REMOTE) + " " + shlex.quote(payload)
        argv = helper.ssh_argv(values) + [command]
        progress.size = identity.st_size
        progress.phase = "validating_target"
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            current = control(process)
            if args.mode == "probe" or current["status"] == "complete":
                if current["status"] == "complete":
                    require(current.get("size") == identity.st_size and current.get("sha256") == sha256, "REMOTE_COMPLETION_MISMATCH")
                process.stdin.close()
                require(process.wait(timeout=30) == 0, "REMOTE_TRANSFER_FAILED")
                emit({"mode": args.mode, "status": current["status"], "target_validated": True, "skipped": current["status"] == "complete", "offset": current.get("offset", identity.st_size), "sha256": sha256})
                return
            offset = current.get("offset")
            require(type(offset) is int and 0 <= offset <= identity.st_size, "REMOTE_OFFSET_INVALID")
            progress.phase = "verifying_resume_prefix"
            require(digest(path, offset) == current.get("prefix_sha256"), "RESUME_PREFIX_MISMATCH")
            require(file_identity(path.stat()) == file_identity(identity), "LOCAL_FILE_CHANGED")
            progress.phase = "uploading"
            progress.offset = offset
            progress.threshold = offset + 256 * 1024**2
            with path.open("rb") as handle:
                require(file_identity(os.fstat(handle.fileno())) == file_identity(identity), "LOCAL_FILE_CHANGED")
                handle.seek(offset)
                while offset < identity.st_size:
                    block = handle.read(min(CHUNK, identity.st_size - offset))
                    require(block, "LOCAL_FILE_TRUNCATED")
                    process.stdin.write(block)
                    offset += len(block)
                    progress.advance(offset)
            process.stdin.close()
            progress.phase = "verifying_remote_sha256"
            result = control(process)
            require(process.wait(timeout=30) == 0 and result.get("status") == "complete" and result.get("sha256") == sha256 and result.get("size") == identity.st_size, "REMOTE_COMPLETION_MISMATCH")
            require(file_identity(path.stat()) == file_identity(identity), "LOCAL_FILE_CHANGED")
            emit({"status": "complete", "size": identity.st_size, "sha256": sha256, "resumed_bytes": current["offset"], "remote_sha256_verified": True, "atomic_promotion": True})
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["probe", "upload"])
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination")
    transfer(parser.parse_args())


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        emit({"status": "error", "code": str(error) if re.fullmatch(r"[A-Z0-9_]+", str(error)) else "TRANSFER_FAILED"})
        raise SystemExit(1)
    except KeyboardInterrupt:
        emit({"status": "interrupted", "partial_retained": True})
        raise SystemExit(130)
    except Exception:
        emit({"status": "error", "code": "TRANSFER_FAILED", "partial_retained": True})
        raise SystemExit(1)
