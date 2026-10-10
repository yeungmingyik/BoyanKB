import argparse
import contextlib
import csv
import gzip
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import trial_backup as trial

shared = trial.shared
recovery = trial.recovery
require = shared.require
ROOT = Path(__file__).resolve().parents[1]
PROJECT = "weknora-local"
SERVICES = tuple(sorted((*trial.SERVICES, "trial-gateway", "neo4j")))
WRITERS = ("trial-gateway", "app", "docreader", "document-renderer", "ollama")
COLD = ("redis", "neo4j")
MOUNTS = {**trial.VOLUME_TARGETS, "neo4j": {"/data": "neo4j-data", "/logs": "neo4j-logs"}}
VOLUME_LABELS = {"redis-data": "redis-data-local", "ollama-data": "ollama-data-local", "neo4j-data": "neo4j-data-local", "neo4j-logs": "neo4j-logs-local"}
BIND_TARGETS = {"app": {"/app/config/config.yaml": "config/config.yaml", "/app/config/builtin_models.local.yaml": "config/builtin_models.local.yaml"}, "frontend": {"/etc/boyankb/trial-entrypoint.sh": "deployment/trial/frontend-entrypoint.sh"}, "trial-gateway": {"/etc/caddy/Caddyfile": "deployment/trial/Caddyfile"}}
IMAGE_REPOSITORIES = {"app": "boyankb/app", "frontend": "boyankb/frontend", "trial-gateway": "boyankb/trial-gateway", "document-renderer": "boyankb/document-renderer", "docreader": "wechatopenai/weknora-docreader", "postgres": "paradedb/paradedb", "redis": "redis", "ollama": "ollama/ollama", "neo4j": "neo4j"}
ARCHIVES = {"data-files.tar.gz": ("data-files", "."), "redis-data.tar.gz": ("redis-data", "."), "neo4j-data.tar.gz": ("neo4j-data", "."), "ollama-models.tar.gz": ("ollama-data", "models")}
PHASE = "initializing"
QUEUE_SCRIPT = """local result={active=0,pending=0,retry=0,scheduled=0,aggregating=0}
local cursor='0'
repeat
 local batch=redis.call('SCAN',cursor,'MATCH','asynq:*','COUNT',500)
 cursor=batch[1]
 for _,key in ipairs(batch[2]) do
  for state,_ in pairs(result) do
   if string.match(key,':'..state..'$') then
    local kind=redis.call('TYPE',key).ok
    if kind=='list' then result[state]=result[state]+redis.call('LLEN',key)
    elseif kind=='zset' then result[state]=result[state]+redis.call('ZCARD',key)
    elseif kind=='set' then result[state]=result[state]+redis.call('SCARD',key)
    elseif kind~='none' then return redis.error_reply('UNEXPECTED_QUEUE_TYPE') end
   end
  end
 end
until cursor=='0'
return cjson.encode(result)"""
IDLE_SQL = """SELECT json_build_object(
'unpaused_sources',(SELECT count(*) FROM data_sources WHERE deleted_at IS NULL AND (status <> 'paused' OR coalesce(sync_schedule,'') <> '')),
'running_syncs',(SELECT count(*) FROM sync_logs WHERE status='running'),
'busy_knowledge',(SELECT count(*) FROM knowledges WHERE deleted_at IS NULL AND (parse_status IN ('pending','processing','finalizing','deleting') OR summary_status IN ('pending','processing'))),
'busy_documents',(SELECT count(*) FROM boyan_document_jobs WHERE state IN ('generating','reviewing')),
'documents',(SELECT count(*) FROM knowledges WHERE deleted_at IS NULL),
'chunks',(SELECT count(*) FROM chunks WHERE deleted_at IS NULL),
'users',(SELECT count(*) FROM users WHERE deleted_at IS NULL)
);"""


