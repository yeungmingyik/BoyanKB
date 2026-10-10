import argparse
import base64
import hashlib
import importlib.util
import ipaddress
import json
import re
import socket
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / "temp/tencent-cloud/.env"
KNOWN = ACCESS.parent / "known_hosts"
PREFLIGHT = r'''set -eu
test "$(id -u)" = 0
printf 'USER='; id -un
printf 'ARCH='; uname -m
printf 'OS='; . /etc/os-release; printf '%s %s\n' "$ID" "$VERSION_ID"
printf 'CPUS='; getconf _NPROCESSORS_ONLN
free -m
df -Pm / /opt
printf 'SSH_SOURCE='; printf '%s\n' "${SSH_CONNECTION%% *}"
if command -v docker >/dev/null 2>&1; then
  docker version --format '{{.Server.Version}}'
  docker compose version
  docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
else
  printf 'DOCKER_NOT_INSTALLED\n'
fi
if test -e /opt/boyankb; then printf 'TARGET_DIRECTORY_EXISTS\n'; else printf 'TARGET_DIRECTORY_NEW\n'; fi
ss -ltn
'''


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def settings():
    spec = importlib.util.spec_from_file_location("production_env", ROOT / "scripts/init-production.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    values = helper.read_env(ACCESS)
    require(values.get("SSH_HOST") == "193.112.17.218", "UNEXPECTED_DEPLOYMENT_HOST")
    ipaddress.ip_address(values["SSH_HOST"])
    require(values.get("SSH_USER") == "root", "ROOT_DEPLOYMENT_REQUIRED")
    port = int(values.get("SSH_PORT", "22"))
    require(1 <= port <= 65535, "INVALID_SSH_PORT")
    identity = Path(values["SSH_IDENTITY_FILE"]).resolve(strict=True)
    require(identity.is_relative_to(ACCESS.parent.resolve()), "IDENTITY_OUTSIDE_DEPLOYMENT_DIRECTORY")
    return values


def probe(values):
    try:
        with socket.create_connection((values["SSH_HOST"], int(values["SSH_PORT"])), timeout=8) as connection:
            connection.settimeout(8)
            banner = connection.recv(255)
        return {"tcp_connected": True, "ssh_banner_received": banner.startswith(b"SSH-2.0-"), "peer_closed": not bool(banner)}
    except (OSError, TimeoutError):
        return {"tcp_connected": False, "ssh_banner_received": False}


def pin(values, expected):
    require(re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", expected), "CONSOLE_HOST_FINGERPRINT_REQUIRED")
    result = subprocess.run(["ssh-keyscan", "-T", "8", "-p", values["SSH_PORT"], "-t", "ed25519", values["SSH_HOST"]], capture_output=True, text=True, timeout=20)
    lines = [line for line in result.stdout.splitlines() if line and not line.startswith("#")]
    require(result.returncode == 0 and len(lines) == 1, "SSH_HOST_KEY_NOT_AVAILABLE")
    fields = lines[0].split()
    require(len(fields) == 3 and fields[1] == "ssh-ed25519", "UNEXPECTED_HOST_KEY_TYPE")
    address = values["SSH_HOST"] if values["SSH_PORT"] == "22" else f"[{values['SSH_HOST']}]:{values['SSH_PORT']}"
    require(fields[0] == address, "HOST_KEY_ADDRESS_MISMATCH")
    actual = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(fields[2], validate=True)).digest()).decode().rstrip("=")
    require(actual == expected, "HOST_KEY_FINGERPRINT_MISMATCH")
    content = lines[0] + "\n"
    if KNOWN.exists():
        require(KNOWN.read_text(encoding="utf-8") == content, "REFUSE_CHANGED_HOST_KEY")
    else:
        with KNOWN.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    return {"host_key_pinned": True, "fingerprint": actual}


def ssh_argv(values):
    require(KNOWN.is_file(), "VERIFIED_HOST_KEY_REQUIRED")
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", f"UserKnownHostsFile={KNOWN.as_posix()}", "-o", "GlobalKnownHostsFile=none", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-p", values["SSH_PORT"], "-i", values["SSH_IDENTITY_FILE"], "root@" + values["SSH_HOST"]]


def preflight(values):
    result = subprocess.run(ssh_argv(values) + ["sh", "-s"], input=PREFLIGHT.encode("utf-8"), capture_output=True, timeout=90)
    require(result.returncode == 0, "SSH_AUTH_OR_PREFLIGHT_FAILED")
    output = result.stdout.decode("utf-8")
    require("USER=root\n" in output and "OS=ubuntu 24.04\n" in output and "ARCH=x86_64\n" in output, "SERVER_PLATFORM_MISMATCH")
    return {"preflight_passed": True, "observations": output.splitlines()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["probe", "pin-host", "preflight"])
    parser.add_argument("--fingerprint")
    args = parser.parse_args()
    values = settings()
    if args.action == "probe":
        result = probe(values)
    elif args.action == "pin-host":
        result = pin(values, args.fingerprint or values.get("SSH_HOST_FINGERPRINT", ""))
    else:
        result = preflight(values)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error))
        raise SystemExit(1)
    except Exception:
        print("TENCENT_SSH_CHECK_FAILED")
        raise SystemExit(1)
