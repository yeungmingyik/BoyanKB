import base64
import importlib.util
import json
import os
import secrets
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "temp/tencent-cloud"


def main():
    spec = importlib.util.spec_from_file_location("production_env", ROOT / "scripts/init-production.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    if DEST.exists():
        raise RuntimeError("REFUSE_EXISTING_ACCESS_DIRECTORY")
    if subprocess.run(["git", "check-ignore", "--quiet", str(DEST / ".env")], cwd=ROOT).returncode:
        raise RuntimeError("ACCESS_DIRECTORY_MUST_BE_IGNORED")
    DEST.mkdir(parents=True)
    helpers.protect(DEST)
    key = DEST / "ssh/.env"
    key.parent.mkdir()
    helpers.protect(key.parent)
    result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "boyankb-tencent-deployment", "-f", str(key)], capture_output=True)
    if result.returncode:
        raise RuntimeError("KEY_GENERATION_FAILED")
    helpers.protect(key)
    public_key = key.with_name(".env.pub").read_text(encoding="utf-8").strip()
    values = {
        "SSH_HOST": "193.112.17.218",
        "SSH_PORT": "22",
        "SSH_USER": "root",
        "SSH_IDENTITY_FILE": key.as_posix(),
        "SSH_HOST_FINGERPRINT": "REPLACE_WITH_SERVER_ED25519_SHA256_FINGERPRINT",
        "PUBLIC_ORIGIN": "http://193.112.17.218",
        "HEADQUARTERS_NAME": "博研文化总部",
        "ADMIN_NAME": "刘伟健",
        "ADMIN_EMAIL": "liuweijian@boyantech.xyz",
        "ADMIN_PASSWORD": "Aa1!" + secrets.token_urlsafe(18),
        "MEMBER_NAME": "冯伟杰",
        "MEMBER_EMAIL": "fengweijian@boyantech.xyz",
        "MEMBER_PASSWORD": "Aa1!" + secrets.token_urlsafe(18),
    }
    env = DEST / ".env"
    with env.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(f"{k}='{v}'" for k, v in values.items()) + "\n")
    helpers.protect(env)
    encoded = base64.b64encode((public_key + "\n").encode()).decode()
    command = f"printf '%s' '{encoded}' | base64 -d | sudo bash -c 'set -eu; IFS= read -r pub; install -d -m 700 /root/.ssh; touch /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys; grep -qxF \"$pub\" /root/.ssh/authorized_keys || printf \"%s\\n\" \"$pub\" >> /root/.ssh/authorized_keys; /usr/sbin/sshd -T | grep -E \"^(permitrootlogin|pubkeyauthentication|port) \"; ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'"
    (DEST / "install-root-key.sh").write_text(command + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"created": True, "env": str(env), "public_key_install_command": str(DEST / "install-root-key.sh"), "private_values_printed": False}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("TENCENT_ACCESS_INITIALIZATION_FAILED")
        raise SystemExit(1)
