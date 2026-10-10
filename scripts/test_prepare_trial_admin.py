import copy
import datetime
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid


PATH = Path(__file__).with_name("prepare-trial-admin.py")
SPEC = importlib.util.spec_from_file_location("prepare_trial_admin", PATH)
TRIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAL)


def uid(number):
    return str(uuid.UUID(int=number))


def fixture():
    data = {key: [] for key in TRIAL.AUDIT.QUERIES}
    data["tenants"] = [{"id": value, "deleted_at": None} for value in sorted(TRIAL.TENANTS)]
    data["users"] = [{"id": uid(value), "deleted_at": None} for value in range(1, 4)]
    data["members"] = [{"id": 1, "user_id": uid(1), "tenant_id": 10000, "role": "owner", "status": "active", "deleted_at": None}]
    data["agents"] = [{"id": uid(100 + value), "tenant_id": 10000, "deleted_at": None} for value in range(11)]
    data["knowledge_bases"] = [{"id": TRIAL.SOURCE_KB, "tenant_id": 10000, "deleted_at": None}] + [{"id": uid(200 + value), "tenant_id": 10000, "deleted_at": None} for value in range(4)]
    data["knowledges"] = [{"id": uid(300 + value), "tenant_id": 10000, "knowledge_base_id": TRIAL.SOURCE_KB if value < 19 else uid(200), "parse_status": "completed", "deleted_at": None} for value in range(22)]
    data["data_sources"] = [{"id": uid(500), "tenant_id": 10000, "knowledge_base_id": TRIAL.SOURCE_KB}]
    data["partner_jobs"] = [{"partner_key": value, "tenant_id": 10000} for value in sorted(TRIAL.PARTNER_KEYS)]
    data["document_jobs"] = [{"id": uid(600 + value), "tenant_id": 10000} for value in range(50)]
    data["models"] = [{"id": "local-chat", "tenant_id": 10000}]
    data["storage_backends"] = [{"id": uid(700 + value), "tenant_id": 10000 + value} for value in range(5)]
    data["sandbox_policies"] = [{"id": uid(800 + value), "tenant_id": value} for value in (10000, 10003, 10004)]
    data["other_counts"] = [{"resource": "api_keys", "total": 0}]
    return {"project": TRIAL.PROJECT, "read_only": True, "data": data}


class FakeClient:
    def __init__(self, fail_login=False, identity_id=None):
        self.calls = []
        self.fail_login = fail_login
        self.new_id = identity_id or uid(999)

    def request(self, method, path, payload=None, token=None, accepted=(200,)):
        self.calls.append((method, path))
        if path.endswith("/users/create"):
            return {"user": {"id": self.new_id, "email": "new@example.invalid", "username": "新管理员", "can_access_all_tenants": False}}
        if path.endswith("/members?page_size=100"):
            return {"data": {"members": []}}
        if path.endswith("/auth/login"):
            if self.fail_login:
                raise TRIAL.TrialError("api_http_401")
            return {"token": "synthetic-access-token-long-value"}
        if path.endswith("/auth/me"):
            return {"data": {"user": {"id": self.new_id, "is_active": True, "is_system_admin": True, "can_access_all_tenants": False}, "tenant": {"id": 10000}, "memberships": [{"tenant_id": 10000, "role": "owner"}]}}
        return {}


