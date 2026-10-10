import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("trial_restore_shared", pathlib.Path(__file__).with_name("restore-prod.py"))
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
shared = recovery.shared
BackupError = shared.BackupError
require = shared.require
command = shared.command
SERVICES = tuple(sorted(("app", "frontend", "docreader", "postgres", "redis", "ollama", "document-renderer")))
PROFILE = "local-renderer-v1"
PROJECT = "weknora-local"
ASSETS = {"app": ("skills/boyan", "presets/boyan/v1"), "document-renderer": ("branding/templates",)}
VOLUME_TARGETS = {"app": {"/data/files": "data-files", "/tmp/docreader": "docreader-tmp"}, "docreader": {"/tmp/docreader": "docreader-tmp"}, "postgres": {"/var/lib/postgresql/data": "postgres-data"}, "redis": {"/data": "redis-data"}, "ollama": {"/root/.ollama": "ollama-data"}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, data):
    with open(path, "xb") as destination:
        destination.write(data)
    os.chmod(path, 0o600)


def write_json(path, data):
    write_new(path, json.dumps(data, ensure_ascii=False, indent=2).encode() + b"\n")


def compose_literals(value):
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [compose_literals(item) for item in value]
    if isinstance(value, dict):
        return {key: compose_literals(item) for key, item in value.items()}
    return value


def local_context(value=None):
    require(not os.environ.get("DOCKER_HOST"), "DOCKER_HOST_OVERRIDE_REJECTED")
    context = value or command(["docker", "context", "show"]).decode().strip()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", context), "DOCKER_CONTEXT_INVALID")
    details = shared.docker_json("context", "inspect", context)
    host = details[0].get("Endpoints", {}).get("docker", {}).get("Host", "")
    require(host.startswith("npipe:////./pipe/") or host.startswith("unix:///"), "LOCAL_DOCKER_ENDPOINT_REQUIRED")
    os.environ["DOCKER_CONTEXT"] = context
    for name in ("DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        os.environ.pop(name, None)
    return context


def validate_ids(ids):
    require(isinstance(ids, dict) and set(ids) == set(SERVICES), "SOURCE_IDS_INVALID")
    require(all(isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) for item in ids.values()), "SOURCE_IDS_INVALID")


