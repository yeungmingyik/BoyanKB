import argparse
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

sys.dont_write_bytecode = True
import trial_backup as trial

shared = trial.shared
recovery = trial.recovery
require = shared.require
command = shared.command
PROJECT = "weknora-local"
FORMAT = "boyankb-partial-snapshot-v1"
PROFILES = {
    "postgres-config-only": {"database.age", "environment.age", "kb-config.age"},
    "neo4j-cold-only": {"neo4j-data.age"},
}
EXCLUSIONS = {
    "postgres-config-only": ["neo4j", "redis", "local-objects", "models", "runtime-images", "postgres-cluster-roles", "cross-store-consistency"],
    "neo4j-cold-only": ["postgres", "redis", "local-objects", "environment", "models", "runtime-images", "neo4j-logs", "neo4j-plugin-cache", "neo4j-runtime-config", "cross-store-consistency"],
}


def source_service(name, running):
    item = shared.service_container(PROJECT, name)
    labels = item.get("Config", {}).get("Labels") or {}
    require(labels.get("com.docker.compose.project") == PROJECT and labels.get("com.docker.compose.service") == name, "SOURCE_LABEL_MISMATCH")
    require(bool(item["State"].get("Running")) == running and not item["State"].get("Restarting"), "SOURCE_RUNNING_STATE_MISMATCH")
    require(re.fullmatch(r"[a-f0-9]{64}", item.get("Id", "")), "SOURCE_ID_INVALID")
    return item


def require_new_files(output, receipt):
    require(output != receipt and output.suffix == ".age", "OUTPUT_PATH_INVALID")
    require(not output.exists() and not receipt.exists(), "OUTPUT_ALREADY_EXISTS")
    require(output.parent.is_dir() and receipt.parent.is_dir(), "OUTPUT_DIRECTORY_REQUIRED")


def credentials(args):
    binary = shared.age_binary(args.age_binary)
    identity = pathlib.Path(args.identity).resolve()
    require(identity.is_file(), "AGE_IDENTITY_FILE_REQUIRED")
    keygen = pathlib.Path(binary).with_name("age-keygen.exe" if os.name == "nt" else "age-keygen")
    recipient = command([str(keygen), "-y", str(identity)]).decode().strip()
    require(re.fullmatch(r"age1[a-z0-9]{40,100}", recipient), "AGE_RECIPIENT_INVALID")
    return binary, identity, recipient


def manifest_for(profile, containers):
    return {"format": FORMAT, "profile": profile, "created_at": trial.now(), "source_project": PROJECT, "source_ids": {name: item["Id"] for name, item in containers.items()}, "full_system_backup": False, "cross_store_consistent": False, "excluded": EXCLUSIONS[profile], "components": []}


def validate_manifest(manifest):
    require(isinstance(manifest, dict) and manifest.get("format") == FORMAT, "PARTIAL_FORMAT_REQUIRED")
    profile = manifest.get("profile")
    require(profile in PROFILES and manifest.get("source_project") == PROJECT, "PARTIAL_PROFILE_INVALID")
    require(manifest.get("full_system_backup") is False and manifest.get("cross_store_consistent") is False and manifest.get("excluded") == EXCLUSIONS[profile], "PARTIAL_SCOPE_INVALID")
    ids = manifest.get("source_ids")
    expected = {"app", "postgres"} if profile == "postgres-config-only" else {"app", "neo4j"}
    require(isinstance(ids, dict) and set(ids) == expected and all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in ids.values()), "SOURCE_IDS_INVALID")
    components = manifest.get("components")
    require(isinstance(components, list) and len(components) == len(PROFILES[profile]) and {item.get("name") for item in components} == PROFILES[profile], "PARTIAL_COMPONENTS_INVALID")
    for entry in components:
        require(type(entry.get("plaintext_size")) is int and entry["plaintext_size"] > 0 and re.fullmatch(r"[a-f0-9]{64}", entry.get("plaintext_sha256", "")), "PLAINTEXT_METADATA_INVALID")
    if profile == "neo4j-cold-only":
        require(manifest.get("consistency") == "neo4j-clean-stop" and re.fullmatch(r"sha256:[a-f0-9]{64}", manifest.get("image_id", "")), "NEO4J_COLD_METADATA_INVALID")
    else:
        require(manifest.get("consistency") == "database-mvcc-config-not-atomic" and re.fullmatch(r"[a-f0-9-]{36}", manifest.get("knowledge_base_id", "")), "DATABASE_SNAPSHOT_METADATA_INVALID")