def validate_source(containers, volumes, locked_images):
    require(set(containers) == set(SERVICES), "SOURCE_EXACT_NINE_SERVICES_REQUIRED")
    for name, container in containers.items():
        labels = container["Config"].get("Labels") or {}
        require(labels.get("com.docker.compose.project") == PROJECT and labels.get("com.docker.compose.service") == name, "SOURCE_CONTAINER_LABEL_MISMATCH")
        require(container["Image"] == locked_images[name], "SOURCE_IMAGE_LOCK_MISMATCH")
        require(re.fullmatch(r"[a-f0-9]{64}", container["Id"]), "SOURCE_CONTAINER_ID_INVALID")
        state = container["State"]
        require(state.get("Running") and not state.get("OOMKilled") and state.get("Health", {}).get("Status") == "healthy", "SOURCE_SERVICE_NOT_HEALTHY")
        host = container.get("HostConfig", {})
        require(not host.get("Privileged") and not host.get("Devices") and not host.get("VolumesFrom") and host.get("NetworkMode") not in ("host", "none"), "SOURCE_HOST_ACCESS_REJECTED")
        for bindings in (host.get("PortBindings") or {}).values():
            require(name in ("app", "frontend", "trial-gateway") and all(item.get("HostIp") == "127.0.0.1" for item in bindings), "SOURCE_NONLOCAL_PORT_REJECTED")
        observed = set()
        for mount in container.get("Mounts", []):
            target = mount["Destination"]
            if mount["Type"] == "volume":
                logical = MOUNTS.get(name, {}).get(target)
                require(logical and mount["Name"] == volumes.get(logical), "SOURCE_VOLUME_LAYOUT_MISMATCH")
                observed.add(target)
            elif mount["Type"] == "bind":
                relative = BIND_TARGETS.get(name, {}).get(target)
                require(relative and shared.bind_source(mount["Source"]).resolve() == (ROOT / relative).resolve(), "SOURCE_BIND_NOT_ALLOWLISTED")
                require((ROOT / relative).is_file(), "SOURCE_CONFIG_FILE_MISSING")
                observed.add(target)
            else:
                require(name == "trial-gateway" and mount["Type"] == "tmpfs" and target in ("/tmp", "/config", "/data"), "SOURCE_MOUNT_UNSUPPORTED")
        require(observed == set(MOUNTS.get(name, {})) | set(BIND_TARGETS.get(name, {})), "SOURCE_MOUNT_MISSING")
    require(set(volumes) == {"postgres-data", "redis-data", "data-files", "docreader-tmp", "ollama-data", "neo4j-data", "neo4j-logs"}, "SOURCE_EXACT_SEVEN_VOLUMES_REQUIRED")


def source():
    trial.local_context()
    ids = shared.command(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT]).decode().split()
    require(len(ids) == len(SERVICES), "SOURCE_EXACT_NINE_CONTAINERS_REQUIRED")
    containers = {name: shared.service_container(PROJECT, name) for name in SERVICES}
    lock = json.loads((ROOT / "deployment/trial/images.lock.json").read_text())
    references = {item["tag"]: item["id"] for item in lock["images"]}
    locked = {}
    for name, container in containers.items():
        reference = container["Config"]["Image"].split("@")[0]
        require(reference in references and reference.rsplit(":", 1)[0] == IMAGE_REPOSITORIES[name], "SOURCE_IMAGE_TAG_NOT_LOCKED")
        locked[name] = references[reference]
    require(len(set(locked.values())) == len(SERVICES), "SOURCE_IMAGE_ROLES_NOT_UNIQUE")
    volumes = {}
    for name, targets in MOUNTS.items():
        for target, logical in targets.items():
            volume = shared.named_mount(containers[name], target)
            require(logical not in volumes or volumes[logical] == volume, "SHARED_VOLUME_MISMATCH")
            volumes[logical] = volume
    validate_source(containers, volumes, locked)
    source_ids = {item["Id"] for item in containers.values()}
    for logical, volume in volumes.items():
        label = VOLUME_LABELS.get(logical, logical)
        require(volume == PROJECT + "_" + label, "SOURCE_VOLUME_NAME_MISMATCH")
        labels = shared.docker_json("volume", "inspect", volume)[0].get("Labels") or {}
        require(labels.get("com.docker.compose.project") == PROJECT and labels.get("com.docker.compose.volume") == label, "SOURCE_VOLUME_LABEL_MISMATCH")
        consumers = set(shared.command(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + volume]).decode().split())
        require(consumers.issubset(source_ids), "FOREIGN_VOLUME_CONSUMER_PRESENT")
    return containers, volumes


