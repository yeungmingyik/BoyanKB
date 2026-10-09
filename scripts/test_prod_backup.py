import argparse
import copy
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("restore_prod", pathlib.Path(__file__).with_name("restore-prod.py"))
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)
backup = restore.shared
arguments = argparse.ArgumentParser(add_help=False)
arguments.add_argument("--age-binary")
options, remaining = arguments.parse_known_args()
age = options.age_binary or shutil.which("age")


def object_archive(name="tenant/document/source.md", linked=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.mode = 0o640
        member.uid = 1000
        member.gid = 1000
        if linked:
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            archive.addfile(member)
        else:
            data = "备份恢复合成材料 V1".encode()
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def fixture_config():
    project = "boyankb-restore-test001"
    volumes = {name: {"name": project + "_" + name} for name in ("postgres-data", "redis-data", "data-files")}
    services = {name: {"image": name + ":fixture", "networks": {"default": {}}} for name in ("postgres", "redis", "app", "frontend")}
    for name, volume, destination in (("postgres", "postgres-data", "/var/lib/postgresql/data"), ("redis", "redis-data", "/data"), ("app", "data-files", "/data/files")):
        services[name]["volumes"] = [{"type": "volume", "source": volume, "target": destination}]
    services["app"]["environment"] = {"AUTO_MIGRATE": "false", "DB_HOST": "postgres", "REDIS_ADDR": "redis:6379"}
    return {"services": services, "volumes": volumes, "networks": {"default": {"internal": True, "name": project + "_default"}}}


class IsolationTests(unittest.TestCase):
    def test_schema_toc_filter_preserves_business_and_extension_data(self):
        toc = b"; fixture\n16; 2615 1 SCHEMA - paradedb owner\n20; 2615 2 SCHEMA - tiger owner\n19; 2615 3 SCHEMA - topology owner\n21; 2615 4 SCHEMA - customer owner\n22; 3079 5 EXTENSION - pg_search \n23; 0 0 COMMENT - SCHEMA topology owner\n24; 0 6 TABLE DATA tiger lookup owner\n25; 0 7 TABLE DATA customer knowledge owner\n26; 1259 8 INDEX public knowledge_idx owner\n"
        filtered, skipped = restore.filter_schema_toc(toc, {"paradedb", "tiger", "topology"})
        self.assertEqual(skipped, ["paradedb", "tiger", "topology"])
        self.assertEqual(filtered, b"\n".join(line for line in toc.splitlines() if not line.startswith((b"16;", b"20;", b"19;"))) + b"\n")
        with self.assertRaisesRegex(backup.BackupError, "RESTORE_SCHEMA_FILTER_REJECTED"):
            restore.filter_schema_toc(toc, {"customer"})
        with self.assertRaisesRegex(backup.BackupError, "RESTORE_SCHEMA_TOC_MISMATCH"):
            restore.filter_schema_toc(b"21; 2615 4 SCHEMA - customer owner\n", {"paradedb"})
        with self.assertRaisesRegex(backup.BackupError, "RESTORE_SCHEMA_TOC_MISMATCH"):
            restore.filter_schema_toc(b"16; 1259 1 SCHEMA - paradedb owner\n", {"paradedb"})

    def test_preinitialized_schema_requires_extension_owned_tables_and_functions(self):
        fixture = {"schemas": ["public", "paradedb", "pdb", "tiger", "topology"], "extensions": ["pg_search", "postgis_tiger_geocoder", "postgis_topology"], "unowned_tables": 0, "unowned_functions": 0}
        with patch.object(backup, "pg_query", return_value=json.dumps(fixture)):
            self.assertEqual(restore.preinitialized_schemas({}), {"paradedb", "tiger", "topology"})
        for change in ({"unowned_tables": 1}, {"unowned_functions": 1}, {"schemas": ["public", "customer"]}, {"extensions": []}):
            with self.subTest(change=change), patch.object(backup, "pg_query", return_value=json.dumps({**fixture, **change})), self.assertRaises(backup.BackupError):
                restore.preinitialized_schemas({})

    def test_empty_database_allows_only_image_owned_postgis_reference_table(self):
        for tables, allowed in (([], True), ([{"name": "spatial_ref_sys", "extension": "postgis"}], True), ([{"name": "spatial_ref_sys", "extension": None}], False), ([{"name": "knowledge_bases", "extension": None}], False)):
            with self.subTest(tables=tables), patch.object(backup, "pg_query", return_value=json.dumps(tables)):
                if allowed:
                    restore.require_empty_database({})
                else:
                    with self.assertRaisesRegex(backup.BackupError, "RESTORE_DATABASE_NOT_EMPTY"):
                        restore.require_empty_database({})

    def test_postgres_commands_wait_for_tcp_server_and_do_not_embed_password(self):
        argv = backup.pg_command({"Id": "fixture-postgres"}, "psql", ["-X"])
        script = argv[argv.index("-ec") + 1]
        self.assertIn("--host=127.0.0.1", script)
        self.assertIn('PGPASSWORD="$POSTGRES_PASSWORD"', script)
        self.assertNotIn("fixture-password", " ".join(argv))

    def test_sticky_directory_permissions_are_preserved_but_setuid_files_rejected(self):
        for directory, mode, accepted in ((True, 0o1777, True), (True, 0o2770, True), (False, 0o4755, False)):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                member = tarfile.TarInfo("cache" if directory else "executable")
                member.mode = mode
                member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
                archive.addfile(member, None if directory else io.BytesIO())
            output.seek(0)
            if accepted:
                self.assertEqual(restore.object_inventory(output)["cache"]["mode"], mode)
            else:
                with self.assertRaisesRegex(backup.BackupError, "OBJECT_SPECIAL_PERMISSION_REJECTED"):
                    restore.object_inventory(output)

    def test_quiesced_source_is_restarted_after_backup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            env_file = directory / "fixture.env"
            env_file.write_bytes(b"SYSTEM_AES_KEY='01234567890123456789012345678901'\nDB_USER='u'\nDB_PASSWORD='p'\nDB_NAME='d'\nREDIS_PASSWORD='r'\n")
            compose = directory / "compose.yaml"
            compose.write_bytes(b"services: {}\n")
            args = SimpleNamespace(age_binary="fixture-age", recipient="age1" + "q" * 58, output=str(directory / "backup.age"), env_file=str(env_file), compose=[str(compose)], project="boyankb-fixture-test", quiesce=True)
            containers = {name: {"Id": name + "-id"} for name in ("app", "frontend", "postgres", "redis", "caddy")}
            calls = []

            def mock_command(argv, *args, **kwargs):
                calls.append(argv)
                if "ps" in argv:
                    return b"app-id\nfrontend-id\n"
                return b""

            with patch.object(backup, "age_binary", return_value="fixture-age"), patch.object(backup, "source_contract", return_value=(containers, {})), patch.object(backup, "command", side_effect=mock_command), patch.object(backup, "config_files", return_value=[]), patch.object(backup, "model_inventory", return_value=[]), patch.object(backup, "docker_json", return_value=[{"State": {"Running": False}}]), patch.object(backup, "database_scope", side_effect=backup.BackupError("FIXTURE_DB_FAILURE")), patch.object(backup, "wait_source_services") as resumed, patch.object(backup, "emit"), self.assertRaisesRegex(backup.BackupError, "FIXTURE_DB_FAILURE"):
                backup.backup(args)
            self.assertIn(["docker", "stop", "--time", "120", "caddy-id", "frontend-id", "app-id"], calls)
            self.assertIn(["docker", "start", "app-id", "frontend-id", "caddy-id"], calls)
            resumed.assert_called_once_with(containers, ["caddy", "frontend", "app"])
            self.assertFalse((directory / "backup.age").exists())

    def test_rejects_production_and_non_restore_project_names(self):
        for name in ("boyankb-prod", "weknora-local", "boyankb-restore-prod-001", "boyankb-restore-production-001"):
            with self.subTest(name=name), patch.object(restore, "command") as command, self.assertRaises(backup.BackupError):
                restore.fresh_project(name, "source")
            command.assert_not_called()

    def test_rejects_existing_target_project_before_writes(self):
        with patch.object(restore, "command", return_value=b"existing-container\n"), self.assertRaisesRegex(backup.BackupError, "RESTORE_PROJECT_ALREADY_EXISTS"):
            restore.fresh_project("boyankb-restore-test001", "source")

    def test_rejects_unlabelled_existing_container_name(self):
        with patch.object(restore, "command", side_effect=[b"", b"", b"", b"boyankb-restore-test001-app-1\n"]), self.assertRaisesRegex(backup.BackupError, "RESTORE_CONTAINER_ALREADY_EXISTS"):
            restore.fresh_project("boyankb-restore-test001", "source")

    def test_isolation_contract_rejects_unsafe_compose(self):
        base = fixture_config()
        manifest = {"components": [], "images": {name: {"id": "image-id"} for name in base["services"]}}
        changes = [
            lambda cfg: cfg["networks"]["default"].update(internal=False),
            lambda cfg: cfg["services"]["app"].update(network_mode="host"),
            lambda cfg: cfg["services"]["app"].update(privileged=True),
            lambda cfg: cfg["services"]["app"].update(cap_add=["SYS_ADMIN"]),
            lambda cfg: cfg["services"]["app"].update(use_api_socket=True),
            lambda cfg: cfg["services"]["app"].update(configs=[{"source": "socket", "target": "/var/run/docker.sock"}]),
            lambda cfg: cfg["services"]["app"].update(secrets=[{"source": "socket", "target": "/var/run/docker.sock"}]),
            lambda cfg: cfg["volumes"]["data-files"].update(name="production-data"),
            lambda cfg: cfg["volumes"]["data-files"].update(external=True),
            lambda cfg: cfg["services"]["frontend"].update(ports=[{"host_ip": "0.0.0.0", "target": 80, "published": "8080"}]),
            lambda cfg: cfg["services"]["app"]["environment"].update(AUTO_MIGRATE="true"),
        ]
        with patch.object(restore, "command", return_value=json.dumps(base).encode()), patch.object(backup, "docker_json", return_value=[{"Id": "image-id"}]):
            self.assertEqual(restore.restore_config([], {}, "boyankb-restore-test001", manifest), base)
        for index, change in enumerate(changes):
            config = copy.deepcopy(base)
            change(config)
            with self.subTest(index=index), patch.object(restore, "command", return_value=json.dumps(config).encode()), patch.object(backup, "docker_json", return_value=[{"Id": "image-id"}]), self.assertRaises(backup.BackupError):
                restore.restore_config([], {}, "boyankb-restore-test001", manifest)

    def test_dotenv_preserves_single_quoted_secret_and_rejects_duplicates(self):
        data = b"SYSTEM_AES_KEY='01234567890123456789012345678901'\nDB_USER='u'\nDB_PASSWORD='x$y#z'\nDB_NAME='d'\nREDIS_PASSWORD='r'\n"
        self.assertEqual(backup.parse_env(data)["DB_PASSWORD"], "x$y#z")
        with self.assertRaisesRegex(backup.BackupError, "ENV_DUPLICATE_KEY"):
            backup.parse_env(data + b"DB_USER='other'\n")

    def test_archived_environment_cannot_redirect_docker_or_process_execution(self):
        data = b"SYSTEM_AES_KEY='01234567890123456789012345678901'\nDB_USER='u'\nDB_PASSWORD='p'\nDB_NAME='d'\nREDIS_PASSWORD='r'\n"
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "PATH", "LD_PRELOAD", "HTTP_PROXY"):
            with self.subTest(name=name), self.assertRaisesRegex(backup.BackupError, "CLIENT_CONTROL_ENV_VALUE_REJECTED"):
                backup.parse_env(data + (name + "='unexpected'\n").encode())
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "operator", "PATH": "operator-path"}):
            actual = backup.clean_env({"DOCKER_CONTEXT": "archive", "PATH": "archive-path", "DB_USER": "fixture"})
            self.assertEqual(actual["DOCKER_CONTEXT"], "operator")
            self.assertEqual(actual["PATH"], "operator-path")
            self.assertEqual(actual["DB_USER"], "fixture")

    def test_configuration_drift_requires_exact_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = pathlib.Path(directory) / "Caddyfile"
            filename.write_bytes(b"fixture v1")
            original = backup.digest_file(filename)
            config = {"services": {"caddy": {"volumes": [{"type": "bind", "source": str(filename), "target": "/etc/caddy/Caddyfile", "read_only": True}]}}}
            manifest = {"components": [{"role": "configuration", "origin": {"kind": "bind", "service": "caddy", "target": "/etc/caddy/Caddyfile"}, "plaintext_size": original["size"], "plaintext_sha256": original["sha256"]}]}
            self.assertEqual(restore.configuration_overrides(config, manifest, []), [])
            filename.write_bytes(b"fixture v2")
            with self.assertRaisesRegex(backup.BackupError, "RESTORE_CONFIG_DIFFERS_FROM_BACKUP"):
                restore.configuration_overrides(config, manifest, [])
            self.assertEqual(restore.configuration_overrides(config, manifest, ["caddy:/etc/caddy/Caddyfile"]), ["caddy:/etc/caddy/Caddyfile"])
            with self.assertRaisesRegex(backup.BackupError, "RESTORE_CONFIG_DIFFERS_FROM_BACKUP"):
                restore.configuration_overrides(config, manifest, ["caddy:/different"])