def unpack(binary, identity, archive_path, directory):
    entries = {}
    manifest = None
    total = 0
    with recovery.decrypt_stream(binary, identity, archive_path) as source:
        with tarfile.open(fileobj=source, mode="r|") as archive:
            for member in archive:
                require(member.isfile() and manifest is None and len(entries) < 4 and member.name not in entries, "ARCHIVE_MEMBER_REJECTED")
                total += member.size
                require(0 <= member.size <= archive_path.stat().st_size and total <= archive_path.stat().st_size, "ARCHIVE_SIZE_REJECTED")
                stream = archive.extractfile(member)
                if member.name == "manifest.json":
                    require(member.size <= 1024 * 1024, "MANIFEST_TOO_LARGE")
                    manifest = json.loads(stream.read())
                else:
                    require(member.name in set.union(*PROFILES.values()), "ARCHIVE_PATH_REJECTED")
                    path = directory / member.name
                    with path.open("xb") as destination:
                        shutil.copyfileobj(stream, destination, 1024 * 1024)
                    os.chmod(path, 0o600)
                    entries[member.name] = shared.digest_file(path)
    validate_manifest(manifest)
    require(set(entries) == PROFILES[manifest["profile"]], "ARCHIVE_COMPONENTS_MISMATCH")
    inventories = {}
    for entry in manifest["components"]:
        name = entry["name"]
        require(entries[name] == {"size": entry.get("size"), "sha256": entry.get("sha256")}, "CIPHERTEXT_CHECKSUM_MISMATCH")
        with recovery.decrypt_stream(binary, identity, directory / name) as source:
            reader = shared.DigestReader(source)
            if name == "neo4j-data.age":
                inventories[name] = recovery.object_inventory(reader)
            elif name == "database.age":
                require(reader.read(5) == b"PGDMP", "POSTGRES_DUMP_SIGNATURE_INVALID")
            elif name == "kb-config.age":
                require(entry["plaintext_size"] <= 4 * 1024 * 1024, "KB_CONFIG_TOO_LARGE")
                record = json.loads(reader.read(entry["plaintext_size"] + 1))
                require(record.get("knowledge_base", {}).get("id") == manifest["knowledge_base_id"], "KB_CONFIG_ID_MISMATCH")
            else:
                require(entry["plaintext_size"] <= 4 * 1024 * 1024, "ENVIRONMENT_TOO_LARGE")
            while reader.read(1024 * 1024):
                pass
            require(reader.size == entry["plaintext_size"] and reader.digest.hexdigest() == entry["plaintext_sha256"], "PLAINTEXT_CHECKSUM_MISMATCH")
    if manifest["profile"] == "neo4j-cold-only":
        require(trial.fingerprint(inventories["neo4j-data.age"]) == manifest.get("inventory_sha256"), "NEO4J_INVENTORY_MISMATCH")
        require({"databases/neo4j", "databases/system", "transactions/neo4j", "transactions/system"}.issubset(inventories["neo4j-data.age"]), "NEO4J_LAYOUT_UNSUPPORTED")
        databases = {path.split("/")[1] for path in inventories["neo4j-data.age"] if path.startswith("databases/") and "/" not in path[len("databases/"):] and inventories["neo4j-data.age"][path]["type"] == "directory"}
        require(databases == {"neo4j", "system"}, "NEO4J_DATABASE_SET_UNSUPPORTED")
    return manifest, inventories


def kb_snapshot(postgres, knowledge_base_id):
    require(str(uuid.UUID(knowledge_base_id)) == knowledge_base_id, "KNOWLEDGE_BASE_ID_INVALID")
    sql = "SELECT jsonb_build_object('knowledge_base',to_jsonb(k),'knowledge_count',(SELECT count(*) FROM knowledges WHERE knowledge_base_id=k.id),'chunk_count',(SELECT count(*) FROM chunks WHERE knowledge_base_id=k.id)) FROM knowledge_bases k WHERE k.id='" + knowledge_base_id + "';"
    value = json.loads(shared.pg_query(postgres, sql))
    require(value.get("knowledge_base", {}).get("id") == knowledge_base_id, "KNOWLEDGE_BASE_NOT_FOUND")
    return trial.canonical(value)


def add_component(manifest, binary, recipient, directory, name, data=None, argv=None):
    metadata = shared.encrypt_command(binary, recipient, directory / name, argv) if argv else shared.encrypt_stream(binary, recipient, directory / name, io.BytesIO(data))
    manifest["components"].append({"name": name, **metadata})


