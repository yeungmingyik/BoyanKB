import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("onboard_partner", Path(__file__).with_name("onboard-partner.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def manifest():
    return {
        "schema_version": 1, "partner_key": "synthetic-partner", "name": "合成伙伴",
        "hq_tenant_id": 10000, "hq_knowledge_base_ids": ["d9285ed1-0760-474b-ba82-a13347598176"],
        "preset": "presets/boyan/v1/manifest.json",
        "models": {"chat": "local-deepseek-chat", "embedding": "local-embedding"},
        "members": [{"email": "contributor@example.test", "role": "contributor"}, {"email": "viewer@example.test", "role": "viewer"}],
        "contributor_email": "contributor@example.test",
    }


def plan_for(value=None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(value or manifest(), ensure_ascii=False), encoding="utf-8")
        return MODULE.load_plan(path)


class FakeClient:
    def __init__(self, plan):
        self.plan = plan
        self.job = None
        self.calls = []
        self.org = None
        self.org_members = [{"tenant_id": 10000, "role": "admin"}]
        self.members = []
        self.catalog = {}
        self.agents = []
        self.kb = None
        self.crash_after_agent_create = False

    def request(self, method, path, payload=None, tenant=None, operation="api", **kwargs):
        self.calls.append((method, path))
        path = path.split("?", 1)[0]
        result = None
        if path == "/api/v1/auth/me":
            result = {"user": {"id": "operator", "is_system_admin": True, "can_access_all_tenants": False}}
        elif path == "/api/v1/tenants/10000/members":
            result = {"members": [{"user_id": "operator", "role": "owner", "status": "active"}]}
        elif path.startswith("/api/v1/models/"):
            model_id = path.rsplit("/", 1)[1]
            result = {"id": model_id, "type": "Embedding" if model_id == "local-embedding" else "KnowledgeQA", "status": "active"}
        elif path == MODULE.PREFIX:
            if self.job is None:
                self.job = {"partner_key": payload["partner_key"], "spec_hash": payload["spec_hash"], "tenant_id": payload.get("target_tenant_id", 10001), "owner_user_id": "operator", "status": "provisioning", "state": {}, "revision": 1}
            else:
                MODULE.require(self.job["spec_hash"] == payload["spec_hash"], "spec_changed")
            result = self.job
        elif path.startswith(MODULE.PREFIX + "/"):
            if path.endswith("/lease"):
                result = self.job
            elif path.endswith("/knowledge-creator"):
                self.kb["creator_id"] = payload["user_id"]
                self.job["revision"] += 1
                result = self.job
            elif path.endswith("/skills/install"):
                self.job["state"]["restricted_skills"] = {name: {"version": "boyan/v1", "sha256": skill["sha256"], "catalog_id": self.catalog[name]["id"], "catalog_sha256": skill["bundle_sha256"], "enabled": True} for name, skill in self.plan["skills"].items()}
                self.job["revision"] += 1
                result = self.job
            elif method == "PATCH":
                MODULE.require(payload["revision"] == self.job["revision"], "bad_revision")
                installed = self.job["state"].get("restricted_skills")
                self.job["state"] = copy.deepcopy(payload["state"])
                if installed:
                    self.job["state"]["restricted_skills"] = installed
                self.job["status"] = payload["status"]
                self.job["revision"] += 1
                result = self.job
            else:
                result = self.job
        elif path == "/api/v1/sandbox-configs/workspace-policy":
            return {"success": True, "workspace_scripts_disabled": True}
        elif path == "/api/v1/sandbox-configs":
            return {"data": [], "workspace_scripts_disabled": True}
        elif path == "/api/v1/tenants/10001/members":
            if method == "POST":
                self.members.append({**payload, "user_id": uuid.uuid4().hex, "status": "active"})
            result = {"members": self.members}
        elif path == "/api/v1/organizations":
            if method == "POST":
                self.org = {**payload, "id": uuid.uuid4().hex, "owner_tenant_id": 10000}
                result = self.org
            else:
                result = {"organizations": [self.org] if self.org else []}
        elif path.startswith("/api/v1/organizations/"):
            if path.endswith("/members"):
                result = {"members": self.org_members}
            elif path.endswith("/invite"):
                self.org_members.append(payload)
                result = {}
            else:
                self.org.update(payload or {})
                result = self.org
        elif path.endswith("/shares"):
            result = {}
        elif path == "/api/v1/knowledge-bases":
            self.kb = {**payload, "tenant_id": tenant, "creator_id": "operator"}
            result = self.kb
        elif path.startswith("/api/v1/knowledge-bases/"):
            if path.rsplit("/", 1)[1] in self.plan["manifest"]["hq_knowledge_base_ids"] and tenant == 10000:
                result = {"id": path.rsplit("/", 1)[1], "tenant_id": 10000, "embedding_model_id": "local-embedding"}
            elif self.kb:
                result = self.kb
            else:
                raise MODULE.HTTPFailure(404, operation)
        elif path == "/api/v1/skills/catalog":
            if method == "POST":
                name = next(name for name in self.plan["skills"] if (name + ".zip").encode() in kwargs["raw"])
                self.catalog[name] = {"id": self.catalog.get(name, {}).get("id", uuid.uuid4().hex), "name": name, "bundle_sha256": self.plan["skills"][name]["bundle_sha256"]}
                result = self.catalog[name]
            else:
                result = list(self.catalog.values())
        elif path == "/api/v1/agents":
            if method == "POST":
                result = {**payload, "id": uuid.uuid4().hex, "created_by": "operator", "is_builtin": False}
                self.agents.append(copy.deepcopy(result))
                if self.crash_after_agent_create:
                    self.crash_after_agent_create = False
                    raise MODULE.OnboardError("agent_create:network_error_outcome_unknown")
            else:
                result = self.agents
        elif path.startswith("/api/v1/agents/"):
            result = next(agent for agent in self.agents if agent["id"] == path.rsplit("/", 1)[1])
            if method == "PUT":
                result.update(copy.deepcopy(payload))
        elif path == "/api/v1/shared-knowledge-bases":
            result = [{"knowledge_base": {"id": kb}, "permission": "viewer"} for kb in self.plan["manifest"]["hq_knowledge_base_ids"]]
        elif path == "/api/v1/business-workflow/readiness":
            result = {"ready": True, "template_integrity": True, "template_version": "boyan-v1", "restricted_skill_count": 9, "formats": ["docx", "pptx", "xlsx", "md"]}
        else:
            raise AssertionError((method, path))
        return {"success": True, "data": copy.deepcopy(result)}


class OnboardTests(unittest.TestCase):
    def headquarters_plan(self):
        value = manifest()
        value.update({"partner_key": "headquarters", "name": "总部业务", "target_tenant_id": 10000, "members": []})
        value.pop("contributor_email")
        return plan_for(value)

    def test_existing_headquarters_installs_without_members_shares_or_sandbox_writes(self):
        plan = self.headquarters_plan()
        client = FakeClient(plan)
        first = MODULE.Onboarder(client, plan).run()
        second = MODULE.Onboarder(client, plan).run()
        self.assertEqual(first["tenant_id"], 10000)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(len(client.agents), 3)
        self.assertEqual(len(client.catalog), 9)
        self.assertEqual(client.kb["creator_id"], "operator")
        self.assertIsNone(client.org)
        self.assertFalse(any("/organizations" in path or path.endswith("/shares") or path == "/api/v1/shared-knowledge-bases" for _, path in client.calls))
        self.assertFalse(any(method != "GET" and ("/members" in path or "/sandbox-configs" in path) for method, path in client.calls))

    def test_headquarters_mode_cannot_modify_members(self):
        value = manifest()
        value["target_tenant_id"] = 10000
        with self.assertRaisesRegex(MODULE.OnboardError, "headquarters_members_unchanged"):
            plan_for(value)
        value["members"] = []
        with self.assertRaisesRegex(MODULE.OnboardError, "headquarters_contributor_not_required"):
            plan_for(value)

    def test_target_identifier_is_a_positive_integer(self):
        for target in (None, 0, -1, True, "10000", 1.5):
            with self.subTest(target=target):
                value = manifest()
                value["target_tenant_id"] = target
                with self.assertRaisesRegex(MODULE.OnboardError, "target_tenant_id"):
                    plan_for(value)

    def test_existing_workspace_sandbox_must_be_disabled_before_any_creation(self):
        plan = self.headquarters_plan()
        client = FakeClient(plan)
        original = client.request
        def request(method, path, *args, **kwargs):
            if path == "/api/v1/sandbox-configs":
                return {"data": [], "workspace_scripts_disabled": False}
            return original(method, path, *args, **kwargs)
        with patch.object(client, "request", side_effect=request):
            with self.assertRaisesRegex(MODULE.OnboardError, "sandbox_policy_failed"):
                MODULE.Onboarder(client, plan).run()
        self.assertIsNone(client.job)
        self.assertFalse(any(method != "GET" for method, _ in client.calls))

    def test_existing_target_owner_required(self):
        value = manifest()
        value["target_tenant_id"] = 10001
        plan = plan_for(value)
        client = FakeClient(plan)
        client.members = [{"user_id": "operator", "role": "admin", "status": "active"}]
        with self.assertRaisesRegex(MODULE.OnboardError, "target_owner_required"):
            MODULE.Onboarder(client, plan).run()
        self.assertIsNone(client.job)

    def test_existing_custom_agent_is_not_overwritten_by_installation(self):
        plan = self.headquarters_plan()
        client = FakeClient(plan)
        custom = copy.deepcopy(plan["agents"]["mentor"])
        custom.update({"id": "existing-custom", "created_by": "operator", "is_builtin": False})
        custom["config"]["system_prompt"] = "用户自定义内容"
        client.agents.append(custom)
        with self.assertRaisesRegex(MODULE.OnboardError, "existing_agent_requires_review"):
            MODULE.Onboarder(client, plan).run()
        self.assertEqual(client.agents[0]["config"]["system_prompt"], "用户自定义内容")
        self.assertNotIn(("PUT", "/api/v1/agents/existing-custom"), client.calls)

    def test_existing_workspace_repeat_preserves_agent_metadata_and_rejects_config_drift(self):
        plan = self.headquarters_plan()
        client = FakeClient(plan)
        MODULE.Onboarder(client, plan).run()
        client.agents[0]["avatar"] = "用户头像"
        client.agents[0]["config"]["additional_default_field"] = ""
        MODULE.Onboarder(client, plan).run()
        self.assertEqual(client.agents[0]["avatar"], "用户头像")
        client.agents[0]["config"]["web_search_enabled"] = True
        with self.assertRaisesRegex(MODULE.OnboardError, "existing_agent_requires_review"):
            MODULE.Onboarder(client, plan).run()
        self.assertTrue(client.agents[0]["config"]["web_search_enabled"])
        self.assertFalse(any(method == "PUT" and path.startswith("/api/v1/agents/") for method, path in client.calls))

    def test_transport_accepts_https_and_exact_loopback_only(self):
        for origin in ("https://example.test", "http://127.0.0.1:8080"):
            with self.subTest(origin=origin):
                client = MODULE.Client(origin, None, {"ONBOARD_ADMIN_TOKEN": "test-token"})
                self.assertEqual(client.base, origin)
        for origin in ("http://example.test", "http://localhost:8080", "http://127.1:8080", "http://2130706433:8080", "http://127.0.0.1.example.test", "http://127.0.0.1@other.test", "https://example.test/api", "https://example.test?x=1", "http://127.0.0.1:8080#fragment"):
            with self.subTest(origin=origin), self.assertRaisesRegex(MODULE.OnboardError, "origin_required"):
                MODULE.Client(origin, None, {"ONBOARD_ADMIN_TOKEN": "test-token"})

    def test_transport_disables_environment_proxy_and_redirects(self):
        with patch.dict(MODULE.os.environ, {"HTTP_PROXY": "http://untrusted.test:3128", "HTTPS_PROXY": "http://untrusted.test:3128"}):
            client = MODULE.Client("http://127.0.0.1:8080", None, {"ONBOARD_ADMIN_TOKEN": "test-token"})
        self.assertFalse(any(isinstance(handler, MODULE.urllib.request.ProxyHandler) and handler.proxies for handler in client.opener.handlers))
        handler = next(handler for handler in client.opener.handlers if isinstance(handler, MODULE.NoRedirect))
        request = MODULE.urllib.request.Request("http://127.0.0.1:8080/api/v1/auth/me", headers={"Authorization": "Bearer test-token"})
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(request, None, code, "redirect", {}, "https://untrusted.test/receive"))

    def test_optional_rerank_requires_a_real_active_rerank_model(self):
        value = manifest()
        value["models"]["rerank"] = "not-a-rerank-model"
        plan = plan_for(value)
        client = FakeClient(plan)
        with self.assertRaisesRegex(MODULE.OnboardError, "model_binding_unavailable"):
            MODULE.Onboarder(client, plan).run()
        self.assertIsNone(client.job)

    def test_plan_loads_real_presets_without_network(self):
        plan = plan_for()
        self.assertEqual(len(plan["skills"]), 9)
        self.assertEqual(len(plan["agents"]), 3)
        self.assertIn("博研文化", plan["agents"]["planner"]["config"]["system_prompt"])

    def test_plan_rejects_owner_member(self):
        value = manifest()
        value["members"][0]["role"] = "owner"
        with self.assertRaisesRegex(MODULE.OnboardError, "partner_member_role"):
            plan_for(value)

    def test_plan_digest_stable_and_changes_on_member_scope(self):
        first = plan_for()
        self.assertEqual(first["spec_hash"], plan_for()["spec_hash"])
        value = manifest()
        value["members"][1]["role"] = "contributor"
        self.assertNotEqual(first["spec_hash"], plan_for(value)["spec_hash"])

    def test_archive_digest_is_deterministic(self):
        directory = MODULE.ROOT / "skills/boyan/docx"
        self.assertEqual(MODULE.deterministic_archive(directory), MODULE.deterministic_archive(directory))

    def test_complete_reconcile_and_repeat_keep_resource_ids(self):
        plan = plan_for()
        client = FakeClient(plan)
        first = MODULE.Onboarder(client, plan).run()
        second = MODULE.Onboarder(client, plan).run()
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(len(client.agents), 3)
        self.assertEqual(len(client.members), 2)
        self.assertEqual(len(client.org_members), 2)
        forbidden = ("/auth/register", "/api/v1/tenants", "/system/admin/settings")
        self.assertFalse(any(path in forbidden for _, path in client.calls))

    def test_crash_after_agent_creation_recovers_without_duplicate(self):
        plan = plan_for()
        client = FakeClient(plan)
        client.crash_after_agent_create = True
        with self.assertRaisesRegex(MODULE.OnboardError, "network_error_outcome_unknown"):
            MODULE.Onboarder(client, plan).run()
        self.assertEqual(client.job["status"], "blocked")
        self.assertEqual(len(client.agents), 1)
        result = MODULE.Onboarder(client, plan).run()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(client.agents), 3)

    def test_extra_partner_in_organization_is_rejected(self):
        plan = plan_for()
        client = FakeClient(plan)
        client.org_members.append({"tenant_id": 30000, "role": "viewer"})
        with self.assertRaisesRegex(MODULE.OnboardError, "unexpected_tenant"):
            MODULE.Onboarder(client, plan).run()

    def test_renderer_unavailable_does_not_mark_job_ready(self):
        plan = plan_for()
        client = FakeClient(plan)
        original = client.request
        def request(method, path, *args, **kwargs):
            if path.endswith("/readiness"):
                raise MODULE.HTTPFailure(503, "renderer_readiness")
            return original(method, path, *args, **kwargs)
        with patch.object(client, "request", side_effect=request):
            with self.assertRaisesRegex(MODULE.OnboardError, "readiness_timeout"):
                MODULE.Onboarder(client, plan, timeout=0).run()
        self.assertEqual(client.job["status"], "blocked")

    def test_existing_owner_is_not_silently_downgraded(self):
        plan = plan_for()
        client = FakeClient(plan)
        client.members = [{"email": "contributor@example.test", "role": "owner", "user_id": "wrong", "status": "active"}]
        with self.assertRaisesRegex(MODULE.OnboardError, "member_role_mismatch"):
            MODULE.Onboarder(client, plan).run()

    def test_plan_cli_does_not_read_credentials_or_call_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            output = io.StringIO()
            with patch.object(MODULE, "Client", side_effect=AssertionError("network forbidden")), patch.object(MODULE, "read_environment", side_effect=AssertionError("secrets forbidden")), redirect_stdout(output):
                code = MODULE.main(["--manifest", str(path), "--env-file", "unreadable.env"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["mode"], "plan")

    def test_secret_values_do_not_appear_in_http_error(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            with patch.object(MODULE, "read_environment", return_value={"ONBOARD_ADMIN_PASSWORD": "never-print-this-password"}), patch.object(MODULE, "Client", side_effect=MODULE.HTTPFailure(403, "login")), redirect_stderr(output):
                code = MODULE.main(["--manifest", str(path), "--apply", "--base-url", "https://example.test"])
        self.assertEqual(code, 2)
        self.assertNotIn("never-print", output.getvalue())
        self.assertIn("http_403", output.getvalue())


if __name__ == "__main__":
    unittest.main()
