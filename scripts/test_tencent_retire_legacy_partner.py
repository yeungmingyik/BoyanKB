import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCOUNTS = load_module('retirement_test_accounts', 'tencent-accounts.py')
RETIREMENT = load_module('retirement_test_target', 'tencent-retire-legacy-partner.py')


class RetirementValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'tenant': [{'id': 10005, 'name': '星辰教育有限公司', 'status': 'active', 'deleted_at': None}],
            'organization': [{'id': RETIREMENT.ORG, 'name': 'boyan-partner-xingchen-education', 'owner_tenant_id': 10000, 'deleted_at': None}],
            'shares': [{'id': '9c3c3e05-e207-4228-b5da-a69fa96fda72', 'knowledge_base_id': ACCOUNTS.KB, 'source_tenant_id': 10000, 'deleted_at': None}],
            'org_members': [{'role': 'admin', 'tenant_id': 10000}, {'role': 'viewer', 'tenant_id': 10005}],
            'counts': [{key: 0 for key in ('members', 'users', 'kbs', 'keys', 'invitations', 'im_channels', 'embed_channels', 'document_jobs', 'agent_shares', 'leases')}],
            'archived_assets': [{'agents': 3, 'sessions': 4, 'skills': 9}],
            'protected': {},
        }

    def rejects(self, data, code):
        with self.assertRaises(ACCOUNTS.HandoverError) as raised:
            RETIREMENT.validate(ACCOUNTS, data)
        self.assertEqual(str(raised.exception), code)

    def test_untouched_legacy_target_is_eligible(self):
        original = copy.deepcopy(self.data)
        self.assertFalse(RETIREMENT.validate(ACCOUNTS, self.data))
        self.assertEqual(self.data, original)

    def test_headquarters_and_other_tenants_are_rejected(self):
        for tenant_id in (10000, 10006):
            with self.subTest(tenant_id=tenant_id):
                candidate = copy.deepcopy(self.data)
                candidate['tenant'][0]['id'] = tenant_id
                self.rejects(candidate, 'legacy_tenant_mismatch')

    def test_renamed_tenant_is_rejected(self):
        self.data['tenant'][0]['name'] = '另一家合作伙伴'
        self.rejects(self.data, 'legacy_tenant_mismatch')

    def test_missing_or_ambiguous_targets_are_rejected(self):
        for field, code in (('tenant', 'exact_legacy_tenant_required'), ('organization', 'exact_legacy_organization_required')):
            for cardinality in (0, 2):
                with self.subTest(field=field, cardinality=cardinality):
                    candidate = copy.deepcopy(self.data)
                    candidate[field] *= cardinality
                    self.rejects(candidate, code)

    def test_changed_organization_is_rejected(self):
        for field, value in (('name', 'another-partner'), ('owner_tenant_id', 10006)):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.data)
                candidate['organization'][0][field] = value
                self.rejects(candidate, 'legacy_organization_mismatch')

    def test_any_active_dependency_prevents_retirement(self):
        for dependency in self.data['counts'][0]:
            with self.subTest(dependency=dependency):
                candidate = copy.deepcopy(self.data)
                candidate['counts'][0][dependency] = 1
                self.rejects(candidate, 'legacy_tenant_in_use')

    def test_extra_organization_member_is_rejected(self):
        self.data['org_members'].append({'role': 'viewer', 'tenant_id': 10006})
        self.rejects(self.data, 'unexpected_organization_members')

    def test_changed_organization_role_is_rejected(self):
        self.data['org_members'][1]['role'] = 'admin'
        self.rejects(self.data, 'unexpected_organization_members')

    def test_different_shared_resources_are_rejected(self):
        for field, value in (('knowledge_base_id', 'another-kb'), ('source_tenant_id', 10006)):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.data)
                candidate['shares'][0][field] = value
                self.rejects(candidate, 'unexpected_shared_resources')

    def test_missing_or_additional_share_is_rejected(self):
        for cardinality in (0, 2):
            with self.subTest(cardinality=cardinality):
                candidate = copy.deepcopy(self.data)
                candidate['shares'] *= cardinality
                self.rejects(candidate, 'unexpected_shared_resources')

    def test_changed_archived_asset_counts_are_rejected(self):
        for field in self.data['archived_assets'][0]:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.data)
                candidate['archived_assets'][0][field] += 1
                self.rejects(candidate, 'legacy_assets_changed')

    def test_partial_retirement_is_rejected(self):
        fields = ('tenant', 'organization', 'shares')
        for mask in range(1, 7):
            with self.subTest(mask=mask):
                candidate = copy.deepcopy(self.data)
                for index, field in enumerate(fields):
                    if mask & (1 << index):
                        candidate[field][0]['deleted_at'] = '2026-10-12T00:00:00Z'
                self.rejects(candidate, 'partial_retirement_requires_review')

    def test_fully_retired_target_is_accepted(self):
        for field in ('tenant', 'organization', 'shares'):
            self.data[field][0]['deleted_at'] = '2026-10-12T00:00:00Z'
        original = copy.deepcopy(self.data)
        self.assertTrue(RETIREMENT.validate(ACCOUNTS, self.data))
        self.assertEqual(self.data, original)


if __name__ == '__main__':
    unittest.main()
