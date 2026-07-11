"""
Tests for ``tom_hermes.alertstreams.ingester``.

Verifies that:

- ``ingest_hermes_alert`` creates Targets and ReducedDatums for a HERMES
  photometry message, and populates the returned summary dict shape.
- ``ingest_hermes_alert`` is idempotent: re-ingesting the same message
  (same uuid + topic) does not create duplicate rows.
- ``hermes_alert_handler`` is a thin wrapper that forwards to
  ``ingest_hermes_alert``.
"""
from __future__ import annotations

import copy
from unittest.mock import patch

from django.test import TestCase

from tom_dataproducts.models import ReducedDatum
from tom_hermes.alertstreams import ingester
from tom_hermes.alertstreams.ingester import hermes_alert_handler, ingest_hermes_alert
from tom_targets.models import Target


# A minimal HERMES photometry message. One target, one detection row.
# The ``uuid`` + ``topic`` at the top level are what the archive path
# (metadata=None) uses for idempotence keying.
#
# ``create_new_hermes_target`` pops keys out of its target_table argument
# as it reads them. Tests should pass a deep copy so the module-level
# constant is not mutated between test runs.
PHOTOMETRY_MESSAGE = {
    'uuid': '11111111-2222-3333-4444-555555555555',
    'topic': 'test.hermes.photometry',
    'data': {
        'targets': [
            {'name': 'SN_test_001', 'ra': 150.0, 'dec': 20.0},
        ],
        'photometry': [
            {
                'target_name': 'SN_test_001',
                'date_obs': '2025-01-01T00:00:00Z',
                'bandpass': 'g',
                'brightness': 20.0,
                'brightness_unit': 'AB',
                'brightness_error': 0.1,
            },
        ],
    },
}


def _message():
    """Return a fresh deep-copy of PHOTOMETRY_MESSAGE for each call."""
    return copy.deepcopy(PHOTOMETRY_MESSAGE)


class IngestHermesAlertTests(TestCase):
    """End-to-end: feed a synthetic HERMES message, inspect the TOM database."""

    def test_photometry_message_creates_target_and_reduced_datum(self):
        summary = ingest_hermes_alert(_message())
        # A new Target was created for SN_test_001 with the ra/dec from the message.
        target = Target.objects.get(name='SN_test_001')
        self.assertEqual(target.ra, 150.0)
        self.assertEqual(target.dec, 20.0)
        # One ReducedDatum was created, attached to that Target.
        datum = ReducedDatum.objects.get(target=target, data_type='photometry')
        self.assertEqual(datum.value['magnitude'], 20.0)
        # Provenance lives on source_name + source_location so a later
        # share knows the datum came from this HERMES topic and the
        # consumer can fetch the original message body via the API URL.
        self.assertEqual(datum.source_name, 'Hermes:test.hermes.photometry')
        self.assertTrue(datum.source_location.endswith(
            f'/api/v0/query/message/{PHOTOMETRY_MESSAGE["uuid"]}/'))
        # Summary shape: callers (notably HermesDataService.to_target) read
        # these keys, so check they are populated as the docstring says.
        # Compare by pk rather than instance identity because the summary's
        # Target was returned by create_new_hermes_target, while the one
        # fetched above came from a fresh ORM query.
        self.assertEqual(len(summary['targets']), 1)
        self.assertEqual(summary['targets'][0].pk, target.pk)
        self.assertEqual(len(summary['reduced_datums']), 1)
        self.assertEqual(summary['reduced_datums'][0].pk, datum.pk)

    def test_second_ingest_of_same_message_is_noop(self):
        # Idempotence: per-row, via ReducedDatum.objects.get_or_create()
        # keyed on (target, data_type, timestamp, value). Re-ingesting the
        # same message creates no duplicate Targets or ReducedDatums.
        ingest_hermes_alert(_message())
        second = ingest_hermes_alert(_message())
        self.assertEqual(Target.objects.filter(name='SN_test_001').count(), 1)
        self.assertEqual(ReducedDatum.objects.filter(data_type='photometry').count(), 1)
        # The second call resolves the matched Target through resolve_target
        # so it appears in summary['targets'], but no new ReducedDatum was
        # created so summary['reduced_datums'] is empty.
        self.assertEqual(len(second['targets']), 1)
        self.assertEqual(second['targets'][0].name, 'SN_test_001')
        self.assertEqual(second['reduced_datums'], [])

    def test_empty_message_returns_empty_summary(self):
        # A message with no photometry and no spectroscopy is a legitimate
        # shape (e.g. target-only); the function must return cleanly
        # with no Targets or ReducedDatums.
        summary = ingest_hermes_alert({'uuid': 'u', 'topic': 't', 'data': {}})
        self.assertEqual(summary['targets'], [])
        self.assertEqual(summary['reduced_datums'], [])
        self.assertEqual(Target.objects.count(), 0)
        self.assertEqual(ReducedDatum.objects.count(), 0)


class HermesAlertHandlerWrapsIngestTests(TestCase):
    """``hermes_alert_handler(alert, metadata)`` calls ``ingest_hermes_alert(alert, metadata)``."""

    def test_handler_forwards_to_ingest_function(self):
        # We patch ingest_hermes_alert where the handler imports it so the
        # real DB writes are not attempted. The mock returns the empty-summary
        # shape the handler reads its log-line counts from.
        with patch.object(ingester, 'ingest_hermes_alert') as ingest_mock:
            ingest_mock.return_value = {
                'targets': [],
                'reduced_datums': [],
                'data_products': [],
            }
            metadata = object()  # opaque stand-in for hop.models.Metadata
            hermes_alert_handler('some-alert', metadata)
        ingest_mock.assert_called_once_with(alert='some-alert', metadata=metadata)
