import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("tencent_transfer", Path(__file__).with_name("tencent-transfer.py"))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class SourceGuards(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.private = self.root / "temp/tencent-cloud"
        self.private.mkdir(parents=True)
        (self.root / "deployment/tencent").mkdir(parents=True)
        self.images = [{"tag": "image:v1", "id": "sha256:" + "a" * 64}]
        (self.root / "deployment/tencent/images.lock.json").write_text(json.dumps({"images": self.images}))
        (self.root / "deployment/tencent/env.example").write_text("PUBLIC_HOST=example\nDB_PASSWORD=placeholder\n")
        for key, value in (("ROOT", self.root), ("PRIVATE", self.private)):
            context = patch.object(transfer, key, value)
            context.start()
            self.addCleanup(context.stop)

    def image(self):
        path = self.private / "images-unit.tar.gz"
        path.write_bytes(gzip.compress(b"synthetic image archive"))
        receipt = {"format": "boyankb-tencent-images-v1", "platform": "linux/amd64", "images": self.images, "archive": path.name, "size": path.stat().st_size, "sha256": transfer.digest(path)}
        path.with_suffix(".json").write_text(json.dumps(receipt))
        return path

    def snapshot(self, filename, data):
        directory = self.private / "snapshots/unit"
        directory.mkdir(parents=True)
        path = directory / filename
        path.write_bytes(data)
        manifest = {"format": "boyankb-tencent-snapshot-v1", "snapshot_validated": True, "consistency": "application-quiesced", "writers_resumed": True, "components": {filename: {"size": len(data), "sha256": transfer.digest(path)}}}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return path

    def bundle(self, members):
        path = self.private / "config-bundle-unit.tar.gz"
        files = {}
        with tarfile.open(path, "w:gz") as package:
            for name, kind in members:
                content = b"deployment content"
                item = tarfile.TarInfo(name)
                item.type = kind
                item.size = len(content) if kind == tarfile.REGTYPE else 0
                item.linkname = "outside" if kind != tarfile.REGTYPE else ""
                package.addfile(item, io.BytesIO(content) if item.size else None)
                files[name] = hashlib.sha256(content).hexdigest()
                source = self.root / name
                if name in transfer.CONFIG_MEMBERS:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(content)
        record = {"format": "boyankb-tencent-config-v1", "archive": path.name, "size": path.stat().st_size, "sha256": transfer.digest(path), "files": files}
        path.with_suffix(".json").write_text(json.dumps(record))
        return path

    def test_completed_image_and_receipt_allowed(self):
        path = self.image()
        self.assertEqual(transfer.source_file(path)[2], transfer.digest(path))
        self.assertEqual(transfer.source_file(path.with_suffix(".json"))[0], path.with_suffix(".json"))

    def test_unfinished_image_without_receipt_rejected(self):
        path = self.image()
        path.with_suffix(".json").unlink()
        with self.assertRaises(RuntimeError):
            transfer.source_file(path)

    def test_image_changed_after_export_rejected(self):
        path = self.image()
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
        with self.assertRaisesRegex(RuntimeError, "LOCAL_SHA256_MISMATCH"):
            transfer.source_file(path)

    def test_wrong_image_lock_rejected(self):
        path = self.image()
        receipt = path.with_suffix(".json")
        record = json.loads(receipt.read_text())
        record["images"][0]["id"] = "sha256:" + "b" * 64
        receipt.write_text(json.dumps(record))
        with self.assertRaisesRegex(RuntimeError, "IMAGE_RECEIPT_LOCK_MISMATCH"):
            transfer.source_file(path)

    def test_root_env_and_deployment_ssh_material_rejected(self):
        for path in (self.root / ".env", self.private / ".env", self.private / "known_hosts", self.private / "identity.pem"):
            path.write_text("private fixture")
            with self.subTest(path=path.name), self.assertRaises(RuntimeError):
                transfer.source_file(path)

    def test_renamed_private_key_rejected(self):
        path = self.private / "images-key.tar.gz"
        path.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nfixture")
        with self.assertRaisesRegex(RuntimeError, "PRIVATE_KEY_UPLOAD_FORBIDDEN"):
            transfer.source_file(path)

    def test_source_parent_traversal_rejected(self):
        path = self.image()
        with self.assertRaisesRegex(RuntimeError, "SOURCE_OUTSIDE_PRIVATE_DEPLOYMENT"):
            transfer.source_file(path.parent / "child/.." / path.name)

    def test_source_hardlink_rejected(self):
        path = self.image()
        os.link(path, path.with_name("images-linked.tar.gz"))
        with self.assertRaisesRegex(RuntimeError, "LOCAL_REGULAR_FILE_REQUIRED"):
            transfer.source_file(path)

    def test_snapshot_runtime_env_is_scoped(self):
        path = self.snapshot("runtime.env", b"PUBLIC_HOST='193.112.17.218'\nDB_PASSWORD='fixture'\n")
        self.assertEqual(transfer.source_file(path)[0], path)

    def test_snapshot_cannot_include_cloudflare_credentials(self):
        path = self.snapshot("runtime.env", b"PUBLIC_HOST=x\nDB_PASSWORD=x\nTRIAL_CLOUDFLARE_API_TOKEN=fixture\n")
        with self.assertRaisesRegex(RuntimeError, "RUNTIME_ENV_NOT_ALLOWLISTED"):
            transfer.source_file(path)

    def test_snapshot_wrong_component_hash_rejected(self):
        path = self.snapshot("database.dump", b"PGDMPfixture")
        path.write_bytes(b"PGDMPchanged")
        with self.assertRaises(RuntimeError):
            transfer.source_file(path)

    def test_snapshot_chinese_inventory_uses_canonical_utf8(self):
        path = self.snapshot("data-files.tar.gz", b"fixture")
        inventory = {"中文路径": {"size": 12}}
        sidecar = path.with_name(path.name + ".inventory.json")
        sidecar.write_text(json.dumps(inventory), encoding="utf-8")
        manifest_path = path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["components"][path.name].update({"inventory": sidecar.name, "inventory_sha256": hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()})
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(transfer.source_file(sidecar)[0], sidecar)

    def test_config_exact_allowlist_and_local_hash(self):
        path = self.bundle([("LICENSE", tarfile.REGTYPE)])
        self.assertEqual(transfer.source_file(path)[0], path)
        (self.root / "LICENSE").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "CONFIG_MEMBER_HASH_MISMATCH"):
            transfer.source_file(path)

    def test_config_env_traversal_link_and_duplicate_rejected(self):
        cases = [[(".env", tarfile.REGTYPE)], [("../secret", tarfile.REGTYPE)], [("LICENSE", tarfile.SYMTYPE)], [("LICENSE", tarfile.LNKTYPE)], [("LICENSE", tarfile.REGTYPE), ("LICENSE", tarfile.REGTYPE)]]
        for members in cases:
            with self.subTest(members=members):
                path = self.bundle(members)
                with self.assertRaises(RuntimeError):
                    transfer.source_file(path)