def runtime_environment(containers, source_values, generate=False):
    app = shared.container_environment(containers["app"])
    expected = {"DB_DRIVER": "postgres", "DB_HOST": "postgres", "DB_PORT": "5432", "REDIS_ADDR": "redis:6379", "REDIS_DB": "0", "STORAGE_TYPE": "local", "LOCAL_STORAGE_BASE_DIR": "/data/files", "RETRIEVE_DRIVER": "postgres", "NEO4J_ENABLE": "true", "NEO4J_URI": "bolt://neo4j:7687", "WEKNORA_SANDBOX_DOCKER_ENABLED": "false", "BOYAN_DOCUMENT_RENDERER_URL": "http://document-renderer:8091"}
    require(all(app.get(key, value) == value for key, value in expected.items()), "SOURCE_RUNTIME_NOT_SUPPORTED")
    names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / "deployment/tencent/env.example").read_text(), re.M))
    fixed = {"COMPOSE_PROJECT_NAME": "boyankb-tencent", "PUBLIC_HOST": "193.112.17.218", "PUBLIC_ORIGIN": "http://193.112.17.218", "WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL": "liuweijian@boyantech.xyz"}
    values = dict(fixed)
    for key in names - set(fixed):
        source_key = "NEO4J_PASSWORD" if key == "BOYAN_NEO4J_PASSWORD" else key
        value = app.get(source_key, "")
        require(value == source_values.get(key, ""), "RUNTIME_ENV_MISMATCH_" + key)
        if key == "SYSTEM_SIGNING_KEY" and not value:
            value = app.get("SYSTEM_AES_KEY", "")
        if key == "GRPC_AUTH_TOKEN" and not value:
            value = secrets.token_urlsafe(48) if generate else "GENERATED_ON_BACKUP"
        require(value and not any(character in value for character in ("\r", "\n", "\x00")), "RUNTIME_ENV_VALUE_INVALID_" + key)
        values[key] = value
    postgres = shared.container_environment(containers["postgres"])
    require(all(postgres.get(left) == app.get(right) for left, right in (("POSTGRES_USER", "DB_USER"), ("POSTGRES_PASSWORD", "DB_PASSWORD"), ("POSTGRES_DB", "DB_NAME"))), "POSTGRES_CREDENTIAL_MISMATCH")
    require(shared.container_environment(containers["redis"]).get("REDIS_PASSWORD") == values["REDIS_PASSWORD"], "REDIS_CREDENTIAL_MISMATCH")
    require(shared.container_environment(containers["neo4j"]).get("NEO4J_AUTH") == "neo4j/" + values["BOYAN_NEO4J_PASSWORD"], "NEO4J_CREDENTIAL_MISMATCH")
    require(shared.container_environment(containers["document-renderer"]).get("BOYAN_DOCUMENT_RENDERER_TOKEN") == values["BOYAN_DOCUMENT_RENDERER_TOKEN"], "RENDERER_CREDENTIAL_MISMATCH")
    require(shared.container_environment(containers["docreader"]).get("GRPC_AUTH_TOKEN", "") == app.get("GRPC_AUTH_TOKEN", ""), "DOCREADER_CREDENTIAL_MISMATCH")
    require(len(values["SYSTEM_AES_KEY"].encode()) == 32 and set(values) == names, "RUNTIME_ENV_ALLOWLIST_INVALID")
    return values


def env_bytes(values):
    return ("\n".join(key + "='" + value.replace("'", "\\'") + "'" for key, value in sorted(values.items())) + "\n").encode()


def idle(containers):
    data = json.loads(shared.pg_query(containers["postgres"], IDLE_SQL))
    argv = ["docker", "exec", "-i", containers["redis"]["Id"], "sh", "-ec", 'export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli --no-auth-warning --raw "$@"', "snapshot", "EVAL", QUEUE_SCRIPT, "0"]
    data["queues"] = json.loads(shared.command(argv))
    blockers = [key for key in ("unpaused_sources", "running_syncs", "busy_knowledge", "busy_documents") if data[key] != 0]
    blockers += ["queue_" + key for key, value in data["queues"].items() if value != 0]
    data["blockers"] = blockers
    data["idle"] = not blockers
    return data


def require_idle(state):
    require(state.get("idle") is True and not state.get("blockers"), "SOURCE_NOT_IDLE_" + "_".join(state.get("blockers", [])))


