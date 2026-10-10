import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = 'boyantech.xyz'
HOST = 'kb.boyantech.xyz'
TUNNEL_NAME = 'boyankb-enterprise-trial'
STATE = ROOT / 'temp/enterprise-trial/tunnel-state.json'


class TunnelError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise TunnelError(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TunnelError('redirect_refused')


def environment(path):
    values = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        key = key.strip()
        if separator and key in {'TRIAL_CLOUDFLARE_API_TOKEN', 'TRIAL_TUNNEL_TOKEN', 'TRIAL_PUBLIC_HOST'}:
            require(key not in values, 'duplicate_environment_key')
            values[key] = value.strip().strip('\"\'')
    require(values.get('TRIAL_PUBLIC_HOST') == HOST, 'unexpected_public_host')
    return values


def request_json(opener, url, method='GET', body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        require(len(raw) <= 4 * 1024 * 1024, 'response_too_large')
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        raise TunnelError('http_' + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TunnelError('request_failed') from None


class Cloudflare:
    def __init__(self, token):
        require(token and not token.startswith('REPLACE_') and re.fullmatch(r'[A-Za-z0-9_.-]{20,512}', token), 'cloudflare_token_missing')
        self.headers = {'Authorization': 'Bearer ' + token}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, method='GET', body=None):
        result = request_json(self.opener, 'https://api.cloudflare.com/client/v4' + path, method, body, self.headers)
        require(isinstance(result, dict) and result.get('success') is True, 'cloudflare_api_rejected')
        info = result.get('result_info') or {}
        require(isinstance(info, dict), 'cloudflare_response_invalid')
        if isinstance(result.get('result'), list):
            require(info.get('total_pages', 1) <= 1 and info.get('total_count', len(result['result'])) <= len(result['result']), 'cloudflare_response_incomplete')
        return result['result']


def dns_fingerprint(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cloudflare_identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value), 'invalid_cloudflare_identifier')
    return value


def tunnel_identifier(value):
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, 'invalid_tunnel_identifier')
    except ValueError:
        raise TunnelError('invalid_tunnel_identifier') from None
    return value


def validated_records(raw):
    require(isinstance(raw, list), 'invalid_dns_records')
    require(all(isinstance(row, dict) and row.get('name') == HOST and row.get('type') in {'A', 'AAAA', 'CNAME'} and isinstance(row.get('content'), str) and type(row.get('proxied')) is bool and type(row.get('ttl')) is int for row in raw), 'unexpected_hostname_records')
    for row in raw:
        cloudflare_identifier(row.get('id'))
    records = sorted([{key: row[key] for key in ('id', 'name', 'type', 'content', 'proxied', 'ttl')} for row in raw], key=lambda row: row['id'])
    require(len({row['id'] for row in records}) == len(records), 'duplicate_dns_record')
    return records


def read_dns(client, zone_id):
    return validated_records(client.call('/zones/' + cloudflare_identifier(zone_id) + '/dns_records?' + urllib.parse.urlencode({'name': HOST, 'per_page': 100})))


def inspect(client):
    zones = client.call('/zones?' + urllib.parse.urlencode({'name': DOMAIN, 'status': 'active', 'per_page': 50}))
    require(len(zones) == 1 and zones[0]['name'] == DOMAIN and zones[0]['status'] == 'active', 'active_zone_not_unique')
    zone = zones[0]
    zone_id, account_id = zone['id'], zone['account']['id']
    cloudflare_identifier(zone_id)
    cloudflare_identifier(account_id)
    records = read_dns(client, zone_id)
    tunnels = client.call('/accounts/' + account_id + '/cfd_tunnel?' + urllib.parse.urlencode({'name': TUNNEL_NAME, 'is_deleted': 'false', 'per_page': 100}))
    tunnels = [row for row in tunnels if row['name'] == TUNNEL_NAME and not row.get('deleted_at')]
    require(len(tunnels) <= 1, 'ambiguous_tunnel_name')
    return {'host': HOST, 'zone_id': zone_id, 'account_id': account_id, 'dns_records': records, 'dns_fingerprint': dns_fingerprint(records), 'tunnel_ids': [tunnel_identifier(row['id']) for row in tunnels]}


