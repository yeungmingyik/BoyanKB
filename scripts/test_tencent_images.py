import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("tencent_images", Path(__file__).with_name("tencent-images.py"))
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.enterContext(patch.object(images, "ROOT", self.root))
        self.enterContext(patch("builtins.print"))
        (self.root / "deployment/tencent").mkdir(parents=True)
        (self.root / "temp/tencent-cloud").mkdir(parents=True)
        self.entries = [{"tag": "fixture/service" + str(index) + ":v1", "id": "sha256:" + str(index) * 64, "digests": []} for index in range(1, 10)]
        (self.root / "deployment/tencent/images.lock.json").write_text(json.dumps({"images": self.entries}))
        self.actual = [{"Id": entry["id"], "Os": "linux", "Architecture": "amd64", "Size": 1000} for entry in self.entries]
        self.output = self.root / "temp/tencent-cloud/images-fixture.tar.gz"
        self.enterContext(patch.object(sys, "argv", ["tencent-images.py", "--output", str(self.output)]))

    def test_export_preserves_index_and_exact_locked_references(self):
        index = {"schemaVersion": 2, "manifests": [{"digest": entry["id"], "mediaType": "application/vnd.oci.image.index.v1+json", "annotations": {"io.containerd.image.name": entry["tag"]}} for entry in self.entries]}
        raw = io.BytesIO()
        data = json.dumps(index).encode()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            member = tarfile.TarInfo("index.json")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

        class Process:
            stdout = io.BytesIO(raw.getvalue())
            stderr = io.BytesIO()

            def wait(self):
                return 0

        with patch.object(images.subprocess, "check_output", return_value=json.dumps(self.actual).encode()) as inspect, patch.object(images.subprocess, "Popen", return_value=Process()) as save:
            images.main()
        references = [entry["tag"] for entry in self.entries]
        inspect.assert_called_once_with(["docker", "image", "inspect", *references])
        self.assertEqual(save.call_args.args[0], ["docker", "image", "save", *references])
        self.assertEqual(gzip.decompress(self.output.read_bytes()), raw.getvalue())
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(self.output.read_bytes()))) as archive:
            self.assertEqual(json.load(archive.extractfile("index.json")), index)
        receipt = json.loads(self.output.with_suffix(".json").read_bytes())
        self.assertEqual(receipt["images"], self.entries)
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_wrong_source_id_or_platform_never_exports(self):
        for field, value in (("Id", "sha256:" + "f" * 64), ("Os", "windows"), ("Architecture", "arm64")):
            actual = [dict(entry) for entry in self.actual]
            actual[0][field] = value
            with self.subTest(field=field), patch.object(images.subprocess, "check_output", return_value=json.dumps(actual).encode()), patch.object(images.subprocess, "Popen") as save, self.assertRaisesRegex(RuntimeError, "SOURCE_IMAGE_LOCK_OR_PLATFORM_MISMATCH"):
                images.main()
            save.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_existing_archive_never_inspects_or_overwrites(self):
        self.output.write_bytes(b"existing")
        with patch.object(images.subprocess, "check_output") as inspect, self.assertRaisesRegex(RuntimeError, "NEW_PRIVATE_IMAGE_ARCHIVE_REQUIRED"):
            images.main()
        inspect.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
