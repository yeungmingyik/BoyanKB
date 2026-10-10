import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time


sys.dont_write_bytecode = True


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


A = module('cloud_accounts', 'tencent-accounts.py')
B = module('cloud_acceptance_base', 'acceptance-presets.py')
ROOT = Path('/opt/boyankb')
QUESTION = '请依据企业知识库《第二部分：教学核心套件（内容标准化）》已有资料，向新入职同事说明教学启动前应准备哪些材料、如何核对教学质量红线。用三点说明并给出可定位引用；资料未说明的事项请明确说本次引用资料未提供，不补写报价、案例或承诺。'
DOCUMENT = '请制作《教学准备入门指南》，面向博研文化新入职员工，用于内部熟悉教学准备工作。采用常见问题指南场景，整理3至5个问题，仅介绍企业知识库《第二部分：教学核心套件（内容标准化）》真实已有的产品目录与进度表、教学包三件套、教学质控红线。无需城市、预算或活动日期，不涉及商业报价、合作条款或学校案例。正式品牌为博研文化 BoyanKB。逐项附可定位来源，资料未说明的事项标为待确认。先检索并读取场景与格式指南，再通过 boyan_document 的 propose 提交完整大纲和结构化正文，scene=faq-guide，requirements 包含 audience=新入职员工、topic=教学准备工作。仅输出 {format} 格式，等待确认，不调用 confirm 或 generate。'
PRESENTATION = '请制作《教学准备入门培训》教学PPT，面向博研文化新入职员工，不是学生课程。培训总时长20分钟，本地化要求无，目标是了解产品目录与进度表、教学包三件套、教学质控红线。仅基于企业知识库《第二部分：教学核心套件（内容标准化）》真实已有内容，不新增课程、商业报价、合作条款或学校案例。正式文件品牌为博研文化。场景courseware，格式pptx，requirements包含 audience=新入职员工、duration=20分钟、objectives=掌握教学准备与质控要求、localization=无。核心知识3个模块，安排教学演示建议和互动练习；notes给出讲师要点，每节时长总计20分钟。每页一个观点，保留真实来源。资料未提供的事项标记待确认，不编造案例。先检索并读取课件场景与PPT格式指南，再调用boyan_document的propose提交完整大纲、结构化正文与真实chunk_id，等待用户确认，不调用confirm或generate。'


def collection(value):
    value = B.unwrap(value)
    if isinstance(value, list):
        return value
    B.require(isinstance(value, dict) and isinstance(value.get('items'), list), 'invalid_collection')
    return value['items']


def allowed_tools(role):
    return {'search_knowledge', 'read_document', 'list_documents'} | ({'boyan_document'} if role == 'planner' else set())


def agent_scope(config, role):
    return (
        config.get('knowledge_bases') == [A.KB]
        and config.get('kb_selection_mode') == 'selected'
        and config.get('citation_enabled') is True
        and set(config.get('allowed_tools') or []) == allowed_tools(role)
        and config.get('mcp_selection_mode') == 'none'
        and not config.get('mcp_services')
        and config.get('skills_selection_mode') == 'none'
        and not config.get('selected_skills')
        and not any(config.get(key) for key in ('web_search_enabled', 'web_fetch_enabled', 'data_analysis_enabled', 'memory_enabled', 'sandbox_config_id'))
    )


def job_binding(job, expected):
    return isinstance(job, dict) and all(job.get(key) == expected.get(key) for key in ('id', 'session_id', 'format', 'scene', 'outline_hash', 'outline_markdown'))


