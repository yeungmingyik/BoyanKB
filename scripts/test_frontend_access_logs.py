import importlib.util
import pathlib
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("access_log_probe", ROOT / "scripts/check-frontend-access-logs.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class AccessLogTests(unittest.TestCase):
    def test_format_has_only_safe_path_and_no_query_referrer_or_user_agent(self):
        text = (ROOT / "frontend/nginx-http.conf").read_text(encoding="utf-8")
        variables = set(re.findall(r"\$([a-zA-Z0-9_]+)", text))
        self.assertEqual(variables, {"remote_addr", "remote_user", "time_local", "request_method", "uri", "safe_access_path", "server_protocol", "status", "body_bytes_sent"})
        self.assertRegex(text, r"log_format\s+api_no_query\s+")
        self.assertIn('"$request_method $safe_access_path $server_protocol"', text)

    def test_resource_capability_is_redacted_only_in_logging(self):
        text = (ROOT / "frontend/nginx-http.conf").read_text(encoding="utf-8")
        self.assertRegex(text, r"map\s+\$uri\s+\$safe_access_path\s*\{\s*default\s+\$uri;\s*~\^/r/\s+/r/\[redacted\];\s*\}")
        server = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
        route = re.search(r"location\s+\^~\s+/r/\s*\{([\s\S]+?)\n\s*\}", server)[1]
        self.assertIn("proxy_pass ${APP_SCHEME}://${APP_HOST}:${APP_PORT};", route)
        self.assertNotIn("safe_access_path", route)
        self.assertNotIn("rewrite", route)
        self.assertNotIn("return", route)

    def test_all_enabled_access_logs_use_safe_format(self):
        for filename in ("nginx.conf", "nginx-api-proxy.conf"):
            text = (ROOT / "frontend" / filename).read_text(encoding="utf-8")
            active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            for directive in re.findall(r"\baccess_log\s+([^;]+);", active):
                self.assertIn(directive.strip(), ("off", "/var/log/nginx/access.log api_no_query"))
        self.assertIn("access_log /var/log/nginx/access.log api_no_query;", (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8"))

    def test_server_error_logs_do_not_emit_unsanitized_request_context(self):
        text = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
        active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(re.findall(r"\berror_log\s+([^;]+);", active), ["/dev/null crit"])

    def test_both_frontend_images_install_the_http_format(self):
        for filename in ("frontend/Dockerfile", "docker/Dockerfile.frontend-boyankb"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            self.assertRegex(text, r"COPY (?:frontend/)?nginx-http.conf /etc/nginx/conf.d/00-weknora-http.conf")

    def test_live_probe_requires_redacted_shortlink_and_rejects_any_canary_leak(self):
        expected = (("GET", "/register", 200), ("HEAD", re.escape("/r/[redacted]"), 404))
        safe = '127.0.0.1 - - [fixture] "GET /register HTTP/1.1" 200 100\n127.0.0.1 - - [fixture] "HEAD /r/[redacted] HTTP/1.1" 404 0\n'
        probe.verify_logs(safe, "fixture-secret", expected)
        for bad in (safe.replace("[redacted]", "fixture-secret"), safe + '"fixture-secret"', safe + 'https://example.invalid/?token=fixture-secret', safe.replace("/register", "/register?token=fixture-secret"), safe.replace("/r/[redacted]", "/index.html"), safe.replace('HTTP/1.1" 404', 'HTTP/1.1" 200')):
            with self.subTest(log=bad), self.assertRaises(ValueError):
                probe.verify_logs(bad, "fixture-secret", expected)

    def test_upstream_probe_isolated_and_stopped_after_a_failed_check(self):
        calls = []
        container = "a" * 64

        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1] == "run":
                return SimpleNamespace(stdout=container.encode(), returncode=0)
            if "nc" in argv:
                return SimpleNamespace(stdout=b"HTTP/1.1 200 OK\r\n", returncode=0)
            return SimpleNamespace(stdout=b"", returncode=0)

        with patch.object(probe.subprocess, "run", side_effect=run), self.assertRaises(ValueError):
            probe.check_upstream_failure("sha256:" + "b" * 64)
        self.assertEqual(calls[0][calls[0].index("--network") + 1], "none")
        self.assertNotIn("--publish", calls[0])
        self.assertNotIn("--mount", calls[0])
        self.assertEqual(calls[-1], ["docker", "stop", "--time", "5", container])


if __name__ == "__main__":
    unittest.main()
