import argparse
import importlib.util
import json
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location('onboard', Path(__file__).with_name('onboard-partner.py'))
ONBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ONBOARD)
SETTINGS = {
    'auth.registration_mode': 'invite_only',
    'auth.default_tenant_mode': 'tenantless',
    'auth.complex_password_enabled': True,
    'tenant.self_service_creation_enabled': False,
    'tenant.auto_accept_invitation': False,
    'sandbox.docker_enabled': False,
}


def configure(client, apply=False):
    me = client.request('GET', '/api/v1/auth/me')['data']['user']
    ONBOARD.require(me.get('is_system_admin') is True and me.get('can_access_all_tenants') is False, 'restricted_system_admin_required')
    changes = []
    for key, wanted in SETTINGS.items():
        path = '/api/v1/system/admin/settings/' + key
        saved = client.request('GET', path)
        current = saved.get('value')
        ONBOARD.require(isinstance(current, type(wanted)), 'unexpected_setting_type')
        if apply and current != wanted:
            client.request('PUT', path, {'value': wanted})
            ONBOARD.require(client.request('GET', path).get('value') == wanted, 'setting_readback_failed')
        changes.append({'key': key, 'changed': current != wanted, 'target': wanted})
    if apply:
        config = client.request('GET', '/api/v1/auth/config')
        ONBOARD.require(config.get('registration_mode') == 'invite_only', 'registration_still_enabled')
        ONBOARD.require(config.get('complex_password_enabled') is True, 'complex_password_not_enabled')
    return {'status': 'APPLIED' if apply else 'PLAN', 'changes': changes}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        token = sys.stdin.readline().strip()
        ONBOARD.require(token and len(token) <= 8192, 'operator_token_stdin_required')
        client = ONBOARD.Client('http://127.0.0.1:8080', None, {'ONBOARD_ADMIN_TOKEN': token}, apply=args.apply)
        print(json.dumps(configure(client, args.apply), ensure_ascii=True))
        return 0
    except ONBOARD.OnboardError as error:
        print(json.dumps({'status': 'FAILED', 'code': str(error)}))
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'status': 'FAILED', 'code': 'invalid_local_state_or_response'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