class Run:
    def __init__(self, args):
        A.host_guard()
        self.args = args
        self.output = ROOT / 'acceptance' / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + args.mode)
        self.output.parent.mkdir(mode=0o700, exist_ok=True)
        self.output.mkdir(mode=0o700)
        self.client = B.Client('http://127.0.0.1:18080', 600)
        self.checks, self.secrets, self.sessions, self.artifacts = [], [], [], []
        self.principals, self.agents = {}, {}

    def scrub(self, value):
        if isinstance(value, dict):
            return {key: '[REDACTED]' if any(term in key.lower() for term in ('token', 'password', 'authorization', 'invite_url')) else self.scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, str):
            for secret in sorted(set(self.secrets), key=len, reverse=True):
                if secret:
                    value = value.replace(secret, '[REDACTED]')
        return value

    def save(self, name, value):
        path = self.output / name
        data = (json.dumps(self.scrub(value), ensure_ascii=False, indent=2) + '\n').encode()
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as stream:
            stream.write(data)

    def check(self, name, passed, **details):
        self.checks.append({'name': name, 'passed': bool(passed), **details})
        self.save('progress.json', {'checks': self.checks, 'sessions': self.sessions, 'artifacts': self.artifacts})
        B.require(passed, name)
        print(json.dumps({'check': name, 'passed': True}), flush=True)

    def request(self, principal, path, method='GET', payload=None, allowed=(200,), **options):
        status, body = self.client.request(path, method, payload, principal, **options)
        B.require(status in allowed and (not isinstance(body, dict) or body.get('success') is not False), 'request_http_' + str(status))
        return B.unwrap(body)

    def readonly(self):
        settings = A.credentials(ROOT / '.env')
        self.secrets.extend(settings.values())
        for index, alias in enumerate(('admin', 'member')):
            value = self.request(None, '/api/v1/auth/login', 'POST', {'email': A.NEW_EMAILS[index], 'password': settings['ADMIN_PASSWORD' if index == 0 else 'MEMBER_PASSWORD']})
            token = value.get('token')
            B.require(isinstance(token, str) and token, 'login_token_missing')
            self.secrets.append(token)
            principal = {'token': token, 'tenant_id': A.HQ}
            self.principals[alias] = principal
            profile = self.request(principal, '/api/v1/auth/me')
            user = profile['user']
            self.check(alias + '.identity', user['email'] == A.NEW_EMAILS[index] and user['is_active'] and user['is_system_admin'] == (index == 0) and not user['can_access_all_tenants'])
            members = profile.get('memberships', [])
            self.check(alias + '.membership', len(members) == 1 and members[0]['tenant_id'] == A.HQ and members[0]['role'] == ('owner' if index == 0 else 'viewer'))
            self.request(principal, '/api/v1/knowledge-bases/' + A.KB)
            skills = self.request(principal, '/api/v1/business-workflow/skills')
            entries = skills.get('skills', [])
            self.check(alias + '.nine_skills', skills.get('available') is True and len(entries) == 9 and all(item.get('available') and item.get('hash_verified') for item in entries))
            for entry in entries:
                detail = self.request(principal, '/api/v1/business-workflow/skills/' + B.identifier(entry['name']))
                self.check(alias + '.skill.' + entry['name'], detail.get('hash_verified') and detail.get('sha256') == hashlib.sha256(detail.get('content', '').encode()).hexdigest())
            agents = collection(self.request(principal, '/api/v1/agents'))
            chosen = {}
            for role, name in (('mentor', '业务导师'), ('planner', '落地方案官'), ('reviewer', '内容审校官')):
                matches = [item for item in agents if item.get('name') == name and not item.get('is_builtin') and item.get('tenant_id') == A.HQ]
                self.check(alias + '.agent.' + role, len(matches) == 1)
                item = self.request(principal, '/api/v1/agents/' + matches[0]['id'])
                config = item.get('config', {})
                self.check(alias + '.agent_scope.' + role, agent_scope(config, role))
                chosen[role] = item['id']
            self.agents[alias] = chosen
            status, _ = self.client.request('/api/v1/knowledge-bases', principal=principal, tenant=A.PARTNER)
            self.check(alias + '.old_partner_denied', status == 403)
        status, _ = self.client.request('/api/v1/auth/register', 'POST', {})
        self.check('public_registration_denied', status == 403)
        status, _ = self.client.request('/api/v1/business-workflow/team/invitations', principal=self.principals['member'])
        self.check('member_invitation_admin_denied', status == 403)

    def session(self, title, role):
        value = self.request(self.principals['member'], '/api/v1/sessions', 'POST', {'title': title}, allowed=(200, 201))
        identity = B.identifier(value['id'])
        self.sessions.append({'id': identity, 'title': title, 'role': role})
        self.save('progress.json', {'checks': self.checks, 'sessions': self.sessions, 'artifacts': self.artifacts})
        return identity

    def verify_source(self, reference, cache):
        B.require(isinstance(reference, dict), 'reference_invalid')
        chunk_id = reference.get('chunk_id') or reference.get('id')
        knowledge_id = reference.get('knowledge_id')
        B.require(chunk_id or knowledge_id, 'reference_identity_missing')
        B.require(not reference.get('knowledge_base_id') or reference['knowledge_base_id'] == A.KB, 'reference_outside_enterprise')
        principal = self.principals['member']
        if chunk_id:
            B.require(isinstance(chunk_id, str), 'reference_chunk_invalid')
            chunk_id = B.identifier(chunk_id)
            key = ('chunk', chunk_id)
            if key not in cache:
                cache[key] = self.request(principal, '/api/v1/chunks/by-id/' + chunk_id)
            chunk = cache[key]
            B.require(chunk.get('id') == chunk_id and chunk.get('is_enabled') is True, 'reference_chunk_invalid')
            B.require(chunk.get('knowledge_base_id') == A.KB, 'reference_outside_enterprise')
            B.require(not knowledge_id or chunk.get('knowledge_id') == knowledge_id, 'reference_chunk_knowledge_mismatch')
            knowledge_id = chunk.get('knowledge_id')
        B.require(isinstance(knowledge_id, str) and knowledge_id, 'reference_identity_missing')
        knowledge_id = B.identifier(knowledge_id)
        key = ('knowledge', knowledge_id)
        if key not in cache:
            cache[key] = self.request(principal, '/api/v1/knowledge/' + knowledge_id)
        knowledge = cache[key]
        B.require(knowledge.get('id') == knowledge_id, 'reference_knowledge_identity_mismatch')
        B.require(knowledge.get('knowledge_base_id') == A.KB, 'reference_outside_enterprise')
        return {'knowledge_base_id': A.KB, 'knowledge_id': knowledge_id, 'chunk_id': chunk_id}

    def chat(self, session, role, query, label):
        principal = self.principals['member']
        status, response = self.client.request('/api/v1/agent-chat/' + session, 'POST', {'query': query, 'agent_id': self.agents['member'][role], 'agent_enabled': True, 'disable_title': True, 'web_search_enabled': False, 'channel': 'web'}, principal, stream=True)
        B.require(status == 200, 'chat_http_' + str(status))
        try:
            transcript = B.parse_sse(response, 600)
        except B.AcceptanceError as error:
            if error.transcript:
                self.save(label + '.json', error.transcript)
            raise
        self.save(label + '.json', transcript)
        calls = B.tool_calls(transcript)
        self.check(label + '.tools', all(item.get('tool_name') in allowed_tools(role) for item in calls))
        self.check(label + '.model_did_not_confirm', not any(item.get('tool_name') == 'boyan_document' and (item.get('arguments') or {}).get('operation') in ('confirm', 'generate') for item in calls))
        verified, cache = set(), {}
        for reference in transcript['references']:
            verified.add(self.verify_source(reference, cache)['knowledge_id'])
        self.check(label + '.retrieval_scope', bool(verified), documents=len(verified), answer_citations='需要人工验证')
        proposals = []
        for call in calls:
            args = call.get('arguments') or {}
            if call.get('tool_name') != 'boyan_document' or args.get('operation') != 'propose':
                continue
            sources = args.get('sources')
            B.require(isinstance(sources, list) and 1 <= len(sources) <= 30, 'proposal_sources_missing')
            resolved = []
            for source in sources:
                B.require(isinstance(source, dict) and source.get('chunk_id'), 'proposal_source_chunk_missing')
                item = self.verify_source(source, cache)
                B.require(item['knowledge_id'] in verified, 'proposal_source_not_retrieved')
                resolved.append(item)
            results = [event.get('data') or {} for event in transcript['events'] if event.get('response_type') == 'tool_result' and (event.get('data') or {}).get('tool_call_id') == call.get('tool_call_id') and call.get('tool_call_id')]
            B.require(len(results) == 1 and results[0].get('tool_name') == 'boyan_document', 'proposal_result_unbound')
            result = results[0]
            if result.get('success') is not True:
                continue
            B.require(isinstance(result.get('id'), str) and result['id'], 'proposal_result_identity_missing')
            B.identifier(result['id'])
            B.require(result.get('session_id') == session and result.get('state') == 'awaiting_confirmation', 'proposal_session_mismatch')
            B.require(all(result.get(key) == args.get(key) for key in ('format', 'scene', 'outline_markdown')), 'proposal_content_mismatch')
            B.require(re.fullmatch('[a-f0-9]{64}', result.get('outline_hash') or ''), 'proposal_hash_invalid')
            proposals.append({'job': {key: result.get(key) for key in ('id', 'session_id', 'format', 'scene', 'outline_hash', 'outline_markdown')}, 'sources': resolved})
        return proposals

    def document(self, file_format):
        principal = self.principals['member']
        session = self.session('教学准备入门指南' + ('演示稿' if file_format == 'pptx' else ''), 'planner')
        prompt = PRESENTATION if file_format == 'pptx' else DOCUMENT.format(format=file_format)
        proposals = self.chat(session, 'planner', prompt, file_format + '-proposal')
        path = '/api/v1/sessions/' + session + '/documents'
        jobs = collection(self.request(principal, path))
        for attempt in range(2):
            if any(item.get('state') != 'superseded' for item in jobs):
                break
            proposals.extend(self.chat(session, 'planner', '请继续上一轮任务。当前文档面板还没有可确认任务。请重新检索以刷新真实 chunk_id，并必须调用 boyan_document 的 propose 提交完整结构化正文与 sources，不能只给文字大纲。' + prompt, file_format + '-followup-' + str(attempt + 1)))
            jobs = collection(self.request(principal, path))
        active = [item for item in jobs if item.get('state') != 'superseded']
        self.check(file_format + '.proposal', len(active) == 1 and active[0].get('state') == 'awaiting_confirmation' and active[0].get('format') == file_format)
        job = active[0]
        scene = 'courseware' if file_format == 'pptx' else 'faq-guide'
        self.check(file_format + '.proposal_binding', job.get('session_id') == session and job.get('scene') == scene and bool(job.get('outline_markdown')) and bool(re.fullmatch('[a-f0-9]{64}', job.get('outline_hash') or '')))
        matching = [proposal for proposal in proposals if job_binding(job, proposal['job']) and proposal.get('sources')]
        self.check(file_format + '.proposal_sources', len(matching) == 1)
        path += '/' + B.identifier(job['id'])
        self.save(file_format + '-outline.json', {'job': job, 'verified_sources': matching[0]['sources']})
        status, _ = self.client.request(path + '/download', principal=principal, binary=True)
        self.check(file_format + '.unconfirmed_download_denied', status == 409)
        payload = {'outline_hash': job['outline_hash']}
        confirmed = self.request(principal, path + '/confirm', 'POST', payload)
        self.check(file_format + '.confirmed', job_binding(confirmed, job) and confirmed.get('state') == 'approved' and bool(confirmed.get('confirmed_at')))
        generated = self.request(principal, path + '/generate', 'POST', payload, allowed=(200, 202))
        self.check(file_format + '.generation_binding', job_binding(generated, job) and generated.get('confirmed_at') == confirmed['confirmed_at'] and generated.get('state') in ('generating', 'reviewing', 'published'))
        deadline = time.monotonic() + 600
        while True:
            result = self.request(principal, path)
            B.require(job_binding(result, job) and result.get('confirmed_at') == confirmed['confirmed_at'], 'generated_job_binding_mismatch')
            if result.get('state') not in ('generating', 'reviewing') or time.monotonic() >= deadline:
                break
            time.sleep(3)
        self.save(file_format + '-result.json', result)
        review = result.get('review') or {}
        self.check(file_format + '.published', result.get('state') == 'published' and review.get('passed') is True)
        content = self.request(principal, path + '/download', binary=True)
        details = B.inspect_artifact(content, file_format)
        self.check(file_format + '.artifact_hash', result.get('artifact_sha256') == details['sha256'])
        filename = '教学准备入门指南.' + file_format
        with os.fdopen(os.open(self.output / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(content)
        details.pop('text', None)
        self.artifacts.append({'filename': filename, 'session_id': session, 'job_id': job['id'], **details})
        self.save('artifacts.json', self.artifacts)

    def exercise(self):
        self.chat(self.session('教学准备咨询', 'mentor'), 'mentor', QUESTION, 'teaching-question')
        for file_format in self.args.formats:
            self.document(file_format)

    def finish(self, error=None):
        report = {'automated_result': 'FAIL' if error else 'PASS', 'mode': self.args.mode, 'error': error, 'checks': self.checks, 'sessions': self.sessions, 'artifacts': self.artifacts, 'manual_checks': [{'status': '需要人工验证', 'item': '检索来源校验不代表答案已引用。逐条点击答案引用，核对可定位原文及事实一致性；用知识库没有的问题验证明确说明未知。'}, {'status': '需要人工验证', 'item': '本脚本仅自动化演练确认与服务端审校流程，业务交付前仍需人工确认大纲、正文、引用及审校意见。'}, {'status': '需要人工验证', 'item': '用 Word/PowerPoint 原生打开文件，检查无修复提示、中文字体、分页和品牌；ZIP/XML 校验不能代替目视检查。'}, {'status': '需要人工验证', 'item': '用新成员浏览器账号完成真实操作，确认无旧个人会话且不能管理成员。'}]}
        self.save('report.json', report)
        print(json.dumps({'automated_result': report['automated_result'], 'error': error, 'checks': len(self.checks), 'artifacts': len(self.artifacts), 'report': str(self.output / 'report.json')}, ensure_ascii=False))
        return 1 if error else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('readonly', 'exercise'))
    parser.add_argument('--formats', nargs='+', choices=('docx', 'pptx'), default=['docx', 'pptx'])
    args = parser.parse_args()
    run = None
    try:
        run = Run(args)
        run.readonly()
        if args.mode == 'exercise':
            run.exercise()
        return run.finish()
    except (A.HandoverError, B.AcceptanceError, OSError, ValueError, KeyError, TypeError) as error:
        code = str(error) if isinstance(error, (A.HandoverError, B.AcceptanceError)) else type(error).__name__
        if run:
            return run.finish(code)
        print(json.dumps({'automated_result': 'FAIL', 'error': code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
