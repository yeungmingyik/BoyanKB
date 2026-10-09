import argparse
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone


class BackupError(Exception):
    pass


def require(value, code):
    if not value:
        raise BackupError(code)


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def command(args, data=None, env=None, timeout=180):
    try:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError("COMMAND_UNAVAILABLE_OR_TIMEOUT") from None
    if result.returncode != 0:
        operation = str(args[1]).upper() if pathlib.Path(args[0]).stem.lower() == "docker" and len(args) > 1 and re.fullmatch(r"[a-z-]+", str(args[1])) else "TOOL"
        raise BackupError("COMMAND_FAILED_" + operation)
    return result.stdout


def docker_json(*args):
    try:
        return json.loads(command(["docker", *args]))
    except (ValueError, UnicodeError):
        raise BackupError("DOCKER_RESPONSE_INVALID") from None


def clean_env(values=None):
    result = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    if values:
        result.update({key: value for key, value in values.items() if not controlled_environment_key(key)})
    return result


def controlled_environment_key(key):
    upper = key.upper()
    return upper.startswith(("COMPOSE_", "DOCKER_", "PYTHON", "LD_", "DYLD_")) or upper in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


def protect_directory(directory):
    if os.name == "nt":
        identity = command(["whoami", "/user", "/fo", "csv", "/nh"]).decode(errors="replace")
        sid = re.search(r"S-1-[0-9-]+", identity)
        require(sid, "WINDOWS_IDENTITY_UNAVAILABLE")
        command(["icacls", str(directory), "/inheritance:r", "/grant:r", "*" + sid[0] + ":(OI)(CI)F"])
    else:
        os.chmod(directory, 0o700)


def parse_env(raw):
    require(len(raw) <= 4 * 1024 * 1024, "ENV_FILE_TOO_LARGE")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise BackupError("ENV_FILE_ENCODING_INVALID") from None
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        require(match, "ENV_ASSIGNMENT_INVALID")
        name, value = match.groups()
        require(name not in values, "ENV_DUPLICATE_KEY")
        if value[:1] in ("'", '"'):
            quote = value[0]
            require(value.endswith(quote) and len(value) >= 2, "ENV_MULTILINE_UNSUPPORTED")
            value = value[1:-1]
            if quote == '"':
                require("\\" not in value and "$" not in value, "ENV_ESCAPED_VALUE_UNSUPPORTED")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            require("$" not in value, "ENV_INTERPOLATION_UNSUPPORTED")
        values[name] = value
        require(not value or name.upper().startswith("COMPOSE_") or not controlled_environment_key(name), "CLIENT_CONTROL_ENV_VALUE_REJECTED")
    require(len(values.get("SYSTEM_AES_KEY", "").encode()) == 32, "SYSTEM_AES_KEY_MUST_BE_32_BYTES")
    for name in ("DB_USER", "DB_PASSWORD", "DB_NAME", "REDIS_PASSWORD"):
        require(values.get(name), "REQUIRED_ENV_VALUE_MISSING")
    return values


def age_binary(value):
    executable = value or shutil.which("age")
    require(executable, "AGE_BINARY_REQUIRED")
    command([executable, "--version"])
    return str(executable)


def compose_command(project, files, env_file):
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,62}", project), "PROJECT_NAME_INVALID")
    require(files, "COMPOSE_FILE_REQUIRED")
    result = ["docker", "compose", "--project-name", project, "--env-file", str(env_file)]
    for filename in files:
        source = pathlib.Path(filename).resolve(strict=True)
        require(source.is_file(), "COMPOSE_FILE_INVALID")
        result.extend(["--file", str(source)])
    return result


def service_container(project, service):
    ids = command(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project, "--filter", "label=com.docker.compose.service=" + service]).decode().split()
    require(len(ids) == 1, "SOURCE_SERVICE_NOT_UNIQUE")
    return docker_json("inspect", ids[0])[0]


def container_environment(container):
    return dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)


def named_mount(container, destination):
    matches = [item for item in container.get("Mounts", []) if item.get("Destination") == destination]
    require(len(matches) == 1 and matches[0].get("Type") == "volume", "REQUIRED_NAMED_VOLUME_MISSING")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", matches[0].get("Name", "")), "VOLUME_NAME_INVALID")
    return matches[0]["Name"]