@unittest.skipUnless(age, "age binary not provided")
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boyankb-backup-test-")
        self.directory = pathlib.Path(self.temp.name)
        self.binary = backup.age_binary(age)
        keygen = pathlib.Path(self.binary).with_name("age-keygen.exe" if os.name == "nt" else "age-keygen")
        self.identity = self.directory / "identity.txt"
        backup.command([str(keygen), "-o", str(self.identity)])
        self.recipient = backup.command([str(keygen), "-y", str(self.identity)]).decode().strip()
        self.raw_env = b"SYSTEM_AES_KEY='01234567890123456789012345678901'\nDB_USER='u'\nDB_PASSWORD='fixture$only'\nDB_NAME='d'\nREDIS_PASSWORD='r'\n"

    def tearDown(self):
        self.temp.cleanup()

    def make_archive(self, objects=None, change=None):
        parts = {"database": ("database.dump.age", b"PGDMPfixture"), "objects": ("objects.tar.age", objects or object_archive()), "redis": ("redis.rdb.age", b"REDISfixture"), "environment": ("environment.env.age", self.raw_env)}
        manifest = {"format": "boyankb-encrypted-backup-v1", "storage": "local", "local_base_dir": "/data/files", "consistency": "application-quiesced", "source_project": "fixture", "components": []}
        for role, (name, data) in parts.items():
            metadata = backup.encrypt_stream(self.binary, self.recipient, self.directory / name, io.BytesIO(data))
            manifest["components"].append({"name": name, "role": role, **metadata})
        if change:
            change(manifest)
        output = self.directory / "backup.age"
        backup.pack_archive(self.binary, self.recipient, output, self.directory, manifest)
        return output

    def unpack(self, output):
        stage = self.directory / "encrypted-stage"
        stage.mkdir()
        manifest, roles = restore.unpack_verified(self.binary, self.identity, output, stage)
        return manifest, roles, restore.verify_components(self.binary, self.identity, stage, manifest)

    def test_roundtrip_preserves_environment_and_file_ownership_without_plaintext_stage(self):
        output = self.make_archive()
        manifest, roles, (environment, inventories) = self.unpack(output)
        self.assertEqual(environment, self.raw_env)
        self.assertEqual(set(roles), {"database", "objects", "redis", "environment"})
        item = inventories["objects"]["tenant/document/source.md"]
        self.assertEqual((item["uid"], item["gid"], item["mode"]), (1000, 1000, 0o640))
        self.assertTrue(all(path.suffix == ".age" for path in (self.directory / "encrypted-stage").iterdir()))
        self.assertNotIn(self.raw_env, output.read_bytes())

    def test_corrupted_outer_ciphertext_is_rejected(self):
        output = self.make_archive()
        encrypted = bytearray(output.read_bytes())
        encrypted[-1] ^= 1
        output.write_bytes(encrypted)
        with self.assertRaises((backup.BackupError, tarfile.TarError)):
            self.unpack(output)

    def test_wrong_identity_is_rejected(self):
        output = self.make_archive()
        self.identity = self.directory / "wrong-identity.txt"
        keygen = pathlib.Path(self.binary).with_name("age-keygen.exe" if os.name == "nt" else "age-keygen")
        backup.command([str(keygen), "-o", str(self.identity)])
        with self.assertRaises((backup.BackupError, tarfile.TarError)):
            self.unpack(output)

    def test_authenticated_manifest_with_wrong_checksum_is_rejected(self):
        output = self.make_archive(change=lambda manifest: manifest["components"][0].update(sha256="0" * 64))
        with self.assertRaisesRegex(backup.BackupError, "CIPHERTEXT_CHECKSUM_MISMATCH"):
            self.unpack(output)

    def test_missing_required_component_is_rejected(self):
        output = self.make_archive(change=lambda manifest: manifest["components"].pop())
        with self.assertRaisesRegex(backup.BackupError, "REQUIRED_COMPONENT_MISSING"):
            self.unpack(output)

    def test_object_traversal_is_rejected_before_extraction(self):
        output = self.make_archive(objects=object_archive("../outside.env"))
        with self.assertRaisesRegex(backup.BackupError, "OBJECT_PATH_REJECTED"):
            self.unpack(output)
        self.assertFalse((self.directory / "outside.env").exists())

    def test_object_symlink_is_rejected_before_extraction(self):
        output = self.make_archive(objects=object_archive(linked=True))
        with self.assertRaisesRegex(backup.BackupError, "OBJECT_LINK_OR_SPECIAL_FILE_REJECTED"):
            self.unpack(output)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *remaining])
