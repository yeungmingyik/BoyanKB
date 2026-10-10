import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("tencent_acceptance", Path(__file__).with_name("tencent-acceptance.py"))
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def run_fixture():
    run = acceptance.Run.__new__(acceptance.Run)
    run.args = SimpleNamespace(mode="exercise", formats=["docx"])
    run.checks, run.secrets, run.sessions, run.artifacts = [], [], [], []
    run.principals = {"member": {"token": "fixture-token", "tenant_id": acceptance.A.HQ}}
    run.agents = {"member": {"mentor": "mentor", "planner": "planner"}}
    run.save = Mock()
    run.output = Path("private-report")
    return run


def transcript(references=None, calls=None):
    return {
        "answer": "已依据资料说明。",
        "references": references or [],
        "events": [{"response_type": "tool_call", "data": {"tool_call_id": str(index), **item}} for index, item in enumerate(calls or [])],
    }


def source_request(principal, path, *args, **options):
    if '/chunks/by-id/' in path:
        return {'id': path.rsplit('/', 1)[1], 'knowledge_id': 'knowledge', 'knowledge_base_id': acceptance.A.KB, 'is_enabled': True}
    return {'id': path.rsplit('/', 1)[1], 'knowledge_base_id': acceptance.A.KB}


def job_fixture(**changes):
    return {'id': 'job', 'session_id': 'session', 'state': 'awaiting_confirmation', 'format': 'docx', 'scene': 'faq-guide', 'outline_hash': 'a' * 64, 'outline_markdown': '教学准备大纲', **changes}


def proposal_transcript(**changes):
    args = {'operation': 'propose', 'format': 'docx', 'scene': 'faq-guide', 'outline_markdown': '教学准备大纲', 'sources': [{'chunk_id': 'chunk'}], **changes}
    result = transcript([{'knowledge_id': 'knowledge'}], [{'tool_name': 'boyan_document', 'arguments': args}])
    result['events'].append({'response_type': 'tool_result', 'data': {'tool_call_id': '0', 'tool_name': 'boyan_document', 'success': True, **job_fixture()}})
    return result


class HostAndSecretsTests(unittest.TestCase):
    def test_rejected_host_prevents_client_and_output_creation(self):
        with patch.object(acceptance.A, "host_guard", side_effect=acceptance.A.HandoverError("ubuntu_root_required")), patch.object(acceptance.B, "Client") as client, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(acceptance.A.HandoverError, "ubuntu_root_required"):
                acceptance.Run(SimpleNamespace(mode="readonly"))
        client.assert_not_called()
        mkdir.assert_not_called()

    def test_non_linux_host_rejected(self):
        with patch.object(acceptance.A.sys, "platform", "win32"):
            with self.assertRaisesRegex(acceptance.A.HandoverError, "ubuntu_root_required"):
                acceptance.A.host_guard()

    def test_secret_keys_and_embedded_secret_values_redacted(self):
        run = run_fixture()
        run.secrets = ["fixture-password", "fixture-bearer"]
        value = {"password": "unknown-password", "nested": [{"Authorization": "Bearer unknown", "note": "fixture-password fixture-bearer", "refresh_token": "unknown-refresh"}], "safe": "业务摘要"}
        result = run.scrub(value)
        self.assertEqual(result["password"], "[REDACTED]")
        self.assertEqual(result["nested"][0]["Authorization"], "[REDACTED]")
        self.assertEqual(result["nested"][0]["refresh_token"], "[REDACTED]")
        self.assertEqual(result["nested"][0]["note"], "[REDACTED] [REDACTED]")
        self.assertEqual(result["safe"], "业务摘要")


