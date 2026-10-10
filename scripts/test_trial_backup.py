import argparse
import copy
import io
import json
import os
import pathlib
import shutil
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
import trial_backup as trial

arguments = argparse.ArgumentParser(add_help=False)
arguments.add_argument("--age-binary")
options, remaining = arguments.parse_known_args()
age = options.age_binary or shutil.which("age")


def tar_bytes(path, content=b"fixture"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(path)
        member.size = len(content)
        member.mode = 0o640
        archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def runtime_fixture():
    result = {}
    for index, name in enumerate(trial.SERVICES):
        result[name] = {"image": "sha256:" + str(index) * 64, "config": {"Env": ["FIXTURE=value", "REDIS_PASSWORD=fixture-$-password"], "Cmd": ["fixture"], "User": "", "WorkingDir": "/app"}, "read_only": False, "mounts": []}
        for target, logical in trial.VOLUME_TARGETS.get(name, {}).items():
            result[name]["mounts"].append({"Type": "volume", "Name": "source_" + logical, "Source": "untrusted", "Destination": target, "RW": True})
    return result


def manifest_fixture():
    runtime = runtime_fixture()
    database = {"migration": {"version": 112, "dirty": False}, "row_counts": {"fixture": 2}, "postgres_version": "17.6"}
    return {"format": "boyankb-encrypted-backup-v1", "profile": trial.PROFILE, "created_at": trial.now(), "source_project": trial.PROJECT, "service_names": list(trial.SERVICES), "source_ids": {name: str(index) * 64 for index, name in enumerate(trial.SERVICES)}, "consistency": "application-quiesced", "storage": "local", "local_base_dir": "/data/files", "database": database, "database_fingerprint": trial.fingerprint(database), "images": {name: {"id": item["image"], "reference": "fixture:" + name} for name, item in runtime.items()}, "components": []}


class IsolationTests(unittest.TestCase):
    def test_target_rejects_existing_local_production_and_injected_names(self):
        for project in ("weknora-local", "boyankb-restore-prod", "boyankb-restore-trial-local", "boyankb-restore-trial-a;id", "boyankb-restore-trial-../../x"):
            with self.subTest(project=project), self.assertRaises(trial.BackupError):
                trial.validate_target(project)
        trial.validate_target("boyankb-restore-trial-fixture01")

    def test_backup_refuses_other_projects_before_docker(self):
        with patch.object(trial, "local_context") as context, self.assertRaises(trial.BackupError):
            trial.backup(SimpleNamespace(project="production", quiesce=True))
        context.assert_not_called()

    def test_remote_context_and_docker_host_rejected(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://example.invalid:2375"}), self.assertRaisesRegex(trial.BackupError, "DOCKER_HOST_OVERRIDE_REJECTED"):
            trial.local_context("fixture")
        with patch.dict(os.environ, {}, clear=True), patch.object(trial.shared, "docker_json", return_value=[{"Endpoints": {"docker": {"Host": "ssh://host"}}}]), self.assertRaisesRegex(trial.BackupError, "LOCAL_DOCKER_ENDPOINT_REQUIRED"):
            trial.local_context("fixture")

    def test_local_context_is_pinned_for_all_subprocesses(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(trial.shared, "docker_json", return_value=[{"Endpoints": {"docker": {"Host": "npipe:////./pipe/dockerDesktopLinuxEngine"}}}]):
            self.assertEqual(trial.local_context("fixture"), "fixture")
            self.assertEqual(os.environ["DOCKER_CONTEXT"], "fixture")

    def test_restore_config_seven_services_no_ports_or_source_volumes(self):
        config = trial.build_restore_config("boyankb-restore-trial-fixture", runtime_fixture(), manifest_fixture(), {})
        self.assertEqual(set(config["services"]), set(trial.SERVICES))
        self.assertTrue(config["networks"]["isolated"]["internal"])
        for name, service in config["services"].items():
            self.assertNotIn("ports", service)
            self.assertNotIn("container_name", service)
            self.assertNotIn("network_mode", service)
            self.assertNotIn("build", service)
            self.assertEqual(service["pull_policy"], "never")
            self.assertEqual(service["networks"], ["isolated"])
            if name not in ("postgres", "redis"):
                self.assertEqual(service["profiles"], ["application-not-started"])
        self.assertEqual(config["services"]["app"]["environment"]["AUTO_MIGRATE"], "false")
        self.assertTrue(config["services"]["document-renderer"]["read_only"])
        self.assertTrue(all(item["name"].startswith("boyankb-restore-trial-fixture_") for item in config["volumes"].values()))
        self.assertEqual(config["services"]["redis"]["command"], ['exec redis-server --dir /data --dbfilename dump.rdb --appendonly no --requirepass "$REDIS_PASSWORD"'])

    def test_unarchived_bind_and_docker_socket_refused(self):
        runtime = runtime_fixture()
        runtime["app"]["mounts"].append({"Type": "bind", "Destination": "/var/run/docker.sock", "Source": "/var/run/docker.sock", "RW": True})
        with self.assertRaisesRegex(trial.BackupError, "RESTORE_BIND_NOT_ARCHIVED"):
            trial.build_restore_config("boyankb-restore-trial-fixture", runtime, manifest_fixture(), {})

    def test_dollar_literals_do_not_become_compose_substitutions(self):
        value = {"environment": {"FIXTURE": "a$VALUE${SECRET}$$tail"}, "command": ['echo "$REDIS_PASSWORD"']}
        escaped = trial.compose_literals(value)
        self.assertEqual(escaped["environment"]["FIXTURE"], "a$$VALUE$${SECRET}$$$$tail")
        self.assertEqual(escaped["command"], ['echo "$$REDIS_PASSWORD"'])
        self.assertEqual(value["environment"]["FIXTURE"], "a$VALUE${SECRET}$$tail")

    def test_write_new_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "fixture"
            trial.write_new(path, b"original")
            with self.assertRaises(FileExistsError):
                trial.write_new(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"original")

    def test_manifest_rejects_missing_renderer_and_changed_database(self):
        manifest = manifest_fixture()
        manifest["components"] = [{"role": "configuration", "origin": {"kind": kind, **({"service": service} if service else {})}} for kind, service in (("runtime-config", None), ("model-cache", None), ("runtime-assets", "app"), ("runtime-assets", "document-renderer"), ("compose", None))]
        trial.validate_manifest(manifest)
        changed = copy.deepcopy(manifest)
        changed["database"]["row_counts"]["fixture"] = 3
        with self.assertRaisesRegex(trial.BackupError, "DATABASE_FINGERPRINT_MISMATCH"):
            trial.validate_manifest(changed)
        manifest["service_names"].remove("document-renderer")
        with self.assertRaisesRegex(trial.BackupError, "TRIAL_SERVICES_MISMATCH"):
            trial.validate_manifest(manifest)


@unittest.skipUnless(age, "age executable required")
class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temporary.name)
        self.identity = self.directory / "identity.txt"
        keygen = pathlib.Path(age).with_name("age-keygen.exe" if os.name == "nt" else "age-keygen")
        trial.command([str(keygen), "-o", str(self.identity)])
        self.recipient = trial.command([str(keygen), "-y", str(self.identity)]).decode().strip()

    def tearDown(self):
        self.temporary.cleanup()

    def create_archive(self, bad_asset=False):
        manifest = manifest_fixture()
        runtime = runtime_fixture()
        raw_env = b"SYSTEM_AES_KEY=" + b"x" * 32 + b"\nDB_USER=fixture\nDB_PASSWORD=fixture\nDB_NAME=fixture\nREDIS_PASSWORD=fixture\n"
        components = [("database.age", "database", b"PGDMPsynthetic", None), ("objects.age", "objects", tar_bytes("tenant/fixture.md"), None), ("redis.age", "redis", b"REDISsynthetic", None), ("environment.age", "environment", raw_env, None), ("runtime.age", "configuration", trial.canonical(runtime), {"kind": "runtime-config"}), ("models.age", "configuration", tar_bytes("models/blobs/sha256-fixture"), {"kind": "model-cache"}), ("app-assets.age", "configuration", tar_bytes("../secret" if bad_asset else "skills/boyan/courseware/SKILL.md"), {"kind": "runtime-assets", "service": "app"}), ("renderer-assets.age", "configuration", tar_bytes("branding/templates/ppt-v2/manifest.json"), {"kind": "runtime-assets", "service": "document-renderer"}), ("compose.age", "configuration", b"services: {}", {"kind": "compose", "order": 0})]
        component_dir = self.directory / "components"
        component_dir.mkdir()
        for name, role, data, origin in components:
            entry = {"name": name, "role": role, **trial.shared.encrypt_stream(age, self.recipient, component_dir / name, io.BytesIO(data))}
            if origin:
                entry["origin"] = origin
            manifest["components"].append(entry)
        output = self.directory / "backup.age"
        trial.shared.pack_archive(age, self.recipient, output, component_dir, manifest)
        return output, manifest

    def proof_files(self, archive, manifest):
        digest = trial.shared.digest_file(archive)
        common = {"status": "verified", "created_at": manifest["created_at"], "archive_sha256": digest["sha256"], "archive_size": digest["size"], "source_project": trial.PROJECT, "source_ids": manifest["source_ids"], "database_fingerprint": manifest["database_fingerprint"], "service_names": list(trial.SERVICES)}
        receipt = {**common, "format": "boyankb-trial-backup-receipt-v1", "profile": trial.PROFILE, "consistency": "application-quiesced", "writers_resumed": True}
        report = {**common, "format": "boyankb-trial-restore-report-v1", "target_project": "boyankb-restore-trial-fixture", "database_verified": True, "files_verified": True, "redis_verified": True, "environment_verified": True, "runtime_assets_verified": True, "model_cache_verified": True, "application_started": False, "network_internal": True}
        first, second = self.directory / "receipt.json", self.directory / "report.json"
        trial.write_json(first, receipt)
        trial.write_json(second, report)
        return first, second

    def test_real_age_roundtrip_all_components_and_renderer(self):
        archive, expected = self.create_archive()
        unpacked = self.directory / "unpacked"
        unpacked.mkdir()
        actual, roles, env, inventories, runtime = trial.verify_archive(age, self.identity, archive, unpacked)
        self.assertEqual(actual, expected)
        self.assertEqual(set(roles), {"database", "objects", "redis", "environment"})
        self.assertEqual(set(runtime), set(trial.SERVICES))
        self.assertIn("document-renderer", inventories)
        self.assertIn("ollama-data", inventories)
        self.assertTrue(env.startswith(b"SYSTEM_AES_KEY="))
        receipt, report = self.proof_files(archive, expected)
        args = SimpleNamespace(archive=archive, identity=self.identity, age_binary=age, receipt=receipt, restore_report=report)
        with patch.object(trial.shared, "emit") as emitted:
            proof = trial.verify_evidence(args)
        self.assertEqual(proof["source_ids"], expected["source_ids"])
        self.assertEqual(emitted.call_args.args[0], "trial_evidence_verified")

    def test_archive_tamper_rejected(self):
        archive, _ = self.create_archive()
        data = bytearray(archive.read_bytes())
        data[-20] ^= 1
        archive.write_bytes(data)
        unpacked = self.directory / "unpacked"
        unpacked.mkdir()
        with self.assertRaises((trial.BackupError, tarfile.ReadError)):
            trial.verify_archive(age, self.identity, archive, unpacked)

    def test_unsafe_runtime_asset_path_rejected(self):
        archive, _ = self.create_archive(bad_asset=True)
        unpacked = self.directory / "unpacked"
        unpacked.mkdir()
        with self.assertRaisesRegex(trial.BackupError, "OBJECT_PATH_REJECTED"):
            trial.verify_archive(age, self.identity, archive, unpacked)

    def test_proof_is_bound_to_archive_source_and_age(self):
        archive, manifest = self.create_archive()
        receipt, report = self.proof_files(archive, manifest)
        proof = trial.verify_receipts(archive, receipt, report, manifest["source_ids"])
        self.assertEqual(proof["archive_sha256"], trial.shared.digest_file(archive)["sha256"])
        changed = dict(manifest["source_ids"])
        changed["app"] = "f" * 64
        with self.assertRaisesRegex(trial.BackupError, "SOURCE_CONTAINERS_CHANGED"):
            trial.verify_receipts(archive, receipt, report, changed)
        data = json.loads(report.read_bytes())
        data["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        report.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(trial.BackupError, "BACKUP_EVIDENCE_EXPIRED"):
            trial.verify_receipts(archive, receipt, report)

    def test_verify_evidence_decrypts_instead_of_accepting_manual_fingerprint(self):
        archive, manifest = self.create_archive()
        receipt, report = self.proof_files(archive, manifest)
        for filename in (receipt, report):
            data = json.loads(filename.read_bytes())
            data["database_fingerprint"] = "f" * 64
            filename.write_text(json.dumps(data), encoding="utf-8")
        args = SimpleNamespace(archive=archive, identity=self.identity, age_binary=age, receipt=receipt, restore_report=report)
        with self.assertRaisesRegex(trial.BackupError, "ENCRYPTED_BACKUP_PROOF_MISMATCH"):
            trial.verify_evidence(args)

    def test_quiesced_writers_resume_when_dump_fails(self):
        env = self.directory / "fixture.env"
        env.write_bytes(b"SYSTEM_AES_KEY=" + b"x" * 32 + b"\nDB_USER=fixture\nDB_PASSWORD=fixture\nDB_NAME=fixture\nREDIS_PASSWORD=fixture\n")
        compose = self.directory / "compose.yaml"
        compose.write_text("services: {}", encoding="utf-8")
        args = SimpleNamespace(project=trial.PROJECT, quiesce=True, output=self.directory / "failed.age", receipt=self.directory / "receipt.json", context="fixture", age_binary=age, recipient=self.recipient, identity=self.identity, env_file=env, compose=[compose])
        containers = {name: {"Id": str(index) * 64, "Image": "sha256:" + str(index) * 64, "Config": {"Image": "fixture", "Env": []}, "Mounts": []} for index, name in enumerate(trial.SERVICES)}
        operations = []

        def fake_command(argv, **kwargs):
            operations.append(argv)
            if argv[1] == "compose":
                return "\n".join(item["Id"] for item in containers.values()).encode()
            return b""

        with patch.object(trial, "local_context"), patch.object(trial, "source_contract", return_value=(containers, {})), patch.object(trial.shared, "config_files", return_value=[]), patch.object(trial.shared, "model_inventory", return_value=[]), patch.object(trial, "command", side_effect=fake_command), patch.object(trial.shared, "docker_json", return_value=[{"State": {"Running": False}}]), patch.object(trial.shared, "database_scope", return_value=manifest_fixture()["database"]), patch.object(trial.shared, "encrypt_command", side_effect=trial.BackupError("SYNTHETIC_DUMP_FAILURE")), patch.object(trial.shared, "wait_source_services") as resumed, patch.object(trial.shared, "emit"), self.assertRaisesRegex(trial.BackupError, "SYNTHETIC_DUMP_FAILURE"):
            trial.backup(args)
        self.assertTrue(any(argv[1] == "stop" for argv in operations))
        self.assertTrue(any(argv[1] == "start" for argv in operations))
        resumed.assert_called_once()
        self.assertFalse(pathlib.Path(args.receipt).exists())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *remaining])
