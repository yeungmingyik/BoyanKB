import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import uuid


sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location('trial_presets_acceptance', Path(__file__).with_name('acceptance-presets.py'))
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'temp/enterprise-trial'
ENV_FILE = DIRECTORY / '.env'
PARTNER_RESULT = DIRECTORY / 'partner-result.json'
OUTPUT = DIRECTORY / 'acceptance'
ORIGINS = {'http://127.0.0.1:8080', 'https://kb.boyantech.xyz'}
USER_AGENT = 'BoyanKB-DeploymentCheck/1.0'
HQ_TENANT = 10000
SOURCE_KB = 'd9285ed1-0760-474b-ba82-a13347598176'
SKILLS = {'boyan-' + name for name in ('courseware', 'event-plan', 'local-promotion-plan', 'cooperation-proposal', 'faq-guide', 'docx', 'pptx', 'xlsx', 'md')}
AGENTS = {'mentor': '业务导师', 'planner': '落地方案官', 'reviewer': '内容审校官'}
READ_TOOLS = {'search_knowledge', 'read_document', 'list_documents'}
QUESTION = '请依据企业知识库《第二部分：教学核心套件（内容标准化）》已有资料，向刚加入的合作伙伴说明教学启动前应准备哪些材料、如何核对教学质量红线。用三点说明并给出可定位引用；资料未说明的职责或条件请明确说本次引用资料未提供，不补写报价、案例或承诺。'
DOCUMENT_REQUEST = '请制作一份《合作伙伴入门指南》Word 文档，面向新加入的合作伙伴负责人，用于内部熟悉教学准备工作。采用常见问题指南场景，约2至3页，整理3至5个问题，仅介绍企业知识库《第二部分：教学核心套件（内容标准化）》真实已有的产品目录与进度表、教学包三件套、教学质控红线。无需城市、预算或活动日期，也不涉及课程授课内容、商业报价、合作条款或学校案例。正式品牌为博研文化平台 BoyanKB。逐项附可定位来源，引用资料未说明的事项标为待总部确认；建议必须标明为建议。先检索并读取已安装的场景与 Word 格式指南，再通过 boyan_document.propose 提交完整大纲和结构化正文，仅 docx 格式，等待确认，不能直接生成。'
DOCUMENT_FOLLOWUP = '请继续同一份《合作伙伴入门指南》，当前文档面板没有可确认任务，纯文字大纲不能替代提交。请先重新检索并读取《第二部分：教学核心套件（内容标准化）》以刷新本轮可定位引用，然后必须调用 boyan_document，operation=propose，scene=faq-guide，format=docx，requirements 至少包含 audience=新加入的合作伙伴负责人、topic=教学准备工作。提交 outline_markdown 以及 document.title、document.sections 的完整中文正文、sources 中真实 chunk_id。保持原定3至5问、2至3页、仅三个真实教学模块的范围；不要新增报价或承诺。正式正文不出现 scene、faq-guide、business-docx、模板版本、job_id 或工具术语，答复也只用中文。标题简洁。调用 propose 成功后停止并等待系统确认，不要调用 confirm 或 generate，也不要只重复文字大纲。'


def require(value, code):
    BASE.require(value, code)


def uid(value):
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, 'invalid_resource_id')
    except ValueError:
        raise BASE.AcceptanceError('invalid_resource_id') from None
    return value


def partner_config():
    require(PARTNER_RESULT.is_file() and not PARTNER_RESULT.is_symlink(), 'partner_result_required')
    result = json.loads(PARTNER_RESULT.read_text(encoding='utf-8-sig'))
    require(isinstance(result, dict) and result.get('status') == 'ready', 'partner_not_ready')
    tenant = result.get('tenant_id')
    require(type(tenant) is int and tenant > 0 and tenant != HQ_TENANT, 'partner_tenant_invalid')
    state = result.get('state') or {}
    require(state.get('knowledge_mode') == 'shared_enterprise' and not state.get('knowledge_base_id') and not state.get('contributor_user_id'), 'single_shared_knowledge_mode_required')
    agents = state.get('agent_ids') or {}
    require(set(agents) == set(AGENTS), 'agent_bindings_missing')
    require(len({uid(value) for value in agents.values()}) == 3, 'distinct_agent_bindings_required')
    return {'tenant_id': tenant, 'agent_ids': agents, 'team_manager_user_id': uid(state.get('team_manager_user_id'))}