def pg_restore_list_command(postgres):
    require(re.fullmatch(r"[a-f0-9]{64}", postgres.get("Id", "")), "POSTGRES_ID_INVALID")
    return ["docker", "exec", "-i", postgres["Id"], "sh", "-ec", "pg_restore --list >/dev/null; cat >/dev/null"]


def snapshot(args):
    require(args.project == PROJECT, "LOCAL_PROJECT_REQUIRED")
    trial.local_context(args.context)
    output, receipt = pathlib.Path(args.output).resolve(), pathlib.Path(args.receipt).resolve()
    require_new_files(output, receipt)
    binary, identity, recipient = credentials(args)
    app, postgres = source_service("app", True), source_service("postgres", True)
    env_path = pathlib.Path(args.env_file).resolve(strict=True)
    env_raw = env_path.read_bytes()
    values = shared.parse_env(env_raw)
    running_env = shared.container_environment(app)
    database_env = shared.container_environment(postgres)
    require(all(values.get(key) == running_env.get(key) for key in ("SYSTEM_AES_KEY", "DB_USER", "DB_PASSWORD", "DB_NAME")), "ENVIRONMENT_RUNTIME_MISMATCH")
    require(all(database_env.get(pg) == values.get(key) for pg, key in (("POSTGRES_USER", "DB_USER"), ("POSTGRES_PASSWORD", "DB_PASSWORD"), ("POSTGRES_DB", "DB_NAME"))), "DATABASE_ENVIRONMENT_MISMATCH")
    require(running_env.get("DB_HOST") == "postgres" and running_env.get("DB_DRIVER", "postgres") == "postgres", "LOCAL_POSTGRES_REQUIRED")
    config = kb_snapshot(postgres, args.knowledge_base_id)
    manifest = manifest_for("postgres-config-only", {"app": app, "postgres": postgres})
    manifest.update(consistency="database-mvcc-config-not-atomic", knowledge_base_id=args.knowledge_base_id)
    with tempfile.TemporaryDirectory(prefix=".graph-snapshot-", dir=output.parent) as temporary:
        directory = pathlib.Path(temporary)
        shared.protect_directory(directory)
        add_component(manifest, binary, recipient, directory, "database.age", argv=shared.pg_command(postgres, "pg_dump", ["--format=custom", "--no-owner", "--no-acl"]))
        require(config == kb_snapshot(postgres, args.knowledge_base_id) and env_raw == env_path.read_bytes(), "CONFIGURATION_CHANGED_DURING_SNAPSHOT")
        add_component(manifest, binary, recipient, directory, "environment.age", data=env_raw)
        add_component(manifest, binary, recipient, directory, "kb-config.age", data=config)
        shared.pack_archive(binary, recipient, output, directory, manifest)
        verification = directory / "verified"
        verification.mkdir()
        actual, _ = unpack(binary, identity, output, verification)
        require(actual == manifest, "MANIFEST_VERIFICATION_MISMATCH")
        recovery.pipe_restore(binary, identity, verification / "database.age", pg_restore_list_command(postgres))
    record = {"format": "boyankb-partial-snapshot-receipt-v1", "created_at": trial.now(), "profile": manifest["profile"], "consistency": manifest["consistency"], "source_project": PROJECT, "source_ids": manifest["source_ids"], "full_system_backup": False, "cross_store_consistent": False, "archive_sha256": shared.digest_file(output)["sha256"], "archive_size": output.stat().st_size, "archive_verified": True, "pg_restore_list_verified": True, "database_restore_verified": False, "knowledge_base_id": args.knowledge_base_id, "excluded": manifest["excluded"]}
    trial.write_json(receipt, record)
    shared.emit("partial_database_snapshot_verified", archive=str(output), receipt=str(receipt), full_system_backup=False, pg_restore_list_verified=True)


def cold_source():
    app, neo4j = source_service("app", False), source_service("neo4j", False)
    require(neo4j["State"].get("ExitCode") == 0 and not neo4j["State"].get("OOMKilled") and not neo4j["State"].get("Error"), "NEO4J_CLEAN_STOP_REQUIRED")
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", neo4j.get("Image", "")), "NEO4J_IMAGE_ID_INVALID")
    volume = shared.named_mount(neo4j, "/data")
    labels = shared.docker_json("volume", "inspect", volume)[0].get("Labels") or {}
    require(labels.get("com.docker.compose.project") == PROJECT, "VOLUME_PROJECT_MISMATCH")
    require(not command(["docker", "ps", "-q", "--filter", "volume=" + volume]).strip(), "NEO4J_VOLUME_IN_USE")
    return {"app": app, "neo4j": neo4j}, volume


