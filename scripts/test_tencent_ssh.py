import base64
import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("tencent_ssh", Path(__file__).with_name("tencent-ssh.py"))
ssh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssh)


class HostTrustTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.known = Path(self.directory.name) / "known_hosts"
        self.values = {"SSH_HOST": "193.112.17.218", "SSH_PORT": "22", "SSH_USER": "root", "SSH_IDENTITY_FILE": "/private/.env"}
        self.blob = b"synthetic-public-host-key"
        self.line = "193.112.17.218 ssh-ed25519 " + base64.b64encode(self.blob).decode() + "\n"
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(self.blob).digest()).decode().rstrip("=")
        self.patcher = patch.object(ssh, "KNOWN", self.known)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def scan(self, output=None, code=0):
        return patch.object(ssh.subprocess, "run", return_value=subprocess.CompletedProcess([], code, self.line if output is None else output, ""))

    def test_console_fingerprint_required(self):
        with self.assertRaisesRegex(RuntimeError, "CONSOLE_HOST_FINGERPRINT_REQUIRED"):
            ssh.pin(self.values, "REPLACE_WITH_FINGERPRINT")
        self.assertFalse(self.known.exists())

    def test_mismatched_key_does_not_create_trust(self):
        with self.scan(), self.assertRaisesRegex(RuntimeError, "HOST_KEY_FINGERPRINT_MISMATCH"):
            ssh.pin(self.values, "SHA256:" + "A" * 43)
        self.assertFalse(self.known.exists())

    def test_pinning_is_idempotent(self):
        with self.scan():
            self.assertTrue(ssh.pin(self.values, self.fingerprint)["host_key_pinned"])
            self.assertTrue(ssh.pin(self.values, self.fingerprint)["host_key_pinned"])
        self.assertEqual(self.known.read_text(), self.line)

    def test_existing_different_key_is_preserved(self):
        self.known.write_text("previous-host-key\n")
        with self.scan(), self.assertRaisesRegex(RuntimeError, "REFUSE_CHANGED_HOST_KEY"):
            ssh.pin(self.values, self.fingerprint)
        self.assertEqual(self.known.read_text(), "previous-host-key\n")

    def test_unreachable_host_does_not_create_trust(self):
        with self.scan("", 1), self.assertRaisesRegex(RuntimeError, "SSH_HOST_KEY_NOT_AVAILABLE"):
            ssh.pin(self.values, self.fingerprint)
        self.assertFalse(self.known.exists())

    def test_different_address_rejected(self):
        with self.scan(self.line.replace("193.112.17.218", "127.0.0.1")), self.assertRaisesRegex(RuntimeError, "HOST_KEY_ADDRESS_MISMATCH"):
            ssh.pin(self.values, self.fingerprint)

    def test_connection_requires_prior_verified_key(self):
        with self.assertRaisesRegex(RuntimeError, "VERIFIED_HOST_KEY_REQUIRED"):
            ssh.ssh_argv(self.values)

    def test_connection_keeps_strict_host_verification(self):
        self.known.write_text(self.line)
        args = ssh.ssh_argv(self.values)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn("UpdateHostKeys=no", args)


if __name__ == "__main__":
    unittest.main()
