import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import zipfile


SPEC = importlib.util.spec_from_file_location("acceptance_presets", Path(__file__).with_name("acceptance-presets.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    return {
        "schema_version": 1,
        "environment": "local-isolated-partners",
        "base_url": "http://127.0.0.1:8080",
        "partners": [
            {"alias": alias, "tenant_id": tenant, "agent_ids": {name: alias + "-" + name for name in ("mentor", "planner", "reviewer")}, "private_knowledge_base_id": alias + "-kb", "hq_knowledge_base_ids": ["hq-kb"], "credentials": {"email_env": "TEST_" + alias.upper() + "_EMAIL", "password_env": "TEST_" + alias.upper() + "_PASSWORD"}}
            for alias, tenant in (("a", 101), ("b", 102))
        ],
    }


def package(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        for path, data in parts.items():
            archive.writestr(path, data)
    return buffer.getvalue()


class AcceptanceTests(unittest.TestCase):
    def test_fixture_requires_isolated_origin_and_identities(self):
        for change in (lambda x: x.update(base_url="https://kb.boyantech.com"), lambda x: x.update(base_url="http://127.0.0.1:8080@attacker.test"), lambda x: x["partners"][1].update(tenant_id=101), lambda x: x.update(environment="production")):
            value = fixture()
            change(value)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "fixtures.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(MODULE.AcceptanceError):
                    MODULE.load_fixture(path)

    def test_fixture_accepts_complete_distinct_partners(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(len(MODULE.load_fixture(path)["partners"]), 2)

    def test_only_selected_environment_names_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('TEST_A_PASSWORD="local value"\nDEEPSEEK_API_KEY=never-load-this\nTEST_A_EMAIL=user@example.test\n', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                env = MODULE.read_environment(path, {"TEST_A_EMAIL", "TEST_A_PASSWORD"})
            self.assertEqual(env, {"TEST_A_EMAIL": "user@example.test", "TEST_A_PASSWORD": "local value"})

    def test_sse_keeps_real_tool_events(self):
        events = [{"response_type": "tool_call", "data": {"tool_name": "search_knowledge", "arguments": {"query": "课程"}}}, {"response_type": "answer", "content": "资料中没有这部分信息"}, {"response_type": "complete"}]
        response = io.BytesIO("".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events).encode())
        result = MODULE.parse_sse(response)
        self.assertEqual(MODULE.tool_calls(result)[0]["tool_name"], "search_knowledge")
        self.assertEqual(result["answer"], "资料中没有这部分信息")

    def test_sse_rejects_partial_and_server_errors(self):
        samples = [
            [{"response_type": "answer", "content": "partial"}],
            [{"response_type": "error", "content": "failed"}, {"response_type": "answer", "content": "pretend success"}, {"response_type": "complete"}],
            [{"response_type": "answer", "content": "partial"}, {"response_type": "complete", "finish_reason": "incomplete"}],
            [{"response_type": "answer", "content": "partial", "data": {"truncated": True}}, {"response_type": "complete"}],
        ]
        for events in samples:
            stream = io.BytesIO("".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.parse_sse(stream)

    def test_sse_supersedes_preambles_and_keeps_source_metadata(self):
        events = [
            {"response_type": "answer", "content": "先检索", "data": {"event_id": "first"}},
            {"response_type": "tool_call", "data": {"tool_call_id": "search", "tool_name": "search_knowledge"}},
            {"response_type": "tool_result", "data": {"tool_call_id": "search", "tool_name": "search_knowledge", "success": True, "results": [{"knowledge_id": "doc", "knowledge_base_id": "kb"}]}},
            {"response_type": "answer", "content": "最终", "data": {"event_id": "last"}},
            {"response_type": "answer", "content": "回答", "data": {"event_id": "last"}},
            {"response_type": "complete"},
        ]
        response = io.BytesIO("".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())
        result = MODULE.parse_sse(response)
        self.assertEqual(result["answer"], "最终回答")
        self.assertEqual(result["references"], [{"knowledge_id": "doc", "knowledge_base_id": "kb"}])

    def test_tool_argument_stream_uses_final_dictionary(self):
        events = [{"response_type": "tool_call", "data": {"tool_call_id": "id", "tool_name": "boyan_document", "arguments": value}} for value in (None, {"operation": "generate"})]
        self.assertEqual(MODULE.tool_calls({"events": events}), [{"tool_call_id": "id", "tool_name": "boyan_document", "arguments": {"operation": "generate"}}])

    def test_blocked_revision_requires_new_confirmation_and_review(self):
        job = {"id": "old", "format": "pptx", "state": "blocked", "outline_hash": "a" * 64, "review": {"passed": False, "issues": [{"problem": "unsupported fact", "severity": "blocking"}]}}
        run = SimpleNamespace()
        run.args = SimpleNamespace(download_only=False)
        run.principals = {"a": {"token": "test", "tenant_id": 101}}
        run.jobs = lambda *args: [dict(job)]
        run.save = Mock()
        run.check = lambda name, passed, **kwargs: passed
        run.client = SimpleNamespace(request=Mock(return_value=(409, {})))
        confirmations = []

        def request(path, method="GET", payload=None, alias=None):
            if method == "POST":
                self.assertTrue(path.endswith("/new/confirm"))
                self.assertEqual(payload, {"outline_hash": "b" * 64})
                confirmations.append(payload)
                job["state"] = "approved"
            return dict(job)

        def chat(partner, role, session_id, query, label):
            if label.endswith("-revise"):
                self.assertIn("unsupported fact", query)
                job.update(id="new", state="awaiting_confirmation", outline_hash="b" * 64)
            else:
                self.assertEqual(job["state"], "approved")
                self.assertEqual(len(confirmations), 1)
                job.update(state="published", review={"passed": True, "issues": []})

        run.request, run.chat = request, chat
        result, path = MODULE.Run.publish_artifact(run, {"alias": "a"}, "session", "case", "pptx", "task")
        self.assertEqual(result["state"], "published")
        self.assertTrue(path.endswith("/new"))
        self.assertEqual(run.client.request.call_count, 4)

    def test_download_only_refuses_fallback_or_model_requests(self):
        run = SimpleNamespace(args=SimpleNamespace(download_only=True), save=Mock(), chat=Mock(), request=Mock())
        previous = {"id": "previous", "format": "pptx", "state": "published", "created_at": "2026-10-01"}
        latest = {"id": "latest", "format": "pptx", "state": "blocked", "created_at": "2026-10-02"}
        run.jobs = Mock(return_value=[previous, latest])
        with self.assertRaisesRegex(MODULE.AcceptanceError, "download_only_latest_job_not_published"):
            MODULE.Run.publish_artifact(run, {"alias": "a"}, "session", "label", "pptx", "unused")
        run.request.assert_not_called()
        run.chat.assert_not_called()
        latest["state"] = "published"
        run.request.return_value = latest
        result, path = MODULE.Run.publish_artifact(run, {"alias": "a"}, "session", "label", "pptx", "unused")
        self.assertEqual(result["id"], "latest")
        run.request.assert_called_once_with("/api/v1/sessions/session/documents/latest", alias="a")
        self.assertTrue(path.endswith("/latest"))
        run.chat.assert_not_called()
        with self.assertRaisesRegex(MODULE.AcceptanceError, "download_only_revision_forbidden"):
            MODULE.Run.publish_artifact(run, {"alias": "a"}, "session", "label", "pptx", "unused", "edit")

    def test_download_only_never_creates_missing_session(self):
        run = SimpleNamespace(args=SimpleNamespace(download_only=True), previous={"sessions": []}, request=Mock())
        with self.assertRaisesRegex(MODULE.AcceptanceError, "download_only_session_missing"):
            MODULE.Run.session(run, {"alias": "a"}, "missing")
        run.request.assert_not_called()

    def test_formula_recomputes_and_rejects_code(self):
        cells = {"B2": {"value": "12"}, "C2": {"value": "10"}, "D2": {"formula": "B2*C2", "value": "120"}, "D3": {"value": "1000"}, "D4": {"value": "360"}}
        self.assertEqual(MODULE.evaluate_formula("SUM(D2:D4)", cells), 1480)
        self.assertEqual(MODULE.evaluate_formula("ROUND(1.005,2)", {}), 1.01)
        self.assertEqual(MODULE.evaluate_formula("ROUND(-1.005,2)", {}), -1.01)
        for formula in ("__import__('os').system('anything')", "D2/0", "NOW()"):
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.evaluate_formula(formula, cells)
        with self.assertRaises(MODULE.AcceptanceError):
            MODULE.evaluate_formula("A1", {"A1": {"formula": "A1"}})

    def test_xlsx_requires_correct_cached_formula_results(self):
        parts = {
            "xl/workbook.xml": "<workbook/>",
            "xl/sharedStrings.xml": "<sst><si><t>博研文化</t></si><si><t>资料来源</t></si></sst>",
            "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c r="A1"><v>12</v></c><c r="B1"><v>10</v></c><c r="C1"><f>A1*B1</f><v>120</v></c></row></sheetData></worksheet>',
        }
        result = MODULE.inspect_artifact(package(parts), "xlsx")
        self.assertEqual(result["verified_formula_count"], 1)
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace("<v>120</v>", "<v>121</v>")
        with self.assertRaisesRegex(MODULE.AcceptanceError, "formula_result_mismatch"):
            MODULE.inspect_artifact(package(parts), "xlsx")

    def test_office_rejects_external_links_and_missing_brand(self):
        base = {"word/document.xml": "<document><t>博研文化</t><t>资料来源</t></document>", "word/styles.xml": "<styles/>"}
        self.assertEqual(MODULE.inspect_artifact(package(base), "docx")["format"], "docx")
        for addition in ({"xl/externalLinks/externalLink1.xml": "<externalLink/>"}, {"word/document.xml": "<document><t>无品牌</t></document>"}, {"word/document.xml": "<document><t>博研文化资料来源 Tencent</t></document>"}):
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.inspect_artifact(package({**base, **addition}), "docx")

    def test_ppt_counts_only_referenced_slides_and_checks_targets(self):
        parts = {"ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst>' + ''.join('<p:sldId id="' + str(number) + '" r:id="r' + str(number) + '"/>' for number in range(1, 4)) + '</p:sldIdLst></p:presentation>', "ppt/_rels/presentation.xml.rels": '<Relationships>' + ''.join('<Relationship Id="r' + str(number) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide' + str(number) + '.xml"/>' for number in range(1, 4)) + '</Relationships>'}
        parts.update({'ppt/slides/slide' + str(number) + '.xml': '<slide><t>博研文化</t><t>资料来源</t></slide>' for number in range(1, 5)})
        details = MODULE.inspect_artifact(package(parts), "pptx")
        self.assertEqual(details["slides"], 3)
        self.assertEqual(details["unreferenced_slide_parts"], 1)
        for number, note in ((1, "本节共2页，当前第 1/2 页。预计 15 分钟。"), (2, "本节共2页，当前第 2/2 页。预计 15 分钟。"), (3, "预计时长：30分钟。")):
            parts['ppt/slides/_rels/slide'+str(number)+'.xml.rels'] = '<Relationships><Relationship Id="notes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide'+str(number)+'.xml"/></Relationships>'
            parts['ppt/notesSlides/notesSlide'+str(number)+'.xml'] = '<notes><t>'+note+'</t></notes>'
        details = MODULE.inspect_artifact(package(parts), "pptx")
        self.assertEqual(details["notes_total_minutes"], 45)
        self.assertEqual(len(details["timed_sections"]), 2)
        parts["ppt/notesSlides/notesSlide3.xml"] = "<notes><t>本节对应正文第3页，本节总时长30分钟，本页不单独计时。</t></notes>"
        self.assertEqual(MODULE.inspect_artifact(package(parts), "pptx")["notes_total_minutes"], 45)
        parts["ppt/notesSlides/notesSlide3.xml"] = "<notes><t>预计30分钟，本节总时长35分钟。</t></notes>"
        with self.assertRaisesRegex(MODULE.AcceptanceError, "presentation_section_duration_ambiguous"):
            MODULE.inspect_artifact(package(parts), "pptx")
        parts["ppt/notesSlides/notesSlide3.xml"] = "<notes><t>本节总时长30分钟。</t></notes>"
        del parts["ppt/slides/slide2.xml"]
        with self.assertRaisesRegex(MODULE.AcceptanceError, "presentation_slide_reference_invalid"):
            MODULE.inspect_artifact(package(parts), "pptx")

    def test_no_redirect_carries_token_to_other_host(self):
        handler = MODULE.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "redirect", {}, "https://example.test"))

    def test_followup_reuses_bound_session_and_keeps_original_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "followup.json"
            path.write_text(json.dumps({"schema_version": 1, "queries": {"event-normal": "合成用户补充输入"}}), encoding="utf-8")
            run = SimpleNamespace(args=SimpleNamespace(followup_file=path, input_file=None, resume=Path(directory), case=None, behavior_partner="a"), fixture=fixture(), previous={"sessions": [{"label": "event-normal", "alias": "a", "id": "original"}]}, manual=[])
            run.session = Mock(return_value="original")
            run.chat = Mock(return_value={"events": [], "answer": "大纲待确认", "references": []})
            run.jobs = Mock(return_value=[])
            run.check = Mock(return_value=True)
            MODULE.Run.behavior(run)
            self.assertEqual(run.session.call_args.args[1], "event-normal")
            self.assertEqual(run.chat.call_args.args[2:], ("original", "合成用户补充输入", "event-normal-followup"))
            self.assertEqual(run.manual[0]["evidence"], "transcripts/event-normal-followup.json")
            run.previous = {"sessions": []}
            run.session.reset_mock()
            run.chat.reset_mock()
            MODULE.Run.behavior(run)
            run.session.assert_not_called()
            run.chat.assert_not_called()
            self.assertEqual(run.check.call_args.kwargs["code"], "followup_session_missing")
            run.args.followup_file = None
            run.args.input_file = path
            run.args.resume = None
            MODULE.Run.behavior(run)
            self.assertEqual(run.chat.call_args.args[2:], ("original", "合成用户补充输入", "event-normal"))


if __name__ == "__main__":
    unittest.main()
