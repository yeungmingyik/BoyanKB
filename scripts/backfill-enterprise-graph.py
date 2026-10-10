import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.parse


sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location('graph_check', Path(__file__).with_name('check-skill-graph.py'))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
BASE = CHECK.BASE
KB_ID = 'd9285ed1-0760-474b-ba82-a13347598176'


def summary(plan):
    counts = {}
    for item in plan['items']:
        counts[item['status']] = counts.get(item['status'], 0) + 1
    return {'knowledge_base_id': KB_ID, 'plan_hash': plan['plan_hash'], 'knowledge_count': plan['knowledge_count'], 'chunk_count': plan['chunk_count'], 'states': counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--expected-plan-hash')
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    BASE.require(0 <= args.wait_seconds <= 1800 and (not args.retry_failed or args.apply), 'invalid_options')
    BASE.require(not args.apply or bool(args.expected_plan_hash), 'expected_plan_hash_required')
    values = CHECK.credentials()
    client = BASE.Client('http://127.0.0.1:8080', 120)
    status, value = client.request('/api/v1/auth/login', 'POST', {'email': values['TRIAL_ADMIN_EMAIL'], 'password': values['TRIAL_ADMIN_PASSWORD']})
    BASE.require(status == 200 and value.get('token'), 'admin_login_failed')
    principal = {'tenant_id': 10000, 'token': value['token']}

    def request(path, method='GET', payload=None):
        status, value = client.request(path, method, payload, principal)
        BASE.require(status == 200 and value.get('success') is not False, 'graph_request_http_' + str(status))
        return value

    value = request('/api/v1/knowledge-bases/' + KB_ID + '/knowledge?page=1&page_size=100')
    rows = value.get('data') or []
    BASE.require(isinstance(rows, list) and 0 < len(rows) <= 100 and type(value.get('total')) is int and value['total'] == len(rows), 'knowledge_page_incomplete')
    BASE.require(all(row.get('knowledge_base_id') == KB_ID and row.get('parse_status') == 'completed' for row in rows), 'completed_source_required')
    ids = sorted(row['id'] for row in rows)
    path = '/api/v1/knowledge-bases/' + KB_ID + '/graph-backfill'
    query = '?knowledge_ids=' + urllib.parse.quote(','.join(ids), safe=',')
    plan = request(path + query)['data']
    expected_hash = plan['plan_hash']
    BASE.require(plan['chunk_count'] > 0, 'nothing_to_extract')
    BASE.require(not args.expected_plan_hash or args.expected_plan_hash == expected_hash, 'graph_source_changed')
    print(json.dumps({'event': 'graph_plan', **summary(plan)}), flush=True)
    if args.apply:
        plan = request(path, 'POST', {'knowledge_ids': ids, 'expected_plan_hash': plan['plan_hash'], 'retry_failed': args.retry_failed})['data']
        print(json.dumps({'event': 'graph_submitted', **summary(plan)}), flush=True)
    deadline = time.monotonic() + args.wait_seconds
    while args.wait_seconds and time.monotonic() < deadline:
        states = summary(plan)['states']
        if states.get('completed', 0) == plan['chunk_count'] or states.get('archived', 0):
            break
        time.sleep(10)
        plan = request(path + query)['data']
        BASE.require(plan['plan_hash'] == expected_hash, 'graph_source_changed')
        print(json.dumps({'event': 'graph_progress', **summary(plan)}), flush=True)
    if args.wait_seconds:
        return 0 if summary(plan)['states'].get('completed', 0) == plan['chunk_count'] else 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BASE.AcceptanceError as error:
        print(json.dumps({'event': 'graph_backfill_failed', 'code': str(error)}))
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'event': 'graph_backfill_failed', 'code': 'invalid_response_or_configuration'}))
        raise SystemExit(1)
