import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("graph_backfill_cli_test_target", Path(__file__).with_name("backfill-enterprise-graph.py"))
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)
CHECK = CLI.CHECK
VALUES = {f"TRIAL_{actor}_{field}": f"PRIVATE_{actor}_{field}" for actor in ("ADMIN", "PARTNER") for field in ("EMAIL", "PASSWORD")}
APPLY = ["--apply", "--expected-plan-hash", "a" * 64]


class GraphClient:
    def __init__(self):
        self.calls = []
        self.rows = [{"id": f"00000000-0000-0000-0000-{index:012d}", "knowledge_base_id": CLI.KB_ID, "parse_status": "completed"} for index in (2, 1)]
        self.page = {"success": True, "data": self.rows, "total": len(self.rows)}
        self.plan = {"knowledge_base_id": CLI.KB_ID, "plan_hash": "a" * 64, "knowledge_count": len(self.rows), "chunk_count": 1,
                     "items": [{"task_id": "graph-task", "status": "not_submitted"}]}
        self.followup = None
        self.plan_status = 200
        self.plan_calls = 0

    def request(self, path, method="GET", payload=None, principal=None, **kwargs):
        self.calls.append({"path": path, "method": method, "payload": copy.deepcopy(payload), "principal": principal, **kwargs})
        if path == "/api/v1/auth/login":
            return 200, {"token": "PRIVATE_TOKEN"}
        if "/knowledge?" in path:
            return 200, copy.deepcopy(self.page)
        if "/graph-backfill" in path:
            if method == "GET":
                self.plan_calls += 1
                selected = self.followup if self.followup is not None and self.plan_calls > 1 else self.plan
                return self.plan_status, {"success": self.plan_status == 200, "data": copy.deepcopy(selected)}
            return 200, {"success": True, "data": copy.deepcopy(self.plan)}
        raise AssertionError("Unexpected request path")