class CitationAndToolTests(unittest.TestCase):
    def run_chat(self, evidence, request=None, role="mentor"):
        run = run_fixture()
        run.client = SimpleNamespace(request=Mock(return_value=(200, object())))
        run.request = request or Mock(side_effect=source_request)
        with patch.object(acceptance.B, "parse_sse", return_value=evidence), contextlib.redirect_stdout(io.StringIO()):
            run.proposals = run.chat("session", role, "question", "case")
        return run

    def test_foreign_knowledge_base_reference_rejected(self):
        evidence = transcript([{"knowledge_id": "foreign-knowledge"}])
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, "reference_outside_enterprise"):
            self.run_chat(evidence, Mock(return_value={"id": "foreign-knowledge", "knowledge_base_id": "foreign-kb"}))

    def test_chunk_is_checked_even_when_knowledge_id_is_present(self):
        evidence = transcript([{'knowledge_id': 'knowledge', 'chunk_id': 'foreign-chunk'}])
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, 'reference_outside_enterprise'):
            self.run_chat(evidence, Mock(return_value={'id': 'foreign-chunk', 'knowledge_id': 'knowledge', 'knowledge_base_id': 'foreign-kb', 'is_enabled': True}))

    def test_chunk_document_mismatch_rejected(self):
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, 'reference_chunk_knowledge_mismatch'):
            self.run_chat(transcript([{'knowledge_id': 'another-document', 'chunk_id': 'chunk'}]))

    def test_claimed_reference_scope_mismatch_rejected_without_fetch(self):
        request = Mock()
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, 'reference_outside_enterprise'):
            self.run_chat(transcript([{'knowledge_id': 'knowledge', 'knowledge_base_id': 'foreign-kb'}]), request)
        request.assert_not_called()

    def test_wrong_or_disabled_chunk_rejected(self):
        for changes in ({'id': 'another-chunk'}, {'is_enabled': False}):
            with self.subTest(changes=changes):
                chunk = {**source_request(None, '/chunks/by-id/chunk'), **changes}
                with self.assertRaisesRegex(acceptance.B.AcceptanceError, 'reference_chunk_invalid'):
                    self.run_chat(transcript([{'chunk_id': 'chunk'}]), Mock(return_value=chunk))

    def test_knowledge_only_evidence_does_not_assert_answer_has_citations(self):
        run = self.run_chat(transcript([{'knowledge_id': 'knowledge'}]))
        scope = next(item for item in run.checks if item['name'] == 'case.retrieval_scope')
        self.assertEqual(scope['answer_citations'], '需要人工验证')
        self.assertEqual(run.proposals, [])

    def test_valid_proposal_bound_to_successful_tool_result(self):
        run = self.run_chat(proposal_transcript(), role='planner')
        self.assertEqual(run.proposals[0]['job']['id'], 'job')
        self.assertEqual(run.proposals[0]['sources'][0]['chunk_id'], 'chunk')

    def test_proposal_must_have_real_chunk_sources(self):
        for sources in ([], [{'knowledge_id': 'knowledge'}]):
            with self.subTest(sources=sources), self.assertRaises(acceptance.B.AcceptanceError):
                self.run_chat(proposal_transcript(sources=sources), role='planner')

    def test_proposal_source_must_belong_to_retrieved_document(self):
        evidence = proposal_transcript()
        evidence['references'] = [{'knowledge_id': 'other-enterprise-document'}]
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, 'proposal_source_not_retrieved'):
            self.run_chat(evidence, role='planner')

    def test_proposal_source_outside_enterprise_rejected(self):
        def request(principal, path, *args, **options):
            value = source_request(principal, path)
            if '/chunks/' in path:
                value['knowledge_base_id'] = 'foreign-kb'
            return value
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, 'reference_outside_enterprise'):
            self.run_chat(proposal_transcript(), request, role='planner')

    def test_proposal_result_must_match_tool_call_session_and_content(self):
        for changes, code in (({'tool_call_id': 'unrelated'}, 'proposal_result_unbound'), ({'session_id': 'unrelated'}, 'proposal_session_mismatch'), ({'outline_markdown': 'changed'}, 'proposal_content_mismatch'), ({'format': 'pptx'}, 'proposal_content_mismatch'), ({'outline_hash': 'invalid'}, 'proposal_hash_invalid')):
            with self.subTest(changes=changes):
                evidence = proposal_transcript()
                evidence['events'][-1]['data'].update(changes)
                with self.assertRaisesRegex(acceptance.B.AcceptanceError, code):
                    self.run_chat(evidence, role='planner')

    def test_missing_reference_identity_rejected(self):
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, "reference_identity_missing"):
            self.run_chat(transcript([{"title": "source without identity"}]))

    def test_no_evidence_rejected(self):
        with self.assertRaises(acceptance.B.AcceptanceError):
            self.run_chat(transcript())

    def test_unauthorized_tools_rejected(self):
        evidence = transcript([{"knowledge_id": "valid"}], [{"tool_name": "web_fetch", "arguments": {}}])
        with self.assertRaisesRegex(acceptance.B.AcceptanceError, "case.tools"):
            self.run_chat(evidence)

    def test_model_confirmation_and_generation_rejected(self):
        for operation in ("confirm", "generate"):
            with self.subTest(operation=operation):
                evidence = transcript([{"knowledge_id": "valid"}], [{"tool_name": "boyan_document", "arguments": {"operation": operation}}])
                with self.assertRaisesRegex(acceptance.B.AcceptanceError, "case.model_did_not_confirm"):
                    self.run_chat(evidence, role="planner")