class TrialAdminTests(unittest.TestCase):
    def setUp(self):
        self.baseline = fixture()
        self.data = self.baseline["data"]
        self.values = {"TRIAL_ADMIN_EMAIL": "new@example.invalid", "TRIAL_ADMIN_NAME": "新管理员", "TRIAL_ADMIN_PASSWORD": "Synthetic-only-123"}

    def test_fixed_source_and_nineteen_documents_are_preserved(self):
        data = TRIAL.validate_baseline(self.baseline)
        scope = TRIAL.retirement(data)
        self.assertNotIn(TRIAL.SOURCE_KB, scope["knowledge_bases"])
        self.assertNotIn(10000, scope["tenants"])
        self.assertEqual(len(scope["knowledges"]), 3)
        self.assertEqual(len(scope["storage_backends"]), 4)
        self.assertEqual(len(scope["sandbox_policies"]), 2)
        self.assertEqual(len(scope["document_jobs"]), 50)

    def test_unknown_workspace_blocks_plan(self):
        self.data["tenants"].append({"id": 90000})
        with self.assertRaisesRegex(TRIAL.TrialError, "unexpected_tenant_ids"):
            TRIAL.validate_baseline(self.baseline)

    def test_new_independent_api_key_blocks_retirement(self):
        self.data["other_counts"][0]["total"] = 1
        with self.assertRaisesRegex(TRIAL.TrialError, "unapproved_independent_resource"):
            TRIAL.validate_baseline(self.baseline)

    def test_source_pending_document_blocks_retirement(self):
        self.data["knowledges"][0]["parse_status"] = "processing"
        with self.assertRaisesRegex(TRIAL.TrialError, "source_nineteen_completed_required"):
            TRIAL.validate_baseline(self.baseline)

    def test_source_cannot_be_retired_as_different_tenant(self):
        self.data["knowledge_bases"][0]["tenant_id"] = 10003
        with self.assertRaisesRegex(TRIAL.TrialError, "source_nineteen_completed_required"):
            TRIAL.validate_baseline(self.baseline)

    def test_added_changed_and_deleted_inventory_fail_closed(self):
        for mutate in (lambda d: d["agents"].append({"id": uid(9999)}), lambda d: d["agents"][0].update(tenant_id=10004), lambda d: d["sessions"].append({"id": uid(888)})):
            changed = copy.deepcopy(self.data)
            mutate(changed)
            with self.assertRaisesRegex(TRIAL.TrialError, "inventory_changed_requires_review"):
                TRIAL.compare_inventory(changed, self.data)

    def test_resume_only_allows_exact_new_owner_membership(self):
        current = copy.deepcopy(self.data)
        current["users"].append({"id": uid(999)})
        current["members"].append({"id": 99, "user_id": uid(999), "tenant_id": 10000, "role": "owner", "status": "active", "deleted_at": None})
        TRIAL.compare_inventory(current, self.data, uid(999))
        current["members"][-1]["tenant_id"] = 10003
        with self.assertRaisesRegex(TRIAL.TrialError, "unexpected_new_admin_membership"):
            TRIAL.compare_inventory(current, self.data, uid(999))

    def test_login_must_succeed_before_retirement_checkpoint(self):
        stages = []
        client = FakeClient(fail_login=True)
        with self.assertRaisesRegex(TRIAL.TrialError, "api_http_401"):
            TRIAL.create_and_verify_admin(client, self.values, [uid(1)], lambda stage, new_id: stages.append(stage))
        self.assertEqual(stages, ["created"])
        self.assertNotIn("new_admin_verified", stages)
        self.assertFalse(any("reset-password" in path or method == "DELETE" for method, path in client.calls))

    def test_verified_admin_uses_existing_apis_without_registration(self):
        stages = []
        client = FakeClient()
        new_id, token = TRIAL.create_and_verify_admin(client, self.values, [uid(1)], lambda stage, actor: stages.append(stage))
        self.assertEqual(new_id, uid(999))
        self.assertTrue(token)
        self.assertEqual(stages, ["created", "new_admin_verified"])
        paths = [path for method, path in client.calls]
        self.assertNotIn("/api/v1/auth/register", paths)
        self.assertLess(paths.index("/api/v1/tenants/10000/members"), paths.index("/api/v1/auth/login"))
        self.assertIn("/api/v1/system/admin/list", paths)

    def test_existing_retirement_target_cannot_be_new_admin(self):
        client = FakeClient(identity_id=uid(1))
        with self.assertRaisesRegex(TRIAL.TrialError, "created_admin_identity_invalid"):
            TRIAL.create_and_verify_admin(client, self.values, [uid(1)], lambda *_: self.fail("unexpected checkpoint"))
        self.assertEqual(len(client.calls), 1)

    def test_sql_guards_run_before_destructive_changes(self):
        sql = TRIAL.retirement_sql(self.data, uid(999))
        self.assertLess(sql.index("LOCK TABLE"), sql.index("inventory_changed"))
        self.assertLess(sql.index("inventory_changed"), sql.index("DELETE FROM"))
        self.assertLess(sql.index("new_admin_not_verified"), sql.index("UPDATE auth_tokens"))
        self.assertLess(sql.index("protected_source_changed"), sql.index("COMMIT;"))
        self.assertIn("UPDATE auth_tokens SET is_revoked=true WHERE user_id IN (", sql)
        self.assertNotIn("DELETE FROM users", sql)
        self.assertNotIn("DELETE FROM resources", sql)
        self.assertNotIn("UPDATE sessions", sql)
        self.assertNotIn("UPDATE models", sql)
        self.assertNotIn("UPDATE data_sources", sql)
        self.assertNotIn("password", sql.lower())

    def test_new_admin_cannot_be_in_sql_retirement_set(self):
        with self.assertRaisesRegex(TRIAL.TrialError, "cannot_retire_new_admin"):
            TRIAL.retirement_sql(self.data, uid(1))

    def test_untrusted_titles_cannot_escape_sql_or_procedural_block(self):
        self.data["agents"][0]["name"] = "name'); DELETE FROM users; --"
        sql = TRIAL.retirement_sql(self.data, uid(999))
        self.assertIn("name''); DELETE FROM users; --", sql)
        self.data["agents"][0]["name"] = "$trial$; DELETE FROM users;"
        with self.assertRaisesRegex(TRIAL.TrialError, "unsupported_inventory_delimiter"):
            TRIAL.retirement_sql(self.data, uid(999))

    def test_dry_run_client_rejects_all_mutations(self):
        client = TRIAL.Client("synthetic-access-token-long-value")
        with mock.patch.object(client.opener, "open") as opened:
            with self.assertRaisesRegex(TRIAL.TrialError, "apply_required"):
                client.request("POST", "/api/v1/system/admin/users/create", {})
            opened.assert_not_called()

    def test_only_fixed_loopback_origin_is_accepted(self):
        for address in ("https://example.invalid", "http://localhost:8080", "http://127.0.0.1:8080@evil.invalid", "http://127.0.0.1:8088"):
            with self.assertRaisesRegex(TRIAL.TrialError, "fixed_local_api_required"):
                TRIAL.Client("synthetic-access-token-long-value", base=address)

    def test_remote_docker_is_rejected_before_any_command(self):
        with mock.patch.dict(TRIAL.os.environ, {"DOCKER_HOST": "tcp://example.invalid:2376"}), mock.patch.object(TRIAL, "checked_run") as run:
            with self.assertRaisesRegex(TRIAL.TrialError, "explicit_docker_host_forbidden"):
                TRIAL.local_containers()
            run.assert_not_called()

    def test_backup_requires_actual_archive_matching_restore_and_current_source(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        source = {name: "a" * 64 for name in TRIAL.SERVICES}
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.age"
            archive.write_bytes(b"synthetic encrypted archive fixture")
            receipt = {"format": "boyankb-trial-backup-receipt-v1", "status": "verified", "profile": "local-renderer-v1", "source_project": TRIAL.PROJECT, "consistency": "application-quiesced", "writers_resumed": True, "source_ids": source, "service_names": sorted(TRIAL.SERVICES), "created_at": now.isoformat(), "database_fingerprint": "b" * 64, "archive_sha256": TRIAL.file_digest(archive), "archive_size": archive.stat().st_size}
            restored = {"format": "boyankb-trial-restore-report-v1", "status": "verified", "source_project": TRIAL.PROJECT, "target_project": "boyankb-restore-trial-offline", "created_at": now.isoformat(), "database_verified": True, "files_verified": True, "redis_verified": True, "environment_verified": True, "runtime_assets_verified": True, "application_started": False, "archive_sha256": receipt["archive_sha256"], "archive_size": receipt["archive_size"]}
            self.assertEqual(TRIAL.backup_gate(receipt, restored, archive, source, now), receipt["archive_sha256"])
            changed = dict(source, app="c" * 64)
            with self.assertRaisesRegex(TRIAL.TrialError, "backup_source_ids_mismatch"):
                TRIAL.backup_gate(receipt, restored, archive, changed, now)
            for key in ("files_verified", "redis_verified", "database_verified", "runtime_assets_verified", "environment_verified"):
                with self.subTest(key=key), self.assertRaisesRegex(TRIAL.TrialError, "restore_components_not_verified"):
                    TRIAL.backup_gate(receipt, dict(restored, **{key: False}), archive, source, now)
            with self.assertRaisesRegex(TRIAL.TrialError, "backup_or_restore_expired"):
                TRIAL.backup_gate(receipt, restored, archive, source, now + datetime.timedelta(hours=25))
            archive.write_bytes(b"altered archive")
            with self.assertRaisesRegex(TRIAL.TrialError, "backup_archive_mismatch"):
                TRIAL.backup_gate(receipt, restored, archive, source, now)

    def test_password_does_not_enter_review_plan(self):
        source = {name: "a" * 64 for name in TRIAL.SERVICES}
        plan = TRIAL.make_plan(self.data, source, "b" * 64, TRIAL.identity_hash(self.values), uid(1))
        encoded = json.dumps(plan, ensure_ascii=False)
        for secret in self.values.values():
            self.assertNotIn(secret, encoded)
        self.assertFalse(plan["reinstall_presets"])

    def test_empty_review_digest_cannot_disable_file_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.json"
            source.write_text("{}", encoding="utf-8")
            for value in ("", "0" * 64, "not-a-hash"):
                with self.subTest(value=value), self.assertRaisesRegex(TRIAL.TrialError, "file_digest_mismatch"):
                    TRIAL.load_json(source, value)

    def test_existing_or_external_report_is_rejected_before_writes(self):
        with self.assertRaisesRegex(TRIAL.TrialError, "report_path_outside_trial"):
            TRIAL.report_path(TRIAL.ROOT / "docs/report.json")
        with mock.patch.object(Path, "exists", return_value=True):
            with self.assertRaisesRegex(TRIAL.TrialError, "report_already_exists"):
                TRIAL.report_path(TRIAL.ROOT / "temp/enterprise-trial/report.json")

    def test_http_errors_cannot_print_response_body(self):
        client = TRIAL.Client("synthetic-access-token-long-value")
        failure = TRIAL.urllib.error.HTTPError("http://127.0.0.1:8080", 403, "SYNTHETIC_SECRET", {}, io.BytesIO(b"SYNTHETIC_SECRET"))
        with mock.patch.object(client.opener, "open", side_effect=failure):
            with self.assertRaises(TRIAL.TrialError) as caught:
                client.request("GET", "/api/v1/auth/me")
        self.assertEqual(str(caught.exception), "api_http_403")


if __name__ == "__main__":
    unittest.main()
