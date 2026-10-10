import contextlib
import copy
import io
import json
import pathlib
import runpy
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch


MODULE = runpy.run_path(str(pathlib.Path(__file__).with_name("feishu-space-sync.py")), run_name="test_module")
GLOBALS = MODULE["inventory"].__globals__
SPACE_ID = MODULE["SPACE_ID"]
SafeError = MODULE["SafeError"]
ApiError = MODULE["ApiError"]


class FakeScopes:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def request(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        return {"data": {"scopes": self.rows}}


class FakeTree:
    def __init__(self, changes=None, repeated_cursor=False):
        self.calls = []
        self.changes = changes or {}
        self.repeated_cursor = repeated_cursor

    def request(self, path):
        self.calls.append(path)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if not query.get("parent_node_token") and (not query.get("page_token") or self.repeated_cursor):
            return {"data": {"items": [], "has_more": True, "page_token": "next"}}
        parent = query.get("parent_node_token", [""])[0]
        node = {"node_token": "child" if parent else "root", "space_id": SPACE_ID, "origin_space_id": "",
                "node_type": "origin", "parent_node_token": parent, "obj_type": "docx", "obj_token": "document1",
                "has_child": not parent, "node_edit_time": "100", "obj_edit_time": "100", "title": "PRIVATE_TITLE"}
        node.update(self.changes)
        return {"data": {"items": [node], "has_more": False}}


class FakeAudit:
    def __init__(self, failure):
        self.failure = failure
        self.calls = []

    def request(self, path):
        self.calls.append(path)
        if path.startswith("/open-apis/wiki/v2/spaces/999/"):
            if self.failure is not None:
                raise self.failure
            return {"data": {"items": [], "has_more": False}}
        if path.startswith("/open-apis/wiki/v2/spaces?"):
            return {"data": {"items": [{"space_id": SPACE_ID, "name": "PRIVATE_SPACE"}], "has_more": False}}
        return {"data": {"items": [], "has_more": False}}


class FakeSearch:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def request(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        return {"success": True, "data": self.results}


class FakeDocumentAudit(FakeTree):
    def __init__(self, metadata_changes=None, export_result=True, export_error=None):
        super().__init__()
        self.metadata_changes = metadata_changes or {}
        self.export_result = export_result
        self.export_error = export_error

    def request(self, path):
        if path.startswith("/open-apis/wiki/v2/spaces/get_node?"):
            self.calls.append(path)
            node_id = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["token"][0]
            node = {"node_token": node_id, "space_id": SPACE_ID, "origin_space_id": SPACE_ID,
                    "node_type": "origin", "obj_type": "docx", "obj_token": "PRIVATE_OBJECT_" + node_id, "title": "PRIVATE_TITLE"}
            node.update(self.metadata_changes)
            return {"data": {"node": node}}
        if path.startswith("/open-apis/drive/v1/permissions/"):
            self.calls.append(path)
            action = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["action"][0]
            if action == "export":
                if self.export_error is not None:
                    raise self.export_error
                if self.export_result == "missing":
                    return {"data": {}}
                return {"data": {"auth_result": self.export_result}}
            return {"data": {"auth_result": True}}
        return super().request(path)


class FeishuSpaceSyncTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stdout.__enter__()
        MODULE["SECRET_VALUES"].clear()

    def tearDown(self):
        self.stdout.__exit__(None, None, None)
        self.assertNotIn("PRIVATE_TITLE", self.output.getvalue())
        self.assertNotIn("PRIVATE_SPACE", self.output.getvalue())

    def scopes(self, status=2):
        return [{"scope_name": scope, "grant_status": status} for scope in sorted(MODULE["REQUIRED_SCOPES"])]

    def managed_kb(self, name):
        return {"id": "kb1", "name": name, "description": MODULE["KB_MARKER"], "type": "document",
                "embedding_model_id": "local-embedding", "summary_model_id": "",
                "indexing_strategy": {"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": False}}

    def kb_client(self, kb):
        models = [{"id": "local-embedding", "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest"}]
        client = Mock()
        client.request.side_effect = [{"success": True, "data": value} for value in (models, [kb], kb)]
        return client

    def approved_graph(self):
        kb = self.managed_kb(MODULE["KB_NAME"])
        kb.update({"id": MODULE["ENTERPRISE_KB_ID"], "summary_model_id": "PRIVATE_MODEL_ID",
                   "extract_config": {"enabled": True, "text": "PRIVATE_EXTRACTION", "tags": ["course", "product"]}})
        kb["indexing_strategy"]["graph_enabled"] = True
        policy = {"version": 1, "space_id": SPACE_ID, "knowledge_base_id": kb["id"],
                  "summary_model_id": kb["summary_model_id"], "graph_enabled": True,
                  "extract_config": copy.deepcopy(kb["extract_config"])}
        return kb, policy

    def test_explicit_graph_policy_allows_exact_configuration_without_writes_or_config_output(self):
        kb, policy = self.approved_graph()
        for read_only in (False, True):
            client = self.kb_client(kb)
            self.assertEqual(MODULE["knowledge_base"](client, read_only=read_only, enrichment_policy=policy), kb)
            self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))
            report = MODULE["ingestion_policy"](kb, read_only, policy)
            self.assertTrue(report["enrichment_policy_matches"])
            self.assertTrue(report["sync_allowed"])
            self.assertEqual(len(report["extract_config_sha256"]), 64)
        self.assertNotIn("PRIVATE_MODEL_ID", self.output.getvalue())
        self.assertNotIn("PRIVATE_EXTRACTION", self.output.getvalue())
        reordered = copy.deepcopy(kb)
        reordered["extract_config"] = dict(reversed(list(kb["extract_config"].items())))
        self.assertTrue(MODULE["ingestion_policy"](reordered, True, policy)["enrichment_policy_matches"])

    def test_explicit_graph_policy_rejects_configuration_drift(self):
        for field in ("id", "summary_model_id", "graph_enabled", "text", "tags", "enabled", "unknown", "missing_extract"):
            for read_only in (False, True):
                kb, policy = self.approved_graph()
                if field in ("id", "summary_model_id"):
                    kb[field] = "different"
                elif field == "graph_enabled":
                    kb["indexing_strategy"][field] = False
                elif field == "missing_extract":
                    kb.pop("extract_config")
                else:
                    kb["extract_config"][field] = False if field == "enabled" else "changed"
                client = self.kb_client(kb)
                with self.subTest(field=field, read_only=read_only), self.assertRaisesRegex(SafeError, "differs"):
                    MODULE["knowledge_base"](client, read_only=read_only, enrichment_policy=policy)
                self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))
                report = MODULE["ingestion_policy"](kb, read_only, policy)
                self.assertFalse(report["enrichment_policy_matches"])
                self.assertFalse(report["sync_allowed"])

    def test_explicit_graph_policy_rejects_wrong_scope_or_schema_before_api(self):
        changes = ({"space_id": "other"}, {"knowledge_base_id": "other"}, {"version": True},
                   {"version": 2}, {"graph_enabled": False}, {"graph_enabled": 1}, {"summary_model_id": ""},
                   {"summary_model_id": "https://model.example"}, {"extract_config": {"enabled": True, "api_key": "no"}},
                   {"extract_config": {"enabled": False}}, {"extra": True})
        for change in changes:
            _, policy = self.approved_graph()
            policy.update(change)
            client = Mock()
            with self.subTest(change=change), self.assertRaises(SafeError):
                MODULE["knowledge_base"](client, enrichment_policy=policy)
            client.request.assert_not_called()

    def test_explicit_graph_policy_preserves_non_graph_guards(self):
        changes = [{"embedding_model_id": "remote"}, {"description": "other"},
                   {"vlm_config": {"model_name": "remote"}}, {"image_processing_config": {"model_id": "remote"}}]
        changes.extend({key: {"enabled": True}} for key in (
            "question_generation_config", "auto_tag_config", "profile_config", "vlm_config", "asr_config"))
        for change in changes:
            kb, policy = self.approved_graph()
            kb.update(change)
            client = self.kb_client(kb)
            with self.subTest(change=change), self.assertRaises(SafeError):
                MODULE["knowledge_base"](client, read_only=True, enrichment_policy=policy)
            self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))
        for key in ("wiki_enabled", "vector_enabled", "keyword_enabled"):
            kb, policy = self.approved_graph()
            kb["indexing_strategy"][key] = not kb["indexing_strategy"][key]
            with self.subTest(key=key), self.assertRaises(SafeError):
                MODULE["knowledge_base"](self.kb_client(kb), enrichment_policy=policy)

    def test_explicit_graph_policy_cannot_create_replacement_knowledge_base(self):
        _, policy = self.approved_graph()
        client = Mock()
        models = [{"id": "local-embedding", "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest"}]
        client.request.side_effect = [{"success": True, "data": models}, {"success": True, "data": []}]
        with self.assertRaisesRegex(SafeError, "cannot create"):
            MODULE["knowledge_base"](client, create=True, enrichment_policy=policy)
        self.assertEqual(client.request.call_count, 2)
        self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))

    def test_enrichment_policy_paths_and_json_are_strict(self):
        _, policy = self.approved_graph()
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder).resolve()
            (root / "temp").mkdir()
            valid = root / "temp" / "approved.json"
            valid.write_text(json.dumps(policy), encoding="utf-8")
            outside = root / "outside.json"
            outside.write_text(json.dumps(policy), encoding="utf-8")
            wrong_extension = root / "temp" / "policy.env"
            wrong_extension.write_text(json.dumps(policy), encoding="utf-8")
            oversized = root / "temp" / "oversized.json"
            oversized.write_text(" " * 131073, encoding="utf-8")
            duplicate = root / "temp" / "duplicate.json"
            duplicate.write_text('{"version":1,"version":1}', encoding="utf-8")
            with patch.dict(GLOBALS, {"REPO_ROOT": root}):
                self.assertEqual(MODULE["load_enrichment_policy"]("temp/approved.json"), policy)
                for path in (outside, wrong_extension, oversized, duplicate, root / "temp" / "missing.json", "temp/../outside.json", root / "temp"):
                    with self.subTest(path=str(path)), self.assertRaises(SafeError):
                        MODULE["load_enrichment_policy"](str(path))

    def test_invalid_policy_path_is_rejected_before_loading_credentials(self):
        settings = Mock()
        with patch.dict(GLOBALS, {"load_settings": settings}), patch("sys.argv", ["feishu-space-sync.py", "verify", "--enrichment-policy", "outside.json"]):
            with self.assertRaises(SafeError):
                MODULE["main"]()
        settings.assert_not_called()

    def test_snapshot_records_explicit_enrichment_approval_digest(self):
        kb, policy = self.approved_graph()
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder).resolve()
            (root / "temp").mkdir()
            output = root / "temp" / "snapshot.json"
            with patch.dict(GLOBALS, {"REPO_ROOT": root}):
                MODULE["write_snapshot"](output, kb, {"id": "source"}, {}, {}, enrichment_policy=policy)
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(snapshot["ingestion_policy"]["sync_allowed"])
            self.assertTrue(snapshot["ingestion_policy"]["enrichment_policy_matches"])
            self.assertNotIn("PRIVATE_MODEL_ID", output.read_text(encoding="utf-8"))
            self.assertNotIn("PRIVATE_EXTRACTION", output.read_text(encoding="utf-8"))

    def test_explicit_policy_sync_rejects_drift_before_datasource_and_preserves_verify_read_only(self):
        for verify, drift in ((False, True), (False, False), (True, False)):
            kb, policy = self.approved_graph()
            if drift:
                kb["extract_config"]["text"] = "changed"
            client = self.kb_client(kb)
            source = Mock(return_value={"id": "ds1"})
            sync = Mock(return_value={"items_total": 1})
            with self.subTest(verify=verify, drift=drift), patch.dict(GLOBALS, {
                "HttpClient": Mock(), "require_guard": Mock(), "FeishuClient": Mock(), "permissions": Mock(),
                "inventory": Mock(return_value={"root": {"object_type": "docx"}}), "login": Mock(return_value=client),
                "datasource": source, "documents": Mock(return_value={}), "await_documents": Mock(return_value={}), "await_sync": sync,
            }):
                if drift:
                    with self.assertRaises(SafeError):
                        MODULE["synchronize"]({}, enrichment_policy=policy)
                    source.assert_not_called()
                    sync.assert_not_called()
                else:
                    MODULE["synchronize"]({}, verify=verify, enrichment_policy=policy)
                    source.assert_called_once_with(client, kb, {}, create=not verify)
                    if verify:
                        sync.assert_not_called()
                    else:
                        sync.assert_called_once()

    def test_read_only_accepts_summary_configuration_without_allowing_sync(self):
        kb = self.managed_kb(MODULE["KB_NAME"])
        kb["summary_model_id"] = "PRIVATE_MODEL_ID"
        client = self.kb_client(kb)
        self.assertEqual(MODULE["knowledge_base"](client, read_only=True), kb)
        self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))
        policy = json.loads(self.output.getvalue().splitlines()[0])
        self.assertEqual(policy, {"event": "ingestion_policy", "validation_mode": "read_only",
                                  "summary_model_configured": True, "sync_allowed": False})
        self.assertNotIn("PRIVATE_MODEL_ID", self.output.getvalue())

    def test_sync_rejects_configured_summary_before_any_mutation(self):
        kb = self.managed_kb(MODULE["KB_NAME"])
        kb["summary_model_id"] = "PRIVATE_MODEL_ID"
        for create in (False, True):
            client = self.kb_client(kb)
            with self.subTest(create=create), self.assertRaisesRegex(SafeError, "Sync is blocked"):
                MODULE["knowledge_base"](client, create=create)
            self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))
        self.assertNotIn("PRIVATE_MODEL_ID", self.output.getvalue())

    def test_read_only_cannot_create_missing_resources(self):
        client = Mock()
        with self.assertRaises(SafeError):
            MODULE["knowledge_base"](client, create=True, read_only=True)
        client.request.assert_not_called()

    def test_read_only_retains_embedding_scope_and_enrichment_guards(self):
        cases = [{"embedding_model_id": "remote-embedding"}, {"description": "another-owner"},
                 {"name": "another-space"}, {"type": "faq"},
                 {"image_processing_config": {"model_id": "unexpected"}},
                 {"vlm_config": {"model_name": "unexpected"}}, {"vlm_config": {"base_url": "unexpected"}}]
        cases.extend({key: {"enabled": True}} for key in (
            "question_generation_config", "auto_tag_config", "profile_config", "vlm_config", "asr_config"))
        for key in ("vector_enabled", "keyword_enabled", "wiki_enabled", "graph_enabled"):
            strategy = self.managed_kb(MODULE["KB_NAME"])["indexing_strategy"].copy()
            strategy[key] = not strategy[key]
            cases.append({"indexing_strategy": strategy})
        for changes in cases:
            kb = self.managed_kb(MODULE["KB_NAME"])
            kb.update({"summary_model_id": "PRIVATE_MODEL_ID", **changes})
            client = self.kb_client(kb)
            with self.subTest(changes=changes), self.assertRaises(SafeError):
                MODULE["knowledge_base"](client, read_only=True)
            self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))

    def test_verify_does_not_sync_and_sync_rejects_summary_before_datasource(self):
        kb = self.managed_kb(MODULE["KB_NAME"])
        kb["summary_model_id"] = "PRIVATE_MODEL_ID"
        nodes = {"root": {"object_type": "docx"}}
        for verify in (False, True):
            client = self.kb_client(kb)
            ds = Mock(return_value={"id": "ds1"})
            sync = Mock()
            after = Mock(return_value={})
            with self.subTest(verify=verify), patch.dict(GLOBALS, {
                "HttpClient": Mock(), "require_guard": Mock(), "FeishuClient": Mock(),
                "permissions": Mock(), "inventory": Mock(return_value=nodes), "login": Mock(return_value=client),
                "datasource": ds, "documents": Mock(return_value={}), "await_sync": sync, "await_documents": after,
            }):
                if verify:
                    result = MODULE["synchronize"]({}, verify=True)
                    self.assertEqual(result[1], kb)
                    ds.assert_called_once_with(client, kb, {}, create=False)
                    after.assert_called_once()
                else:
                    with self.assertRaisesRegex(SafeError, "Sync is blocked"):
                        MODULE["synchronize"]({})
                    ds.assert_not_called()
                    after.assert_not_called()
                sync.assert_not_called()

    def test_managed_kb_accepts_old_and_new_names_without_recreating(self):
        for name in MODULE["KB_NAMES"]:
            kb = self.managed_kb(name)
            responses = [[{"id": "local-embedding", "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest"}], [kb], kb]
            client = Mock()
            client.request.side_effect = [{"success": True, "data": value} for value in responses]
            self.assertEqual(MODULE["knowledge_base"](client), kb)
            self.assertTrue(all(call.args[1] == "GET" for call in client.request.call_args_list))

    def test_managed_kb_refuses_conflicting_marker_unknown_name_and_duplicate_aliases(self):
        for changes in ({"description": "another-owner"}, {"name": "another-space"}):
            kb = self.managed_kb(MODULE["KB_NAME"])
            kb.update(changes)
            client = Mock()
            client.request.side_effect = [{"success": True, "data": value} for value in [[{"id": "local-embedding", "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest"}], [kb], kb]]
            with self.assertRaises(SafeError):
                MODULE["knowledge_base"](client)
        client = Mock()
        client.request.side_effect = [{"success": True, "data": value} for value in [[{"id": "local-embedding", "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest"}], [self.managed_kb(name) for name in MODULE["KB_NAMES"]]]]
        with self.assertRaises(SafeError):
            MODULE["knowledge_base"](client)
        self.assertEqual(client.request.call_count, 2)

    def test_new_kb_uses_business_name_and_preserves_ownership_marker(self):
        client = Mock()
        client.request.side_effect = [{"success": True, "data": value} for value in [[{"id": "local-embedding", "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest"}], [], self.managed_kb(MODULE["KB_NAME"])]]
        MODULE["knowledge_base"](client, create=True)
        path, method, body = client.request.call_args.args
        self.assertEqual((path, method), ("/api/v1/knowledge-bases", "POST"))
        self.assertEqual(body["name"], "合作伙伴专用空间")
        self.assertEqual(body["description"], "managed-feishu-sync:" + SPACE_ID)
        self.assertEqual(body["summary_model_id"], "")
        self.assertEqual(body["embedding_model_id"], "local-embedding")

    def test_datasource_name_aliases_do_not_relax_space_or_sync_guards(self):
        base = {"id": "ds1", "knowledge_base_id": "kb1", "name": MODULE["KB_NAME"], "type": "feishu", "status": "paused",
                "sync_mode": "incremental", "sync_schedule": "", "conflict_strategy": "overwrite", "sync_deletions": False,
                "config": {"type": "feishu", "resource_ids": [SPACE_ID], "settings": {"allowed_space_id": SPACE_ID}}}
        for name in MODULE["KB_NAMES"]:
            ds = {**base, "name": name}
            client = Mock()
            client.request.side_effect = [[ds], ds]
            self.assertEqual(MODULE["datasource"](client, {"id": "kb1"}, {}), ds)
            self.assertTrue(all(len(call.args) == 1 for call in client.request.call_args_list))
            for changed in ("name", "knowledge_base_id", "resource_ids", "allowed_space_id", "base_url", "sync_deletions", "status"):
                bad = copy.deepcopy(ds)
                if changed == "resource_ids":
                    bad["config"][changed] = [SPACE_ID, "other"]
                elif changed in {"allowed_space_id", "base_url"}:
                    bad["config"]["settings"][changed] = "other"
                else:
                    bad[changed] = True if changed == "sync_deletions" else "other"
                client = Mock()
                client.request.side_effect = [[bad], bad]
                with self.subTest(name=name, changed=changed), self.assertRaises(SafeError):
                    MODULE["datasource"](client, {"id": "kb1"}, {}, create=True)
                self.assertEqual(client.request.call_count, 2)

    def probe_state(self):
        nodes = {"root": {"node_edit_time": "200", "object_edit_time": "200"}}
        row = {"external_id": "root", "knowledge_id": "knowledge2", "parse_status": "completed", "file_hash": "2" * 64,
               "source_updated_at": "2026-10-08T02:00:00Z", "updated_at": "2026-10-08T02:01:00Z"}
        return nodes, {"root": row}

    def baseline(self, root):
        output = root / "temp" / "before.json"
        output.parent.mkdir(parents=True)
        output.write_text(json.dumps({"version": 1, "space_id": SPACE_ID, "knowledge_base_id": "kb1", "data_source_id": "ds1", "documents": [
            {"external_id": "root", "knowledge_id": "knowledge1", "file_hash": "1" * 64,
             "source_updated_at": "2026-10-08T01:00:00Z", "node_edit_time": "100", "object_edit_time": "100"},
        ]}), encoding="utf-8")
        return output

    def test_apply_has_no_body_and_only_declared_allowed_scopes(self):
        client = FakeScopes(self.scopes())
        MODULE["permissions"](client, apply=True)
        self.assertEqual(client.calls[-1], ("/open-apis/application/v6/scopes/apply", "POST", None))

    def test_apply_refuses_missing_extra_user_and_unknown_scopes(self):
        cases = [self.scopes()[:2], self.scopes() + [{"scope_name": "im:message", "grant_status": 2}],
                 [{**row, "scope_type": "user"} for row in self.scopes()],
                 [{**row, "grant_status": 9} for row in self.scopes()]]
        for rows in cases:
            with self.subTest(rows=rows):
                client = FakeScopes(rows)
                with self.assertRaises(SafeError):
                    MODULE["permissions"](client, apply=True)
                self.assertTrue(all(method == "GET" for path, method, body in client.calls))

    def test_already_granted_broader_download_scope_is_not_requested(self):
        rows = [row for row in self.scopes(1) if row["scope_name"] != "drive:file:download"]
        rows.append({"scope_name": "drive:file:readonly", "grant_status": 1})
        client = FakeScopes(rows)
        MODULE["permissions"](client, apply=True)
        self.assertEqual(len(client.calls), 1)

    def test_inventory_continues_empty_pages_and_recurses_only_target_space(self):
        client = FakeTree()
        nodes = MODULE["inventory"](client)
        self.assertEqual(set(nodes), {"root", "child"})
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(path.startswith("/open-apis/wiki/v2/spaces/" + SPACE_ID + "/nodes?") for path in client.calls))

    def test_inventory_refuses_shortcuts_cross_space_and_unknown_origin(self):
        for changes in ({"space_id": "other"}, {"space_id": ""}, {"origin_space_id": "other"},
                        {"node_type": "shortcut"}, {"node_type": ""}, {"obj_token": ""}):
            with self.subTest(changes=changes), self.assertRaises(SafeError):
                MODULE["inventory"](FakeTree(changes))

    def test_inventory_refuses_repeated_cursor(self):
        with self.assertRaises(SafeError):
            MODULE["inventory"](FakeTree(repeated_cursor=True))

    def test_negative_access_requires_explicit_denial_and_second_positive_probe(self):
        for failure in (ApiError("http", 403), ApiError("feishu", 131006), ApiError("http", 400, 131006)):
            with self.subTest(code=failure.code):
                client = FakeAudit(failure)
                MODULE["audit_access"](client, "999")
                self.assertEqual(sum(("/" + SPACE_ID + "/nodes?") in path for path in client.calls), 2)

    def test_negative_access_does_not_accept_missing_expired_network_or_success(self):
        for failure in (ApiError("http", 404), ApiError("http", 404, 131006), ApiError("feishu", 99991663), SafeError("Network failure"), None):
            with self.subTest(failure=type(failure).__name__), self.assertRaises(SafeError):
                MODULE["audit_access"](FakeAudit(failure), "999")

    def test_snapshot_stays_under_temp_refuses_overwrite_and_filters_fields(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(GLOBALS, {"REPO_ROOT": pathlib.Path(temporary).resolve()}):
            root = pathlib.Path(temporary).resolve()
            for path in ("outside.json", "temp/../outside.json", "temp/output.txt"):
                with self.subTest(path=path), self.assertRaises(SafeError):
                    MODULE["snapshot_path"](path, writing=True)
            nodes, rows = self.probe_state()
            rows["root"]["title"] = "PRIVATE_TITLE"
            rows["root"]["content"] = "PRIVATE_BODY"
            output = MODULE["snapshot_path"]("temp/before.json", writing=True)
            MODULE["write_snapshot"](output, {"id": "kb1"}, {"id": "ds1"}, nodes, rows)
            text = output.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE_TITLE", text)
            self.assertNotIn("PRIVATE_BODY", text)
            with self.assertRaises(SafeError):
                MODULE["write_snapshot"](output, {"id": "kb1"}, {"id": "ds1"}, nodes, rows)
            self.assertEqual(output.read_text(encoding="utf-8"), text)

    def test_snapshot_records_read_only_policy_without_model_configuration(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(GLOBALS, {"REPO_ROOT": pathlib.Path(temporary).resolve()}):
            nodes, rows = self.probe_state()
            for summary in ("", "PRIVATE_MODEL_ID"):
                kb = self.managed_kb(MODULE["KB_NAME"])
                kb["summary_model_id"] = summary
                kb["model_parameters"] = {"api_key": "PRIVATE_SECRET", "base_url": "PRIVATE_ENDPOINT"}
                output = MODULE["snapshot_path"]("temp/with-summary.json" if summary else "temp/no-summary.json", writing=True)
                MODULE["write_snapshot"](output, kb, {"id": "ds1"}, nodes, rows)
                text = output.read_text(encoding="utf-8")
                data = json.loads(text)
                self.assertEqual(data["version"], 1)
                self.assertEqual(data["ingestion_policy"], {"validation_mode": "read_only",
                    "summary_model_configured": bool(summary), "sync_allowed": not bool(summary)})
                self.assertNotIn("PRIVATE_", text)
                self.assertEqual(len(data["documents"]), 1)

    def test_changed_probe_requires_real_indexed_text_in_selected_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = self.baseline(pathlib.Path(temporary))
            nodes, rows = self.probe_state()
            client = FakeSearch([{"id": "chunk1", "knowledge_id": "knowledge2", "content": "WEKNORA_FEISHU_PROBE_V2"}])
            MODULE["verify_marker"](client, {"id": "kb1"}, {"id": "ds1"}, nodes, rows, "root", "WEKNORA_FEISHU_PROBE_V2", baseline)
            request = client.calls[-1][2]
            self.assertEqual(request["knowledge_ids"], ["knowledge2"])
            self.assertTrue(request["disable_vector_match"])
            self.assertNotIn("WEKNORA_FEISHU_PROBE_V2", self.output.getvalue())

    def test_changed_identifier_alone_is_not_update_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = self.baseline(pathlib.Path(temporary))
            nodes, rows = self.probe_state()
            rows["root"]["file_hash"] = "1" * 64
            rows["root"]["source_updated_at"] = "2026-10-08T01:00:00Z"
            client = FakeSearch([])
            with self.assertRaises(SafeError):
                MODULE["verify_marker"](client, {"id": "kb1"}, {"id": "ds1"}, nodes, rows, "root", "WEKNORA_FEISHU_PROBE_V2", baseline)
            self.assertFalse(client.calls)

    def test_hash_change_without_matching_indexed_text_is_not_update_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = self.baseline(pathlib.Path(temporary))
            nodes, rows = self.probe_state()
            for results in ([], [{"id": "chunk1", "knowledge_id": "knowledge2", "content": "WEKNORA_FEISHU_PROBE_V1", "knowledge_title": "WEKNORA_FEISHU_PROBE_V2"}],
                            [{"id": "chunk1", "knowledge_id": "another-document", "content": "WEKNORA_FEISHU_PROBE_V2"}]):
                with self.subTest(results=bool(results)), self.assertRaises(SafeError):
                    MODULE["verify_marker"](FakeSearch(results), {"id": "kb1"}, {"id": "ds1"}, nodes, rows, "root", "WEKNORA_FEISHU_PROBE_V2", baseline)

    def test_guard_checks_running_image_label_and_environment(self):
        image_id = "sha256:" + "a" * 64
        container = {"Image": image_id, "State": {"Running": True, "Health": {"Status": "healthy"}},
                     "Config": {"Env": ["FEISHU_DOCX_PARSE_MODE=export", "SYSTEM_AES_KEY=" + "x" * 32, "OLLAMA_BASE_URL=http://ollama:11434"]},
                     "NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}}}
        image = {"Config": {"Labels": {MODULE["GUARD_LABEL"]: "1"}}}
        with patch.dict(GLOBALS, {"docker_json": lambda args: container if args[0] == "container" else image}):
            MODULE["require_guard"]("http://127.0.0.1:8080")
            image["Config"]["Labels"] = {}
            with self.assertRaises(SafeError):
                MODULE["require_guard"]("http://127.0.0.1:8080")
        self.assertNotIn("x" * 32, self.output.getvalue())

    def test_http_origin_and_redirects_fail_closed(self):
        for base in ("http://localhost:8080", "https://127.0.0.1:8080", "http://127.0.0.1:8080@evil.invalid", "http://127.0.0.1:8080/remote"):
            with self.subTest(base=base), self.assertRaises(SafeError):
                MODULE["HttpClient"](base)
        with self.assertRaises(SafeError):
            MODULE["RejectRedirects"]().redirect_request(None, None, 302, "", {}, "https://evil.invalid")

    def test_http_error_exposes_only_numeric_codes(self):
        client = MODULE["HttpClient"]("https://open.feishu.cn", feishu=True)
        for code in (131006, "PRIVATE_BODY", True):
            payload = json.dumps({"code": code, "msg": "PRIVATE_BODY", "app_secret": "PRIVATE_SECRET", "tenant_access_token": "PRIVATE_TOKEN"}).encode()
            error = urllib.error.HTTPError("https://open.feishu.cn/private", 400, "PRIVATE_MESSAGE", {}, io.BytesIO(payload))
            client.opener = Mock()
            client.opener.open.side_effect = error
            with self.subTest(code_type=type(code).__name__), self.assertRaises(ApiError) as raised:
                client.request("/open-apis/wiki/v2/spaces/" + SPACE_ID + "/nodes")
            result = raised.exception
            self.assertEqual(result.code, 400)
            self.assertEqual(result.api_code, code if type(code) is int else None)
            self.assertNotIn("PRIVATE_", str(result))
            self.assertIn("400", str(result))
            if type(code) is int:
                self.assertIn(str(code), str(result))

    def test_explicit_tenant_argument_rejects_ambiguous_ids_before_credentials(self):
        for value in ("0", "-1", "+10000", "1.0", "01", "10000x", "18446744073709551616"):
            settings = Mock()
            with self.subTest(value=value), patch.dict(GLOBALS, {"load_settings": settings}), patch("sys.argv", ["feishu-space-sync.py", "verify", "--tenant-id", value]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                MODULE["main"]()
            self.assertEqual(error.exception.code, 2)
            settings.assert_not_called()
        self.assertEqual(MODULE["tenant_id_argument"]("10000"), 10000)

    def test_tenant_header_only_applies_to_local_requests(self):
        for feishu in (False, True):
            client = MODULE["HttpClient"]("https://open.feishu.cn" if feishu else "http://127.0.0.1:8080", feishu=feishu)
            client.tenant_id = 10000
            client.opener = Mock()
            client.opener.open.return_value = io.BytesIO(b'{"code":0,"success":true}')
            client.request("/read")
            headers = dict(client.opener.open.call_args.args[0].header_items())
            self.assertEqual(headers.get("X-tenant-id"), None if feishu else "10000")

    def test_login_explicit_tenant_does_not_change_active_workspace(self):
        client = Mock()
        client.request.return_value = {"token": "PRIVATE_LOGIN_TOKEN", "active_tenant": {"id": 10005}}
        with patch.dict(GLOBALS, {"HttpClient": Mock(return_value=client)}):
            actual = MODULE["login"]({"SMOKE_ADMIN_EMAIL": "fixture@example.invalid", "SMOKE_ADMIN_PASSWORD": "fixture-password"}, tenant_id=10000)
        self.assertIs(actual, client)
        self.assertEqual(actual.tenant_id, 10000)
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(client.request.call_args.args[:2], ("/api/v1/auth/login", "POST"))
        self.assertNotIn("PRIVATE_LOGIN_TOKEN", self.output.getvalue())

    def test_explicit_tenant_permission_failure_has_no_default_fallback(self):
        client = MODULE["HttpClient"]("http://127.0.0.1:8080")
        client.tenant_id = 10000
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError("http://127.0.0.1:8080/read", 403, "private", {}, io.BytesIO(b"PRIVATE_BODY"))
        with self.assertRaises(ApiError) as error:
            client.request("/api/v1/knowledge-bases")
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.tenant_id, 10000)
        self.assertNotIn("PRIVATE_BODY", str(error.exception))

    def test_read_only_verification_forwards_explicit_tenant_without_sync(self):
        client, kb, ds = Mock(), {"id": "kb1"}, {"id": "source1"}
        login, sync = Mock(return_value=client), Mock()
        with patch.dict(GLOBALS, {
            "HttpClient": Mock(), "require_guard": Mock(), "FeishuClient": Mock(), "permissions": Mock(),
            "inventory": Mock(return_value={"root": {"object_type": "docx"}}), "login": login,
            "knowledge_base": Mock(return_value=kb), "datasource": Mock(return_value=ds),
            "documents": Mock(return_value={}), "await_documents": Mock(return_value={}), "await_sync": sync,
        }):
            MODULE["synchronize"]({}, verify=True, tenant_id=10000)
        login.assert_called_once_with({}, tenant_id=10000)
        sync.assert_not_called()

    def test_document_audit_checks_all_docx_without_outputting_object_ids(self):
        client = FakeDocumentAudit()
        MODULE["audit_documents"](client)
        events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual(len(events), 4)
        self.assertEqual({event["node_id"] for event in events}, {"root", "child"})
        self.assertTrue(all(event["auth_result"] is True for event in events))
        self.assertNotIn("PRIVATE_OBJECT_", self.output.getvalue())
        metadata = [index for index, path in enumerate(client.calls) if "/get_node?" in path]
        authorization = [index for index, path in enumerate(client.calls) if "/members/auth?" in path]
        self.assertLess(max(metadata), min(authorization))

    def test_document_audit_reports_denied_separately_from_api_check_error(self):
        for client in (FakeDocumentAudit(export_result=False), FakeDocumentAudit(export_error=ApiError("http", 403, 1069902))):
            self.output.seek(0)
            self.output.truncate()
            with self.subTest(error=client.export_error is not None), self.assertRaises(SafeError):
                MODULE["audit_documents"](client, ["root", "root"])
            events = [json.loads(line) for line in self.output.getvalue().splitlines()]
            self.assertEqual(len(events), 2)
            self.assertTrue(events[0]["auth_result"])
            if client.export_error is None:
                self.assertIs(events[1]["auth_result"], False)
                self.assertEqual(events[1]["event"], "document_permission")
            else:
                self.assertEqual(events[1]["event"], "check_error")
                self.assertEqual(events[1]["http_status"], 403)
                self.assertEqual(events[1]["api_code"], 1069902)
                self.assertNotIn("auth_result", events[1])

    def test_document_audit_rejects_missing_or_non_boolean_result(self):
        for value in ("missing", 0, 1, "true", None):
            self.output.seek(0)
            self.output.truncate()
            with self.subTest(value=value), self.assertRaises(SafeError):
                MODULE["audit_documents"](FakeDocumentAudit(export_result=value), ["root"])
            result = json.loads(self.output.getvalue().splitlines()[-1])
            self.assertEqual(result["event"], "check_error")
            self.assertNotIn("auth_result", result)

    def test_document_audit_fails_before_auth_for_invalid_or_changed_scope(self):
        cases = [(FakeDocumentAudit(), ["unknown"]), (FakeDocumentAudit(), ["root/other"])]
        for changes in ({"space_id": "other"}, {"origin_space_id": "other"}, {"node_token": "another"},
                        {"node_type": "shortcut"}, {"node_type": ""}, {"obj_type": "sheet"}, {"obj_token": ""}):
            cases.append((FakeDocumentAudit(metadata_changes=changes), ["root"]))
        for client, selected in cases:
            with self.subTest(selected=selected, changed=bool(client.metadata_changes)), self.assertRaises(SafeError):
                MODULE["audit_documents"](client, selected)
            self.assertFalse(any("/members/auth?" in path for path in client.calls))


if __name__ == "__main__":
    unittest.main()
