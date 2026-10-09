import argparse
import contextlib
import importlib.util
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

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("backup_prod", pathlib.Path(__file__).with_name("backup-prod.py"))
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
require = shared.require
emit = shared.emit
command = shared.command
BackupError = shared.BackupError


@contextlib.contextmanager
def decrypt_stream(binary, identity, filename):
    process = subprocess.Popen([binary, "--decrypt", "--identity", str(identity), str(filename)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        yield process.stdout
        while process.stdout.read(1024 * 1024):
            pass
        require(process.wait() == 0, "AGE_AUTHENTICATION_FAILED")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def unpack_verified(binary, identity, filename, directory):
    entries = {}
    manifest = None
    total = 0
    with decrypt_stream(binary, identity, filename) as source:
        with tarfile.open(fileobj=source, mode="r|") as archive:
            for member in archive:
                require(member.isfile() and len(entries) < 128, "ARCHIVE_MEMBER_TYPE_OR_COUNT_REJECTED")
                require(member.name not in entries and manifest is None, "ARCHIVE_DUPLICATE_OR_TRAILING_MEMBER")
                total += member.size
                require(0 <= member.size <= filename.stat().st_size and total <= filename.stat().st_size, "ARCHIVE_MEMBER_SIZE_REJECTED")
                stream = archive.extractfile(member)
                if member.name == "manifest.json":
                    require(member.size <= 8 * 1024 * 1024, "MANIFEST_TOO_LARGE")
                    manifest = json.loads(stream.read())
                else:
                    require(re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,80}\.age", member.name), "ARCHIVE_MEMBER_NAME_REJECTED")
                    target = directory / member.name
                    with open(target, "xb") as destination:
                        shutil.copyfileobj(stream, destination, 1024 * 1024)
                    os.chmod(target, 0o600)
                    entries[member.name] = shared.digest_file(target)
    require(isinstance(manifest, dict) and manifest.get("format") == "boyankb-encrypted-backup-v1", "BACKUP_FORMAT_UNSUPPORTED")
    require(manifest.get("storage") == "local" and manifest.get("local_base_dir") == "/data/files", "BACKUP_STORAGE_UNSUPPORTED")
    require(manifest.get("consistency") in ("application-quiesced", "online-cross-store-not-atomic"), "BACKUP_CONSISTENCY_INVALID")
    components = manifest.get("components")
    require(isinstance(components, list) and len(components) == len(entries), "MANIFEST_COMPONENTS_MISMATCH")
    names = set()
    roles = {}
    for entry in components:
        name = entry.get("name")
        require(name in entries and name not in names, "MANIFEST_COMPONENT_NAME_MISMATCH")
        names.add(name)
        require(entries[name] == {"size": entry.get("size"), "sha256": entry.get("sha256")}, "CIPHERTEXT_CHECKSUM_MISMATCH")
        require(isinstance(entry.get("plaintext_size"), int) and entry["plaintext_size"] > 0 and re.fullmatch(r"[a-f0-9]{64}", entry.get("plaintext_sha256", "")), "PLAINTEXT_MANIFEST_INVALID")
        role = entry.get("role")
        require(role in ("database", "objects", "redis", "environment", "configuration", "caddy-data", "caddy-config"), "COMPONENT_ROLE_REJECTED")
        if role != "configuration":
            require(role not in roles, "COMPONENT_ROLE_DUPLICATE")
            roles[role] = entry
    require({"database", "objects", "redis", "environment"}.issubset(roles), "REQUIRED_COMPONENT_MISSING")
    require(("caddy-data" in roles) == ("caddy-config" in roles), "CADDY_COMPONENT_PAIR_REQUIRED")
    return manifest, roles


def object_inventory(stream):
    inventory = {}
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            path = pathlib.PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in member.name and ":" not in member.name, "OBJECT_PATH_REJECTED")
            name = str(path)
            require(member.isfile() or member.isdir(), "OBJECT_LINK_OR_SPECIAL_FILE_REJECTED")
            require(member.mode & (0o4000 if member.isdir() else 0o7000) == 0, "OBJECT_SPECIAL_PERMISSION_REJECTED")
            require(name not in inventory, "OBJECT_PATH_DUPLICATE")
            if member.isdir():
                inventory[name] = {"type": "directory", "mode": member.mode & 0o7777, "uid": member.uid, "gid": member.gid}
            else:
                source = shared.DigestReader(archive.extractfile(member))
                while source.read(1024 * 1024):
                    pass
                require(source.size == member.size, "OBJECT_TRUNCATED")
                inventory[name] = {"type": "file", "size": source.size, "sha256": source.digest.hexdigest(), "mode": member.mode & 0o777, "uid": member.uid, "gid": member.gid}
    return inventory


def verify_components(binary, identity, directory, manifest):
    env_raw = None
    inventories = {}
    for entry in manifest["components"]:
        with decrypt_stream(binary, identity, directory / entry["name"]) as stream:
            reader = shared.DigestReader(stream)
            if entry["role"] in ("objects", "caddy-data", "caddy-config"):
                inventories[entry["role"]] = object_inventory(reader)
            elif entry["role"] == "environment":
                require(entry["plaintext_size"] <= 4 * 1024 * 1024, "ENV_FILE_TOO_LARGE")
                env_raw = reader.read(entry["plaintext_size"] + 1)
            elif entry["role"] in ("database", "redis"):
                require(reader.read(5) == (b"PGDMP" if entry["role"] == "database" else b"REDIS"), "COMPONENT_SIGNATURE_INVALID")
            while reader.read(1024 * 1024):
                pass
            require(reader.size == entry["plaintext_size"] and reader.digest.hexdigest() == entry["plaintext_sha256"], "PLAINTEXT_CHECKSUM_MISMATCH")
    require(env_raw and "objects" in inventories, "VERIFIED_COMPONENT_MISSING")
    return env_raw, inventories


def fresh_project(project, source_project):
    require(re.fullmatch(r"boyankb-restore-[a-z0-9][a-z0-9-]{2,35}", project), "RESTORE_PROJECT_PREFIX_REQUIRED")
    require(project != source_project and not any(item in project.split("-") for item in ("prod", "production", "local", "staging")), "PRODUCTION_OR_SOURCE_TARGET_REJECTED")
    for resource, flags in (("ps", ["-aq"]), ("volume", ["ls", "-q"]), ("network", ["ls", "-q"])):
        existing = command(["docker", resource, *flags, "--filter", "label=com.docker.compose.project=" + project]).strip()
        require(not existing, "RESTORE_PROJECT_ALREADY_EXISTS")
    all_containers = command(["docker", "ps", "-a", "--format", "{{.Names}}"] ).decode().splitlines()
    require(not any(name.startswith(project + "-") or name.startswith(project + "_") or name == project for name in all_containers), "RESTORE_CONTAINER_ALREADY_EXISTS")
    all_volumes = command(["docker", "volume", "ls", "--format", "{{.Name}}"] ).decode().splitlines()
    require(not any(name.startswith(project + "_") or name == project for name in all_volumes), "RESTORE_VOLUME_ALREADY_EXISTS")
    all_networks = command(["docker", "network", "ls", "--format", "{{.Name}}"] ).decode().splitlines()
    require(not any(name.startswith(project + "_") or name == project for name in all_networks), "RESTORE_NETWORK_ALREADY_EXISTS")


def restore_config(compose, environment, project, manifest):
    config = json.loads(command(compose + ["config", "--format", "json"], env=environment))
    services = config.get("services", {})
    require({"postgres", "redis", "app", "frontend"}.issubset(services), "RESTORE_SERVICES_MISSING")
    require(set(services).issubset({"postgres", "redis", "app", "frontend", "docreader", "ollama", "caddy"}), "RESTORE_EXTRA_SERVICE_REJECTED")
    volumes = config.get("volumes", {})
    for key, value in volumes.items():
        require(not value.get("external") and value.get("name") == project + "_" + key, "RESTORE_VOLUME_NOT_ISOLATED")
        require(not value.get("driver_opts") and value.get("driver", "local") == "local", "RESTORE_VOLUME_DRIVER_REJECTED")
    networks = config.get("networks", {})
    require(networks and all(value.get("internal") and not value.get("external") and value.get("name") == project + "_" + key for key, value in networks.items()), "RESTORE_NETWORK_MUST_BE_INTERNAL_AND_NEW")
    for name, service in services.items():
        require(not service.get("container_name") and not service.get("network_mode") and not service.get("privileged") and not service.get("pid"), "RESTORE_SERVICE_ISOLATION_REJECTED")
        require(not service.get("devices") and not service.get("volumes_from"), "RESTORE_SERVICE_HOST_ACCESS_REJECTED")
        require(not service.get("configs") and not service.get("secrets") and not service.get("use_api_socket"), "RESTORE_SERVICE_HIDDEN_MOUNT_REJECTED")
        require(set(service.get("cap_add", [])).issubset({"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE"}), "RESTORE_SERVICE_CAPABILITY_REJECTED")
        require(all(port.get("host_ip") == "127.0.0.1" for port in service.get("ports", [])), "RESTORE_PORT_MUST_BE_LOOPBACK")
        require(set(service.get("networks", {})).issubset(networks) and service.get("networks"), "RESTORE_SERVICE_NETWORK_REJECTED")
        for mount in service.get("volumes", []):
            if mount.get("type") == "volume":
                require(mount.get("source") in volumes and not mount.get("volume", {}).get("subpath"), "RESTORE_VOLUME_MOUNT_REJECTED")
            else:
                require(mount.get("type") == "bind" and mount.get("read_only") and pathlib.Path(mount.get("source", "")).is_file(), "RESTORE_BIND_MUST_BE_READ_ONLY_FILE")
    expected_mounts = {"postgres": ("postgres-data", "/var/lib/postgresql/data"), "app": ("data-files", "/data/files"), "redis": ("redis-data", "/data")}
    has_caddy = any(entry["role"] == "caddy-data" for entry in manifest["components"])
    require(has_caddy == ("caddy" in services), "CADDY_BACKUP_AND_RESTORE_SERVICE_MISMATCH")
    for service, (volume, destination) in expected_mounts.items():
        matches = [mount for mount in services[service].get("volumes", []) if mount.get("target") == destination]
        require(len(matches) == 1 and matches[0].get("source") == volume and matches[0].get("type") == "volume", "RESTORE_VOLUME_LAYOUT_MISMATCH")
    for name in ("postgres", "redis", "app", "frontend"):
        image = shared.docker_json("image", "inspect", services[name]["image"])[0]
        require(image["Id"] == manifest["images"][name]["id"], "RESTORE_IMAGE_DIFFERS_FROM_BACKUP")
    if has_caddy:
        for volume, destination in (("caddy-data", "/data"), ("caddy-config", "/config")):
            matches = [mount for mount in services["caddy"].get("volumes", []) if mount.get("target") == destination]
            require(len(matches) == 1 and matches[0].get("source") == volume and matches[0].get("type") == "volume", "RESTORE_CADDY_VOLUME_LAYOUT_MISMATCH")
        require(shared.docker_json("image", "inspect", services["caddy"]["image"])[0]["Id"] == manifest["images"]["caddy"]["id"], "RESTORE_CADDY_IMAGE_MISMATCH")
    app_env = services["app"].get("environment", {})
    require(str(app_env.get("AUTO_MIGRATE", "")).lower() == "false", "RESTORE_AUTO_MIGRATE_MUST_BE_FALSE")
    require(app_env.get("DB_HOST") == "postgres" and app_env.get("REDIS_ADDR") == "redis:6379", "RESTORE_DATABASE_ENDPOINT_REJECTED")
    return config


def configuration_overrides(config, manifest, allowed):
    allowed = set(allowed)
    require(all(re.fullmatch(r"[a-z][a-z0-9-]*:/[A-Za-z0-9_./-]+", item) for item in allowed), "CONFIG_OVERRIDE_SELECTOR_INVALID")
    seen = set()
    drift = []
    for entry in manifest["components"]:
        origin = entry.get("origin", {})
        if entry["role"] != "configuration" or origin.get("kind") != "bind":
            continue
        service, target = origin.get("service"), origin.get("target")
        require(service in config["services"] and isinstance(target, str), "ARCHIVED_CONFIG_SERVICE_MISSING")
        selector = service + ":" + target
        require(selector not in seen, "ARCHIVED_CONFIG_TARGET_DUPLICATE")
        seen.add(selector)
        matches = [mount for mount in config["services"][service].get("volumes", []) if mount.get("target") == target and mount.get("type") == "bind" and mount.get("read_only")]
        require(len(matches) == 1, "ARCHIVED_CONFIG_TARGET_NOT_RESTORED")
        actual = shared.digest_file(matches[0]["source"])
        if actual != {"size": entry["plaintext_size"], "sha256": entry["plaintext_sha256"]}:
            require(selector in allowed, "RESTORE_CONFIG_DIFFERS_FROM_BACKUP")
            drift.append(selector)
    require(allowed.issubset(seen), "CONFIG_OVERRIDE_SELECTOR_NOT_IN_BACKUP")
    return sorted(drift)


def export_configurations(binary, identity, directory, manifest, target):
    output = target / "config"
    output.mkdir(mode=0o700)
    entries = []
    for entry in manifest["components"]:
        if entry["role"] != "configuration":
            continue
        filename = entry["name"].removesuffix(".age")
        descriptor = os.open(output / filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as destination, decrypt_stream(binary, identity, directory / entry["name"]) as source:
            shutil.copyfileobj(source, destination, 1024 * 1024)
        entries.append({"file": filename, "origin": entry.get("origin", {})})
    with open(output / "index.json", "x", encoding="utf-8") as destination:
        json.dump(entries, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


def pipe_restore(binary, identity, filename, argv):
    target = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with decrypt_stream(binary, identity, filename) as source:
            shutil.copyfileobj(source, target.stdin, 1024 * 1024)
        target.stdin.close()
        require(target.wait() == 0, "RESTORE_COMPONENT_COMMAND_FAILED")
    finally:
        if target.poll() is None:
            target.kill()
        target.wait()


def volume_command(image, volume, script, read_only=False):
    mount = "type=volume,src=" + volume + ",dst=/restore" + (",readonly" if read_only else "")
    return ["docker", "run", "--rm", "-i", "--network", "none", "--mount", mount, "--entrypoint", "sh", image, "-ec", script]


def wait_postgres(project):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            container = shared.service_container(project, "postgres")
            require(shared.pg_query(container, "SELECT 1;") == "1", "POSTGRES_NOT_READY")
            return container
        except BackupError:
            time.sleep(1)
    raise BackupError("RESTORED_POSTGRES_START_TIMEOUT")


def require_empty_database(postgres):
    tables = json.loads(shared.pg_query(postgres, """
SELECT coalesce(json_agg(json_build_object('name', c.relname, 'extension', (
 SELECT e.extname FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
 WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
 AND d.refclassid='pg_extension'::regclass AND d.deptype='e' LIMIT 1
))), '[]') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p','f');
"""))
    require(all(table == {"name": "spatial_ref_sys", "extension": "postgis"} for table in tables), "RESTORE_DATABASE_NOT_EMPTY")


def preinitialized_schemas(postgres):
    inventory = json.loads(shared.pg_query(postgres, """
SELECT json_build_object(
 'schemas', (SELECT json_agg(nspname) FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'),
 'extensions', (SELECT json_agg(extname) FROM pg_extension),
 'unowned_tables', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','f','S')
  AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.refclassid='pg_extension'::regclass AND d.deptype='e')),
 'unowned_functions', (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
  AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.refclassid='pg_extension'::regclass AND d.deptype='e'))
);
"""))
    schemas = set(inventory["schemas"] or [])
    extensions = set(inventory["extensions"] or [])
    require(schemas.issubset({"public", "paradedb", "pdb", "tiger", "topology"}), "RESTORE_PREINITIALIZED_SCHEMA_REJECTED")
    require(inventory["unowned_tables"] == 0 and inventory["unowned_functions"] == 0, "RESTORE_PREINITIALIZED_OBJECT_REJECTED")
    required = {"paradedb": "pg_search", "pdb": "pg_search", "tiger": "postgis_tiger_geocoder", "topology": "postgis_topology"}
    require(all(required[name] in extensions for name in schemas if name in required), "RESTORE_PREINITIALIZED_EXTENSION_MISSING")
    return schemas.intersection({"paradedb", "tiger", "topology"})


def filter_schema_toc(toc, existing):
    require(set(existing).issubset({"paradedb", "tiger", "topology"}), "RESTORE_SCHEMA_FILTER_REJECTED")
    lines = []
    skipped = []
    for line in toc.decode("utf-8").splitlines():
        match = re.fullmatch(r"\d+; 2615 \d+ SCHEMA - ([A-Za-z_][A-Za-z_0-9]*) \S+", line)
        if match and match[1] in existing:
            require(match[1] not in skipped, "RESTORE_SCHEMA_TOC_DUPLICATE")
            skipped.append(match[1])
        else:
            lines.append(line)
    require(set(skipped) == set(existing), "RESTORE_SCHEMA_TOC_MISMATCH")
    return ("\n".join(lines) + "\n").encode(), sorted(skipped)


def restore_database(binary, identity, filename, postgres):
    existing = preinitialized_schemas(postgres)
    with decrypt_stream(binary, identity, filename) as source:
        process = subprocess.run(["docker", "exec", "-i", postgres["Id"], "pg_restore", "--list"], stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180)
        require(process.returncode == 0, "RESTORE_DATABASE_TOC_FAILED")
    toc, skipped = filter_schema_toc(process.stdout, existing)
    path = "/tmp/boyankb-restore-" + os.urandom(12).hex() + ".list"
    command(["docker", "exec", "-i", postgres["Id"], "sh", "-ec", 'umask 077; set -C; cat > "$1"', "restore", path], toc)
    try:
        pipe_restore(binary, identity, filename, shared.pg_command(postgres, "pg_restore", ["--no-owner", "--no-acl", "--exit-on-error", "--single-transaction", "--use-list=" + path]))
    finally:
        command(["docker", "exec", postgres["Id"], "rm", "--", path])
    return skipped


def restore(args):
    binary = shared.age_binary(args.age_binary)
    archive = pathlib.Path(args.archive).resolve(strict=True)
    identity = pathlib.Path(args.identity).resolve(strict=True)
    require(archive.is_file() and identity.is_file(), "ARCHIVE_OR_IDENTITY_NOT_FILE")
    target = pathlib.Path(args.target_dir).resolve()
    require(not target.exists(), "RESTORE_DIRECTORY_ALREADY_EXISTS")
    require(target.parent.is_dir(), "RESTORE_PARENT_DIRECTORY_REQUIRED")
    fresh_project(args.project, "")
    with tempfile.TemporaryDirectory(prefix=".boyankb-restore-encrypted-", dir=target.parent) as temporary:
        directory = pathlib.Path(temporary)
        os.chmod(directory, 0o700)
        manifest, roles = unpack_verified(binary, identity, archive, directory)
        require(manifest["consistency"] == "application-quiesced" or args.accept_online_snapshot, "ONLINE_SNAPSHOT_REQUIRES_EXPLICIT_ACCEPTANCE")
        fresh_project(args.project, manifest["source_project"])
        env_raw, inventories = verify_components(binary, identity, directory, manifest)
        objects = inventories["objects"]
        values = shared.parse_env(env_raw)
        emit("archive_verified", components=len(manifest["components"]), objects=sum(item["type"] == "file" for item in objects.values()), consistency=manifest["consistency"])
        target.mkdir(mode=0o700)
        shared.protect_directory(target)
        env_file = target / ".env"
        descriptor = os.open(env_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(env_raw)
        values.update({"BOYANKB_ENV_FILE": str(env_file), "PRODUCTION_ENV_FILE": str(env_file), "BOYANKB_PROJECT_NAME": args.project, "AUTO_MIGRATE": "false"})
        environment = shared.clean_env(values)
        compose = shared.compose_command(args.project, args.compose, env_file)
        config = restore_config(compose, environment, args.project, manifest)
        overrides = configuration_overrides(config, manifest, args.allow_config_override)
        export_configurations(binary, identity, directory, manifest, target)
        fresh_project(args.project, manifest["source_project"])
        volume_names = ["postgres-data", "data-files", "redis-data", *[name for name in ("caddy-data", "caddy-config") if name in roles]]
        required_volumes = {name: config["volumes"][name]["name"] for name in volume_names}
        for name, volume in required_volumes.items():
            command(["docker", "volume", "create", "--label", "com.docker.compose.project=" + args.project, "--label", "com.docker.compose.volume=" + name, volume])
        pg_image = config["services"]["postgres"]["image"]
        redis_image = config["services"]["redis"]["image"]
        empty = 'test -z "$(find /restore -mindepth 1 -maxdepth 1 -print -quit)"'
        for volume in required_volumes.values():
            command(volume_command(pg_image, volume, empty, True))
        for role, inventory in inventories.items():
            volume = required_volumes["data-files" if role == "objects" else role]
            pipe_restore(binary, identity, directory / roles[role]["name"], volume_command(pg_image, volume, empty + '; exec tar --numeric-owner --same-owner --same-permissions -xf - -C /restore'))
            process = subprocess.Popen(volume_command(pg_image, volume, 'exec tar --numeric-owner -cf - -C /restore .', True), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                restored_inventory = object_inventory(process.stdout)
                while process.stdout.read(1024 * 1024):
                    pass
                require(process.wait() == 0 and restored_inventory == inventory, "RESTORED_OBJECT_INVENTORY_MISMATCH")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
        emit("object_files_restored", files=sum(item["type"] == "file" for item in objects.values()))
        pipe_restore(binary, identity, directory / roles["redis"]["name"], volume_command(redis_image, required_volumes["redis-data"], empty + '; umask 077; cat > /restore/dump.rdb; redis-check-rdb /restore/dump.rdb >/dev/null'))
        actual_hash = command(volume_command(redis_image, required_volumes["redis-data"], 'sha256sum /restore/dump.rdb', True)).decode().split()[0]
        require(actual_hash == roles["redis"]["plaintext_sha256"], "RESTORED_REDIS_CHECKSUM_MISMATCH")
        redis_convert = '''
redis-server --dir /restore --dbfilename dump.rdb --appendonly no --save "" --port 0 --unixsocket /tmp/boyankb-restore.sock >/dev/null 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
count=0
until redis-cli -s /tmp/boyankb-restore.sock ping >/dev/null 2>&1; do
  count=$((count + 1))
  test "$count" -lt 120
  sleep 1
done
test "$(redis-cli -s /tmp/boyankb-restore.sock CONFIG SET appendonly yes)" = OK
count=0
until redis-cli -s /tmp/boyankb-restore.sock INFO persistence | tr -d '\\r' | grep -q '^aof_rewrite_in_progress:0$'; do
  count=$((count + 1))
  test "$count" -lt 120
  sleep 1
done
redis-cli -s /tmp/boyankb-restore.sock INFO persistence | tr -d '\\r' | grep -q '^aof_last_bgrewrite_status:ok$'
redis-cli -s /tmp/boyankb-restore.sock SHUTDOWN SAVE >/dev/null
wait "$pid"
trap - EXIT
'''
        command(volume_command(redis_image, required_volumes["redis-data"], redis_convert), timeout=300)
        command(compose + ["up", "-d", "--no-build", "--no-deps", "postgres"], env=environment, timeout=180)
        postgres = wait_postgres(args.project)
        require_empty_database(postgres)
        preinitialized = restore_database(binary, identity, directory / roles["database"]["name"], postgres)
        restored_database = shared.database_scope(postgres)
        require(restored_database["row_counts"] == manifest["database"]["row_counts"] and restored_database["migration"] == manifest["database"]["migration"], "RESTORED_DATABASE_COUNTS_OR_MIGRATION_MISMATCH")
        emit("database_restored", tables=len(restored_database["row_counts"]), rows=sum(restored_database["row_counts"].values()), migration=restored_database["migration"])
        command(compose + ["up", "-d", "--no-build", "--no-deps", "redis"], env=environment, timeout=180)
        redis = shared.service_container(args.project, "redis")
        ready = False
        for _ in range(60):
            try:
                response = command(["docker", "exec", redis["Id"], "sh", "-ec", 'export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli ping'])
                if response.strip() == b"PONG":
                    ready = True
                    break
            except BackupError:
                pass
            time.sleep(1)
        require(ready, "RESTORED_REDIS_START_FAILED")
        require((target / ".env").read_bytes() == env_raw, "RESTORED_ENVIRONMENT_MISMATCH")
        invocation = {"arguments": compose, "environment": {"PRODUCTION_ENV_FILE": str(env_file), "BOYANKB_ENV_FILE": str(env_file), "BOYANKB_PROJECT_NAME": args.project, "AUTO_MIGRATE": "false"}}
        report = {"format": "boyankb-restore-report-v1", "project": args.project, "archive_sha256": shared.digest_file(archive)["sha256"], "consistency": manifest["consistency"], "database": restored_database, "preinitialized_schema_declarations_skipped": preinitialized, "object_files": sum(item["type"] == "file" for item in objects.values()), "caddy_volumes_verified": "caddy-data" in roles, "redis_rdb_verified": True, "environment_exact_match": True, "application_started": False, "network_internal": True, "ollama_models": manifest.get("ollama_models", []), "configuration_overrides": overrides, "compose_invocation": invocation}
        with open(target / "restore-report.json", "x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
        emit("restore_complete", project=args.project, report=str(target / "restore-report.json"), application_started=False)


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--archive", required=True)
    result.add_argument("--identity", required=True)
    result.add_argument("--age-binary")
    result.add_argument("--project", required=True)
    result.add_argument("--compose", action="append", required=True)
    result.add_argument("--target-dir", required=True)
    result.add_argument("--accept-online-snapshot", action="store_true")
    result.add_argument("--allow-config-override", action="append", default=[])
    return result


def main():
    signal.signal(signal.SIGTERM, shared.interrupted)
    try:
        restore(parser().parse_args())
        return 0
    except KeyboardInterrupt:
        emit("restore_failed", code="INTERRUPTED_TARGET_LEFT_ISOLATED")
        return 130
    except BackupError as error:
        emit("restore_failed", code=str(error))
        return 1
    except Exception:
        emit("restore_failed", code="UNEXPECTED_FAILURE_TARGET_LEFT_ISOLATED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
