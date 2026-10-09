import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "check-sandbox-isolation.py"
checker = types.ModuleType("sandbox_checker")
exec(compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec"), checker.__dict__)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            context="sandbox-test",
            mode="offline-sandbox",
            container="a" * 64,
            tenant_id=123,
            config_id="01234567-89ab-cdef-0123-456789abcdef",
            image_id="sha256:" + "b" * 64,
            max_cpus=1,
            max_memory_mb=1024,
            max_pids=128,
            probe_authorized_test_container=False,
        )
        self.info = {
            "State": {"Running": True},
            "Image": self.args.image_id,
            "Mounts": [],
            "Config": {
                "Env": ["WEKNORA_SANDBOX_DOCKER_ENABLED=false"],
                "Labels": {
                    "com.weknora.sandbox.managed": "true",
                    "weknora_tenant_id": "123",
                    "weknora_sandbox_config_id": self.args.config_id,
                },
            },
            "NetworkSettings": {"Networks": {"none": {}}},
            "HostConfig": {
                "Privileged": False,
                "NetworkMode": "none",
                "Runtime": "runsc",
                "PidMode": "",
                "IpcMode": "private",
                "CgroupnsMode": "private",
                "NanoCpus": 1_000_000_000,
                "Memory": 1024 * 1024 * 1024,
                "MemorySwap": 1024 * 1024 * 1024,
                "PidsLimit": 128,
                "CapDrop": ["ALL"],
                "CapAdd": ["CHOWN", "SETUID"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Init": True,
            },
        }

    def test_app_baseline(self):
        self.assertTrue(all(checker.app_checks(self.info).values()))

    def test_app_runtime_socket_rejected(self):
        self.info["Mounts"] = [{"Source": "/var/run/docker.sock", "Destination": "/run/runtime"}]
        self.assertFalse(checker.app_checks(self.info)["no_runtime_mounts"])

    def test_app_runtime_certificate_directory_rejected(self):
        self.info["Mounts"] = [{"Source": "/certs", "Destination": "/run/sandbox-docker-tls"}]
        self.assertFalse(checker.app_checks(self.info)["no_runtime_mounts"])

    def test_app_enablement_and_runtime_environment_rejected(self):
        self.info["Config"]["Env"] = ["WEKNORA_SANDBOX_DOCKER_ENABLED=true", "DOCKER_HOST=tcp://example.invalid:2376"]
        checks = checker.app_checks(self.info)
        self.assertFalse(checks["docker_fallback_disabled"])
        self.assertFalse(checks["no_remote_docker_environment"])

    def test_offline_envelope(self):
        self.assertTrue(all(checker.sandbox_checks(self.info, self.args).values()))

    def test_resource_limits_rejected(self):
        for field, value in [("NanoCpus", 0), ("NanoCpus", 2_000_000_000), ("Memory", 0), ("MemorySwap", -1), ("PidsLimit", 0), ("PidsLimit", 129)]:
            with self.subTest(field=field, value=value):
                modified = copy.deepcopy(self.info)
                modified["HostConfig"][field] = value
                self.assertFalse(all(checker.sandbox_checks(modified, self.args).values()))

    def test_runtime_network_and_namespace_escape_rejected(self):
        for field, value in [("Runtime", "runc"), ("NetworkMode", "bridge"), ("Privileged", True), ("PidMode", "container:other"), ("IpcMode", "host"), ("UTSMode", "host"), ("CgroupnsMode", "host"), ("ExtraHosts", ["host.docker.internal:host-gateway"]), ("VolumesFrom", ["other:rw"]), ("DeviceCgroupRules", ["a *:* rwm"])]:
            with self.subTest(field=field):
                modified = copy.deepcopy(self.info)
                modified["HostConfig"][field] = value
                self.assertFalse(all(checker.sandbox_checks(modified, self.args).values()))

    def test_extra_network_rejected(self):
        self.info["NetworkSettings"]["Networks"]["business"] = {}
        self.assertFalse(checker.sandbox_checks(self.info, self.args)["offline_network"])

    def test_nnp_false_rejected(self):
        for value in ["no-new-privileges:false", "no-new-privileges=false"]:
            self.info["HostConfig"]["SecurityOpt"] = ["no-new-privileges:true", value]
            self.assertFalse(checker.sandbox_checks(self.info, self.args)["no_new_privileges"])

    def test_elevated_capability_rejected(self):
        self.info["HostConfig"]["CapAdd"].append("SYS_ADMIN")
        self.assertFalse(checker.sandbox_checks(self.info, self.args)["capabilities_restricted"])

    def test_identity_and_image_mismatch_rejected(self):
        self.info["Config"]["Labels"]["weknora_tenant_id"] = "999"
        self.info["Image"] = "sha256:" + "c" * 64
        checks = checker.sandbox_checks(self.info, self.args)
        self.assertFalse(checks["expected_workspace"])
        self.assertFalse(checks["expected_image"])

    def test_read_only_mode_never_executes(self):
        with patch.object(checker, "parse_args", return_value=self.args), patch.object(checker, "inspect_container", return_value=self.info), patch.object(checker, "docker") as docker, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(), 0)
            docker.assert_not_called()

    def test_failed_preflight_never_executes(self):
        self.args.probe_authorized_test_container = True
        self.info["HostConfig"]["NetworkMode"] = "bridge"
        with patch.object(checker, "parse_args", return_value=self.args), patch.object(checker, "inspect_container", return_value=self.info), patch.object(checker, "docker") as docker, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(), 1)
            docker.assert_not_called()

    def test_local_or_plaintext_context_never_executes(self):
        self.args.probe_authorized_test_container = True
        for host in ["unix:///var/run/docker.sock", "tcp://127.0.0.1:2376", "tcp://[::1]:2376", "tcp://sandbox.localhost:2376", "tcp://runtime.example.invalid:2375"]:
            with self.subTest(host=host), patch.object(checker, "parse_args", return_value=self.args), patch.object(checker, "inspect_container", return_value=self.info), patch.object(checker, "docker", return_value=[{"Endpoints": {"docker": {"Host": host}}}]) as docker:
                with self.assertRaises(checker.CheckError):
                    checker.main()
                self.assertEqual(docker.call_count, 1)

    def test_probe_runs_only_on_selected_container(self):
        self.args.probe_authorized_test_container = True
        keys = {"loopback_positive_control", "loopback_only", "no_ipv4_routes", "no_ipv6_external_routes", "metadata_ipv4", "private_10", "private_172", "private_192", "host_gateway", "public_ipv4", "metadata_ipv6", "private_ipv6", "link_local_ipv6", "mapped_metadata_ipv6", "public_ipv6", "no_docker_socket"}
        responses = [[{"Endpoints": {"docker": {"Host": "tcp://runtime.example.invalid:2376", "SkipTLSVerify": False}}, "TLSMaterial": {"docker": ["ca.pem", "cert.pem", "key.pem"]}}], dict.fromkeys(keys, True)]
        output = io.StringIO()
        with patch.object(checker, "parse_args", return_value=self.args), patch.object(checker, "inspect_container", return_value=self.info), patch.object(checker, "docker", side_effect=responses) as docker, contextlib.redirect_stdout(output):
            self.assertEqual(checker.main(), 0)
            self.assertEqual(docker.call_args.args[1][:3], ["exec", "-i", self.args.container])
            self.assertEqual(docker.call_args.kwargs["stdin"], checker.PROBE)
        result = json.loads(output.getvalue())
        self.assertEqual(result["scope"], "sandbox_create_envelope_and_network_probe")
        self.assertNotIn(self.args.container, output.getvalue())

    def test_docker_error_does_not_expose_output(self):
        result = subprocess.CompletedProcess([], 1, stdout="private-content", stderr="private-error")
        with patch.object(checker.subprocess, "run", return_value=result):
            with self.assertRaises(checker.CheckError) as caught:
                checker.docker("test", ["inspect"])
        self.assertEqual(str(caught.exception), "DOCKER_COMMAND_FAILED")

    def test_probe_and_office_syntax(self):
        compile(checker.PROBE, "probe", "exec")
        path = ROOT / "deployment" / "sandbox" / "office-smoke.py"
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_templates(self):
        directory = ROOT / "deployment" / "sandbox"
        config = json.loads((directory / "offline-config.example.json").read_text(encoding="utf-8"))["config"]
        self.assertEqual(config["docker"]["network_mode"], "none")
        self.assertEqual(config["docker"]["runtime"], "runsc")
        self.assertTrue(config["network"]["deny_egress_by_default"])
        daemon = json.loads((directory / "daemon.example.json").read_text(encoding="utf-8"))
        self.assertTrue(daemon["tlsverify"])
        self.assertEqual(daemon["default-runtime"], "runsc")


if __name__ == "__main__":
    unittest.main()
