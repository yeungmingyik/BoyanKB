import contextlib
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
