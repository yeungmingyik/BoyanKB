import argparse
import copy
import importlib.util
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("graph_trial_backup", pathlib.Path(__file__).with_name("graph-trial-backup.py"))
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)

options = argparse.ArgumentParser(add_help=False)
options.add_argument("--age-binary")
arguments, remaining = options.parse_known_args()
age = arguments.age_binary
KB = "d9285ed1-0760-474b-ba82-a13347598176"


def fixture_manifest(profile="postgres-config-only"):
    names = ("app", "postgres") if profile == "postgres-config-only" else ("app", "neo4j")
    value = graph.manifest_for(profile, {name: {"Id": str(index + 1) * 64} for index, name in enumerate(names)})
    if profile == "postgres-config-only":
        value.update(consistency="database-mvcc-config-not-atomic", knowledge_base_id=KB)
    else:
        value.update(consistency="neo4j-clean-stop", image_id="sha256:" + "3" * 64)
    return value


def neo4j_tar(unsafe=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name in ("databases/neo4j", "databases/system", "transactions/neo4j", "transactions/system"):
            item = tarfile.TarInfo(name)
            item.type = tarfile.DIRTYPE
            item.mode = 0o755
            archive.addfile(item)
        item = tarfile.TarInfo("../escape" if unsafe else "databases/neo4j/store")
        item.size = 7
        item.mode = 0o600
        archive.addfile(item, io.BytesIO(b"fixture"))
    return output.getvalue()


class GuardTests(unittest.TestCase):
    def test_source_refuses_wrong_project_and_no_maintenance_before_docker(self):
        with patch.object(graph.trial, "local_context") as local:
            with self.assertRaisesRegex(graph.shared.BackupError, "LOCAL_PROJECT_REQUIRED"):
                graph.snapshot(SimpleNamespace(project="production"))
            with self.assertRaisesRegex(graph.shared.BackupError, "EXTERNAL_MAINTENANCE_REQUIRED"):
                graph.cold_backup(SimpleNamespace(project=graph.PROJECT, maintenance_stopped=False))
            local.assert_not_called()

    def test_running_graph_is_not_a_cold_backup(self):
        def container(project, name):
            return {"Id": "1" * 64, "Config": {"Labels": {"com.docker.compose.project": project, "com.docker.compose.service": name}}, "State": {"Running": name == "neo4j", "Restarting": False}}
        with patch.object(graph.shared, "service_container", side_effect=container), self.assertRaisesRegex(graph.shared.BackupError, "SOURCE_RUNNING_STATE_MISMATCH"):
            graph.cold_source()

    def test_graph_requires_clean_shutdown(self):
        def container(name, running):
            return {"Id": "1" * 64, "State": {"ExitCode": 137, "OOMKilled": True}}
        with patch.object(graph, "source_service", side_effect=container), self.assertRaisesRegex(graph.shared.BackupError, "NEO4J_CLEAN_STOP_REQUIRED"):
            graph.cold_source()

    def test_source_labels_are_checked(self):
        value = {"Config": {"Labels": {"com.docker.compose.project": "production", "com.docker.compose.service": "app"}}}
        with patch.object(graph.shared, "service_container", return_value=value), self.assertRaisesRegex(graph.shared.BackupError, "SOURCE_LABEL_MISMATCH"):
            graph.source_service("app", True)

    def test_targets_and_volume_mounts_are_confined(self):
        for value in ("weknora-local", "boyankb-restore-graph-prod", "boyankb-restore-graph-a;id", "boyankb-restore-graph-local"):
            with self.subTest(value=value), self.assertRaises(graph.shared.BackupError):
                graph.validate_target(value)
        project = "boyankb-restore-graph-fixture"
        argv = graph.isolated_command(project, "sha256:" + "3" * 64, project + "_neo4j-data", "neo4j", "neo4j-admin", ["database", "check", "neo4j"])
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--hostname") + 1], "localhost")
        self.assertIn("/tmp:rw,exec,nosuid,size=512m", argv)
        self.assertIn("--read-only", argv)
        self.assertEqual(argv[argv.index("--memory") + 1], "2g")
        self.assertEqual(argv[argv.index("--cpus") + 1], "1")
        self.assertNotIn("--publish", argv)
        self.assertNotIn("-p", argv)
        self.assertNotIn("--privileged", argv)
        self.assertEqual(argv[argv.index("--mount") + 1], "type=volume,src=" + project + "_neo4j-data,dst=/data,volume-nocopy")
        readonly = graph.isolated_command(project, "sha256:" + "3" * 64, project + "_neo4j-data", "inventory", "tar", [], True)
        self.assertTrue(readonly[readonly.index("--mount") + 1].endswith(",volume-nocopy,readonly"))
        self.assertIn("/tmp:rw,noexec,nosuid,size=512m", readonly)
        with self.assertRaisesRegex(graph.shared.BackupError, "RESTORE_VOLUME_INVALID"):
            graph.isolated_command(project, "sha256:" + "3" * 64, "weknora-local_neo4j-data-local", "extract", "tar", [])

    def test_native_execution_is_limited_to_offline_database_checks(self):
        project = "boyankb-restore-graph-fixture"
        for purpose in ("extract", "inventory", "neo4j", "system"):
            with self.subTest(purpose=purpose):
                argv = graph.isolated_command(project, "sha256:" + "3" * 64, project + "_neo4j-data", purpose, "neo4j-admin", [])
                expected = "exec" if purpose in ("neo4j", "system") else "noexec"
                self.assertIn("/tmp:rw," + expected + ",nosuid,size=512m", argv)
                self.assertIn("/logs:rw,noexec,nosuid,size=64m", argv)
                self.assertIn("no-new-privileges:true", argv)

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "snapshot.age"
            output.write_bytes(b"original")
            with self.assertRaisesRegex(graph.shared.BackupError, "OUTPUT_ALREADY_EXISTS"):
                graph.require_new_files(output, pathlib.Path(directory) / "receipt.json")
            self.assertEqual(output.read_bytes(), b"original")

    def test_knowledge_id_cannot_inject_sql(self):
        with patch.object(graph.shared, "pg_query") as query, self.assertRaises(ValueError):
            graph.kb_snapshot({}, "' OR TRUE --")
        query.assert_not_called()

    def test_toc_verification_drains_archive_without_database_connection(self):
        argv = graph.pg_restore_list_command({"Id": "1" * 64})
        self.assertEqual(argv[-2], "-ec")
        self.assertEqual(argv[-1], "pg_restore --list >/dev/null; cat >/dev/null")
        self.assertNotIn("--dbname", argv)
        self.assertNotIn("--clean", argv)

    def test_scope_claims_cannot_be_upgraded(self):
        value = fixture_manifest()
        value["components"] = [{"name": name, "plaintext_size": 1, "plaintext_sha256": "a" * 64} for name in graph.PROFILES[value["profile"]]]
        graph.validate_manifest(value)
        for key in ("full_system_backup", "cross_store_consistent"):
            changed = copy.deepcopy(value)
            changed[key] = True
            with self.assertRaisesRegex(graph.shared.BackupError, "PARTIAL_SCOPE_INVALID"):
                graph.validate_manifest(changed)
        with self.assertRaises(graph.shared.BackupError):
            graph.trial.validate_manifest(value)


@unittest.skipUnless(age, "age executable required")
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temporary.name)
        self.identity = self.directory / "identity.txt"
        keygen = pathlib.Path(age).with_name("age-keygen.exe" if os.name == "nt" else "age-keygen")
        graph.command([str(keygen), "-o", str(self.identity)])
        self.recipient = graph.command([str(keygen), "-y", str(self.identity)]).decode().strip()

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, profile="postgres-config-only", unsafe=False):
        manifest = fixture_manifest(profile)
        components = self.directory / "components"
        components.mkdir()
        if profile == "postgres-config-only":
            values = {"database.age": b"PGDMPsynthetic", "environment.age": b"SYNTHETIC_SECRET=never-print", "kb-config.age": graph.trial.canonical({"knowledge_base": {"id": KB}})}
        else:
            values = {"neo4j-data.age": neo4j_tar(unsafe)}
            manifest["inventory_sha256"] = "f" * 64 if unsafe else graph.trial.fingerprint(graph.recovery.object_inventory(io.BytesIO(values["neo4j-data.age"])))
        for name, data in values.items():
            graph.add_component(manifest, age, self.recipient, components, name, data=data)
        archive = self.directory / "snapshot.age"
        graph.shared.pack_archive(age, self.recipient, archive, components, manifest)
        target = self.directory / "verify"
        target.mkdir()
        return archive, target, manifest

    def test_database_snapshot_real_encryption_roundtrip(self):
        archive, target, expected = self.archive()
        actual, inventory = graph.unpack(age, self.identity, archive, target)
        self.assertEqual(actual, expected)
        self.assertEqual(inventory, {})
        self.assertNotIn(b"never-print", archive.read_bytes())
        with self.assertRaises(graph.shared.BackupError):
            graph.trial.validate_manifest(actual)

    def test_graph_snapshot_real_encryption_and_file_inventory(self):
        archive, target, expected = self.archive("neo4j-cold-only")
        actual, inventory = graph.unpack(age, self.identity, archive, target)
        self.assertEqual(actual, expected)
        self.assertEqual(inventory["neo4j-data.age"]["databases/neo4j/store"]["size"], 7)

    def test_tampering_fails_authentication(self):
        archive, target, _ = self.archive()
        changed = bytearray(archive.read_bytes())
        changed[-10] ^= 1
        archive.write_bytes(changed)
        with self.assertRaises((graph.shared.BackupError, tarfile.TarError)):
            graph.unpack(age, self.identity, archive, target)

    def test_graph_paths_cannot_escape_new_volume(self):
        archive, target, _ = self.archive("neo4j-cold-only", True)
        with self.assertRaisesRegex(graph.shared.BackupError, "OBJECT_PATH_REJECTED"):
            graph.unpack(age, self.identity, archive, target)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *remaining])
