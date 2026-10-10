import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('skill_graph_acceptance', ROOT / 'scripts/acceptance-presets.py')
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)


def credentials():
    names = {f'TRIAL_{actor}_{field}' for actor in ('ADMIN', 'PARTNER') for field in ('EMAIL', 'PASSWORD')}
    values = {}
    for line in (ROOT / 'temp/enterprise-trial/.env').read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        key = key.strip()
        if separator and key in names:
            BASE.require(key not in values, 'duplicate_credential')
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    BASE.require(all(values.get(key) for key in names), 'credentials_missing')
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', choices=['http://127.0.0.1:8080', 'https://kb.boyantech.xyz'], default='http://127.0.0.1:8080')
    args = parser.parse_args()
    client = BASE.Client(args.base_url, 60)
    client.opener.addheaders = [('User-Agent', 'BoyanKB-DeploymentCheck/1.0')]
    values = credentials()
    checks = []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})

    for actor, tenant in [('ADMIN', 10000), ('PARTNER', 10005)]:
        status, result = client.request('/api/v1/auth/login', 'POST', {'email': values[f'TRIAL_{actor}_EMAIL'], 'password': values[f'TRIAL_{actor}_PASSWORD']})
        BASE.require(status == 200 and result.get('token'), 'login_failed')
        principal = {'token': result['token'], 'tenant_id': tenant}
        status, result = client.request('/api/v1/skills/catalog', principal=principal)
        entries = result.get('data') or []
        check(actor + '.catalog', status == 200 and len(entries) >= 9)
        managed = next((row for row in entries if row.get('name') == 'boyan-docx'), None)
        BASE.require(managed and re.fullmatch('[a-zA-Z0-9_-]+', managed['id']), 'managed_catalog_missing')
        status, _ = client.request('/api/v1/skills/catalog/' + managed['id'] + '/files', principal=principal)
        check(actor + '.files_permission', status == (200 if actor == 'ADMIN' else 403))
        status, result = client.request('/api/v1/business-workflow/skills', principal=principal)
        check(actor + '.business_ready', status == 200 and result.get('data', {}).get('available') is True)
        if actor == 'PARTNER':
            status, _ = client.request('/api/v1/skills/catalog', 'POST', {}, principal)
            check('PARTNER.cannot_register', status == 403)
            status, _ = client.request('/api/v1/skills/catalog', principal=principal, tenant=10000)
            check('PARTNER.cannot_read_hq_catalog', status == 403)
            continue
        status, result = client.request('/api/v1/system/info', principal=principal)
        check('graph.enabled', status == 200 and result.get('data', {}).get('graph_database_engine') == 'Neo4j')
        status, result = client.request('/api/v1/tenants/kv/prompt-templates', principal=principal)
        templates = result.get('data') or {}
        for key in ('system_prompt', 'agent_system_prompt', 'context_template', 'rewrite', 'fallback', 'intent_prompts'):
            rows = templates.get(key) or []
            check('prompts.' + key, status == 200 and bool(rows) and all(re.search('[\u4e00-\u9fff]', row.get('content', '')) for row in rows))
        fixed = [row for row in templates.get('fallback', []) if row.get('mode') != 'model']
        check('prompts.fixed_literal', bool(fixed) and all('{{' not in row.get('content', '') for row in fixed))
    report = {'base_url': args.base_url, 'passed': all(row['passed'] for row in checks), 'checks': checks}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (BASE.AcceptanceError, OSError, ValueError, KeyError):
        print(json.dumps({'passed': False, 'code': 'skill_graph_check_failed'}))
        raise SystemExit(1)
