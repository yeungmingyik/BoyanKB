import argparse
import ipaddress
import os
import re
import secrets
import subprocess
from pathlib import Path


def read_env(path):
    values = {}
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def protect(path):
    if os.name == "nt":
        who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
        sid = re.search(r"S-1-[0-9-]+", who.stdout)
        if not sid:
            raise RuntimeError("WINDOWS_IDENTITY_UNAVAILABLE")
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid.group()}:F"], capture_output=True)
        if result.returncode:
            raise RuntimeError("FILE_PERMISSION_UPDATE_FAILED")
    else:
        path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--domain", default="kb.boyantech.com")
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--import-model-env")
    parser.add_argument("--web-subnet")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    if output.exists() or output == root / ".env":
        raise RuntimeError("REFUSE_EXISTING_ENVIRONMENT_FILE")
    output.relative_to(root)
    ignored = subprocess.run(["git", "check-ignore", "--quiet", str(output)], cwd=root, capture_output=True)
    if ignored.returncode:
        raise RuntimeError("ENVIRONMENT_FILE_MUST_BE_GIT_IGNORED")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", args.domain):
        raise RuntimeError("INVALID_DOMAIN")
    subnet = ipaddress.ip_network(args.web_subnet or ("10.239.50.0/24" if args.staging else "10.239.40.0/24"))
    if subnet.version != 4 or subnet.prefixlen != 24 or not subnet.is_private:
        raise RuntimeError("PRIVATE_IPV4_24_REQUIRED")
    values = {
        "PRODUCTION_ENV_FILE": output.as_posix(),
        "PUBLIC_HOST": "localhost" if args.staging else args.domain,
        "PROD_WEB_SUBNET": str(subnet),
        "PROD_PROXY_IP": str(subnet[2]),
        "PROD_FRONTEND_IP": str(subnet[3]),
        "PROD_APP_IP": str(subnet[4]),
        "DB_USER": "boyankb",
        "DB_NAME": "boyankb",
        "DB_PASSWORD": secrets.token_urlsafe(32),
        "REDIS_PASSWORD": secrets.token_urlsafe(32),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "SYSTEM_AES_KEY": secrets.token_hex(16),
        "SYSTEM_SIGNING_KEY": secrets.token_urlsafe(48),
        "GRPC_AUTH_TOKEN": secrets.token_urlsafe(32),
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_MODEL_NAME": "deepseek-chat",
        "DEEPSEEK_API_KEY": "REPLACE_WITH_DEEPSEEK_API_KEY",
        "EMBEDDING_MODEL_NAME": "bge-m3:latest",
        "PROD_OUTBOUND_HOSTS": "api.deepseek.com,open.feishu.cn,ollama",
        "WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL": "admin@boyankb.invalid" if args.staging else "REPLACE_WITH_ADMIN_EMAIL",
        "ACCEPTANCE_ADMIN_EMAIL": "admin@boyankb.invalid" if args.staging else "REPLACE_WITH_ADMIN_EMAIL",
        "ACCEPTANCE_ADMIN_PASSWORD": "Aa1!" + secrets.token_urlsafe(18),
        "ACCEPTANCE_ISOLATED_ORIGIN": "https://localhost:8443" if args.staging else "",
        "ACCEPTANCE_A_EMAIL": "partner-a@boyankb.invalid",
        "ACCEPTANCE_A_PASSWORD": "Aa1!" + secrets.token_urlsafe(18),
        "ACCEPTANCE_B_EMAIL": "partner-b@boyankb.invalid",
        "ACCEPTANCE_B_PASSWORD": "Aa1!" + secrets.token_urlsafe(18),
    }
    if args.import_model_env:
        imported = read_env(args.import_model_env)
        for key in ["DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL_NAME", "DEEPSEEK_API_KEY", "EMBEDDING_MODEL_NAME"]:
            if imported.get(key):
                values[key] = imported[key]
    if any("\n" in value or "\r" in value or "'" in value for value in values.values()):
        raise RuntimeError("UNSUPPORTED_ENVIRONMENT_VALUE")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(f"{key}='{value}'" for key, value in values.items()) + "\n")
    protect(output)
    print("CREATED_PROTECTED_ENVIRONMENT", output.relative_to(root).as_posix())
    print("MODEL_CREDENTIALS_IMPORTED" if args.import_model_env else "MODEL_CREDENTIALS_REQUIRE_INPUT")
    print("BOOTSTRAP_ADMIN_PASSWORD_STORED_IN_ENVIRONMENT")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "INITIALIZATION_FAILED")
        raise SystemExit(1)
