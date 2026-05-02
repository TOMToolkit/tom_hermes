"""
Tests for ``tom_hermes.sharing``.

Focuses on the SharingBackend surface and the credential resolver. Lower
layers (``publish_to_hermes``, ``HermesDataConverter``) are mocked at the
seam between ``HermesSharingBackend`` and the HTTP layer — we verify that
the backend assembles the right inputs and calls the right function, not
that the HERMES API itself returns a particular value.
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings

from tom_hermes.credentials import resolve_hermes_credentials
from tom_hermes.sharing import HermesSharingBackend
from tom_hermes.sharing import backend as hermes_backend


class ResolveCredentialsSettingsFallbackTests(TestCase):
    """``resolve_hermes_credentials`` must fall back to settings without a DeprecationWarning."""

    @override_settings(DATA_SHARING={
        'hermes': {'HERMES_API_KEY': 'from-settings', 'BASE_URL': 'https://h.example/'},
    })
    def test_no_user_uses_settings_api_key(self):
        # Background callers (management commands, signals) have no user;
        # the resolver must fall through to settings cleanly.
        creds = resolve_hermes_credentials(user=None)
        self.assertEqual(creds['api_key'], 'from-settings')
        self.assertEqual(creds['base_url'], 'https://h.example/')

    @override_settings(DATA_SHARING={
        'hermes': {'HERMES_API_KEY': 'from-settings', 'BASE_URL': 'https://h.example/'},
    })
    def test_anonymous_user_uses_settings_api_key(self):
        # An unauthenticated request still goes through the resolver.
        # AnonymousUser.is_authenticated is False, so the Profile lookup
        # branch is skipped and we fall through to settings.
        creds = resolve_hermes_credentials(user=AnonymousUser())
        self.assertEqual(creds['api_key'], 'from-settings')

    @override_settings(DATA_SHARING={})
    def test_missing_settings_returns_defaults(self):
        # When neither profile nor settings have HERMES config, api_key is
        # None and base_url defaults to the public instance.
        creds = resolve_hermes_credentials(user=None)
        self.assertIsNone(creds['api_key'])
        self.assertEqual(creds['base_url'], 'https://hermes.lco.global/')


class HermesSharingBackendDestinationChoicesTests(TestCase):
    """``get_destination_choices`` returns ``hermes:<topic>`` tuples sourced from HERMES topics."""

    def test_choices_come_from_get_hermes_topics(self):
        # Patch get_hermes_topics to avoid network; the backend does not
        # care where the topics come from, just that the list is iterable.
        with patch.object(hermes_backend, 'get_hermes_topics',
                          return_value=['hermes.test', 'gw.lvk.public']):
            choices = HermesSharingBackend.get_destination_choices(user=None)
        self.assertEqual(choices, [
            ('hermes:hermes.test', 'hermes.test'),
            ('hermes:gw.lvk.public', 'gw.lvk.public'),
        ])

    def test_empty_topics_returns_empty_choices(self):
        # No writable topics means the backend renders no dropdown entries;
        # the share-destination dropdown simply omits HERMES in that case.
        with patch.object(hermes_backend, 'get_hermes_topics', return_value=[]):
            self.assertEqual(HermesSharingBackend.get_destination_choices(user=None), [])

    @override_settings(DATA_SHARING={
        'hermes': {
            'HERMES_API_KEY': 'k',
            'BASE_URL': 'https://h.example/',
            'USER_TOPICS': ['hermes.test'],
        },
    })
    def test_user_topics_setting_intersects_with_hermes_topics(self):
        # When the operator configures USER_TOPICS, the dropdown should
        # show only the intersection of what HERMES says the user can
        # write to AND what the operator has whitelisted.
        with patch.object(hermes_backend, 'get_hermes_topics',
                          return_value=['hermes.test', 'gw.lvk.public']):
            choices = HermesSharingBackend.get_destination_choices(user=None)
        self.assertEqual(choices, [('hermes:hermes.test', 'hermes.test')])


class HermesSharingBackendValidateTests(TestCase):
    """``validate_credentials`` returns True iff an API key is available from any source."""

    @override_settings(DATA_SHARING={'hermes': {'HERMES_API_KEY': 'k'}})
    def test_settings_provides_api_key(self):
        self.assertTrue(HermesSharingBackend().validate_credentials(user=None))

    @override_settings(DATA_SHARING={})
    def test_no_credentials_anywhere_is_false(self):
        self.assertFalse(HermesSharingBackend().validate_credentials(user=None))


class HermesSharingBackendShareTests(TestCase):
    """``share`` parses the topic, builds a message, and calls ``publish_to_hermes``."""

    def test_share_calls_publish_to_hermes_with_parsed_topic(self):
        from tom_dataproducts.models import ReducedDatum
        from tom_targets.models import Target

        form_data = {
            'share_destination': 'hermes:test.topic',
            'share_title': 'My title',
            'submitter': 'alice',
            'share_message': 'details',
        }
        # Build a minimal real Target + ReducedDatum so ``_resolve_inputs``
        # can infer title_name and resolve the first datum's target. We
        # use real models rather than a fake queryset because
        # ``_resolve_inputs`` touches queryset methods (``.first()``,
        # ``.filter()``) that are awkward to mock comprehensively.
        target = Target.objects.create(name='SN_share_test', type='SIDEREAL', ra=0, dec=0)
        datum = ReducedDatum.objects.create(
            target=target, data_type='photometry',
            timestamp='2025-01-01T00:00:00Z',
            value={'magnitude': 20.0, 'filter': 'g', 'unit': 'AB'},
        )
        datums_qs = ReducedDatum.objects.filter(pk=datum.pk)

        # Patch out the actual publish + the datum-filter so no DB query
        # (beyond the qs above) and no network. We only care that
        # publish_to_hermes is called with the right BuildHermesMessage
        # (topic parsed out of share_destination).
        with patch.object(hermes_backend, 'publish_to_hermes') as pub_mock, \
             patch('tom_dataproducts.sharing.check_for_share_safe_datums',
                   side_effect=lambda destination, datums, **kwargs: datums):
            HermesSharingBackend().share(form_data, reduced_datums=datums_qs)

        # publish_to_hermes was called once; the message_info argument
        # carries topic='test.topic' parsed out of 'hermes:test.topic'.
        self.assertEqual(pub_mock.call_count, 1)
        message_info = pub_mock.call_args.args[0]
        self.assertEqual(message_info.topic, 'test.topic')
        self.assertEqual(message_info.title, 'My title')

    def test_share_rejects_missing_topic_separator(self):
        # A share_destination without the ':<topic>' half cannot be routed
        # to a HERMES topic and must return an error feedback dict.
        backend = HermesSharingBackend()
        result = backend.share({'share_destination': 'hermes'})
        self.assertIn('ERROR', result['message'])