def bind_source(value):
    if os.name == "nt":
        match = re.fullmatch(r"/(?:run/desktop/mnt/host|host_mnt)/([A-Za-z])/(.*)", value)
        if match:
            return pathlib.Path(match[1] + ":/" + match[2])
    return pathlib.Path(value)


def pg_command(container, executable, extra=None):
    require(executable in ("psql", "pg_dump", "pg_restore"), "POSTGRES_COMMAND_INVALID")
    script = 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec ' + executable + ' --host=127.0.0.1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"'
    return ["docker", "exec", "-i", container["Id"], "sh", "-ec", script + ' "$@"', "backup", *(extra or [])]


def pg_query(container, sql):
    return command(pg_command(container, "psql", ["-X", "-A", "-t", "-v", "ON_ERROR_STOP=1"]), (sql + "\n").encode()).decode().strip()


def database_scope(container):
    tables = json.loads(pg_query(container, "SELECT coalesce(json_agg(tablename ORDER BY tablename), '[]') FROM pg_tables WHERE schemaname='public';"))
    required = {"storage_backends", "knowledge_bases", "resources", "tenants", "vector_stores", "schema_migrations"}
    require(required.issubset(tables), "DATABASE_SCHEMA_NOT_SUPPORTED")
    sql = """
SELECT json_build_object(
 'backends', (SELECT coalesce(json_agg(DISTINCT provider), '[]') FROM storage_backends),
 'resources', (SELECT coalesce(json_agg(DISTINCT provider), '[]') FROM resources),
 'kb_providers', (SELECT coalesce(json_agg(DISTINCT coalesce(storage_provider_config->>'provider', cos_config->>'provider', '')), '[]') FROM knowledge_bases),
 'tenant_providers', (SELECT coalesce(json_agg(DISTINCT coalesce(storage_engine_config->>'default_provider', '')), '[]') FROM tenants),
 'tenant_engines', (SELECT coalesce(json_agg(retriever_engines), '[]') FROM tenants),
 'external_vector_stores', (SELECT count(*) FROM vector_stores),
 'migration', (SELECT row_to_json(m) FROM schema_migrations m LIMIT 1)
);
"""
    scope = json.loads(pg_query(container, sql))
    for name in ("backends", "resources", "kb_providers", "tenant_providers"):
        require(all(str(item).lower() in ("", "local", "__pending_env__") for item in scope[name]), "NONLOCAL_OBJECT_STORAGE_NOT_SUPPORTED")
    require(scope["external_vector_stores"] == 0, "EXTERNAL_VECTOR_STORE_NOT_SUPPORTED")
    for item in scope["tenant_engines"]:
        engines = item.get("engines", []) if isinstance(item, dict) else item or []
        require(isinstance(engines, list) and all(engine.get("retriever_engine_type") == "postgres" for engine in engines), "NONPOSTGRES_RETRIEVAL_NOT_SUPPORTED")
    require(scope["migration"] and scope["migration"].get("dirty") is False, "DATABASE_MIGRATION_DIRTY_OR_MISSING")
    count_queries = []
    for table in tables:
        require(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table), "DATABASE_TABLE_NAME_UNSUPPORTED")
        count_queries.append("SELECT '" + table + "' AS name, count(*) AS total FROM \"" + table + '\"')
    counts = json.loads(pg_query(container, "SELECT json_object_agg(name, total) FROM (" + " UNION ALL ".join(count_queries) + ") inventory;"))
    return {"migration": scope["migration"], "row_counts": counts, "postgres_version": pg_query(container, "SHOW server_version;")}


