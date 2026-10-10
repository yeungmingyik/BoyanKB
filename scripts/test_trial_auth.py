import copy
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location('trial_auth', Path(__file__).with_name('configure-trial-auth.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Client:
    def __init__(self):
        self.user = {'is_system_admin': True, 'can_access_all_tenants': False}
        self.settings = copy.deepcopy(MODULE.SETTINGS)
        self.settings['auth.registration_mode'] = 'self_serve'
        self.writes = []
        self.discard_writes = False

    def request(self, method, path, payload=None):
        if path.endswith('/auth/me'):
            return {'data': {'user': self.user}}
        if path.endswith('/auth/config'):
            return {'registration_mode': self.settings['auth.registration_mode'], 'complex_password_enabled': self.settings['auth.complex_password_enabled']}
        key = path.rsplit('/', 1)[1]
        if method == 'PUT':
            self.writes.append((path, payload))
            if not self.discard_writes:
                self.settings[key] = payload['value']
        return {'value': self.settings[key]}


class TrialAuthTests(unittest.TestCase):
    def test_plan_has_no_writes(self):
        client = Client()
        self.assertEqual(MODULE.configure(client)['status'], 'PLAN')
        self.assertEqual(client.writes, [])

    def test_apply_reads_back_and_repeat_is_idempotent(self):
        client = Client()
        MODULE.configure(client, True)
        self.assertEqual(len(client.writes), 1)
        result = MODULE.configure(client, True)
        self.assertTrue(all(not row['changed'] for row in result['changes']))
        self.assertEqual(len(client.writes), 1)

    def test_unauthorized_identity_cannot_mutate(self):
        for user in ({'is_system_admin': False, 'can_access_all_tenants': False}, {'is_system_admin': True, 'can_access_all_tenants': True}):
            client = Client()
            client.user = user
            with self.assertRaisesRegex(MODULE.ONBOARD.OnboardError, 'restricted_system_admin_required'):
                MODULE.configure(client, True)
            self.assertFalse(client.writes)

    def test_failed_readback_cannot_report_success(self):
        client = Client()
        client.discard_writes = True
        with self.assertRaisesRegex(MODULE.ONBOARD.OnboardError, 'setting_readback_failed'):
            MODULE.configure(client, True)


if __name__ == '__main__':
    unittest.main()