def gateway_ready():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    value = request_json(opener, 'http://127.0.0.1:8090/api/v1/auth/config', headers={'Host': HOST})
    require(value.get('registration_mode') == 'invite_only' and value.get('complex_password_enabled') is True, 'gateway_auth_not_ready')


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.pending')
    with temporary.open('x', encoding='utf-8') as handle:
        json.dump(state, handle, indent=2)
    os.replace(temporary, STATE)


def tunnel_environment(env_file):
    path = Path(env_file)
    require(path.name == '.env' and path.is_file() and not path.is_symlink(), 'tunnel_environment_file_required')
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    matching = [index for index, line in enumerate(lines) if line.partition('=')[0].strip() == 'TRIAL_TUNNEL_TOKEN']
    require(len(matching) <= 1, 'duplicate_tunnel_token')
    return path, lines, matching


def save_tunnel_token(env_file, token):
    require(re.fullmatch(r'[A-Za-z0-9_+/=-]{30,8192}', token), 'invalid_tunnel_token')
    path, lines, matching = tunnel_environment(env_file)
    if matching:
        lines[matching[0]] = 'TRIAL_TUNNEL_TOKEN=' + token
    else:
        lines.append('TRIAL_TUNNEL_TOKEN=' + token)
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(lines) + '\n')
    os.replace(temporary, path)


def load_state(snapshot):
    state = json.loads(STATE.read_text(encoding='utf-8'))
    require(isinstance(state, dict) and state.get('schema_version') == 1 and state['host'] == HOST and state['zone_id'] == snapshot['zone_id'] and state['account_id'] == snapshot['account_id'], 'saved_tunnel_scope_mismatch')
    cloudflare_identifier(state['zone_id'])
    cloudflare_identifier(state['account_id'])
    tunnel_identifier(state['tunnel_id'])
    require(snapshot['tunnel_ids'] == [state['tunnel_id']], 'saved_tunnel_missing')
    require(dns_fingerprint(validated_records(state['original_dns'])) == state['original_dns_fingerprint'], 'saved_dns_fingerprint_invalid')
    return state


def tunnel_config():
    return {'ingress': [{'hostname': HOST, 'service': 'http://127.0.0.1:8090', 'originRequest': {'httpHostHeader': HOST}}, {'service': 'http_status:404'}]}


def require_tunnel_config(existing, allow_empty=False):
    require(isinstance(existing, dict) and set(existing) <= {'ingress', 'warp-routing', 'originRequest'}, 'tunnel_routes_changed')
    warp = existing.get('warp-routing') or {}
    require(isinstance(warp, dict) and not warp.get('enabled') and not existing.get('originRequest'), 'tunnel_routes_changed')
    require(existing.get('ingress') == tunnel_config()['ingress'] or allow_empty and existing.get('ingress') in (None, []), 'tunnel_routes_changed')


def prepare(client, snapshot, env_file, fingerprint):
    require(snapshot['dns_fingerprint'] == fingerprint, 'dns_changed_since_review')
    require(len(snapshot['dns_records']) <= 1, 'multiple_dns_records_require_review')
    tunnel_environment(env_file)
    gateway_ready()
    if STATE.exists():
        state = load_state(snapshot)
    else:
        require(not snapshot['tunnel_ids'], 'unmanaged_tunnel_name_conflict')
        result = client.call('/accounts/' + snapshot['account_id'] + '/cfd_tunnel', 'POST', {'name': TUNNEL_NAME, 'config_src': 'cloudflare', 'tunnel_secret': base64.b64encode(secrets.token_bytes(32)).decode()})
        state = {'schema_version': 1, 'host': HOST, 'zone_id': snapshot['zone_id'], 'account_id': snapshot['account_id'], 'tunnel_id': tunnel_identifier(result['id']), 'original_dns': snapshot['dns_records'], 'original_dns_fingerprint': snapshot['dns_fingerprint'], 'published': False}
        save_state(state)
    prefix = '/accounts/' + state['account_id'] + '/cfd_tunnel/' + state['tunnel_id']
    config = tunnel_config()
    existing = client.call(prefix + '/configurations')
    existing_config = existing.get('config') or {}
    require_tunnel_config(existing_config, allow_empty=True)
    if existing_config.get('ingress') != config['ingress']:
        client.call(prefix + '/configurations', 'PUT', {'config': config})
    save_tunnel_token(env_file, client.call(prefix + '/token'))
    state['prepared'] = True
    save_state(state)
    return {'status': 'PREPARED', 'tunnel_id': state['tunnel_id'], 'dns_changed': False, 'token_saved': True}