def source_contract(args, values):
    service_names = command(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + args.project, "--format", '{{.Label "com.docker.compose.service"}}']).decode().splitlines()
    require(set(service_names).issubset({"postgres", "app", "frontend", "redis", "caddy", "docreader", "ollama"}), "SOURCE_EXTRA_SERVICE_NOT_SUPPORTED")
    require({"postgres", "app", "frontend", "redis"}.issubset(service_names), "SOURCE_SERVICES_MISSING")
    containers = {name: service_container(args.project, name) for name in set(service_names)}
    require(all(container["State"].get("Running") for container in containers.values()), "SOURCE_SERVICE_NOT_RUNNING")
    app_env = container_environment(containers["app"])
    pg_env = container_environment(containers["postgres"])
    redis_env = container_environment(containers["redis"])
    for name in ("SYSTEM_AES_KEY", "DB_USER", "DB_PASSWORD", "DB_NAME", "REDIS_PASSWORD"):
        require(app_env.get(name) == values.get(name), "ENV_DOES_NOT_MATCH_RUNNING_APPLICATION")
    require(pg_env.get("POSTGRES_USER") == values["DB_USER"] and pg_env.get("POSTGRES_DB") == values["DB_NAME"] and pg_env.get("POSTGRES_PASSWORD") == values["DB_PASSWORD"], "DATABASE_ENVIRONMENT_MISMATCH")
    require(redis_env.get("REDIS_PASSWORD") == values["REDIS_PASSWORD"], "REDIS_ENVIRONMENT_MISMATCH")
    require(app_env.get("DB_DRIVER", "postgres") == "postgres" and app_env.get("DB_HOST", "postgres") == "postgres", "EXTERNAL_DATABASE_NOT_SUPPORTED")
    require(app_env.get("STORAGE_TYPE", "local") == "local" and app_env.get("LOCAL_STORAGE_BASE_DIR", "/data/files") == "/data/files", "LOCAL_STORAGE_LAYOUT_NOT_SUPPORTED")
    require(app_env.get("RETRIEVE_DRIVER", "postgres") == "postgres", "NONPOSTGRES_RETRIEVAL_NOT_SUPPORTED")
    require(app_env.get("NEO4J_ENABLE", "false").lower() not in ("true", "1"), "NEO4J_BACKUP_NOT_SUPPORTED")
    require(app_env.get("WEKNORA_SANDBOX_DOCKER_ENABLED", "false").lower() not in ("true", "1"), "SANDBOX_DATA_BACKUP_NOT_SUPPORTED")
    require(app_env.get("REDIS_ADDR", "redis:6379") == "redis:6379", "EXTERNAL_REDIS_NOT_SUPPORTED")
    for mount in containers["app"].get("Mounts", []):
        if mount.get("Type") == "volume":
            require(mount.get("Destination") in ("/data/files", "/tmp/docreader"), "APPLICATION_VOLUME_NOT_SUPPORTED")
        else:
            require(mount.get("Type") == "bind" and bind_source(mount["Source"]).is_file(), "APPLICATION_BIND_MOUNT_NOT_SUPPORTED")
    volumes = {"data-files": named_mount(containers["app"], "/data/files"), "postgres-data": named_mount(containers["postgres"], "/var/lib/postgresql/data"), "redis-data": named_mount(containers["redis"], "/data")}
    if "caddy" in containers:
        volumes.update({"caddy-data": named_mount(containers["caddy"], "/data"), "caddy-config": named_mount(containers["caddy"], "/config")})
    for volume in volumes.values():
        labels = docker_json("volume", "inspect", volume)[0].get("Labels") or {}
        require(labels.get("com.docker.compose.project") == args.project, "SOURCE_VOLUME_PROJECT_MISMATCH")
    writers = command(["docker", "ps", "--quiet", "--no-trunc", "--filter", "volume=" + volumes["data-files"]]).decode().split()
    require(set(writers) == {containers["app"]["Id"]}, "OTHER_OBJECT_VOLUME_CONSUMER_PRESENT")
    return containers, volumes


def wait_source_services(containers, names):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        states = [docker_json("inspect", containers[name]["Id"])[0]["State"] for name in names]
        if all(state.get("Running") and state.get("Health", {}).get("Status", "healthy") == "healthy" for state in states):
            return
        time.sleep(2)
    raise BackupError("SOURCE_SERVICES_FAILED_TO_RESUME_HEALTHY")


class DigestReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.digest.update(data)
        self.size += len(data)
        return data


def digest_file(filename):
    with open(filename, "rb") as stream:
        reader = DigestReader(stream)
        while reader.read(1024 * 1024):
            pass
    return {"size": reader.size, "sha256": reader.digest.hexdigest()}


def encrypt_stream(binary, recipient, filename, source):
    with open(filename, "xb") as destination:
        process = subprocess.Popen([binary, "--encrypt", "--recipient", recipient], stdin=subprocess.PIPE, stdout=destination, stderr=subprocess.DEVNULL)
        reader = DigestReader(source)
        try:
            shutil.copyfileobj(reader, process.stdin, 1024 * 1024)
            process.stdin.close()
            require(process.wait() == 0, "AGE_ENCRYPTION_FAILED")
        except BaseException:
            process.kill()
            process.wait()
            raise
    return {"plaintext_size": reader.size, "plaintext_sha256": reader.digest.hexdigest(), **digest_file(filename)}


