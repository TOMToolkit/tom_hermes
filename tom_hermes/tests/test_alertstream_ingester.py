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

from tom_alerts.models import AlertStreamMessage
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
        # The AlertStreamMessage record ties the datum to this ingestion
        # so a later publish knows not to re-emit it.
        self.assertEqual(datum.message.count(), 1)
        asm = datum.message.first()
        self.assertEqual(asm.topic, 'test.hermes.photometry')
        self.assertEqual(asm.exchange_status, 'ingested')
        # Summary shape: callers (notably HermesDataService.to_target) read
        # these keys, so check they are populated as the docstring says.
        # Compare by pk rather than instance identity because the summary's
        # Target was returned by create_new_hermes_target, while the one
        # fetched above came from a fresh ORM query.
        self.assertEqual(len(summary['targets']), 1)
        self.assertEqual(summary['targets'][0].pk, target.pk)
        self.assertEqual(len(summary['reduced_datums']), 1)
        self.assertEqual(summary['reduced_datums'][0].pk, datum.pk)
        self.assertIsNotNone(summary['alert_stream_message'])

    def test_second_ingest_of_same_message_is_noop(self):
        # Idempotence: the AlertStreamMessage lookup short-circuits the
        # second call so no duplicate Targets or ReducedDatums are created.
        ingest_hermes_alert(_message())
        second = ingest_hermes_alert(_message())
        self.assertEqual(Target.objects.filter(name='SN_test_001').count(), 1)
        self.assertEqual(ReducedDatum.objects.filter(data_type='photometry').count(), 1)
        # On the duplicate call the summary has no new rows but still
        # reports the AlertStreamMessage (already-ingested marker) so
        # the caller can tell this was a no-op, not a failure.
        self.assertEqual(second['targets'], [])
        self.assertEqual(second['reduced_datums'], [])
        self.assertIsNotNone(second['alert_stream_message'])

    def test_empty_message_returns_empty_summary(self):
        # A message with no photometry and no spectroscopy is a legitimate
        # shape (e.g. target-only); the function must return cleanly
        # without creating an AlertStreamMessage.
        summary = ingest_hermes_alert({'uuid': 'u', 'topic': 't', 'data': {}})
        self.assertIsNone(summary['alert_stream_message'])
        self.assertEqual(summary['targets'], [])
        self.assertEqual(AlertStreamMessage.objects.count(), 0)


class HermesAlertHandlerWrapsIngestTests(TestCase):
    """``hermes_alert_handler(alert, metadata)`` calls ``ingest_hermes_alert(alert, metadata)``."""

    def test_handler_forwards_to_ingest_function(self):
        # We patch ingest_hermes_alert where the handler imports it so the
        # real DB writes are not attempted.
        with patch.object(ingester, 'ingest_hermes_alert') as ingest_mock:
            metadata = object()  # opaque stand-in for hop.models.Metadata
            hermes_alert_handler('some-alert', metadata)
        ingest_mock.assert_called_once_with(alert='some-alert', metadata=metadata)