def credentials():
    require(ENV_FILE.is_file() and not ENV_FILE.is_symlink(), 'credential_file_required')
    names = {'TRIAL_PARTNER_EMAIL', 'TRIAL_PARTNER_PASSWORD'}
    values = {}
    for line in ENV_FILE.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        key = key.strip()
        if separator and key in names:
            require(key not in values, 'duplicate_credential_key')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
                value = value[1:-1]
            values[key] = value
    require(all(values.get(name) and not values[name].startswith('REPLACE_') for name in names), 'partner_credentials_missing')
    return values


def collection(value):
    value = BASE.unwrap(value)
    if isinstance(value, list):
        return value
    require(isinstance(value, dict) and isinstance(value.get('items'), list), 'collection_response_invalid')
    return value['items']


class TrialRun:
    def __init__(self, args, config, client):
        self.args, self.config, self.client = args, config, client
        self.started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.output = (args.output or OUTPUT / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])).resolve()
        require(self.output.is_relative_to(OUTPUT.resolve()) and self.output != OUTPUT.resolve(), 'private_output_directory_required')
        self.output.mkdir(parents=True, exist_ok=False)
        self.secrets, self.checks, self.sessions, self.artifacts, self.manual = [], [], [], [], []
        self.principal = None
        self.previous, self.document_followups = None, 0
        if getattr(args, 'resume', None):
            previous_path = args.resume.resolve()
            require(args.exercise and previous_path.is_relative_to(OUTPUT.resolve()), 'resume_requires_private_exercise_report')
            self.previous = json.loads((previous_path / 'report.json').read_text(encoding='utf-8'))
            require(self.previous.get('tenant_id') == config['tenant_id'] and self.previous.get('base_url') == args.base_url and self.previous.get('source_knowledge_base_id') == SOURCE_KB and self.previous.get('mode') in {'exercise', 'document_resume'}, 'resume_scope_mismatch')
            self.document_followups = self.previous.get('document_followups', 0)
            require(type(self.document_followups) is int and 0 <= self.document_followups <= 2, 'resume_followup_count_invalid')

    def scrub(self, value):
        if isinstance(value, dict):
            return {key: '[REDACTED]' if key.lower() in {'token', 'access_token', 'refresh_token', 'password', 'authorization', 'invite_url', 'email'} else self.scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, str):
            for secret in sorted(set(self.secrets), key=len, reverse=True):
                if secret:
                    value = value.replace(secret, '[REDACTED]')
            return re.sub(r'(?i)([?&](?:token|api_key|access_token)=)[^\s&"<>]+', r'\1[REDACTED]', value)
        return value

    def save(self, name, value):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.scrub(value), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def check(self, name, passed, **details):
        self.checks.append({'name': name, 'status': 'PASS' if passed else 'FAIL', **details})
        self.save('progress.json', {'checks': self.checks, 'sessions': self.sessions, 'artifacts': self.artifacts})
        return passed

    def request(self, path, method='GET', payload=None, allowed=(200,), **options):
        require(method == 'GET' or self.args.exercise, 'exercise_required_for_mutation')
        status, body = self.client.request(path, method, payload, self.principal, **options)
        require(status in allowed and (not isinstance(body, dict) or body.get('success') is not False), 'request_http_' + str(status))
        return BASE.unwrap(body)

    def denial(self, name, path, method='GET', payload=None, allowed=(403,), **options):
        require(method == 'GET' or self.args.exercise, 'exercise_required_for_mutation')
        status, _ = self.client.request(path, method, payload, self.principal, **options)
        require(self.check(name, status in allowed, http_status=status), name + '_unexpected_access')

    def login(self):
        settings = credentials()
        self.secrets.extend(settings.values())
        status, value = self.client.request('/api/v1/auth/login', 'POST', {'email': settings['TRIAL_PARTNER_EMAIL'], 'password': settings['TRIAL_PARTNER_PASSWORD']})
        require(status == 200 and isinstance(value, dict) and isinstance(value.get('token'), str) and value['token'], 'partner_login_failed')
        self.secrets.extend(item for name, item in value.items() if 'token' in name and isinstance(item, str))
        self.principal = {'token': value['token'], 'tenant_id': self.config['tenant_id']}
        profile = self.request('/api/v1/auth/me')
        user = profile.get('user') or {}
        require(self.check('partner.non_admin', user.get('id') == self.config['team_manager_user_id'] and user.get('is_active') is True and not user.get('is_system_admin') and not user.get('can_access_all_tenants')), 'viewer_principal_required')
        memberships = profile.get('memberships') or []
        own = [item for item in memberships if item.get('tenant_id') == self.config['tenant_id']]
        require(self.check('partner.viewer_membership', len(own) == 1 and own[0].get('role') == 'viewer' and all(item.get('tenant_id') != HQ_TENANT for item in memberships)), 'viewer_membership_required')

    def readonly(self):
        catalog = self.request('/api/v1/business-workflow/skills')
        entries = catalog.get('skills') or []
        require(self.check('skills.nine_ready', catalog.get('available') is True and catalog.get('installed') is True and catalog.get('status') == 'ready' and catalog.get('planner_agent_id') == self.config['agent_ids']['planner'] and len(entries) == 9 and {item.get('name') for item in entries} == SKILLS), 'skills_not_ready')
        for entry in entries:
            name = entry['name']
            detail = self.request('/api/v1/business-workflow/skills/' + name)
            content = detail.get('content', '')
            digest = hashlib.sha256(content.encode()).hexdigest()
            valid = all(item.get('available') is True and item.get('installed') is True and item.get('hash_verified') is True and item.get('version') == 'boyan/v1' and item.get('status') == 'ready' and item.get('sha256') == digest for item in (entry, detail))
            require(self.check('skills.' + name, bool(content) and valid), 'skill_integrity_failed')
        agents = collection(self.request('/api/v1/agents'))
        require(self.check('agents.three_bound', all(any(item.get('id') == agent_id and not item.get('is_builtin') for item in agents) for agent_id in self.config['agent_ids'].values())), 'agents_missing')
        for role, agent_id in self.config['agent_ids'].items():
            agent = self.request('/api/v1/agents/' + agent_id)
            config = agent.get('config') or {}
            tools = READ_TOOLS | ({'boyan_document'} if role == 'planner' else set())
            valid = agent.get('name') == AGENTS[role] and str(agent.get('tenant_id')) == str(self.config['tenant_id']) and config.get('kb_selection_mode') == 'selected' and config.get('knowledge_bases') == [SOURCE_KB] and set(config.get('allowed_tools') or []) == tools and not config.get('knowledge_ids')
            valid = valid and config.get('mcp_selection_mode') == 'none' and config.get('skills_selection_mode') == 'none' and not config.get('mcp_services') and not config.get('selected_skills') and not config.get('sandbox_config_id') and config.get('citation_enabled') is True
            valid = valid and not any(config.get(key) for key in ('web_search_enabled', 'web_fetch_enabled', 'data_analysis_enabled', 'memory_enabled'))
            require(self.check('agents.' + role + '.scope_and_tools', valid), 'agent_scope_invalid')
        shared = collection(self.request('/api/v1/shared-knowledge-bases'))
        require(self.check('knowledge.shared_source_readonly', bool(shared) and all((item.get('knowledge_base') or {}).get('id') == SOURCE_KB and item.get('effective_permission', item.get('permission')) == 'viewer' for item in shared)), 'shared_knowledge_scope_invalid')
        own = collection(self.request('/api/v1/knowledge-bases'))
        require(self.check('knowledge.no_extra_workspace_kb', all(item.get('id') == SOURCE_KB for item in own)), 'unexpected_workspace_knowledge')
        source = self.request('/api/v1/knowledge-bases/' + SOURCE_KB)
        require(self.check('knowledge.source_accessible', source.get('id') == SOURCE_KB and str(source.get('tenant_id')) == str(HQ_TENANT)), 'source_knowledge_mismatch')
        team = self.request('/api/v1/business-workflow/team')
        require(self.check('team.viewer_manager', team.get('available') is True and any(item.get('user_id') == self.config['team_manager_user_id'] for item in team.get('members', [])) and all(item.get('role') == 'viewer' for item in team.get('members', []))), 'team_unavailable')
        invitations = self.request('/api/v1/business-workflow/team/invitations')
        require(self.check('team.list_contains_no_tokens', all(not item.get('token') and not item.get('invite_url') for item in collection(invitations))), 'invitation_list_exposes_tokens')
        for name, path in (('agents', '/api/v1/agents'), ('team', '/api/v1/business-workflow/team'), ('sessions', '/api/v1/sessions')):
            self.denial('isolation.hq_' + name + '_header_denied', path, tenant=HQ_TENANT)
        self.denial('isolation.hq_members_path_denied', '/api/v1/tenants/10000/members')
        self.denial('permissions.admin_readiness_denied', '/api/v1/business-workflow/readiness')
        self.manual.append({'name': 'cross_user_files', 'status': '需要人工验证', 'method': '使用另一位非管理员员工的独立登录会话，确认无法读取此负责人的聊天与文件；当前脚本没有创建第二个员工账号，也不以总部管理员代替普通用户对照。'})

    def write_denials(self):
        probes = [
            ('create_agent', '/api/v1/agents', 'POST', {'name': [], 'config': []}),
            ('edit_agent', '/api/v1/agents/' + self.config['agent_ids']['planner'], 'PUT', {'name': [], 'config': []}),
            ('create_kb', '/api/v1/knowledge-bases', 'POST', {'name': []}),
            ('edit_source', '/api/v1/knowledge-bases/' + SOURCE_KB, 'PUT', {'name': []}),
            ('upload_source', '/api/v1/knowledge-bases/' + SOURCE_KB + '/knowledge/manual', 'POST', {'title': [], 'content': []}),
            ('add_member', '/api/v1/tenants/' + str(self.config['tenant_id']) + '/members', 'POST', {'email': [], 'role': []}),
        ]
        for name, path, method, payload in probes:
            self.denial('permissions.' + name + '_denied', path, method, payload)
        self.denial('team.invalid_role_payload_rejected', '/api/v1/business-workflow/team/invitations', 'POST', {'kind': [], 'role': 'owner'}, allowed=(400,))

    def invitation_cycle(self):
        require(self.args.exercise, 'exercise_required_for_mutation')
        created = self.request('/api/v1/business-workflow/team/invitations', 'POST', {'kind': 'link'}, allowed=(201,))
        self.secrets.extend(created[key] for key in ('token', 'invite_url') if isinstance(created.get(key), str))
        invitation_id = created.get('id')
        require(type(invitation_id) is int and invitation_id > 0, 'invitation_id_invalid')
        try:
            require(self.check('team.link_created', created.get('kind') == 'link' and created.get('status') == 'pending' and bool(created.get('token')) and created.get('accepted_count') == 0, invitation_id=invitation_id), 'invitation_create_invalid')
        finally:
            try:
                self.request('/api/v1/business-workflow/team/invitations/' + str(invitation_id), 'DELETE')
            except BASE.AcceptanceError:
                self.check('team.link_cleanup', False, invitation_id=invitation_id)
                self.manual.append({'name': 'invitation_cleanup', 'status': '需要人工验证', 'invitation_id': invitation_id, 'method': '在此伙伴负责人的团队页面确认该邀请已撤销；只撤销此编号，不处理其他邀请。'})
                raise
        items = collection(self.request('/api/v1/business-workflow/team/invitations'))
        matches = [item for item in items if item.get('id') == invitation_id]
        require(self.check('team.link_revoked', len(matches) == 1 and matches[0].get('status') == 'revoked' and not matches[0].get('token') and not matches[0].get('invite_url'), invitation_id=invitation_id), 'invitation_revoke_unverified')

    def session(self, title, role):
        if self.previous:
            matches = [item for item in self.previous.get('sessions', []) if item.get('agent') == role and item.get('title') == title]
            require(len(matches) == 1, 'resume_session_missing')
            session_id = uid(matches[0].get('id'))
            current = self.request('/api/v1/sessions/' + session_id)
            require(current.get('id') == session_id, 'resume_session_access_failed')
            self.sessions.append(matches[0])
            self.save('progress.json', {'checks': self.checks, 'sessions': self.sessions, 'document_followups': self.document_followups})
            return session_id
        value = self.request('/api/v1/sessions', 'POST', {'title': title}, allowed=(200, 201))
        session_id = uid(value.get('id'))
        self.sessions.append({'id': session_id, 'title': title, 'agent': role})
        self.save('progress.json', {'checks': self.checks, 'sessions': self.sessions, 'artifacts': self.artifacts})
        return session_id

    def chat(self, session, role, query, label):
        require(self.args.exercise, 'exercise_required_for_mutation')
        payload = {'query': query, 'agent_id': self.config['agent_ids'][role], 'agent_enabled': True, 'disable_title': True, 'web_search_enabled': False, 'channel': 'api'}
        status, stream = self.client.request('/api/v1/agent-chat/' + session, 'POST', payload, self.principal, stream=True)
        require(status == 200, 'chat_http_' + str(status))
        try:
            transcript = BASE.parse_sse(stream, self.args.timeout)
        except BASE.AcceptanceError as error:
            if error.transcript:
                self.save('transcripts/' + label + '.json', {'session_id': session, 'agent': role, 'query': query, 'error': str(error), **error.transcript})
            raise
        self.save('transcripts/' + label + '.json', {'session_id': session, 'agent': role, 'query': query, **transcript})
        calls = BASE.tool_calls(transcript)
        allowed = READ_TOOLS | ({'boyan_document'} if role == 'planner' else set())
        require(self.check(label + '.tool_scope', all(call.get('tool_name') in allowed for call in calls)), 'tool_scope_violation')
        verified = []
        for reference in transcript['references']:
            require(isinstance(reference, dict), 'reference_invalid')
            knowledge_id = reference.get('knowledge_id')
            if not knowledge_id:
                chunk_id = reference.get('chunk_id') or reference.get('id')
                require(chunk_id, 'reference_identity_missing')
                chunk = self.request('/api/v1/chunks/by-id/' + BASE.identifier(chunk_id))
                knowledge_id = chunk.get('knowledge_id')
            knowledge = self.request('/api/v1/knowledge/' + BASE.identifier(knowledge_id))
            require(knowledge.get('knowledge_base_id') == SOURCE_KB and reference.get('knowledge_base_id') in (None, '', SOURCE_KB), 'reference_outside_source_knowledge')
            verified.append(knowledge_id)
        require(self.check(label + '.verified_source_references', bool(verified), count=len(set(verified))), 'source_references_required')
        return transcript

    def document(self):
        session = self.session('合作伙伴入门指南', 'planner')
        if not self.previous:
            self.document_proposal_turn(session, DOCUMENT_REQUEST, 'partner-guide')
        path = '/api/v1/sessions/' + session + '/documents'
        jobs = collection(self.request(path))
        while not any(job.get('state') != 'superseded' for job in jobs) and self.document_followups < 2:
            self.document_followups += 1
            self.save('progress.json', {'checks': self.checks, 'sessions': self.sessions, 'document_followups': self.document_followups})
            self.document_proposal_turn(session, DOCUMENT_FOLLOWUP, 'partner-guide-followup-' + str(self.document_followups))
            jobs = collection(self.request(path))
        active = [job for job in jobs if job.get('state') != 'superseded']
        require(len(active) == 1 and active[0].get('format') == 'docx', 'one_word_outline_required')
        job = active[0]
        require(job.get('state') in {'awaiting_confirmation', 'approved', 'published'}, 'resume_job_not_actionable')
        if not self.previous:
            require(job.get('state') == 'awaiting_confirmation', 'unexpected_unconfirmed_job_state')
        path += '/' + uid(job.get('id'))
        outline_hash = job.get('outline_hash')
        require(isinstance(outline_hash, str) and re.fullmatch(r'[a-f0-9]{64}', outline_hash), 'outline_hash_invalid')
        self.save('jobs/partner-guide-outline.json', job)
        payload = {'outline_hash': outline_hash}
        if job.get('state') == 'awaiting_confirmation':
            self.denial('document.unconfirmed_download_denied', path + '/download', allowed=(409,), binary=True)
            self.denial('document.unconfirmed_generation_denied', path + '/generate', 'POST', payload, allowed=(409,))
            wrong_hash = ('0' if outline_hash != '0' * 64 else '1') * 64
            self.denial('document.wrong_hash_denied', path + '/confirm', 'POST', {'outline_hash': wrong_hash}, allowed=(409,))
            job = self.request(path + '/confirm', 'POST', payload)
            require(job.get('state') == 'approved' and job.get('outline_hash') == outline_hash, 'confirmation_state_invalid')
        if job.get('state') == 'approved':
            generated = self.request(path + '/generate', 'POST', payload)
            self.save('jobs/partner-guide-generated.json', generated)
        result = self.request(path)
        self.save('jobs/partner-guide.json', result)
        review = result.get('review') or {}
        require(self.check('document.published_after_review', result.get('state') == 'published' and review.get('passed') is True and not any(issue.get('severity') == 'blocking' for issue in review.get('issues', []))), 'document_not_published')
        content = self.request(path + '/download', binary=True)
        details = BASE.inspect_artifact(content, 'docx')
        require(result.get('artifact_sha256') == details['sha256'], 'document_hash_mismatch')
        self.save('artifact-inspection.json', details)
        artifact_path = self.output / 'artifacts/合作伙伴入门指南.docx'
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(content)
        details.pop('text', None)
        self.artifacts.append({'path': str(artifact_path.relative_to(self.output)), 'session_id': session, 'job_id': result['id'], **details})
        self.check('document.word_structure_brand_and_hash', True, **details)
        self.denial('document.forged_hq_download_denied', path + '/download', tenant=HQ_TENANT, binary=True)

    def document_proposal_turn(self, session, query, label):
        transcript = self.chat(session, 'planner', query, label)
        calls = BASE.tool_calls(transcript)
        require(self.check('document.no_model_confirmation_or_generation', not any(call.get('tool_name') == 'boyan_document' and (call.get('arguments') or {}).get('operation') in {'confirm', 'generate'} for call in calls)), 'premature_generation_attempted')

    def exercise(self):
        require(self.args.exercise, 'exercise_required_for_mutation')
        if not self.previous:
            self.write_denials()
            self.invitation_cycle()
            session = self.session('合作伙伴教学准备咨询', 'mentor')
            self.chat(session, 'mentor', QUESTION, 'partner-question')
        self.document()
        self.manual.extend([
            {'name': 'business_facts_and_refusal', 'status': '需要人工验证', 'method': '逐条核对 transcripts 中问答与引用原文，确认事实有依据，资料缺失限定为本次引用未提供，无编造职责、报价或承诺。再用知识库没有的问题追问，核对明确说明未知。'},
            {'name': 'word_visual_open', 'status': '需要人工验证', 'method': '用 Microsoft Word 打开 artifacts/合作伙伴入门指南.docx，确认无修复提示，检查中文字体、分页、品牌和来源。ZIP/XML 与审校通过不能代替原生打开和逐页目视检查。'},
            {'name': 'employee_invitation_registration', 'status': '需要人工验证', 'method': '由管理员另行安排受邀员工接受链接、设置强密码并登录，确认 Viewer 权限及不能继续邀请。脚本仅创建并撤销自己的邀请链接，不注册员工或发送邮件。'},
        ])

    def finish(self):
        if not self.args.exercise:
            self.manual.append({'name': 'exercise_not_run', 'status': '未验证，需要人工验证', 'method': '准备好真实模型与渲染器后显式加 --exercise；该模式产生两段业务会话、Word 文件及一条随后撤销的邀请链接，并消耗模型额度。'})
        failed = sum(item['status'] == 'FAIL' for item in self.checks)
        report = {'schema_version': 1, 'started_at': self.started, 'finished_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'base_url': self.args.base_url, 'mode': 'document_resume' if self.previous else 'exercise' if self.args.exercise else 'read_only', 'tenant_id': self.config['tenant_id'], 'source_knowledge_base_id': SOURCE_KB, 'previous_report': str(self.args.resume / 'report.json') if self.previous else None, 'document_followups': self.document_followups, 'automated_result': 'FAIL' if failed else 'PASS', 'overall_result': 'FAIL' if failed else '需要人工验证', 'checks': self.checks, 'manual_checks': self.manual, 'sessions': self.sessions, 'artifacts': self.artifacts}
        self.save('report.json', report)
        print(json.dumps({'automated_result': report['automated_result'], 'overall_result': report['overall_result'], 'checks': len(self.checks), 'failed': failed, 'artifacts': len(self.artifacts), 'report': str(self.output / 'report.json')}, ensure_ascii=False))
        return 1 if failed else 2


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', choices=sorted(ORIGINS), default='http://127.0.0.1:8080')
    parser.add_argument('--exercise', action='store_true')
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args(argv)
    run = None
    try:
        require(30 <= args.timeout <= 900, 'timeout_invalid')
        require(not args.resume or args.exercise, 'resume_requires_exercise')
        config = partner_config()
        client = BASE.Client(args.base_url, args.timeout)
        client.opener.addheaders = [('User-Agent', USER_AGENT)]
        run = TrialRun(args, config, client)
        run.login()
        run.readonly()
        if args.exercise:
            run.exercise()
    except (BASE.AcceptanceError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        code = str(error) if isinstance(error, BASE.AcceptanceError) else type(error).__name__
        if run:
            run.check('execution.completed', False, code=code)
        else:
            print(json.dumps({'automated_result': 'FAIL', 'code': code}))
            return 1
    return run.finish()


if __name__ == '__main__':
    sys.exit(main())