class GraphBackfillCLITests(unittest.TestCase):
    def run_cli(self, client, args=()):
        output = io.StringIO()
        with patch.object(CLI.BASE, "Client", return_value=client), patch.object(CHECK, "credentials", return_value=VALUES), \
                patch("sys.argv", ["backfill-enterprise-graph.py", *args]), contextlib.redirect_stdout(output):
            result = CLI.main()
        self.assertNotIn("PRIVATE", output.getvalue())
        return result, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_default_plan_performs_no_business_write(self):
        client = GraphClient()
        result, output = self.run_cli(client)
        self.assertEqual(result, 0)
        self.assertEqual([row["event"] for row in output], ["graph_plan"])
        writes = [row for row in client.calls if row["method"] != "GET"]
        self.assertEqual([row["path"] for row in writes], ["/api/v1/auth/login"])
        self.assertTrue(all(row["principal"]["tenant_id"] == 10000 for row in client.calls[1:]))

    def test_apply_only_posts_reviewed_knowledge_ids_and_plan_hash(self):
        client = GraphClient()
        result, output = self.run_cli(client, APPLY)
        self.assertEqual(result, 0)
        writes = [row for row in client.calls if row["method"] != "GET" and row["path"] != "/api/v1/auth/login"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["path"], "/api/v1/knowledge-bases/" + CLI.KB_ID + "/graph-backfill")
        self.assertEqual(writes[0]["payload"], {"knowledge_ids": sorted(row["id"] for row in client.rows), "expected_plan_hash": client.plan["plan_hash"], "retry_failed": False})
        self.assertEqual(output[-1]["event"], "graph_submitted")

    def test_incomplete_or_empty_inventory_never_plans_or_submits(self):
        for total, rows in ((101, 2), (3, 2), (0, 0)):
            client = GraphClient()
            client.page["total"] = total
            client.page["data"] = client.rows[:rows]
            with self.subTest(total=total, rows=rows), self.assertRaises(CLI.BASE.AcceptanceError):
                self.run_cli(client, APPLY)
            self.assertFalse(any("graph-backfill" in row["path"] for row in client.calls))

    def test_missing_or_boolean_total_is_rejected(self):
        for total in (None, True):
            client = GraphClient()
            client.page["data"] = client.rows[:1]
            if total is None:
                client.page.pop("total")
            else:
                client.page["total"] = total
            with self.subTest(total=total), self.assertRaises(CLI.BASE.AcceptanceError):
                self.run_cli(client, APPLY)
            self.assertFalse(any("graph-backfill" in row["path"] for row in client.calls))

    def test_foreign_or_unfinished_knowledge_is_rejected_before_plan(self):
        for changes in ({"knowledge_base_id": "foreign"}, {"parse_status": "processing"}):
            client = GraphClient()
            client.rows[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(CLI.BASE.AcceptanceError):
                self.run_cli(client, APPLY)
            self.assertFalse(any("graph-backfill" in row["path"] for row in client.calls))

    def test_failed_plan_request_never_submits(self):
        client = GraphClient()
        client.plan_status = 409
        with self.assertRaises(CLI.BASE.AcceptanceError):
            self.run_cli(client, APPLY)
        self.assertFalse(any(row["method"] == "POST" and "graph-backfill" in row["path"] for row in client.calls))

    def test_retry_without_apply_rejects_before_credentials(self):
        credentials = Mock()
        with patch.object(CHECK, "credentials", credentials), patch("sys.argv", ["backfill-enterprise-graph.py", "--retry-failed"]):
            with self.assertRaises(CLI.BASE.AcceptanceError):
                CLI.main()
        credentials.assert_not_called()

    def test_wait_does_not_treat_changed_plan_as_submitted_work_completed(self):
        client = GraphClient()
        client.followup = {**copy.deepcopy(client.plan), "plan_hash": "b" * 64, "items": [{"task_id": "other-task", "status": "completed"}]}
        with patch.object(CLI.time, "sleep"), patch.object(CLI.time, "monotonic", side_effect=[0, 0, 0]):
            try:
                result, _ = self.run_cli(client, APPLY + ["--wait-seconds", "20"])
            except CLI.BASE.AcceptanceError as error:
                self.assertEqual(str(error), "graph_source_changed")
                self.assertEqual(client.plan_calls, 2)
                return
        self.assertNotEqual(result, 0)

    def test_apply_requires_prior_plan_hash_before_credentials(self):
        client = GraphClient()
        with self.assertRaisesRegex(CLI.BASE.AcceptanceError, "expected_plan_hash_required"):
            self.run_cli(client, ["--apply"])
        self.assertEqual(client.calls, [])

    def test_changed_expected_hash_never_submits(self):
        client = GraphClient()
        with self.assertRaisesRegex(CLI.BASE.AcceptanceError, "graph_source_changed"):
            self.run_cli(client, ["--apply", "--expected-plan-hash", "b" * 64])
        self.assertFalse(any(row["method"] == "POST" and "graph-backfill" in row["path"] for row in client.calls))

    def test_zero_extractable_chunks_never_reports_completion(self):
        client = GraphClient()
        client.plan["chunk_count"] = 0
        client.plan["items"] = []
        with self.assertRaisesRegex(CLI.BASE.AcceptanceError, "nothing_to_extract"):
            self.run_cli(client, APPLY + ["--wait-seconds", "20"])
        self.assertFalse(any(row["method"] == "POST" and "graph-backfill" in row["path"] for row in client.calls))


class CheckClient:
    def __init__(self, allow_viewer_write=False):
        self.calls = []
        self.opener = Mock()
        self.allow_viewer_write = allow_viewer_write

    def request(self, path, method="GET", payload=None, principal=None, tenant=None):
        self.calls.append({"path": path, "method": method, "payload": payload, "principal": principal, "tenant": tenant})
        if path == "/api/v1/auth/login":
            return 200, {"token": "PRIVATE_TOKEN"}
        viewer = principal["tenant_id"] == 10005
        if tenant == 10000 and viewer:
            return 403, {}
        if method == "POST":
            return (200 if self.allow_viewer_write else 403), {}
        if path == "/api/v1/skills/catalog":
            return 200, {"data": [{"id": "catalog-docx", "name": "boyan-docx"}] + [{"id": str(i), "name": "other" + str(i)} for i in range(8)]}
        if path.endswith("/files"):
            return (403 if viewer else 200), {}
        if path == "/api/v1/business-workflow/skills":
            return 200, {"data": {"available": True}}
        if path == "/api/v1/system/info":
            return 200, {"data": {"graph_database_engine": "Neo4j"}}
        if path.endswith("prompt-templates"):
            return 200, {"data": {key: [{"content": "中文提示词", "mode": "fixed"}] for key in ("system_prompt", "agent_system_prompt", "context_template", "rewrite", "fallback", "intent_prompts")}}
        raise AssertionError("Unexpected request path")


class SkillGraphCheckTests(unittest.TestCase):
    def test_public_checks_only_send_empty_viewer_negative_write(self):
        client = CheckClient()
        output = io.StringIO()
        with patch.object(CHECK.BASE, "Client", return_value=client), patch.object(CHECK, "credentials", return_value=VALUES), \
                patch("sys.argv", ["check-skill-graph.py", "--base-url", "https://kb.boyantech.xyz"]), contextlib.redirect_stdout(output):
            self.assertEqual(CHECK.main(), 0)
        writes = [row for row in client.calls if row["method"] != "GET" and row["path"] != "/api/v1/auth/login"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["principal"]["tenant_id"], 10005)
        self.assertEqual(writes[0]["path"], "/api/v1/skills/catalog")
        self.assertEqual(writes[0]["payload"], {})
        self.assertEqual(client.opener.addheaders, [("User-Agent", "BoyanKB-DeploymentCheck/1.0")])
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_viewer_write_unexpected_success_fails_acceptance(self):
        client = CheckClient(allow_viewer_write=True)
        with patch.object(CHECK.BASE, "Client", return_value=client), patch.object(CHECK, "credentials", return_value=VALUES), \
                patch("sys.argv", ["check-skill-graph.py"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CHECK.main(), 1)


if __name__ == "__main__":
    unittest.main()