class DestinationGuards(unittest.TestCase):
    def test_valid_nested_destination(self):
        self.assertEqual(transfer.destination("snapshots/20261010/runtime.env", "runtime.env"), "snapshots/20261010/runtime.env")

    def test_escape_reserved_and_shell_syntax_rejected(self):
        for value in ("/etc/file", "../file", "a/../file", "a//file", "a\\file", "./file", "file;id", "file$(id)", "file.partial", "file.partial.json", "file.lock", "a/b/c/d/file"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                transfer.destination(value, Path(value).name)

    def test_destination_cannot_rename_to_secret_filename(self):
        with self.assertRaisesRegex(RuntimeError, "DESTINATION_FILENAME_MISMATCH"):
            transfer.destination("runtime.env", "images-unit.tar.gz")


class KeptBytes(io.BytesIO):
    def close(self):
        self.sent = self.getvalue()
        super().close()


class BinaryProtocolTests(unittest.TestCase):
    def test_resume_stream_is_binary_and_host_verification_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images-unit.tar.gz"
            data = bytes(range(256)) * 8192
            path.write_bytes(data)
            sha256 = transfer.digest(path)
            offset = 731
            ready = {"status": "ready", "offset": offset, "prefix_sha256": hashlib.sha256(data[:offset]).hexdigest()}
            complete = {"status": "complete", "size": len(data), "sha256": sha256}
            input_stream = KeptBytes()
            process = SimpleNamespace(stdin=input_stream, stdout=io.BytesIO((json.dumps(ready) + "\n" + json.dumps(complete) + "\n").encode()), wait=lambda timeout=None: 0, poll=lambda: 0)
            helper = SimpleNamespace(settings=lambda: {}, ssh_argv=lambda values: ["ssh", "-o", "StrictHostKeyChecking=yes"])
            with patch.object(transfer, "source_file", return_value=(path, path.stat(), sha256)), patch.object(transfer, "ssh_helper", return_value=helper), patch.object(transfer.subprocess, "Popen", return_value=process) as launch, patch.object(transfer, "emit"):
                transfer.transfer(SimpleNamespace(mode="upload", source=str(path), destination=None))
            self.assertEqual(input_stream.sent, data[offset:])
            self.assertNotIn("text", launch.call_args.kwargs)
            self.assertNotIn("encoding", launch.call_args.kwargs)
            self.assertNotIn("shell", launch.call_args.kwargs)
            self.assertIn("StrictHostKeyChecking=yes", launch.call_args.args[0])


@unittest.skipUnless(os.name == "posix", "Linux remote protocol")
class RemoteProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        os.chmod(self.root, 0o700)
        self.data = bytes(range(256)) * 4096
        self.request = {"mode": "upload", "destination": "images-unit.tar.gz", "size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}
        self.code = transfer.REMOTE.split("if __name__ == '__main__':", 1)[0] + "\ntarget_directory = lambda relative, create: Path(sys.argv[2])\nmain()\n"

    def run_remote(self, content, request=None):
        payload = base64.b64encode(json.dumps(request or self.request).encode()).decode()
        return subprocess.run([sys.executable, "-c", self.code, payload, str(self.root)], input=content, capture_output=True, timeout=30)

    def test_interruption_resume_atomic_promotion_and_idempotency(self):
        split = 438719
        interrupted = self.run_remote(self.data[:split])
        self.assertNotEqual(interrupted.returncode, 0)
        partial = self.root / "images-unit.tar.gz.partial"
        target = self.root / "images-unit.tar.gz"
        self.assertEqual(partial.read_bytes(), self.data[:split])
        self.assertFalse(target.exists())
        resumed = self.run_remote(self.data[split:])
        self.assertEqual(resumed.returncode, 0, resumed.stderr.decode())
        first, last = [json.loads(line) for line in resumed.stdout.splitlines()]
        self.assertEqual(first["offset"], split)
        self.assertEqual(last["sha256"], self.request["sha256"])
        self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(partial.exists())
        skipped = self.run_remote(b"")
        self.assertEqual(skipped.returncode, 0, skipped.stderr.decode())
        self.assertEqual(json.loads(skipped.stdout)["status"], "complete")

    def test_existing_different_file_is_unchanged(self):
        target = self.root / "images-unit.tar.gz"
        target.write_bytes(b"existing")
        os.chmod(target, 0o600)
        result = self.run_remote(self.data)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), b"existing")

    def test_remote_symlink_and_hardlink_rejected(self):
        external = self.root / "external"
        external.write_bytes(b"existing")
        os.chmod(external, 0o600)
        target = self.root / "images-unit.tar.gz"
        target.symlink_to(external)
        self.assertNotEqual(self.run_remote(self.data).returncode, 0)
        target.unlink()
        os.link(external, target)
        self.assertNotEqual(self.run_remote(self.data).returncode, 0)
        self.assertEqual(external.read_bytes(), b"existing")

    def test_corrupt_payload_never_promoted(self):
        result = self.run_remote(b"x" * len(self.data))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "images-unit.tar.gz").exists())
        self.assertTrue((self.root / "images-unit.tar.gz.partial").exists())

    def test_probe_creates_no_files(self):
        result = self.run_remote(b"", {**self.request, "mode": "probe"})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_partial_for_other_digest_rejected(self):
        self.run_remote(self.data[:100])
        result = self.run_remote(self.data, {**self.request, "sha256": "f" * 64})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "images-unit.tar.gz.partial").read_bytes(), self.data[:100])


if __name__ == "__main__":
    unittest.main()
