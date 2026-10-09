import argparse
import json
import re
import runpy
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
read_env = runpy.run_path(str(ROOT / "scripts/init-production.py"))["read_env"]


def command(args, data=None, required=True):
    result = subprocess.run(args, input=data, capture_output=True, cwd=ROOT)
    if required and result.returncode:
        raise RuntimeError("BOOTSTRAP_COMMAND_FAILED")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--compose", action="append", required=True)
    parser.add_argument("--initialize-empty-database", action="store_true", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"boyankb-[a-z0-9][a-z0-9-]{1,48}", args.project):
        raise RuntimeError("INVALID_PROJECT")
    values = read_env(args.env_file)
    email, password = values.get("ACCEPTANCE_ADMIN_EMAIL", ""), values.get("ACCEPTANCE_ADMIN_PASSWORD", "")
    if not email or "REPLACE_" in email or not 12 <= len(password) <= 32:
        raise RuntimeError("ADMIN_CREDENTIALS_REQUIRED_IN_ENVIRONMENT")
    if values.get("WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL") != email:
        raise RuntimeError("BOOTSTRAP_EMAIL_MISMATCH")
    compose = ["docker", "compose", "--env-file", str(Path(args.env_file).resolve()), "-p", args.project]
    for file in args.compose:
        compose.extend(["-f", file])
    existing = command(compose + ["ps", "--status", "running", "--services"]).stdout.decode().splitlines()
    if set(existing) & {"app", "frontend", "caddy"}:
        raise RuntimeError("REFUSE_BOOTSTRAP_WITH_RUNNING_APPLICATION")
    command(compose + ["up", "-d", "--wait", "--wait-timeout", "180", "postgres", "redis", "docreader", "ollama"])
    pg = command(compose + ["ps", "-q", "postgres"]).stdout.decode().strip()
    query = "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='users';"
    has_users = command(["docker", "exec", pg, "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"', "sh", query]).stdout.decode().strip()
    if has_users != "0":
        raise RuntimeError("REFUSE_EXISTING_DATABASE_SCHEMA")
    bootstrap = args.project + "-bootstrap"
    if command(["docker", "inspect", bootstrap], required=False).returncode == 0:
        raise RuntimeError("BOOTSTRAP_CONTAINER_ALREADY_EXISTS")
    started = False
    try:
        command(compose + ["run", "--rm", "--no-deps", "-d", "--name", bootstrap,
                          "-e", "AUTO_MIGRATE=true", "-e", "AUTO_RECOVER_DIRTY=false",
                          "-e", "DISABLE_REGISTRATION=false", "-e", "WEKNORA_AUTH_DEFAULT_TENANT_MODE=create_personal", "app"])
        started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            ready = command(["docker", "exec", bootstrap, "curl", "-fsS", "http://127.0.0.1:8080/health"], required=False)
            if ready.returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("BOOTSTRAP_HEALTH_TIMEOUT")
        migration = command(["docker", "exec", pg, "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version,dirty FROM schema_migrations"']).stdout.decode().strip()
        if not re.fullmatch(r"[0-9]+\|f", migration):
            raise RuntimeError("MIGRATION_STATE_INVALID")
        payload = json.dumps({"username": "BoyanKB Administrator", "email": email, "password": password}).encode()
        result = command(["docker", "exec", "-i", bootstrap, "curl", "-fsS", "-X", "POST",
                          "-H", "Content-Type: application/json", "--data-binary", "@-", "http://127.0.0.1:8080/api/v1/auth/register"], payload)
        if not json.loads(result.stdout).get("success"):
            raise RuntimeError("ADMIN_REGISTRATION_FAILED")
        print("EMPTY_DATABASE_INITIALIZED", "schema=" + migration.split("|")[0])
    finally:
        if started:
            command(["docker", "stop", "--time", "30", bootstrap])
    command(compose + ["up", "-d", "--wait", "--wait-timeout", "180", "app"])
    command(compose + ["up", "-d", "--wait", "--wait-timeout", "180", "frontend", "caddy"])
    print("INVITE_ONLY_APPLICATION_STARTED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "BOOTSTRAP_FAILED")
        raise SystemExit(1)