def content_counts(postgres):
    queries = {
        "documents": "SELECT count(*) AS count, md5(string_agg(id||':'||coalesce(file_hash,'')||':'||parse_status,',' ORDER BY id)) AS digest FROM knowledges WHERE deleted_at IS NULL",
        "chunks": "SELECT count(*) AS count, sum(length(content)) AS characters, md5(string_agg(id||':'||md5(content),',' ORDER BY id)) AS digest FROM chunks WHERE deleted_at IS NULL",
        "embeddings": "SELECT count(*) AS count, md5(string_agg(id::text||':'||chunk_id||':'||md5(content)||':'||md5(embedding::text),',' ORDER BY id)) AS digest FROM embeddings WHERE true",
    }
    result = {}
    for scope, condition in (("all", ""), ("enterprise", " AND knowledge_base_id='d9285ed1-0760-474b-ba82-a13347598176'")):
        result[scope] = {name: json.loads(shared.pg_query(postgres, "SELECT row_to_json(q) FROM (" + sql + condition + ") q;")) for name, sql in queries.items()}
    return result


def plan_state(containers, volumes):
    database = shared.database_scope(containers["postgres"])
    state = idle(containers)
    query = b"MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r]->() RETURN nodes,count(r) AS relationships;\n"
    graph = shared.command(["docker", "exec", "-i", containers["neo4j"]["Id"], "sh", "-ec", 'export NEO4J_USERNAME=neo4j NEO4J_PASSWORD="${NEO4J_AUTH#*/}"; exec cypher-shell -a bolt://127.0.0.1:7687 --format plain'], query)
    rows = list(csv.reader(graph.decode().splitlines()))
    require(len(rows) == 2 and [column.strip() for column in rows[0]] == ["nodes", "relationships"], "GRAPH_COUNTS_INVALID")
    graph_counts = dict(zip(("nodes", "relationships"), map(int, rows[1])))
    return {"format": "boyankb-tencent-snapshot-plan-v1", "source_project": PROJECT, "created_at": trial.now(), "images": {name: {"id": item["Image"], "reference": item["Config"]["Image"]} for name, item in containers.items()}, "source_ids": {name: item["Id"] for name, item in containers.items()}, "volumes": volumes, "database": database, "database_fingerprint": trial.fingerprint(database), "content": content_counts(containers["postgres"]), "idle": state, "models": shared.model_inventory(containers), "graph_counts_before_quiesce": graph_counts, "omitted": ["docreader temporary files", "Neo4j logs", "Ollama identity keys", "container images", "host tunnel credentials"], "application_restore_verified": False}


def output_directory(value):
    base = (ROOT / "temp/tencent-cloud/snapshots").resolve()
    path = Path(value).resolve()
    require(path.is_relative_to(base) and path != base and not path.exists(), "NEW_SNAPSHOT_DIRECTORY_REQUIRED")
    for parent in (path, *path.parents):
        if parent == ROOT:
            break
        require(not parent.is_symlink() and not (hasattr(parent, "is_junction") and parent.is_junction()), "SNAPSHOT_LINK_PATH_REJECTED")
    base.mkdir(parents=True, exist_ok=True)
    shared.protect_directory(base)
    require(path.parent == base, "FLAT_SNAPSHOT_DIRECTORY_REQUIRED")
    path.mkdir(mode=0o700)
    shared.protect_directory(path)
    return path


def stopped(containers, names):
    for name in names:
        current = shared.docker_json("inspect", containers[name]["Id"])[0]
        state = current["State"]
        require(current["Id"] == containers[name]["Id"] and not state.get("Running") and not state.get("Restarting") and not state.get("OOMKilled") and state.get("ExitCode") == 0, "SOURCE_NOT_CLEANLY_STOPPED_" + name)


def frozen_volumes(volumes):
    for logical in ("data-files", "docreader-tmp", "redis-data", "neo4j-data", "ollama-data"):
        consumers = shared.command(["docker", "ps", "-q", "--filter", "volume=" + volumes[logical]]).strip()
        require(not consumers, "VOLUME_STILL_HAS_RUNNING_CONSUMER_" + logical)