def source_contract(args, values):
    require(args.project == PROJECT and args.quiesce, "LOCAL_QUIESCED_PROFILE_REQUIRED")
    names = command(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + PROJECT, "--format", '{{.Label "com.docker.compose.service"}}']).decode().splitlines()
    require(sorted(names) == list(SERVICES), "SOURCE_EXACT_SEVEN_SERVICES_REQUIRED")
    containers = {name: shared.service_container(PROJECT, name) for name in SERVICES}
    require(all(item["State"].get("Running") for item in containers.values()), "SOURCE_SERVICE_NOT_RUNNING")
    app = shared.container_environment(containers["app"])
    postgres = shared.container_environment(containers["postgres"])
    redis = shared.container_environment(containers["redis"])
    renderer = shared.container_environment(containers["document-renderer"])
    for key in ("SYSTEM_AES_KEY", "DB_USER", "DB_PASSWORD", "DB_NAME", "REDIS_PASSWORD", "BOYAN_DOCUMENT_RENDERER_TOKEN"):
        require(values.get(key) and app.get(key) == values[key], "ENV_DOES_NOT_MATCH_RUNNING_APPLICATION")
    require(postgres.get("POSTGRES_USER") == values["DB_USER"] and postgres.get("POSTGRES_PASSWORD") == values["DB_PASSWORD"] and postgres.get("POSTGRES_DB") == values["DB_NAME"], "DATABASE_ENVIRONMENT_MISMATCH")
    require(redis.get("REDIS_PASSWORD") == values["REDIS_PASSWORD"] and renderer.get("BOYAN_DOCUMENT_RENDERER_TOKEN") == values["BOYAN_DOCUMENT_RENDERER_TOKEN"], "SERVICE_ENVIRONMENT_MISMATCH")
    required = {"DB_DRIVER": "postgres", "DB_HOST": "postgres", "REDIS_ADDR": "redis:6379", "STORAGE_TYPE": "local", "LOCAL_STORAGE_BASE_DIR": "/data/files", "RETRIEVE_DRIVER": "postgres", "BOYAN_DOCUMENT_RENDERER_URL": "http://document-renderer:8091", "BOYAN_PRESETS_ROOT": "/app/presets/boyan/v1", "BOYAN_SKILLS_ROOT": "/app/skills/boyan"}
    require(all(app.get(key, default) == default for key, default in required.items()), "SOURCE_RUNTIME_LAYOUT_UNSUPPORTED")
    require(all(app.get(key, "false").lower() not in ("true", "1") for key in ("NEO4J_ENABLE", "WEKNORA_SANDBOX_DOCKER_ENABLED")), "EXTERNAL_STATE_OR_SANDBOX_UNSUPPORTED")
    volumes = {}
    for name, item in containers.items():
        host = item.get("HostConfig", {})
        require(not host.get("Privileged") and not host.get("Devices") and host.get("NetworkMode") not in ("host", "none") and not host.get("VolumesFrom"), "SOURCE_HOST_ACCESS_UNSUPPORTED")
        for port in (host.get("PortBindings") or {}).values():
            require(name in ("app", "frontend") and all(binding.get("HostIp") == "127.0.0.1" for binding in port), "SOURCE_NONLOCAL_PORT_REJECTED")
        for mount in item.get("Mounts", []):
            target = mount["Destination"]
            if mount["Type"] == "volume":
                logical = VOLUME_TARGETS.get(name, {}).get(target)
                require(logical, "SOURCE_VOLUME_LAYOUT_UNSUPPORTED")
                volume = shared.named_mount(item, target)
                require(logical not in volumes or volumes[logical] == volume, "SOURCE_SHARED_VOLUME_MISMATCH")
                volumes[logical] = volume
            else:
                require(mount["Type"] == "bind" and shared.bind_source(mount["Source"]).is_file(), "SOURCE_BIND_DIRECTORY_OR_SOCKET_REJECTED")
                require(not any(target == "/app/" + path or target.startswith("/app/" + path + "/") for path in ASSETS.get(name, ())), "SOURCE_ASSETS_BIND_OVERRIDE_REJECTED")
    require(set(volumes) == {"postgres-data", "redis-data", "data-files", "docreader-tmp", "ollama-data"}, "SOURCE_VOLUMES_MISSING")
    for volume in volumes.values():
        labels = shared.docker_json("volume", "inspect", volume)[0].get("Labels") or {}
        require(labels.get("com.docker.compose.project") == PROJECT, "SOURCE_VOLUME_PROJECT_MISMATCH")
        consumers = set(command(["docker", "ps", "-q", "--no-trunc", "--filter", "volume=" + volume]).decode().split())
        require(consumers.issubset({item["Id"] for item in containers.values()}), "FOREIGN_VOLUME_CONSUMER_PRESENT")
    return containers, volumes


