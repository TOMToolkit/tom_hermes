from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import TestCase, override_settings

from tom_hermes.dataservices.hermes import HermesDataService, _flatten_hermes_archive_row


class BuildQueryParametersTests(TestCase):
    def test_only_fields_user_set_appear_in_params(self):
        svc = HermesDataService()
        params = svc.build_query_parameters({'exact_name': 'MyTarget'})
        self.assertEqual(params, {'name_exact': 'MyTarget'})

    def test_all_fields_map_correctly(self):
        svc = HermesDataService()
        input_fields = {
            'exact_name': 'name1',
            'target_name': 'name2',
            'uuid': '1234',
            'ra': 10,
            'dec': 10,
            'radius': 2
        }
        params = svc.build_query_parameters(input_fields)
        self.assertEqual(params, {
            'name_exact': input_fields['exact_name'],
            'name': input_fields['target_name'],
            'referenced_by_uuid': input_fields['uuid'],
            'cone_search': (input_fields['ra'], input_fields['dec'], input_fields['radius'])
        })


class ModelCreationTests(TestCase):

    def test_create_sidereal_target_from_query(self):
        svc = HermesDataService()
        target_result = {'id': 289,
                           'name': 'SN2026bpu',
                           'right_ascension': 75.6680083333,
                           'right_ascension_sexagesimal': '5:02:40.32199999',
                           'declination': -8.308975,
                           'declination_sexagesimal': '-8:18:32.31',
                           'messages': [{'id': 5875603,
                                         'uuid': '0a6d59dd-0f18-486a-baa4-971162a82394'}]
                          }
        
        target = svc.create_target_from_query(target_result)
        self.assertEqual(target.name, target_result['name'])
        self.assertEqual(target.ra, target_result['right_ascension'])
        self.assertEqual(target.target_type, 'SIDEREAL')

    def test_create_nonsidereal_target_from_query(self):
        svc = HermesDataService()
        target_result = {'id': 289,
                           'name': 'moooov',
                           'eccentricity': 0.6680083333,
                           'inclination': 12,
                           'semimajor_axis': -8.308975,
                           'messages': [{'id': 5875603,
                                         'uuid': '0a6d59dd-0f18-486a-baa4-971162a82394'}]
                          }
        
        target = svc.create_target_from_query(target_result)
        self.assertEqual(target.name, target_result['name'])
        self.assertEqual(target.inclination, target_result['inclination'])
        self.assertEqual(target.target_type, 'NONSIDEREAL')


class CredentialTests(TestCase):

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'settings-key', 'BASE_URL': 'https://h.example/'})
    def test_settings_api_key_produces_token_header(self):
        # No user → resolve_hermes_credentials reads from settings. Real
        # code path, no mocks: settings → resolve → build_headers.
        svc = HermesDataService()
        self.assertEqual(svc.build_headers(), {'Authorization': 'Token settings-key'})

    @override_settings(HERMES_CONFIGURATION={})
    def test_no_credentials_returns_empty_headers(self):
        # When neither user profile nor settings provide an api_key,
        # build_headers returns {} so HERMES will respond 403, which the
        # view surfaces to the user as a query-feedback banner.
        svc = HermesDataService()
        self.assertEqual(svc.build_headers(), {})

    def test_resolve_credentials_called_with_self_user(self):
        # build_headers must thread ``self.user`` into resolve so per-user
        # HermesProfile credentials are picked up correctly. Patch resolve
        # so the test does not depend on the session cipher.
        fake_user = AnonymousUser()
        svc = HermesDataService(user=fake_user)
        with patch('tom_hermes.dataservices.hermes.resolve_hermes_credentials',
                   return_value={'api_key': 'mocked-key', 'base_url': 'x'}) as resolve_mock:
            headers = svc.build_headers()
        resolve_mock.assert_called_once_with(fake_user)
        self.assertEqual(headers, {'Authorization': 'Token mocked-key'})