def phase(name):
    global PHASE
    require(re.fullmatch(r"[a-z0-9_.-]+", name), "SNAPSHOT_PHASE_INVALID")
    PHASE = name
    shared.emit("tencent_snapshot_phase", phase=name)


def failure_details(error):
    line = 0
    traceback = error.__traceback__
    while traceback:
        if traceback.tb_frame.f_code.co_filename == __file__:
            line = traceback.tb_lineno
        traceback = traceback.tb_next
    code = str(error) if isinstance(error, shared.BackupError) else "UNEXPECTED_FAILURE"
    if not re.fullmatch(r"[A-Z][A-Za-z0-9_]{0,200}", code):
        code = "SNAPSHOT_FAILED"
    details = {"code": code, "phase": getattr(error, "snapshot_phase", PHASE), "error_type": type(error).__name__, "line": line}
    if isinstance(getattr(error, "errno", None), int):
        details["errno"] = error.errno
    return details


def unhealthy_services(containers, names):
    unhealthy = []
    for name in names:
        try:
            current = shared.docker_json("inspect", containers[name]["Id"])[0]
            state = current["State"]
            healthy = current["Id"] == containers[name]["Id"] and state.get("Running") and not state.get("Restarting") and not state.get("OOMKilled") and state.get("Health", {}).get("Status") == "healthy"
        except Exception:
            healthy = False
        if not healthy:
            unhealthy.append(name)
    return unhealthy


def wait_source_service(containers, name, timeout=600):
    deadline = time.monotonic() + timeout
    while True:
        if not unhealthy_services(containers, [name]):
            return
        remaining = deadline - time.monotonic()
        require(remaining > 0, "SOURCE_RESUME_TIMEOUT_" + name)
        time.sleep(min(2, remaining))


@contextlib.contextmanager
def quiesced(containers):
    touched = []
    original = None
    try:
        for name in WRITERS:
            phase("stop." + name)
            touched.append(name)
            shared.command(["docker", "stop", "--time", "120", containers[name]["Id"]], timeout=180)
        phase("check-idle-after-stop")
        require_idle(idle(containers))
        for name in COLD:
            phase("stop." + name)
            touched.append(name)
            shared.command(["docker", "stop", "--time", "120", containers[name]["Id"]], timeout=180)
        stopped(containers, COLD)
        yield
    except BaseException as error:
        original = error
        error.snapshot_phase = PHASE
        shared.emit("tencent_snapshot_work_failed", **failure_details(error))
        raise
    finally:
        for name in ("redis", "neo4j", "docreader", "document-renderer", "ollama", "app", "trial-gateway"):
            if name not in touched:
                continue
            try:
                phase("resume." + name)
                shared.command(["docker", "start", containers[name]["Id"]])
                wait_source_service(containers, name)
            except Exception as error:
                shared.emit("tencent_snapshot_resume_warning", service=name, **failure_details(error))
        phase("resume-final-health")
        unhealthy = unhealthy_services(containers, SERVICES)
        shared.emit("tencent_snapshot_source_health", healthy=len(SERVICES) - len(unhealthy), total=len(SERVICES), unhealthy=unhealthy)
        if unhealthy:
            shared.emit("tencent_snapshot_resume_failed", services=unhealthy)
            if original is None:
                raise shared.BackupError("SOURCE_RESUME_FAILED_" + "_".join(unhealthy))


def save_command(path, argv, compressed=False):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with open(path, "xb") as output:
            os.chmod(path, 0o600)
            if compressed:
                with gzip.GzipFile(filename="", fileobj=output, mode="wb", compresslevel=1, mtime=0) as archive:
                    shutil.copyfileobj(process.stdout, archive, 1024 * 1024)
            else:
                shutil.copyfileobj(process.stdout, output, 1024 * 1024)
        require(process.wait() == 0, "SNAPSHOT_COMPONENT_COMMAND_FAILED")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    return shared.digest_file(path)


def export_volume(path, image, volume, member):
    argv = trial.tar_volume(image, volume, member)
    argv[3:3] = ["--pull", "never", "--security-opt", "no-new-privileges:true", "--memory", "256m", "--cpus", "1", "--pids-limit", "64"]
    result = save_command(path, argv, True)
    with gzip.open(path, "rb") as stream:
        inventory = recovery.object_inventory(stream)
    trial.write_json(path.with_name(path.name + ".inventory.json"), inventory)
    return {**result, "inventory": path.name + ".inventory.json", "inventory_sha256": trial.fingerprint(inventory), "members": len(inventory)}