class DocumentGateTests(unittest.TestCase):
    def run_document(self, proposal=None, confirm=None, generate=None, result=None, evidence=None, error=None):
        run = run_fixture()
        job = job_fixture(**(proposal or {}))
        confirmation = {**job, 'state': 'approved', 'confirmed_at': '2026-10-10T01:00:00Z', **(confirm or {})}
        generated = {**confirmation, 'state': 'generating', **(generate or {})}
        published = {**confirmation, 'state': 'published', 'review': {'passed': True}, 'artifact_sha256': 'b' * 64, **(result or {})}
        run.session = Mock(return_value="session")
        run.chat = Mock(return_value=[{'job': job_fixture(), 'sources': [{'chunk_id': 'chunk', 'knowledge_id': 'knowledge', 'knowledge_base_id': acceptance.A.KB}]}] if evidence is None else evidence)
        run.client = SimpleNamespace(request=Mock(return_value=(409, b"")))
        actions = []

        def request(principal, path, method="GET", payload=None, **options):
            actions.append((path, method))
            if path.endswith("/documents"):
                return [job]
            if path.endswith('/confirm'):
                return confirmation
            if path.endswith('/generate'):
                return generated
            if path.endswith('/download'):
                return b'fixture-office-file'
            return published

        run.request = request
        with tempfile.TemporaryDirectory() as directory, patch.object(acceptance.B, "inspect_artifact", return_value={'sha256': 'b' * 64, 'text': '博研文化 资料来源'}) as inspect, contextlib.redirect_stdout(io.StringIO()):
            run.output = Path(directory)
            if error:
                with self.assertRaisesRegex(acceptance.B.AcceptanceError, error):
                    run.document('docx')
                inspect.assert_not_called()
            else:
                run.document("docx")
                inspect.assert_called_once()
                self.assertEqual((run.output / '教学准备入门指南.docx').read_bytes(), b'fixture-office-file')
        for call in run.client.request.call_args_list:
            self.assertEqual(call.kwargs['principal'], run.principals['member'])
        return actions

    def test_already_published_proposal_never_confirmed(self):
        actions = self.run_document(proposal={'state': 'published'}, error='docx.proposal')
        self.assertFalse(any(path.endswith(("/confirm", "/generate")) for path, method in actions))

    def test_failed_review_never_downloaded_as_completed_artifact(self):
        actions = self.run_document(result={'review': {'passed': False}}, error='docx.published')
        self.assertFalse(any(path.endswith("/download") for path, method in actions))

    def test_wrong_proposal_binding_prevents_confirmation(self):
        for changes in ({'session_id': 'other-session'}, {'scene': 'courseware'}, {'outline_hash': 'invalid'}):
            with self.subTest(changes=changes):
                actions = self.run_document(proposal=changes, error='docx.proposal_binding')
                self.assertFalse(any(path.endswith(('/confirm', '/generate')) for path, method in actions))

    def test_missing_proposal_source_evidence_prevents_confirmation(self):
        actions = self.run_document(evidence=[], error='docx.proposal_sources')
        self.assertFalse(any(path.endswith(('/confirm', '/generate')) for path, method in actions))

    def test_confirmed_task_must_match_proposal(self):
        for changes in ({'id': 'another-job'}, {'outline_hash': 'c' * 64}, {'confirmed_at': None}, {'state': 'awaiting_confirmation'}):
            with self.subTest(changes=changes):
                actions = self.run_document(confirm=changes, error='docx.confirmed')
                self.assertFalse(any(path.endswith('/generate') for path, method in actions))

    def test_generation_response_must_match_confirmed_task(self):
        for changes in ({'id': 'another-job'}, {'session_id': 'another-session'}, {'scene': 'courseware'}, {'format': 'pptx'}, {'outline_hash': 'c' * 64}, {'confirmed_at': 'another-time'}):
            with self.subTest(changes=changes):
                actions = self.run_document(generate=changes, error='docx.generation_binding')
                self.assertFalse(any(path.endswith('/download') for path, method in actions))

    def test_polled_task_must_match_confirmed_task(self):
        for changes in ({'id': 'another-job'}, {'session_id': 'another-session'}, {'scene': 'courseware'}, {'format': 'pptx'}, {'outline_hash': 'c' * 64}, {'confirmed_at': 'another-time'}):
            with self.subTest(changes=changes):
                actions = self.run_document(result=changes, error='generated_job_binding_mismatch')
                self.assertFalse(any(path.endswith('/download') for path, method in actions))

    def test_valid_confirmation_review_and_artifact_flow(self):
        actions = self.run_document()
        self.assertTrue(any(path.endswith('/confirm') for path, method in actions))
        self.assertTrue(any(path.endswith('/generate') for path, method in actions))
        self.assertTrue(any(path.endswith('/download') for path, method in actions))

    def test_manual_limits_remain_explicit(self):
        run = run_fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.finish(), 0)
        report = run.save.call_args.args[1]
        self.assertTrue(all(item["status"] == "需要人工验证" for item in report["manual_checks"]))
        self.assertTrue(any("原生打开" in item["item"] for item in report["manual_checks"]))
        self.assertTrue(any('检索来源校验不代表答案已引用' in item['item'] for item in report['manual_checks']))
        self.assertTrue(any('人工确认大纲' in item['item'] for item in report['manual_checks']))