def encrypt_command(binary, recipient, filename, argv):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        metadata = encrypt_stream(binary, recipient, filename, process.stdout)
        require(process.wait() == 0, "BACKUP_COMPONENT_COMMAND_FAILED")
        return metadata
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def pack_archive(binary, recipient, output, directory, manifest):
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with open(output, "xb") as destination:
        process = subprocess.Popen([binary, "--encrypt", "--recipient", recipient], stdin=subprocess.PIPE, stdout=destination, stderr=subprocess.DEVNULL)
        try:
            with tarfile.open(fileobj=process.stdin, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                for entry in manifest["components"]:
                    item = tarfile.TarInfo(entry["name"])
                    item.size = entry["size"]
                    item.mode = 0o600
                    with open(directory / entry["name"], "rb") as source:
                        archive.addfile(item, source)
                item = tarfile.TarInfo("manifest.json")
                item.size = len(manifest_bytes)
                item.mode = 0o600
                archive.addfile(item, io.BytesIO(manifest_bytes))
            process.stdin.close()
            require(process.wait() == 0, "ARCHIVE_ENCRYPTION_FAILED")
        except BaseException:
            process.kill()
            process.wait()
            raise


def config_files(args, containers):
    files = [("compose-" + str(index) + ".yaml", pathlib.Path(filename).resolve(strict=True), {"kind": "compose", "order": index}) for index, filename in enumerate(args.compose)]
    for name, container in containers.items():
        for index, mount in enumerate(container.get("Mounts", [])):
            if mount.get("Type") == "bind":
                source = bind_source(mount["Source"]).resolve(strict=True)
                require(source.is_file(), "CONFIGURATION_DIRECTORY_BIND_NOT_SUPPORTED")
                files.append((name + "-bind-" + str(index), source, {"kind": "bind", "service": name, "target": mount["Destination"]}))
    return files


def model_inventory(containers):
    if "ollama" not in containers:
        return []
    data = json.loads(command(["docker", "exec", containers["app"]["Id"], "curl", "-fsS", "http://ollama:11434/api/tags"]))
    models = [{"name": model["name"], "digest": model["digest"], "size": model["size"]} for model in data.get("models", [])]
    require(all(re.fullmatch(r"[a-f0-9]{64}", model["digest"]) for model in models), "OLLAMA_MODEL_DIGEST_INVALID")
    return models


def backup(args):
    binary = age_binary(args.age_binary)
    require(re.fullmatch(r"age1[a-z0-9]{58}", args.recipient), "X25519_AGE_RECIPIENT_REQUIRED")
    output = pathlib.Path(args.output).resolve()
    require(not output.exists() and output.suffix == ".age" and output.parent.is_dir(), "OUTPUT_MUST_BE_NEW_AGE_FILE")
    env_file = pathlib.Path(args.env_file).resolve(strict=True)
    env_raw = env_file.read_bytes()
    values = parse_env(env_raw)
    compose = compose_command(args.project, args.compose, env_file)
    containers, volumes = source_contract(args, values)
    compose_env = clean_env({**values, "BOYANKB_ENV_FILE": str(env_file), "PRODUCTION_ENV_FILE": str(env_file)})
    active_ids = command(compose + ["ps", "--status", "running", "--quiet", "app", "frontend"], env=compose_env).decode().split()
    require(set(active_ids) == {containers[name]["Id"] for name in ("app", "frontend")}, "COMPOSE_SOURCE_CONTAINER_MISMATCH")
    command([binary, "--encrypt", "--recipient", args.recipient], b"preflight")
    configurations = config_files(args, containers)
    models = model_inventory(containers)
    writers = [name for name in ("caddy", "frontend", "app") if name in containers]
    paused = False
    successful = False
    try:
        if args.quiesce:
            require(args.project != "weknora-local", "LOCAL_PRIMARY_QUIESCE_REJECTED")
            paused = True
            command(["docker", "stop", "--time", "120", *[containers[name]["Id"] for name in writers]], timeout=480)
            require(all(not docker_json("inspect", containers[name]["Id"])[0]["State"]["Running"] for name in writers), "WRITERS_NOT_STOPPED")
            emit("writers_quiesced", project=args.project)
        manifest = {"format": "boyankb-encrypted-backup-v1", "created_at": datetime.now(timezone.utc).isoformat(), "source_project": args.project, "consistency": "application-quiesced" if args.quiesce else "online-cross-store-not-atomic", "storage": "local", "local_base_dir": "/data/files", "database": database_scope(containers["postgres"]), "images": {name: {"id": container["Image"], "reference": container["Config"]["Image"]} for name, container in containers.items()}, "ollama_models": models, "components": [], "excluded": ["ollama-model-cache", "docreader-temporary-files", "container-images", "external-object-backends", "external-vector-stores", "neo4j", "sandbox-workspaces"]}
        with tempfile.TemporaryDirectory(prefix=".boyankb-encrypted-", dir=output.parent) as temporary:
            directory = pathlib.Path(temporary)
            os.chmod(directory, 0o700)

            def add(name, role, argv=None, data=None, source=None, origin=None):
                emit("backup_component", role=role)
                filename = directory / name
                if argv:
                    metadata = encrypt_command(binary, args.recipient, filename, argv)
                else:
                    with contextlib.closing(io.BytesIO(data)) if source is None else open(source, "rb") as stream:
                        metadata = encrypt_stream(binary, args.recipient, filename, stream)
                manifest["components"].append({"name": name, "role": role, **metadata, **({"origin": origin} if origin else {})})

            add("database.dump.age", "database", argv=pg_command(containers["postgres"], "pg_dump", ["--format=custom", "--no-owner", "--no-acl"]))
            add("objects.tar.age", "objects", argv=["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", "type=volume,src=" + volumes["data-files"] + ",dst=/source,readonly", "--entrypoint", "tar", containers["postgres"]["Image"], "--numeric-owner", "-cf", "-", "-C", "/source", "."])
            add("redis.rdb.age", "redis", argv=["docker", "exec", containers["redis"]["Id"], "sh", "-ec", 'export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli --rdb -'])
            add("environment.env.age", "environment", data=env_raw)
            for name in ("caddy-data", "caddy-config"):
                if name in volumes:
                    add(name + ".tar.age", name, argv=["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", "type=volume,src=" + volumes[name] + ",dst=/source,readonly", "--entrypoint", "tar", containers["postgres"]["Image"], "--numeric-owner", "-cf", "-", "-C", "/source", "."])
            for name, filename, origin in configurations:
                add("config-" + name + ".age", "configuration", source=filename, origin={**origin, "source_path": str(filename)})
            require(all(entry["plaintext_size"] > 0 for entry in manifest["components"]), "EMPTY_BACKUP_COMPONENT")
            if args.quiesce:
                require(database_scope(containers["postgres"])["row_counts"] == manifest["database"]["row_counts"], "DATABASE_CHANGED_DURING_QUIESCED_BACKUP")
            pack_archive(binary, args.recipient, output, directory, manifest)
            os.chmod(output, 0o600)
            successful = True
    finally:
        if paused:
            command(["docker", "start", *[containers[name]["Id"] for name in reversed(writers)]], timeout=180)
            wait_source_services(containers, writers)
            emit("source_services_resumed", project=args.project)
        if not successful and output.exists():
            emit("incomplete_archive", path=str(output))
    emit("backup_complete", project=args.project, consistency=manifest["consistency"], components=len(manifest["components"]), archive=str(output), **digest_file(output))


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--project", required=True)
    result.add_argument("--compose", action="append", required=True)
    result.add_argument("--env-file", required=True)
    result.add_argument("--recipient", required=True)
    result.add_argument("--age-binary")
    result.add_argument("--output", required=True)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--quiesce", action="store_true")
    mode.add_argument("--online", action="store_true")
    return result


def interrupted(signum, frame):
    raise KeyboardInterrupt()


def main():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        backup(parser().parse_args())
        return 0
    except KeyboardInterrupt:
        emit("backup_failed", code="INTERRUPTED")
        return 130
    except BackupError as error:
        emit("backup_failed", code=str(error))
        return 1
    except Exception:
        emit("backup_failed", code="UNEXPECTED_FAILURE")
        return 1


if __name__ == "__main__":
    sys.exit(main())