def runtime_record(containers):
    return {name: {"image": item["Image"], "config": {key: item["Config"].get(key) for key in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir", "Healthcheck")}, "read_only": bool(item.get("HostConfig", {}).get("ReadonlyRootfs")), "mounts": [{key: mount.get(key) for key in ("Type", "Name", "Source", "Destination", "RW")} for mount in item.get("Mounts", [])]} for name, item in containers.items()}


def validate_manifest(manifest):
    require(manifest.get("profile") == PROFILE and manifest.get("source_project") == PROJECT and manifest.get("consistency") == "application-quiesced", "TRIAL_PROFILE_MISMATCH")
    require(manifest.get("service_names") == list(SERVICES) and set(manifest.get("images", {})) == set(SERVICES), "TRIAL_SERVICES_MISMATCH")
    require(all(re.fullmatch(r"sha256:[a-f0-9]{64}", item.get("id", "")) for item in manifest["images"].values()), "TRIAL_IMAGE_ID_INVALID")
    validate_ids(manifest.get("source_ids"))
    require(manifest.get("database_fingerprint") == fingerprint(manifest.get("database")), "DATABASE_FINGERPRINT_MISMATCH")
    kinds = [item.get("origin", {}).get("kind") for item in manifest["components"] if item["role"] == "configuration"]
    require(kinds.count("runtime-config") == 1 and kinds.count("model-cache") == 1 and kinds.count("runtime-assets") == 2 and "compose" in kinds, "TRIAL_RUNTIME_COMPONENTS_MISSING")
    assets = {item.get("origin", {}).get("service") for item in manifest["components"] if item.get("origin", {}).get("kind") == "runtime-assets"}
    require(assets == set(ASSETS), "TRIAL_ASSET_SERVICES_MISMATCH")


def read_component(binary, identity, directory, entry, limit=16 * 1024 * 1024):
    require(entry["plaintext_size"] <= limit, "CONFIGURATION_TOO_LARGE")
    with recovery.decrypt_stream(binary, identity, directory / entry["name"]) as stream:
        data = stream.read(limit + 1)
    require(len(data) == entry["plaintext_size"] and hashlib.sha256(data).hexdigest() == entry["plaintext_sha256"], "COMPONENT_RECHECK_MISMATCH")
    return data


def verify_archive(binary, identity, archive, directory):
    manifest, roles = recovery.unpack_verified(binary, identity, archive, directory)
    validate_manifest(manifest)
    env_raw, inventories = recovery.verify_components(binary, identity, directory, manifest)
    shared.parse_env(env_raw)
    runtime = None
    for entry in manifest["components"]:
        kind = entry.get("origin", {}).get("kind")
        if kind == "runtime-config":
            runtime = json.loads(read_component(binary, identity, directory, entry))
        elif kind in ("runtime-assets", "model-cache"):
            with recovery.decrypt_stream(binary, identity, directory / entry["name"]) as stream:
                inventory = recovery.object_inventory(stream)
            require(any(item["type"] == "file" for item in inventory.values()), "RUNTIME_ARCHIVE_EMPTY")
            if kind == "runtime-assets":
                service = entry["origin"]["service"]
                require(all(any(path == prefix or path.startswith(prefix + "/") for prefix in ASSETS[service]) for path in inventory if path != "."), "RUNTIME_ASSET_PATH_REJECTED")
                inventories[service] = inventory
            else:
                require(all(path == "models" or path.startswith("models/") for path in inventory), "MODEL_CACHE_PATH_REJECTED")
                inventories["ollama-data"] = inventory
    require(isinstance(runtime, dict) and set(runtime) == set(SERVICES), "RUNTIME_CONFIG_INVALID")
    require(all(runtime[name].get("image") == manifest["images"][name]["id"] for name in SERVICES), "RUNTIME_IMAGE_MISMATCH")
    return manifest, roles, env_raw, inventories, runtime


def tar_volume(image, volume, member="."):
    return ["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", "type=volume,src=" + volume + ",dst=/source,readonly", "--entrypoint", "tar", image, "--numeric-owner", "-cf", "-", "-C", "/source", member]


def backup(args):
    require(args.project == PROJECT and args.quiesce, "LOCAL_QUIESCED_PROFILE_REQUIRED")
    archive = pathlib.Path(args.output).resolve()
    receipt = pathlib.Path(args.receipt).resolve()
    require(archive != receipt and not archive.exists() and archive.suffix == ".age" and archive.parent.is_dir() and not receipt.exists() and receipt.parent.is_dir(), "OUTPUT_MUST_BE_NEW")
    local_context(args.context)
    binary = shared.age_binary(args.age_binary)
    require(re.fullmatch(r"age1[a-z0-9]{58}", args.recipient), "X25519_AGE_RECIPIENT_REQUIRED")
    identity = pathlib.Path(args.identity).resolve(strict=True)
    env_file = pathlib.Path(args.env_file).resolve(strict=True)
    env_raw = env_file.read_bytes()
    values = shared.parse_env(env_raw)
    containers, volumes = source_contract(args, values)
    compose = shared.compose_command(PROJECT, args.compose, env_file)
    active = command(compose + ["ps", "--status", "running", "--quiet"], env=shared.clean_env(values)).decode().split()
    require(set(active) == {item["Id"] for item in containers.values()}, "COMPOSE_SOURCE_CONTAINER_MISMATCH")
    configs = shared.config_files(args, containers)
    models = shared.model_inventory(containers)
    writers = ("frontend", "app", "docreader", "document-renderer", "ollama")
    paused = False
    manifest = None
    with tempfile.TemporaryDirectory(prefix=".trial-encrypted-", dir=archive.parent) as temporary:
        directory = pathlib.Path(temporary)
        shared.protect_directory(directory)
        probe = directory / "probe.age"
        shared.encrypt_stream(binary, args.recipient, probe, io.BytesIO(b"trial-key-check"))
        with recovery.decrypt_stream(binary, identity, probe) as stream:
            require(stream.read() == b"trial-key-check", "AGE_IDENTITY_RECIPIENT_MISMATCH")
        probe.unlink()
        try:
            paused = True
            command(["docker", "stop", "--time", "120", *[containers[name]["Id"] for name in writers]], timeout=660)
            require(all(not shared.docker_json("inspect", containers[name]["Id"])[0]["State"]["Running"] for name in writers), "WRITERS_NOT_STOPPED")
            database = shared.database_scope(containers["postgres"])
            manifest = {"format": "boyankb-encrypted-backup-v1", "profile": PROFILE, "created_at": now(), "source_project": PROJECT, "service_names": list(SERVICES), "source_ids": {name: item["Id"] for name, item in containers.items()}, "consistency": "application-quiesced", "storage": "local", "local_base_dir": "/data/files", "database": database, "database_fingerprint": fingerprint(database), "images": {name: {"id": item["Image"], "reference": item["Config"]["Image"]} for name, item in containers.items()}, "ollama_models": models, "components": [], "excluded": ["container-images", "docreader-temporary-files", "ollama-identity", "external-storage", "sandbox-workspaces"]}

            def add(name, role, argv=None, data=None, filename=None, origin=None):
                if argv is not None:
                    metadata = shared.encrypt_command(binary, args.recipient, directory / name, argv)
                else:
                    with open(filename, "rb") if filename else contextlib.closing(io.BytesIO(data)) as stream:
                        metadata = shared.encrypt_stream(binary, args.recipient, directory / name, stream)
                manifest["components"].append({"name": name, "role": role, **metadata, **({"origin": origin} if origin else {})})

            add("database.dump.age", "database", argv=shared.pg_command(containers["postgres"], "pg_dump", ["--format=custom", "--no-owner", "--no-acl"]))
            add("objects.tar.age", "objects", argv=tar_volume(containers["postgres"]["Image"], volumes["data-files"]))
            add("redis.rdb.age", "redis", argv=["docker", "exec", containers["redis"]["Id"], "sh", "-ec", 'export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli --rdb -'])
            add("environment.env.age", "environment", data=env_raw)
            add("runtime.json.age", "configuration", data=canonical(runtime_record(containers)), origin={"kind": "runtime-config"})
            add("models.tar.age", "configuration", argv=tar_volume(containers["postgres"]["Image"], volumes["ollama-data"], "models"), origin={"kind": "model-cache"})
            for service, paths in ASSETS.items():
                add(service + "-assets.tar.age", "configuration", argv=["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "tar", containers[service]["Image"], "--numeric-owner", "-cf", "-", "-C", "/app", *paths], origin={"kind": "runtime-assets", "service": service})
            for name, filename, origin in configs:
                add("config-" + name + ".age", "configuration", filename=filename, origin=origin)
            require(shared.database_scope(containers["postgres"]) == database, "DATABASE_CHANGED_DURING_QUIESCED_BACKUP")
            shared.pack_archive(binary, args.recipient, archive, directory, manifest)
        finally:
            if paused:
                command(["docker", "start", *[containers[name]["Id"] for name in reversed(writers)]], timeout=240)
                shared.wait_source_services(containers, writers)
                shared.emit("trial_source_resumed", services=len(writers))
    with tempfile.TemporaryDirectory(prefix=".trial-verify-", dir=archive.parent) as temporary:
        verified, _, _, _, _ = verify_archive(binary, identity, archive, pathlib.Path(temporary))
        require(verified == manifest, "FINAL_ARCHIVE_MANIFEST_MISMATCH")
    info = shared.digest_file(archive)
    result = {"format": "boyankb-trial-backup-receipt-v1", "status": "verified", "profile": PROFILE, "source_project": PROJECT, "consistency": "application-quiesced", "created_at": manifest["created_at"], "verified_at": now(), "archive_sha256": info["sha256"], "archive_size": info["size"], "source_ids": manifest["source_ids"], "service_names": list(SERVICES), "database_fingerprint": manifest["database_fingerprint"], "writers_resumed": True}
    write_json(receipt, result)
    shared.emit("trial_backup_verified", receipt=str(receipt), archive=str(archive), archive_sha256=info["sha256"], services=len(SERVICES))


def validate_target(project):
    require(re.fullmatch(r"boyankb-restore-trial-[a-z0-9][a-z0-9-]{2,29}", project), "TRIAL_RESTORE_PROJECT_REQUIRED")
    require(not any(item in project.split("-") for item in ("prod", "production", "local", "staging")), "RESTORE_RESERVED_PROJECT_REJECTED")


def build_restore_config(project, runtime, manifest, bind_files):
    validate_target(project)
    result = {"name": project, "services": {}, "volumes": {}, "networks": {"isolated": {"name": project + "_isolated", "internal": True}}}
    for name in SERVICES:
        original = runtime[name]
        config = original["config"]
        env = dict(item.split("=", 1) for item in config.get("Env") or [] if "=" in item)
        if name == "app":
            env.update({"AUTO_MIGRATE": "false", "WEKNORA_SANDBOX_DOCKER_ENABLED": "false"})
        item = {"image": manifest["images"][name]["id"], "pull_policy": "never", "environment": env, "networks": ["isolated"], "restart": "no", "security_opt": ["no-new-privileges:true"]}
        for source, target in (("Cmd", "command"), ("Entrypoint", "entrypoint"), ("User", "user"), ("WorkingDir", "working_dir")):
            if config.get(source):
                item[target] = config[source]
        if name not in ("postgres", "redis"):
            item["profiles"] = ["application-not-started"]
        if original.get("read_only"):
            item["read_only"] = True
        if name == "document-renderer":
            item.update({"read_only": True, "user": "65532:65532", "cap_drop": ["ALL"], "tmpfs": ["/tmp:rw,nosuid,nodev,size=384m,mode=1777"], "mem_limit": "768m", "cpus": 1.5, "pids_limit": 96})
        mounts = []
        for mount in original.get("mounts", []):
            target = mount["Destination"]
            if mount["Type"] == "volume":
                logical = VOLUME_TARGETS.get(name, {}).get(target)
                require(logical, "RESTORE_VOLUME_LAYOUT_REJECTED")
                result["volumes"][logical] = {"name": project + "_" + logical}
                mounts.append({"type": "volume", "source": logical, "target": target, "read_only": not mount.get("RW", True)})
            else:
                require(mount["Type"] == "bind" and (name, target) in bind_files, "RESTORE_BIND_NOT_ARCHIVED")
                mounts.append({"type": "bind", "source": str(bind_files[(name, target)]), "target": target, "read_only": True})
        if mounts:
            item["volumes"] = mounts
        result["services"][name] = item
    require(set(result["volumes"]) == {"postgres-data", "redis-data", "data-files", "docreader-tmp", "ollama-data"}, "RESTORE_VOLUMES_MISSING")
    result["services"]["redis"]["entrypoint"] = ["sh", "-ec"]
    result["services"]["redis"]["command"] = ['exec redis-server --dir /data --dbfilename dump.rdb --appendonly no --requirepass "$REDIS_PASSWORD"']
    return result


def restore_volume(binary, identity, directory, entry, inventory, image, volume):
    empty = 'test -z "$(find /restore -mindepth 1 -maxdepth 1 -print -quit)"'
    recovery.pipe_restore(binary, identity, directory / entry["name"], recovery.volume_command(image, volume, empty + '; exec tar --numeric-owner --same-owner --same-permissions -xf - -C /restore'))
    process = subprocess.Popen(recovery.volume_command(image, volume, 'exec tar --numeric-owner -cf - -C /restore .', True), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        actual = recovery.object_inventory(process.stdout)
        while process.stdout.read(1024 * 1024):
            pass
        require(process.wait() == 0, "RESTORED_VOLUME_READ_FAILED")
        if "." not in inventory:
            actual.pop(".", None)
        require(actual == inventory, "RESTORED_VOLUME_INVENTORY_MISMATCH")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def restore(args):
    validate_target(args.project)
    target = pathlib.Path(args.target_dir).resolve()
    require(not target.exists() and target.parent.is_dir(), "RESTORE_DIRECTORY_MUST_BE_NEW")
    archive = pathlib.Path(args.archive).resolve(strict=True)
    identity = pathlib.Path(args.identity).resolve(strict=True)
    binary = shared.age_binary(args.age_binary)
    local_context(args.context)
    recovery.fresh_project(args.project, PROJECT)
    with tempfile.TemporaryDirectory(prefix=".trial-restore-", dir=target.parent) as temporary:
        directory = pathlib.Path(temporary)
        shared.protect_directory(directory)
        manifest, roles, env_raw, inventories, runtime = verify_archive(binary, identity, archive, directory)
        for name in SERVICES:
            image = shared.docker_json("image", "inspect", manifest["images"][name]["id"])[0]
            require(image["Id"] == manifest["images"][name]["id"], "RESTORE_EXACT_IMAGE_REQUIRED")
        target.mkdir(mode=0o700)
        shared.protect_directory(target)
        write_new(target / ".env", env_raw)
        config_dir = target / "configuration"
        config_dir.mkdir(mode=0o700)
        bind_files = {}
        for entry in manifest["components"]:
            kind = entry.get("origin", {}).get("kind")
            if kind in ("bind", "compose", "runtime-config"):
                output = config_dir / entry["name"].removesuffix(".age")
                write_new(output, read_component(binary, identity, directory, entry))
                if kind == "bind":
                    key = (entry["origin"]["service"], entry["origin"]["target"])
                    require(key not in bind_files, "RESTORE_BIND_DUPLICATE")
                    bind_files[key] = output
        config = build_restore_config(args.project, runtime, manifest, bind_files)
        compose_file = target / "compose.json"
        write_json(compose_file, compose_literals(config))
        compose = shared.compose_command(args.project, [compose_file], target / ".env")
        environment = shared.clean_env()
        recovery.fresh_project(args.project, PROJECT)
        volume_names = dict((name, value["name"]) for name, value in config["volumes"].items())
        for name, volume in volume_names.items():
            command(["docker", "volume", "create", "--label", "com.docker.compose.project=" + args.project, "--label", "com.docker.compose.volume=" + name, volume])
        image = manifest["images"]["postgres"]["id"]
        restore_volume(binary, identity, directory, roles["objects"], inventories["objects"], image, volume_names["data-files"])
        for entry in manifest["components"]:
            kind = entry.get("origin", {}).get("kind")
            if kind == "model-cache":
                restore_volume(binary, identity, directory, entry, inventories["ollama-data"], image, volume_names["ollama-data"])
            elif kind == "runtime-assets":
                service = entry["origin"]["service"]
                process = subprocess.Popen(["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "tar", manifest["images"][service]["id"], "--numeric-owner", "-cf", "-", "-C", "/app", *ASSETS[service]], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    actual = recovery.object_inventory(process.stdout)
                    while process.stdout.read(1024 * 1024):
                        pass
                    require(process.wait() == 0 and actual == inventories[service], "RESTORED_IMAGE_ASSETS_MISMATCH")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    process.stdout.close()
                with open(directory / entry["name"], "rb") as source:
                    with open(config_dir / entry["name"], "xb") as destination:
                        shutil.copyfileobj(source, destination)
        redis_image = manifest["images"]["redis"]["id"]
        recovery.pipe_restore(binary, identity, directory / roles["redis"]["name"], recovery.volume_command(redis_image, volume_names["redis-data"], 'test -z "$(find /restore -mindepth 1 -maxdepth 1 -print -quit)"; umask 077; cat > /restore/dump.rdb; redis-check-rdb /restore/dump.rdb >/dev/null'))
        redis_hash = command(recovery.volume_command(redis_image, volume_names["redis-data"], 'sha256sum /restore/dump.rdb', True)).decode().split()[0]
        require(redis_hash == roles["redis"]["plaintext_sha256"], "RESTORED_REDIS_CHECKSUM_MISMATCH")
        command(compose + ["up", "-d", "--no-build", "--no-deps", "postgres"], env=environment, timeout=180)
        postgres = recovery.wait_postgres(args.project)
        recovery.require_empty_database(postgres)
        recovery.restore_database(binary, identity, directory / roles["database"]["name"], postgres)
        database = shared.database_scope(postgres)
        require(database == manifest["database"], "RESTORED_DATABASE_INVENTORY_MISMATCH")
        command(compose + ["up", "-d", "--no-build", "--no-deps", "redis"], env=environment, timeout=180)
        redis = shared.service_container(args.project, "redis")
        ready = False
        for _ in range(60):
            try:
                ready = command(["docker", "exec", redis["Id"], "sh", "-ec", 'export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli ping']).strip() == b"PONG"
                if ready:
                    break
            except BackupError:
                pass
            time.sleep(1)
        require(ready, "RESTORED_REDIS_START_FAILED")
        ids = command(["docker", "ps", "-aq", "--no-trunc", "--filter", "label=com.docker.compose.project=" + args.project]).decode().split()
        require(set(ids) == {postgres["Id"], redis["Id"]}, "RESTORE_UNEXPECTED_CONTAINER_PRESENT")
        for item in (postgres, redis):
            require(not item["HostConfig"].get("PortBindings"), "RESTORE_PUBLISHED_PORT_REJECTED")
            networks = item["NetworkSettings"].get("Networks", {})
            require(set(networks) == {args.project + "_isolated"}, "RESTORE_NETWORK_MISMATCH")
            network = shared.docker_json("network", "inspect", next(iter(networks.values()))["NetworkID"])[0]
            require(network.get("Internal") is True and network.get("Labels", {}).get("com.docker.compose.project") == args.project, "RESTORE_NETWORK_NOT_ISOLATED")
        require((target / ".env").read_bytes() == env_raw, "RESTORED_ENVIRONMENT_MISMATCH")
        info = shared.digest_file(archive)
        report = {"format": "boyankb-trial-restore-report-v1", "status": "verified", "created_at": now(), "archive_sha256": info["sha256"], "archive_size": info["size"], "source_project": PROJECT, "target_project": args.project, "database_verified": True, "files_verified": True, "redis_verified": True, "environment_verified": True, "runtime_assets_verified": True, "model_cache_verified": True, "application_started": False, "network_internal": True, "database_fingerprint": fingerprint(database), "source_ids": manifest["source_ids"], "service_names": list(SERVICES), "running_services": ["postgres", "redis"]}
        write_json(target / "restore-report.json", report)
        shared.emit("trial_restore_verified", report=str(target / "restore-report.json"), application_started=False, running_services=2)


def verify_receipts(archive, receipt_file, restore_file, expected_source_ids=None, max_age_hours=24):
    receipt = json.loads(pathlib.Path(receipt_file).read_bytes())
    report = json.loads(pathlib.Path(restore_file).read_bytes())
    info = shared.digest_file(pathlib.Path(archive))
    require(receipt.get("format") == "boyankb-trial-backup-receipt-v1" and receipt.get("profile") == PROFILE, "BACKUP_RECEIPT_FORMAT_REJECTED")
    require(report.get("format") == "boyankb-trial-restore-report-v1", "RESTORE_REPORT_FORMAT_REJECTED")
    for item in (receipt, report):
        require(item.get("status") == "verified" and item.get("source_project") == PROJECT and item.get("archive_sha256") == info["sha256"] and item.get("archive_size") == info["size"], "BACKUP_EVIDENCE_MISMATCH")
        validate_ids(item.get("source_ids"))
        require(item.get("service_names") == list(SERVICES), "BACKUP_EVIDENCE_SERVICES_INVALID")
        created = datetime.fromisoformat(item["created_at"])
        require(created.tzinfo is not None and -60 <= (datetime.now(timezone.utc) - created).total_seconds() <= max_age_hours * 3600, "BACKUP_EVIDENCE_EXPIRED")
    require(receipt.get("writers_resumed") is True and receipt.get("consistency") == "application-quiesced", "BACKUP_NOT_QUIESCED_OR_RESUMED")
    validate_target(report.get("target_project", ""))
    require(all(report.get(key) is True for key in ("database_verified", "files_verified", "redis_verified", "environment_verified", "runtime_assets_verified", "model_cache_verified", "network_internal")) and report.get("application_started") is False, "RESTORE_PROOF_INCOMPLETE")
    require(receipt["source_ids"] == report["source_ids"] and receipt["database_fingerprint"] == report["database_fingerprint"], "SOURCE_PROOF_MISMATCH")
    if expected_source_ids is not None:
        require(receipt["source_ids"] == expected_source_ids, "SOURCE_CONTAINERS_CHANGED")
    return {"archive_sha256": info["sha256"], "archive_size": info["size"], "source_ids": receipt["source_ids"], "database_fingerprint": receipt["database_fingerprint"], "target_project": report["target_project"]}


def verify_evidence(args):
    archive = pathlib.Path(args.archive).resolve(strict=True)
    identity = pathlib.Path(args.identity).resolve(strict=True)
    binary = shared.age_binary(args.age_binary)
    proof = verify_receipts(archive, args.receipt, args.restore_report)
    with tempfile.TemporaryDirectory(prefix=".trial-proof-", dir=archive.parent) as temporary:
        directory = pathlib.Path(temporary)
        shared.protect_directory(directory)
        manifest, _, _, _, _ = verify_archive(binary, identity, archive, directory)
        require(proof["source_ids"] == manifest["source_ids"] and proof["database_fingerprint"] == manifest["database_fingerprint"], "ENCRYPTED_BACKUP_PROOF_MISMATCH")
        receipt = json.loads(pathlib.Path(args.receipt).read_bytes())
        require(receipt.get("created_at") == manifest.get("created_at"), "SNAPSHOT_CREATION_TIME_MISMATCH")
        require(shared.digest_file(archive) == {"size": proof["archive_size"], "sha256": proof["archive_sha256"]}, "ARCHIVE_CHANGED_DURING_VERIFICATION")
    shared.emit("trial_evidence_verified", **proof)
    return proof


def parser(mode):
    result = argparse.ArgumentParser()
    result.add_argument("--age-binary")
    result.add_argument("--context")
    result.add_argument("--identity", required=True)
    if mode == "backup":
        result.add_argument("--project", default=PROJECT)
        result.add_argument("--compose", action="append", required=True)
        result.add_argument("--env-file", required=True)
        result.add_argument("--recipient", required=True)
        result.add_argument("--output", required=True)
        result.add_argument("--receipt", required=True)
        result.add_argument("--quiesce", action="store_true", required=True)
    elif mode == "restore":
        result.add_argument("--archive", required=True)
        result.add_argument("--project", required=True)
        result.add_argument("--target-dir", required=True)
    else:
        result.add_argument("--archive", required=True)
        result.add_argument("--receipt", required=True)
        result.add_argument("--restore-report", required=True)
    return result


def main(mode):
    signal.signal(signal.SIGTERM, shared.interrupted)
    try:
        {"backup": backup, "restore": restore, "verify": verify_evidence}[mode](parser(mode).parse_args())
        return 0
    except KeyboardInterrupt:
        shared.emit("trial_" + mode + "_failed", code="INTERRUPTED")
        return 130
    except BackupError as error:
        shared.emit("trial_" + mode + "_failed", code=str(error))
        return 1
    except Exception:
        shared.emit("trial_" + mode + "_failed", code="UNEXPECTED_FAILURE")
        return 1