def cold_backup(args):
    require(args.project == PROJECT and args.maintenance_stopped, "EXTERNAL_MAINTENANCE_REQUIRED")
    trial.local_context(args.context)
    output, receipt = pathlib.Path(args.output).resolve(), pathlib.Path(args.receipt).resolve()
    require_new_files(output, receipt)
    binary, identity, recipient = credentials(args)
    containers, volume = cold_source()
    manifest = manifest_for("neo4j-cold-only", containers)
    manifest.update(consistency="neo4j-clean-stop", image_id=containers["neo4j"]["Image"])
    with tempfile.TemporaryDirectory(prefix=".graph-cold-", dir=output.parent) as temporary:
        directory = pathlib.Path(temporary)
        shared.protect_directory(directory)
        add_component(manifest, binary, recipient, directory, "neo4j-data.age", argv=trial.tar_volume(manifest["image_id"], volume))
        again, current_volume = cold_source()
        require(current_volume == volume and all(again[name]["Id"] == containers[name]["Id"] and again[name]["State"].get("FinishedAt") == containers[name]["State"].get("FinishedAt") for name in containers), "MAINTENANCE_STATE_CHANGED")
        with recovery.decrypt_stream(binary, identity, directory / "neo4j-data.age") as source:
            inventory = recovery.object_inventory(source)
        manifest["inventory_sha256"] = trial.fingerprint(inventory)
        shared.pack_archive(binary, recipient, output, directory, manifest)
        verification = directory / "verified"
        verification.mkdir()
        unpack(binary, identity, output, verification)
    record = {"format": "boyankb-graph-cold-receipt-v1", "created_at": trial.now(), "source_project": PROJECT, "profile": manifest["profile"], "full_system_backup": False, "source_ids": manifest["source_ids"], "archive_sha256": shared.digest_file(output)["sha256"], "archive_size": output.stat().st_size, "archive_verified": True, "restore_verified": False, "inventory_sha256": manifest["inventory_sha256"], "writers_resumed": False}
    trial.write_json(receipt, record)
    shared.emit("graph_cold_archive_verified", archive=str(output), receipt=str(receipt), full_system_backup=False, writers_resumed=False)


def validate_target(project):
    require(re.fullmatch(r"boyankb-restore-graph-[a-z0-9][a-z0-9-]{2,23}", project), "GRAPH_RESTORE_PROJECT_REQUIRED")
    require(not any(part in project.split("-") for part in ("prod", "production", "local", "staging")), "RESTORE_TARGET_REJECTED")


def isolated_command(project, image, volume, purpose, entrypoint, arguments, read_only=False):
    validate_target(project)
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", image), "RESTORE_IMAGE_INVALID")
    require(volume == project + "_neo4j-data", "RESTORE_VOLUME_INVALID")
    require(purpose in ("extract", "inventory", "neo4j", "system"), "RESTORE_PURPOSE_INVALID")
    temporary_mode = "exec" if purpose in ("neo4j", "system") else "noexec"
    return ["docker", "run", "--rm", "-i", "--name", project + "-" + purpose, "--label", "com.docker.compose.project=" + project, "--hostname", "localhost", "--network", "none", "--read-only", "--memory", "2g", "--cpus", "1", "--pids-limit", "128", "--security-opt", "no-new-privileges:true", "--env", "HEAP_SIZE=512m", "--tmpfs", "/tmp:rw," + temporary_mode + ",nosuid,size=512m", "--tmpfs", "/logs:rw,noexec,nosuid,size=64m", "--user", "0:0", "--mount", "type=volume,src=" + volume + ",dst=/data,volume-nocopy" + (",readonly" if read_only else ""), "--entrypoint", entrypoint, image, *arguments]