def publish(client, snapshot):
    state = load_state(snapshot)
    require(state.get('prepared') is True, 'tunnel_not_prepared')
    gateway_ready()
    connector = client.call('/accounts/' + state['account_id'] + '/cfd_tunnel/' + state['tunnel_id'])
    connections = connector.get('connections')
    require(connector.get('id') == state['tunnel_id'] and connector.get('status') == 'healthy' and isinstance(connections, list) and any(isinstance(row, dict) and row and not row.get('is_pending_reconnect') for row in connections), 'tunnel_connector_not_ready')
    config = client.call('/accounts/' + state['account_id'] + '/cfd_tunnel/' + state['tunnel_id'] + '/configurations')
    require_tunnel_config(config.get('config'))
    target = state['tunnel_id'] + '.cfargotunnel.com'
    records = read_dns(client, state['zone_id'])
    require(dns_fingerprint(records) == snapshot['dns_fingerprint'], 'dns_changed_during_publish')
    if len(records) == 1 and records[0]['type'] == 'CNAME' and records[0]['content'] == target and records[0]['proxied']:
        state['published'] = True
        state['dns_record_id'] = records[0]['id']
        save_state(state)
        return {'status': 'PUBLISHED', 'host': HOST, 'changed': False}
    require(snapshot['dns_fingerprint'] == state['original_dns_fingerprint'], 'dns_changed_since_review')
    require(len(records) <= 1, 'multiple_dns_records_require_review')
    body = {'type': 'CNAME', 'name': HOST, 'content': target, 'proxied': True, 'ttl': 1}
    path = '/zones/' + state['zone_id'] + '/dns_records'
    result = client.call(path + ('/' + records[0]['id'] if records else ''), 'PUT' if records else 'POST', body)
    published = validated_records([result])
    require(all(published[0][key] == value for key, value in body.items()) and (not records or published[0]['id'] == records[0]['id']), 'dns_publish_unverified')
    require(read_dns(client, state['zone_id']) == published, 'dns_publish_unverified')
    state['published'] = True
    state['dns_record_id'] = result['id']
    save_state(state)
    return {'status': 'PUBLISHED', 'host': HOST, 'changed': True}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, default=ROOT / 'temp/enterprise-trial/.env')
    parser.add_argument('--action', choices=('inspect', 'prepare', 'publish'), default='inspect')
    parser.add_argument('--dns-fingerprint')
    args = parser.parse_args(argv)
    try:
        values = environment(args.env_file)
        client = Cloudflare(values.get('TRIAL_CLOUDFLARE_API_TOKEN'))
        snapshot = inspect(client)
        if args.action == 'inspect':
            result = {'status': 'READ_ONLY', **snapshot}
        elif args.action == 'prepare':
            result = prepare(client, snapshot, args.env_file, args.dns_fingerprint)
        else:
            result = publish(client, snapshot)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (TunnelError, OSError, ValueError, KeyError, TypeError):
        error = sys.exc_info()[1]
        print(json.dumps({'status': 'FAILED', 'code': str(error) if isinstance(error, TunnelError) else 'local_state_or_response_invalid'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
