import copy
import gzip
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("tencent_snapshot", Path(__file__).with_name("tencent-snapshot.py"))
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def fixture():
    volumes = {logical: snapshot.PROJECT + "_" + snapshot.VOLUME_LABELS.get(logical, logical) for targets in snapshot.MOUNTS.values() for logical in targets.values()}
    containers = {}
    images = {}
    for index, name in enumerate(snapshot.SERVICES, 1):
        images[name] = "sha256:" + str(index) * 64
        mounts = [{"Type": "volume", "Name": volumes[logical], "Destination": target} for target, logical in snapshot.MOUNTS.get(name, {}).items()]
        mounts += [{"Type": "bind", "Source": str(snapshot.ROOT / relative), "Destination": target} for target, relative in snapshot.BIND_TARGETS.get(name, {}).items()]
        containers[name] = {"Id": str(index) * 64, "Image": images[name], "Config": {"Env": [], "Labels": {"com.docker.compose.project": snapshot.PROJECT, "com.docker.compose.service": name}}, "State": {"Running": True, "Health": {"Status": "healthy"}, "OOMKilled": False}, "HostConfig": {"NetworkMode": "fixture"}, "Mounts": mounts}
    return containers, volumes, images


def environment_fixture():
    containers, _, _ = fixture()
    values = {"DB_USER": "fixture", "DB_NAME": "fixture", "DB_PASSWORD": "secret-db", "REDIS_PASSWORD": "secret-redis", "SYSTEM_AES_KEY": "A" * 32, "SYSTEM_SIGNING_KEY": "S" * 64, "JWT_SECRET": "secret-jwt", "BOYAN_NEO4J_PASSWORD": "secret-graph", "BOYAN_DOCUMENT_RENDERER_TOKEN": "R" * 32, "GRPC_AUTH_TOKEN": "secret-grpc", "DEEPSEEK_API_KEY": "secret-model", "DEEPSEEK_BASE_URL": "https://api.deepseek.com", "DEEPSEEK_MODEL_NAME": "deepseek-chat", "EMBEDDING_MODEL_NAME": "bge-m3:latest", "TRIAL_CLOUDFLARE_API_TOKEN": "must-never-export"}
    app = {key: value for key, value in values.items() if key != "BOYAN_NEO4J_PASSWORD"}
    app["NEO4J_PASSWORD"] = values["BOYAN_NEO4J_PASSWORD"]
    containers["app"]["Config"]["Env"] = [key + "=" + value for key, value in app.items()]
    for name, data in {"postgres": {"POSTGRES_USER": values["DB_USER"], "POSTGRES_DB": values["DB_NAME"], "POSTGRES_PASSWORD": values["DB_PASSWORD"]}, "redis": {"REDIS_PASSWORD": values["REDIS_PASSWORD"]}, "neo4j": {"NEO4J_AUTH": "neo4j/" + values["BOYAN_NEO4J_PASSWORD"]}, "document-renderer": {"BOYAN_DOCUMENT_RENDERER_TOKEN": values["BOYAN_DOCUMENT_RENDERER_TOKEN"]}, "docreader": {"GRPC_AUTH_TOKEN": values["GRPC_AUTH_TOKEN"]}}.items():
        containers[name]["Config"]["Env"] = [key + "=" + value for key, value in data.items()]
    return containers, values


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.events = self.enterContext(patch.object(snapshot.shared, "emit"))

    def test_exact_source_layout(self):
        snapshot.validate_source(*fixture())

    def test_missing_service_rejected(self):
        containers, volumes, images = fixture()
        del containers["neo4j"]
        with self.assertRaisesRegex(snapshot.shared.BackupError, "EXACT_NINE"):
            snapshot.validate_source(containers, volumes, images)

    def test_wrong_project_image_health_and_public_port_rejected(self):
        cases = [lambda c: c["app"]["Config"]["Labels"].update({"com.docker.compose.project": "other"}), lambda c: c["app"].update({"Image": "sha256:" + "f" * 64}), lambda c: c["neo4j"]["State"].update({"OOMKilled": True}), lambda c: c["app"]["HostConfig"].update({"PortBindings": {"8080/tcp": [{"HostIp": "0.0.0.0"}]}})]
        for change in cases:
            with self.subTest(change=cases.index(change)):
                containers, volumes, images = fixture()
                change(containers)
                with self.assertRaises(snapshot.shared.BackupError):
                    snapshot.validate_source(containers, volumes, images)

    def test_socket_or_extra_volume_rejected(self):
        containers, volumes, images = fixture()
        containers["app"]["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"})
        with self.assertRaises(snapshot.shared.BackupError):
            snapshot.validate_source(containers, volumes, images)
        containers, volumes, images = fixture()
        volumes["foreign"] = "foreign"
        with self.assertRaises(snapshot.shared.BackupError):
            snapshot.validate_source(containers, volumes, images)

    def test_shared_volume_mismatch_rejected(self):
        containers, volumes, images = fixture()
        containers["docreader"]["Mounts"][0]["Name"] = "other-volume"
        with self.assertRaisesRegex(snapshot.shared.BackupError, "VOLUME_LAYOUT"):
            snapshot.validate_source(containers, volumes, images)

    def test_environment_is_allowlisted(self):
        containers, source = environment_fixture()
        result = snapshot.runtime_environment(containers, source)
        self.assertNotIn("TRIAL_CLOUDFLARE_API_TOKEN", result)
        self.assertNotIn("must-never-export", snapshot.env_bytes(result).decode())
        self.assertEqual(result["SYSTEM_AES_KEY"], source["SYSTEM_AES_KEY"])
        self.assertEqual(result["WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL"], "liuweijian@boyantech.xyz")

    def test_environment_mismatch_fails_without_secret_in_error(self):
        containers, source = environment_fixture()
        source["DEEPSEEK_API_KEY"] = "mismatch-secret"
        with self.assertRaises(snapshot.shared.BackupError) as caught:
            snapshot.runtime_environment(containers, source)
        self.assertNotIn("mismatch-secret", str(caught.exception))

    def test_empty_grpc_token_generated_for_cloud_only(self):
        containers, source = environment_fixture()
        source["GRPC_AUTH_TOKEN"] = ""
        for name in ("app", "docreader"):
            containers[name]["Config"]["Env"] = ["GRPC_AUTH_TOKEN=" if value.startswith("GRPC_AUTH_TOKEN=") else value for value in containers[name]["Config"]["Env"]]
        result = snapshot.runtime_environment(containers, source, True)
        self.assertGreaterEqual(len(result["GRPC_AUTH_TOKEN"]), 32)
        self.assertEqual(source["GRPC_AUTH_TOKEN"], "")

    def test_env_quotes_and_dollars_are_literal(self):
        self.assertEqual(snapshot.env_bytes({"VALUE": "a'b$c\\d"}), b"VALUE='a\\'b$c\\d'\n")

    def test_active_work_rejected(self):
        for key in ("unpaused_sources", "running_syncs", "busy_knowledge", "busy_documents", "queue_active", "queue_pending", "queue_retry", "queue_scheduled"):
            with self.subTest(key=key), self.assertRaises(snapshot.shared.BackupError):
                snapshot.require_idle({"idle": False, "blockers": [key]})

    def test_backup_without_quiesce_never_inspects_or_stops(self):
        with patch.object(snapshot, "source") as source, self.assertRaises(snapshot.shared.BackupError):
            snapshot.backup(SimpleNamespace(quiesce=False))
        source.assert_not_called()

    def test_busy_source_never_creates_output_or_stops(self):
        with patch.object(snapshot, "source", return_value=({}, {})), patch.object(snapshot.shared, "parse_env", return_value={}), patch.object(Path, "read_bytes", return_value=b""), patch.object(snapshot, "runtime_environment", return_value={}), patch.object(snapshot, "plan_state", return_value={"idle": {"idle": False, "blockers": ["queue_active"]}}), patch.object(snapshot, "output_directory") as output, patch.object(snapshot, "quiesced") as stop, self.assertRaises(snapshot.shared.BackupError):
            snapshot.backup(SimpleNamespace(quiesce=True, output="unused"))
        output.assert_not_called()
        stop.assert_not_called()

    def test_stop_failure_restores_every_touched_service(self):
        containers, _, _ = fixture()
        calls = []

        def command(args, **kwargs):
            calls.append(args)
            if args[1] == "stop" and args[-1] == containers["docreader"]["Id"]:
                raise snapshot.shared.BackupError("SYNTHETIC_STOP_FAILURE")
            return b""

        with patch.object(snapshot.shared, "command", side_effect=command), patch.object(snapshot, "wait_source_service"), patch.object(snapshot, "unhealthy_services", return_value=[]), self.assertRaisesRegex(snapshot.shared.BackupError, "SYNTHETIC"):
            with snapshot.quiesced(containers):
                self.fail("must not yield")
        started = [args[-1] for args in calls if args[1] == "start"]
        self.assertEqual(started, [containers[name]["Id"] for name in ("docreader", "app", "trial-gateway")])

    def test_component_failure_restores_dependency_order(self):
        containers, _, _ = fixture()
        calls = []
        with patch.object(snapshot.shared, "command", side_effect=lambda args, **kwargs: calls.append(args) or b""), patch.object(snapshot, "idle", return_value={"idle": True, "blockers": []}), patch.object(snapshot, "stopped"), patch.object(snapshot, "wait_source_service"), patch.object(snapshot, "unhealthy_services", return_value=[]), self.assertRaisesRegex(ValueError, "synthetic"):
            with snapshot.quiesced(containers):
                raise ValueError("synthetic")
        started = [args[-1] for args in calls if args[1] == "start"]
        self.assertEqual(started, [containers[name]["Id"] for name in ("redis", "neo4j", "docreader", "document-renderer", "ollama", "app", "trial-gateway")])

    def test_resume_error_does_not_skip_other_services(self):
        containers, _, _ = fixture()
        calls = []

        def command(args, **kwargs):
            calls.append(args)
            if args[1] == "start" and args[-1] == containers["neo4j"]["Id"]:
                raise snapshot.shared.BackupError("SYNTHETIC_START_FAILURE")
            return b""

        with patch.object(snapshot.shared, "command", side_effect=command), patch.object(snapshot, "idle", return_value={"idle": True, "blockers": []}), patch.object(snapshot, "stopped"), patch.object(snapshot, "wait_source_service"), patch.object(snapshot, "unhealthy_services", return_value=["neo4j"]), self.assertRaisesRegex(snapshot.shared.BackupError, "SOURCE_RESUME_FAILED"):
            with snapshot.quiesced(containers):
                pass
        self.assertIn(["docker", "start", containers["trial-gateway"]["Id"]], calls)

    def test_resume_failure_preserves_original_error_and_phase(self):
        containers, _, _ = fixture()
        original = ValueError("secret-must-never-print")
        with patch.object(snapshot.shared, "command"), patch.object(snapshot, "idle", return_value={"idle": True, "blockers": []}), patch.object(snapshot, "stopped"), patch.object(snapshot, "wait_source_service", side_effect=TimeoutError("private-value")), patch.object(snapshot, "unhealthy_services", return_value=["neo4j"]), self.assertRaises(ValueError) as caught:
            with snapshot.quiesced(containers):
                snapshot.phase("database-dump-verify")
                raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(original.snapshot_phase, "database-dump-verify")
        self.assertNotIn("secret-must-never-print", str(self.events.call_args_list))
        self.assertNotIn("private-value", str(self.events.call_args_list))
        failures = [call.kwargs for call in self.events.call_args_list if call.args[0] == "tencent_snapshot_work_failed"]
        self.assertEqual(failures[0]["error_type"], "ValueError")
        self.assertGreater(failures[0]["line"], 0)

    def test_late_healthy_service_clears_earlier_timeout(self):
        containers, _, _ = fixture()

        def wait(containers, name):
            if name == "neo4j":
                raise snapshot.shared.BackupError("SOURCE_RESUME_TIMEOUT_neo4j")

        with patch.object(snapshot.shared, "command"), patch.object(snapshot, "idle", return_value={"idle": True, "blockers": []}), patch.object(snapshot, "stopped"), patch.object(snapshot, "wait_source_service", side_effect=wait), patch.object(snapshot, "unhealthy_services", return_value=[]) as final_check:
            with snapshot.quiesced(containers):
                pass
        final_check.assert_called_once_with(containers, snapshot.SERVICES)
        self.assertTrue(any(call.args[0] == "tencent_snapshot_resume_warning" for call in self.events.call_args_list))
        self.assertFalse(any(call.args[0] == "tencent_snapshot_resume_failed" for call in self.events.call_args_list))

    def test_service_wait_accepts_health_after_180_seconds(self):
        containers, _, _ = fixture()
        with patch.object(snapshot.time, "monotonic", side_effect=[0, 200]), patch.object(snapshot.time, "sleep"), patch.object(snapshot, "unhealthy_services", side_effect=[["neo4j"], []]):
            snapshot.wait_source_service(containers, "neo4j")

    def test_service_wait_times_out_after_600_seconds(self):
        containers, _, _ = fixture()
        with patch.object(snapshot.time, "monotonic", side_effect=[0, 600]), patch.object(snapshot.time, "sleep") as sleep, patch.object(snapshot, "unhealthy_services", return_value=["neo4j"]), self.assertRaisesRegex(snapshot.shared.BackupError, "SOURCE_RESUME_TIMEOUT_neo4j"):
            snapshot.wait_source_service(containers, "neo4j")
        sleep.assert_not_called()

    def test_final_health_checks_id_oom_and_health(self):
        containers, _, _ = fixture()
        for mutate in (lambda c: c.update({"Id": "other"}), lambda c: c["State"].update({"OOMKilled": True}), lambda c: c["State"].update({"Restarting": True}), lambda c: c["State"]["Health"].update({"Status": "starting"})):
            current = copy.deepcopy(containers["neo4j"])
            mutate(current)
            with patch.object(snapshot.shared, "docker_json", return_value=[current]):
                self.assertEqual(snapshot.unhealthy_services(containers, ["neo4j"]), ["neo4j"])
        with patch.object(snapshot.shared, "docker_json", return_value=[containers["neo4j"]]):
            self.assertEqual(snapshot.unhealthy_services(containers, ["neo4j"]), [])

    def test_dump_validation_child_reads_header_from_fresh_descriptor(self):
        original_run = subprocess.run

        def child(args, **kwargs):
            self.assertEqual(args[-2:], ["pg_restore", "--list"])
            self.assertEqual(kwargs["stdin"].raw.tell(), 0)
            return original_run([sys.executable, "-c", "import sys; sys.exit(0 if sys.stdin.buffer.read(5) == b'PGDMP' else 1)"], **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.dump"
            path.write_bytes(b"PGDMP" + b"x" * 100000)
            with patch.object(snapshot.subprocess, "run", side_effect=child):
                snapshot.validate_database_dump(path, {"Id": "fixture"})

    def test_dump_validation_rejects_bad_header_before_child(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.dump"
            path.write_bytes(b"invalid")
            with patch.object(snapshot.subprocess, "run") as child, self.assertRaisesRegex(snapshot.shared.BackupError, "HEADER_INVALID"):
                snapshot.validate_database_dump(path, {"Id": "fixture"})
            child.assert_not_called()

    def test_cold_oom_or_running_state_rejected(self):
        containers, _, _ = fixture()
        for state in ({"Running": True, "ExitCode": 0}, {"Running": False, "ExitCode": 137}, {"Running": False, "ExitCode": 0, "OOMKilled": True}):
            current = copy.deepcopy(containers["neo4j"])
            current["State"] = state
            with patch.object(snapshot.shared, "docker_json", return_value=[current]), self.assertRaises(snapshot.shared.BackupError):
                snapshot.stopped(containers, ["neo4j"])

    def test_foreign_running_volume_consumer_rejected(self):
        _, volumes, _ = fixture()
        with patch.object(snapshot.shared, "command", return_value=b"foreign-id"), self.assertRaises(snapshot.shared.BackupError):
            snapshot.frozen_volumes(volumes)

    def test_output_is_fresh_flat_and_confined(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(snapshot, "ROOT", Path(directory)), patch.object(snapshot.shared, "protect_directory"):
            base = Path(directory) / "temp/tencent-cloud/snapshots"
            created = snapshot.output_directory(str(base / "fixture"))
            self.assertEqual(created, base / "fixture")
            for target in (created, base, Path(directory) / "outside", base / "nested" / "child"):
                with self.subTest(target=target), self.assertRaises(snapshot.shared.BackupError):
                    snapshot.output_directory(str(target))

    def test_archive_inventory_rejects_links_and_traversal(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "target"
                archive.addfile(member)
            stream.seek(0)
            with self.subTest(name=name), self.assertRaises(snapshot.shared.BackupError):
                snapshot.recovery.object_inventory(stream)

    def test_streaming_compressed_component_digest_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.gz"
            argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'fixture-data' * 10000)"]
            result = snapshot.save_command(path, argv, True)
            self.assertEqual(gzip.decompress(path.read_bytes()), b"fixture-data" * 10000)
            self.assertEqual(result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                snapshot.save_command(path, argv, True)

    def test_component_nonzero_exit_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(snapshot.shared.BackupError, "COMPONENT_COMMAND_FAILED"):
            snapshot.save_command(Path(directory) / "incomplete", [sys.executable, "-c", "import sys; sys.stdout.write('partial'); sys.exit(7)"])


if __name__ == "__main__":
    unittest.main()