def validate_database_dump(path, postgres):
    with open(path, "rb") as source_file:
        require(source_file.read(5) == b"PGDMP", "DATABASE_DUMP_HEADER_INVALID")
    with open(path, "rb") as source_file:
        result = subprocess.run(["docker", "exec", "-i", postgres["Id"], "pg_restore", "--list"], stdin=source_file, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
    require(result.returncode == 0, "DATABASE_DUMP_LIST_FAILED")


def backup(args):
    phase("preflight")
    require(args.quiesce, "QUIESCE_REQUIRED")
    containers, volumes = source()
    source_values = shared.parse_env((ROOT / ".env").read_bytes())
    environment = runtime_environment(containers, source_values, True)
    plan = plan_state(containers, volumes)
    require_idle(plan["idle"])
    directory = output_directory(args.output)
    trial.write_json(directory / "plan.json", plan)
    components = {}
    with quiesced(containers):
        phase("database-before")
        frozen_volumes(volumes)
        before = shared.database_scope(containers["postgres"])
        content_before = content_counts(containers["postgres"])
        other_connections = shared.pg_query(containers["postgres"], "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid();")
        require(other_connections == "0", "EXTERNAL_DATABASE_CONNECTION_PRESENT")
        phase("database-dump")
        components["database.dump"] = save_command(directory / "database.dump", shared.pg_command(containers["postgres"], "pg_dump", ["--format=custom", "--no-owner", "--no-acl"]))
        phase("database-dump-verify")
        validate_database_dump(directory / "database.dump", containers["postgres"])
        for filename, (logical, member) in ARCHIVES.items():
            phase("archive." + logical)
            shared.emit("tencent_snapshot_component", name=filename)
            components[filename] = export_volume(directory / filename, containers["postgres"]["Image"], volumes[logical], member)
        phase("consistency-after")
        after = shared.database_scope(containers["postgres"])
        require(before == after and content_before == content_counts(containers["postgres"]), "DATABASE_CHANGED_DURING_SNAPSHOT")
        stopped(containers, COLD)
        frozen_volumes(volumes)
        phase("runtime-config")
        trial.write_new(directory / "runtime.env", env_bytes(environment))
        components["runtime.env"] = shared.digest_file(directory / "runtime.env")
        trial.write_json(directory / "source-counts.json", {"database": before, "content": content_before})
        components["source-counts.json"] = shared.digest_file(directory / "source-counts.json")
        for filename, relative in (("config.yaml", "config/config.yaml"), ("builtin_models.local.yaml", "config/builtin_models.local.yaml")):
            trial.write_new(directory / filename, (ROOT / relative).read_bytes())
            components[filename] = shared.digest_file(directory / filename)
    phase("manifest")
    manifest = {**plan, "format": "boyankb-tencent-snapshot-v1", "database": before, "content": content_before, "database_fingerprint": trial.fingerprint(before), "components": components, "created_at": trial.now(), "consistency": "application-quiesced", "writers_resumed": True, "archive_encrypted": False, "snapshot_validated": True, "application_restore_verified": False, "full_system_backup": False}
    trial.write_json(directory / "manifest.json", manifest)
    shared.emit("tencent_snapshot_created", directory=str(directory), components=len(components), writers_resumed=True, application_restore_verified=False)


def interrupted(signum, frame):
    raise KeyboardInterrupt()


def main():
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("plan")
    create = modes.add_parser("backup")
    create.add_argument("--quiesce", action="store_true")
    create.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "backup":
            backup(args)
        else:
            containers, volumes = source()
            runtime_environment(containers, shared.parse_env((ROOT / ".env").read_bytes()))
            print(json.dumps(plan_state(containers, volumes), ensure_ascii=False, indent=2))
    except shared.BackupError as error:
        shared.emit("tencent_snapshot_failed", **failure_details(error))
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        shared.emit("tencent_snapshot_interrupted")
        raise SystemExit(130) from None
    except Exception as error:
        shared.emit("tencent_snapshot_failed", **failure_details(error))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
