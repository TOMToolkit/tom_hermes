from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from tom_hermes.dataservices.hermes import HermesDataService
from tom_hermes.models import HermesProfile


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
            'cone_search': f'{input_fields['ra']}, {input_fields['dec']}, {input_fields['radius']}'
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
                                       'uuid': '0a6d59dd-0f18-486a-baa4-971162a82394',
                                       }
                                      ]
                         }

        target = svc.create_target_from_query(target_result)
        self.assertEqual(target.name, target_result['name'])
        self.assertEqual(target.ra, target_result['right_ascension'])
        self.assertEqual(target.type, 'SIDEREAL')

    def test_create_nonsidereal_target_from_query(self):
        svc = HermesDataService()
        target_result = {'id': 289,
                         'name': 'moooov',
                         'eccentricity': 0.6680083333,
                         'orbital_inclination': 12,
                         'semimajor_axis': -8.308975,
                         'messages': [{'id': 5875603,
                                       'uuid': '0a6d59dd-0f18-486a-baa4-971162a82394'}]
                         }

        target = svc.create_target_from_query(target_result)
        self.assertEqual(target.name, target_result['name'])
        self.assertEqual(target.inclination, target_result['orbital_inclination'])
        self.assertEqual(target.type, 'NON_SIDEREAL')


class CredentialTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pw')
        self.client.force_login(self.user)

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'settings-key', 'BASE_URL': 'https://h.example/'})
    def test_credentials_no_user_defaults_to_settings(self):
        svc = HermesDataService()
        self.assertEqual(svc.build_headers(), {'Authorization': 'Token settings-key'})

    @override_settings(HERMES_CONFIGURATION={})
    def test_no_credentials_returns_empty_headers(self):
        svc = HermesDataService()
        self.assertEqual(svc.build_headers(), {})

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'settings-key', 'BASE_URL': 'https://h.example/'})
    def test_credentials_with_user_token(self):
        HermesProfile.objects.create(user=self.user, hermes_api_key="user-key")
        svc = HermesDataService(user=self.user)
        self.assertEqual(svc.build_headers(), {'Authorization': 'Token user-key'})

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'settings-key', 'BASE_URL': 'https://h.example/'})
    def test_credentials_with_user_but_no_token(self):
        HermesProfile.objects.create(user=self.user, hermes_api_key="")
        svc = HermesDataService(user=self.user)
        self.assertEqual(svc.build_headers(), {'Authorization': 'Token settings-key'})

    def test_user_credentials_but_no_settings(self):
        HermesProfile.objects.create(user=self.user, hermes_api_key="user-key")
        svc = HermesDataService(user=self.user)
        self.assertEqual(svc.build_headers(), {'Authorization': 'Token user-key'})
