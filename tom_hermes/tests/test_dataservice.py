"""
Tests for ``tom_hermes.dataservices.hermes.HermesDataService``.

Covers the main DataService framework seams:

- ``build_query_parameters`` maps form fields to HERMES /query params.
- ``query_service`` issues an HTTP GET to the query URL.
- ``get_topic_choices`` caches results so the form render does not hit HERMES twice.
- ``to_target`` delegates to ``ingest_hermes_alert`` (shared with the stream path).
- ``get_additional_context_data`` reports whether tom_nonlocalizedevents is installed.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import TestCase, override_settings

from tom_hermes.dataservices.hermes import HermesDataService, _flatten_hermes_archive_row


class BuildQueryParametersTests(TestCase):
    def test_only_fields_user_set_appear_in_params(self):
        svc = HermesDataService()
        params = svc.build_query_parameters({'search': 'kilonova'})
        # Empty / absent fields are dropped; page_size is always set.
        self.assertEqual(params, {'search': 'kilonova', 'page_size': 25})

    def test_topic_list_passes_through(self):
        svc = HermesDataService()
        params = svc.build_query_parameters({'topics': ['a', 'b']})
        # requests handles list values as repeatable ?topic=a&topic=b query args.
        self.assertEqual(params['topic'], ['a', 'b'])

    def test_all_fields_map_correctly(self):
        svc = HermesDataService()
        params = svc.build_query_parameters({
            'search': 'sn',
            'topics': ['hermes.test'],
            'published_after': '2025-01-01',
            'published_before': '2025-12-31',
        })
        self.assertEqual(params, {
            'search': 'sn',
            'topic': ['hermes.test'],
            'published_after': '2025-01-01',
            'published_before': '2025-12-31',
            'page_size': 25,
        })


class QueryServiceTests(TestCase):
    def test_query_service_issues_get_with_params(self):
        svc = HermesDataService()
        fake_response = MagicMock()
        fake_response.json.return_value = {'results': [{'uuid': 'x'}]}
        with patch('tom_hermes.dataservices.hermes.requests.get',
                   return_value=fake_response) as get_mock:
            result = svc.query_service({'search': 'x', 'page_size': 25})
        self.assertEqual(result, {'results': [{'uuid': 'x'}]})
        # The backend hits the query_url (not the topics_url) with the
        # params dict from build_query_parameters.
        get_mock.assert_called_once()
        call_kwargs = get_mock.call_args.kwargs
        self.assertEqual(call_kwargs['params'], {'search': 'x', 'page_size': 25})
        self.assertIn('/api/v0/query', get_mock.call_args.args[0])


class QueryTargetsTests(TestCase):
    # ``query_targets`` flattens each HERMES archive row by promoting
    # ``metadata.topic`` / ``annotations.title`` / … up to the top level
    # (see ``_flatten_hermes_archive_row``). The assertions below use
    # ``assertIn`` / dict comparisons that tolerate those extra keys.

    def test_handles_paginated_dict_response(self):
        svc = HermesDataService()
        svc.query_results = {'results': [{'uuid': 'a'}, {'uuid': 'b'}], 'count': 2}
        out = svc.query_targets({})
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['uuid'], 'a')
        self.assertEqual(out[1]['uuid'], 'b')

    def test_handles_bare_list_response(self):
        svc = HermesDataService()
        svc.query_results = [{'uuid': 'a'}]
        out = svc.query_targets({})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['uuid'], 'a')

    def test_handles_hermes_messages_response(self):
        # The live HERMES /api/v0/query endpoint (verified 2026-04-23)
        # returns ``{'messages': [...], 'next': '<cursor>', 'prev': '<cursor>'}``
        # — cursor pagination with the row list under 'messages', not
        # 'results'. Regression guard so a future refactor doesn't break
        # this again.
        svc = HermesDataService()
        svc.query_results = {
            'messages': [
                {'metadata': {'topic': 'hermes.test', 'timestamp': 1776973782947},
                 'annotations': {'title': 'hello', 'sender': 'alice',
                                 'con_text_uuid': 'uuid-a'}},
            ],
            'next': '>cursor-fwd',
            'prev': '<cursor-back',
        }
        out = svc.query_targets({})
        self.assertEqual(len(out), 1)
        row = out[0]
        # Flattened convenience fields for the results template.
        self.assertEqual(row['topic'], 'hermes.test')
        self.assertEqual(row['title'], 'hello')
        self.assertEqual(row['submitter'], 'alice')
        self.assertEqual(row['uuid'], 'uuid-a')
        # ``published`` is an ISO-8601 UTC string derived from the ms timestamp.
        self.assertTrue(row['published'].startswith('2026'))
        # Original nested fields remain for downstream code (e.g. to_target).
        self.assertEqual(row['metadata']['topic'], 'hermes.test')
        self.assertEqual(row['annotations']['title'], 'hello')


# ``get_topic_choices`` requires credentials (the HERMES /topics/ endpoint
# returns 403 without auth). These tests use settings-based credentials
# (HERMES_API_TOKEN in HERMES_CONFIGURATION) so they exercise the real code
# path without needing a test User with a HermesProfile and session cipher.
#
# We also override CACHES to use an in-memory backend for this test class.
# Some integration TOMs (including the reference TOM_hermes_migration) use
# a file-based or otherwise persistent cache; without this override the
# test writes would persist on disk and corrupt the live server's cache.
# The LocMemCache used here is per-process and wiped when the test
# process exits, so test pollution of the production cache becomes impossible.
@override_settings(
    HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'test-key', 'BASE_URL': 'https://hermes.example/'},
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'tom-hermes-test-topic-choices',
        },
    },
)
class GetTopicChoicesTests(TestCase):
    def setUp(self):
        # Clear the per-user cache entry this test class will populate.
        # Belt-and-suspenders alongside the CACHES override: resets state
        # between tests within the class.
        cache.delete('tom_hermes:topics:shared')

    def test_caches_after_first_call(self):
        # First call fetches; second call must not hit the network.
        first_response = MagicMock()
        first_response.json.return_value = ['a', 'b']
        with patch('tom_hermes.dataservices.hermes.requests.get',
                   return_value=first_response) as get_mock:
            HermesDataService.get_topic_choices()
            HermesDataService.get_topic_choices()
        self.assertEqual(get_mock.call_count, 1)

    def test_paginated_response_shape(self):
        # HERMES may return either a bare list or a DRF-style
        # {'results': [...]} envelope. Accept both.
        paginated = MagicMock()
        paginated.json.return_value = {'results': ['x', 'y']}
        with patch('tom_hermes.dataservices.hermes.requests.get',
                   return_value=paginated):
            choices = HermesDataService.get_topic_choices()
        self.assertEqual(choices, [('x', 'x'), ('y', 'y')])

    def test_network_error_returns_empty_list(self):
        import requests
        with patch('tom_hermes.dataservices.hermes.requests.get',
                   side_effect=requests.RequestException('boom')):
            choices = HermesDataService.get_topic_choices()
        self.assertEqual(choices, [])

    @override_settings(HERMES_CONFIGURATION={})
    def test_no_credentials_returns_empty_without_http_call(self):
        # New behavior: get_topic_choices short-circuits to [] before
        # making any HTTP call if no credentials are available. This
        # keeps /topics/ from 403-ing for per-user-only TOMs where the
        # form-render path has no user context.
        with patch('tom_hermes.dataservices.hermes.requests.get') as get_mock:
            choices = HermesDataService.get_topic_choices()
        self.assertEqual(choices, [])
        self.assertEqual(get_mock.call_count, 0)


class ToTargetDelegatesToIngesterTests(TestCase):
    def test_to_target_fetches_full_message_then_calls_ingest_hermes_alert(self):
        # to_target does a two-step dance because the archive /query
        # response is metadata-only:
        #   1. fetch the full message body by uuid
        #      (GET /api/v0/query/message/<uuid>/)
        #   2. unwrap ``body['message']`` (the archive wraps the published
        #      body alongside metadata+annotations)
        #   3. delegate to ingest_hermes_alert with the unwrapped body.
        svc = HermesDataService()
        fake_target = MagicMock(name='Target')
        summary = {
            'targets': [fake_target],
            'reduced_datums': [],
            'data_products': [],
            'target_extras': {'foo': 'bar'},
            'aliases': ['alt_name'],
            'alert_stream_message': None,
        }
        # The shape /api/v0/query/message/<uuid>/ actually returns: three
        # top-level keys (metadata, annotations, message). ``message`` is
        # the originally-POSTed body with topic/title/data.*/message_text.
        published_message = {
            'topic': 'tomtoolkit.test',
            'title': 'UC3 fixture',
            'data': {'targets': [{'name': 'M31', 'ra': 10.68, 'dec': 41.27}]},
        }
        full_body = {
            'metadata': {'topic': 'tomtoolkit.test', 'timestamp': 0},
            'annotations': {'title': 'UC3 fixture', 'con_text_uuid': 'x'},
            'message': published_message,
        }
        with patch.object(svc, '_fetch_full_message', return_value=full_body) as fetch_mock, \
             patch('tom_hermes.dataservices.hermes.ingest_hermes_alert',
                   return_value=summary) as ingest_mock:
            target, extras, aliases = svc.to_target({'uuid': 'x'})
        fetch_mock.assert_called_once_with('x')
        # ingest is called with the UNWRAPPED published message, not
        # the outer envelope — that is the shape ingest_hermes_alert
        # expects (same shape as a hop-stream-delivered message).
        ingest_mock.assert_called_once_with(alert=published_message, metadata=None)
        self.assertIs(target, fake_target)
        self.assertEqual(extras, {'foo': 'bar'})
        self.assertEqual(aliases, ['alt_name'])

    def test_to_target_returns_empty_when_message_key_absent(self):
        # If the HERMES response is well-formed JSON but lacks the
        # ``message`` wrapper (older API version, bug, permission-gated
        # fallback), to_target logs and returns empty rather than passing
        # the outer envelope to ingest_hermes_alert (which would silently
        # do nothing because there's no ``data`` key).
        svc = HermesDataService()
        with patch.object(svc, '_fetch_full_message',
                          return_value={'metadata': {}, 'annotations': {}}):
            target, extras, aliases = svc.to_target({'uuid': 'x'})
        self.assertIsNone(target)

    def test_to_target_falls_back_to_annotations_uuid(self):
        # If the flat top-level 'uuid' is absent but the archive row's
        # 'annotations.con_text_uuid' is present (e.g., the caller passed
        # an un-flattened archive row), to_target still finds the uuid.
        svc = HermesDataService()
        with patch.object(svc, '_fetch_full_message', return_value={}) as fetch_mock, \
             patch('tom_hermes.dataservices.hermes.ingest_hermes_alert',
                   return_value={'targets': []}):
            svc.to_target({'annotations': {'con_text_uuid': 'from-annotations'}})
        fetch_mock.assert_called_once_with('from-annotations')

    def test_to_target_returns_empty_when_no_uuid(self):
        # Defensive: if we cannot determine which message to fetch,
        # return (None, {}, []) so the framework skips this row.
        svc = HermesDataService()
        target, extras, aliases = svc.to_target({'some_other_field': 'x'})
        self.assertIsNone(target)
        self.assertEqual(extras, {})
        self.assertEqual(aliases, [])

    def test_to_target_returns_empty_when_fetch_fails(self):
        # Network error / 404 / etc. — _fetch_full_message returns None.
        # to_target must not crash; it returns the framework's expected
        # empty tuple so CreateTargetFromQueryView moves on.
        svc = HermesDataService()
        with patch.object(svc, '_fetch_full_message', return_value=None):
            target, extras, aliases = svc.to_target({'uuid': 'x'})
        self.assertIsNone(target)

    def test_to_target_requires_target_result(self):
        svc = HermesDataService()
        with self.assertRaises(ValueError):
            svc.to_target(None)


class AdditionalContextTests(TestCase):
    def test_reports_tom_nonlocalizedevents_install_status(self):
        svc = HermesDataService()
        ctx = svc.get_additional_context_data()
        # tom_nonlocalizedevents is not installed in the test TOM's INSTALLED_APPS.
        self.assertFalse(ctx['tom_nonlocalizedevents_installed'])
        # version is whatever tom_hermes.__version__ is at test time.
        self.assertIn('version', ctx)


class FlattenHermesArchiveRowTests(TestCase):
    """``_flatten_hermes_archive_row`` promotes nested metadata/annotations to flat top-level keys.

    Previously covered only indirectly via ``QueryTargetsTests.test_handles_hermes_messages_response``;
    these tests exercise the helper directly so edge cases (missing
    metadata, missing annotations, setdefault behaviour, bad timestamp)
    are guarded against regression.
    """

    def test_flattens_standard_archive_row(self):
        row = {
            'metadata': {'topic': 'hermes.test', 'timestamp': 1776973782947},
            'annotations': {'title': 'hello', 'sender': 'alice', 'con_text_uuid': 'uuid-a'},
        }
        _flatten_hermes_archive_row(row)
        self.assertEqual(row['topic'], 'hermes.test')
        self.assertEqual(row['title'], 'hello')
        self.assertEqual(row['submitter'], 'alice')
        self.assertEqual(row['uuid'], 'uuid-a')
        # ``published`` is an ISO-8601 UTC string derived from the ms timestamp.
        self.assertTrue(row['published'].startswith('2026'))
        # Original nested fields remain so downstream code (to_target) can still reach them.
        self.assertEqual(row['metadata']['topic'], 'hermes.test')
        self.assertEqual(row['annotations']['title'], 'hello')

    def test_missing_metadata_yields_empty_topic_and_no_published(self):
        # A row without ``metadata`` (defensive: malformed HERMES response)
        # gets an empty ``topic`` and no ``published`` key — the helper
        # only sets ``published`` when a usable timestamp is present.
        row = {'annotations': {'title': 'hello'}}
        _flatten_hermes_archive_row(row)
        self.assertEqual(row['topic'], '')
        self.assertNotIn('published', row)
        self.assertEqual(row['title'], 'hello')

    def test_missing_annotations_yields_empty_strings(self):
        row = {'metadata': {'topic': 'x'}}
        _flatten_hermes_archive_row(row)
        self.assertEqual(row['title'], '')
        self.assertEqual(row['submitter'], '')
        self.assertEqual(row['uuid'], '')

    def test_setdefault_preserves_existing_flat_keys(self):
        # If a row already has ``topic`` at the top level (e.g. a future
        # HERMES API version returns it natively), the flattening is a
        # no-op for that key — ``setdefault`` does not overwrite.
        row = {
            'topic': 'already-flat',
            'metadata': {'topic': 'should-not-overwrite'},
            'annotations': {},
        }
        _flatten_hermes_archive_row(row)
        self.assertEqual(row['topic'], 'already-flat')

    def test_bad_timestamp_yields_empty_published(self):
        # A non-numeric timestamp must not crash; ``published`` becomes
        # an empty string so the template renders something benign.
        row = {
            'metadata': {'timestamp': 'not-a-number'},
            'annotations': {},
        }
        _flatten_hermes_archive_row(row)
        self.assertEqual(row['published'], '')


class BuildHeadersTests(TestCase):
    """``build_headers`` derives Authorization: Token <api_key> from resolve_hermes_credentials.

    Previously covered only indirectly via the QueryServiceTests setup;
    these tests exercise build_headers directly so the user-threading and
    no-credentials behaviours are guarded against regression.
    """

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