def restored_inventory(argv):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        inventory = recovery.object_inventory(process.stdout)
        while process.stdout.read(1024 * 1024):
            pass
        require(process.wait() == 0, "RESTORED_INVENTORY_COMMAND_FAILED")
        return inventory
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def restore(args):
    validate_target(args.target_project)
    trial.local_context(args.context)
    archive, report = pathlib.Path(args.archive).resolve(strict=True), pathlib.Path(args.report).resolve()
    require(not report.exists() and report.parent.is_dir(), "REPORT_PATH_INVALID")
    binary, identity, _ = credentials(args)
    with tempfile.TemporaryDirectory(prefix=".graph-restore-", dir=report.parent) as temporary:
        directory = pathlib.Path(temporary)
        shared.protect_directory(directory)
        shared.emit("graph_restore_stage", stage="archive_verification")
        manifest, inventories = unpack(binary, identity, archive, directory)
        require(manifest["profile"] == "neo4j-cold-only", "GRAPH_COLD_ARCHIVE_REQUIRED")
        recovery.fresh_project(args.target_project, PROJECT)
        image = manifest["image_id"]
        require(shared.docker_json("image", "inspect", image)[0]["Id"] == image, "EXACT_LOCAL_IMAGE_REQUIRED")
        volume = args.target_project + "_neo4j-data"
        command(["docker", "volume", "create", "--label", "com.docker.compose.project=" + args.target_project, volume])
        require(not command(["docker", "ps", "-aq", "--filter", "volume=" + volume]).strip(), "NEW_VOLUME_CONSUMER_PRESENT")
        shared.emit("graph_restore_stage", stage="extract_into_new_volume")
        extraction = isolated_command(args.target_project, image, volume, "extract", "sh", ["-ec", "test -z \"$(ls -A /data)\"; exec tar --numeric-owner -xpf - -C /data"])
        try:
            recovery.pipe_restore(binary, identity, directory / "neo4j-data.age", extraction)
        except BrokenPipeError:
            raise shared.BackupError("GRAPH_EXTRACTION_PIPE_CLOSED") from None
        shared.emit("graph_restore_stage", stage="file_inventory")
        actual_inventory = restored_inventory(isolated_command(args.target_project, image, volume, "inventory", "tar", ["--numeric-owner", "-cf", "-", "-C", "/data", "."], True))
        require(actual_inventory == inventories["neo4j-data.age"], "RESTORED_FILE_INVENTORY_MISMATCH")
        for database in ("neo4j", "system"):
            shared.emit("graph_restore_stage", stage="database_consistency_" + database)
            command(isolated_command(args.target_project, image, volume, database, "neo4j-admin", ["database", "check", "--from-path-data=/data/databases", "--from-path-txn=/data/transactions", "--report-path=/tmp", "--max-off-heap-memory=512m", "--threads=1", database]), timeout=900)
    result = {"format": "boyankb-graph-offline-restore-report-v1", "created_at": trial.now(), "source_project": PROJECT, "source_ids": manifest["source_ids"], "profile": manifest["profile"], "target_project": args.target_project, "archive_sha256": shared.digest_file(archive)["sha256"], "archive_size": archive.stat().st_size, "inventory_sha256": manifest["inventory_sha256"], "files_verified": True, "database_consistency_verified": ["neo4j", "system"], "dbms_started": False, "network_mode": "none", "published_ports": [], "application_restore_verified": False, "full_system_backup": False, "restored_volume": volume}
    trial.write_json(report, result)
    shared.emit("graph_offline_restore_verified", report=str(report), full_system_backup=False, dbms_started=False)


def parser():
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="mode", required=True)
    for name in ("snapshot", "cold-backup", "restore"):
        item = commands.add_parser(name)
        item.add_argument("--age-binary")
        item.add_argument("--context")
        item.add_argument("--identity", required=True)
        if name == "restore":
            item.add_argument("--archive", required=True)
            item.add_argument("--target-project", required=True)
            item.add_argument("--report", required=True)
        else:
            item.add_argument("--project", default=PROJECT)
            item.add_argument("--output", required=True)
            item.add_argument("--receipt", required=True)
            if name == "snapshot":
                item.add_argument("--env-file", required=True)
                item.add_argument("--knowledge-base-id", required=True)
            else:
                item.add_argument("--maintenance-stopped", action="store_true", required=True)
    return result


def main():
    try:
        args = parser().parse_args()
        {"snapshot": snapshot, "cold-backup": cold_backup, "restore": restore}[args.mode](args)
        return 0
    except KeyboardInterrupt:
        shared.emit("partial_snapshot_failed", code="INTERRUPTED")
        return 130
    except shared.BackupError as error:
        shared.emit("partial_snapshot_failed", code=str(error))
        return 1
    except Exception:
        shared.emit("partial_snapshot_failed", code="UNEXPECTED_FAILURE")
        return 1


if __name__ == "__main__":
    sys.exit(main())