class AgentScopeTests(unittest.TestCase):
    def config(self):
        return {'knowledge_bases': [acceptance.A.KB], 'kb_selection_mode': 'selected', 'citation_enabled': True, 'allowed_tools': sorted(acceptance.allowed_tools('planner')), 'mcp_selection_mode': 'none', 'mcp_services': [], 'skills_selection_mode': 'none', 'selected_skills': []}

    def test_expected_planner_scope_passes(self):
        self.assertTrue(acceptance.agent_scope(self.config(), 'planner'))

    def test_additional_mcp_skills_sandbox_or_web_access_rejected(self):
        for changes in ({'mcp_selection_mode': 'all'}, {'mcp_services': ['service']}, {'skills_selection_mode': 'all'}, {'selected_skills': ['skill']}, {'sandbox_config_id': 'sandbox'}, {'web_search_enabled': True}, {'web_fetch_enabled': True}, {'memory_enabled': True}, {'data_analysis_enabled': True}, {'allowed_tools': [*acceptance.allowed_tools('planner'), 'shell_exec']}, {'knowledge_bases': [acceptance.A.KB, 'other-kb']}):
            with self.subTest(changes=changes):
                self.assertFalse(acceptance.agent_scope({**self.config(), **changes}, 'planner'))


if __name__ == "__main__":
    unittest.main()
