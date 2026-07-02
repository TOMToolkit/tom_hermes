"""
HERMES publisher: HTTP calls, message assembly, and the data converter.

### What lives here

- ``HermesMessageException`` — raised by ``HermesDataConverter`` when a
  ReducedDatum cannot be translated into a valid HERMES row.
- ``HermesDataConverter`` and the three ``convert_astropy_*_unit_to_hermes``
  helpers — map TOM data (``Target``, ``ReducedDatum``) into the HERMES
  message schema. Subclassable; a TOM operator can point at a custom
  subclass via ``settings.DATA_SHARING['hermes']['DATA_CONVERTER_CLASS']``.
- ``BuildHermesMessage`` — small container for the human-authored parts
  of a HERMES message (title, authors, topic, etc.).
- ``publish_to_hermes`` / ``preload_to_hermes`` / ``create_hermes_alert``
  / ``get_hermes_topics`` — the HTTP calls and alert-body assembly.
- ``get_hermes_data_converter_class`` — looks up the configured converter
  class from settings, defaulting to ``HermesDataConverter``.

### What does NOT live here

- ``HermesSharingBackend`` — lives in ``tom_hermes.sharing.backend`` so
  the SharingBackend plug-in surface is separate from the HTTP code.
- ``resolve_hermes_credentials`` — lives in ``tom_hermes.credentials``
  (app-level) because credentials are not a sharing-only concern.

### Import graph

::

    backend.py  ─────►  publisher.py  (this file)
       │                    │
       └──►  tom_hermes.credentials  ◄──┘

All three point one way; no cycles. The functions in this file read
credentials via ``resolve_hermes_credentials(user)``, imported at the
top. The ``HermesSharingBackend`` in ``backend.py`` imports the public
names from here.

### Provenance

Functions and classes here were previously in
``tom_base/tom_dataproducts/alertstreams/hermes_publisher.py`` (merged
into tom_base branch ``1430-migrate-hermes-broker-to-data-service`` at
commit ``2df08f22``). They have been moved here as part of consolidating
HERMES-specific code into ``tom_hermes``. Credential reads have been
changed to go through ``tom_hermes.credentials.resolve_hermes_credentials``
so a per-user ``HermesProfile`` credential takes precedence over the
TOM-wide ``settings.HERMES_CONFIGURATION`` credential.
"""
from __future__ import annotations

import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils.module_loading import import_string

from tom_targets.models import Target

from tom_hermes.credentials import resolve_hermes_credentials

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception type raised by the data converter
# ---------------------------------------------------------------------------

class HermesMessageException(Exception):
    """Raised when a ReducedDatum cannot be translated into a valid HERMES row.

    Caught by ``publish_to_hermes`` and surfaced to the user as an
    error-shaped feedback dict rather than propagated.
    """


# ---------------------------------------------------------------------------
# Data converter (TOM models -> HERMES JSON rows)
# ---------------------------------------------------------------------------

def get_hermes_data_converter_class():
    """Return the configured HermesDataConverter class (defaults to ``HermesDataConverter``).

    A TOM operator can point at a custom converter by setting
    ``settings.HERMES_CONFIGURATION['DATA_CONVERTER_CLASS']`` to the
    dotted path of a subclass. 

    """
    hermes_cfg = getattr(settings, 'HERMES_CONFIGURATION', {})
    return import_string(hermes_cfg.get(
        'DATA_CONVERTER_CLASS', 'tom_hermes.publisher.HermesDataConverter'))


class HermesDataConverter:
    """Translate TOM models (Target, ReducedDatum) into HERMES message rows.

    Subclassable: a TOM whose ReducedDatum ``value`` dicts use a different
    shape/format can provide a subclass via the ``DATA_CONVERTER_CLASS`` setting
    and override any of the ``get_hermes_target`` / ``get_hermes_photometry``
    / ``get_hermes_spectroscopy`` methods to be compatible.
    """

    def __init__(self, validate=True):
        # ``validate=True`` enables the sanity checks in
        # ``get_hermes_spectroscopy`` (matched flux/wavelength lengths,
        # non-empty arrays). Turn off in tests or when feeding
        # known-good data to skip the checks.
        self.validate = validate

    def build_hermes_target_table_row(self, target):
        """Return a HERMES target-table row for a TOM BaseTarget instance.
        """
        if target.type == 'SIDEREAL':
            target_table_row = {
                'name': target.name,
                'ra': target.ra,
                'dec': target.dec,
            }
            if target.epoch:
                target_table_row['epoch'] = target.epoch
            if target.pm_ra:
                target_table_row['pm_ra'] = target.pm_ra
            if target.pm_dec:
                target_table_row['pm_dec'] = target.pm_dec
        else:
            target_table_row = {
                'name': target.name,
                'orbital_elements': {
                    'epoch_of_elements': target.epoch_of_elements,
                    'eccentricity': target.eccentricity,
                    'argument_of_the_perihelion': target.arg_of_perihelion,
                    'mean_anomaly': target.mean_anomaly,
                    'orbital_inclination': target.inclination,
                    'longitude_of_the_ascending_node': target.lng_asc_node,
                    'semimajor_axis': target.semimajor_axis,
                    'epoch_of_perihelion': target.epoch_of_perihelion,
                    'perihelion_distance': target.perihdist,
                },
            }
        target_table_row['aliases'] = [alias.name for alias in target.aliases.all()]
        return target_table_row

    def build_hermes_photometry_table_row(self, datum):
        """Return a HERMES photometry-table row for a TOM PhotometryReducedDatum.

        """
        phot_table_row = {
            'target_name': datum.target.name,
            'date_obs': datum.timestamp.isoformat(),
            'telescope': datum.value.get('telescope'),
            'instrument': datum.value.get('instrument'),
            'bandpass': datum.value.get('filter', ''),
        }
        brightness_unit = convert_astropy_brightness_unit_to_hermes(datum.value.get('unit'))
        if brightness_unit:
            phot_table_row['brightness_unit'] = brightness_unit
        if datum.value.get('magnitude', None):
            phot_table_row['brightness'] = datum.value['magnitude']
        else:
            phot_table_row['limiting_brightness'] = datum.value.get('limit', None)
        error_value = datum.value.get('error', datum.value.get('magnitude_error', None))
        if error_value is not None and isinstance(error_value, (int, float)):
            phot_table_row['brightness_error'] = error_value
        return phot_table_row

    def get_hermes_spectroscopy(self, datum):
        """Return a HERMES spectroscopy-table row for a TOM spectroscopy ReducedDatum.

        Tolerates two shapes of ``datum.value``:

        - ``{'flux': [...], 'wavelength': [...], 'flux_error': [...]}`` — the
          "arrays at top level" shape produced by the spectroscopy processor.
        - ``{'1': {'flux': ..., 'wavelength': ...}, '2': {...}, ...}`` — the
          legacy "dict of rows" shape.

        When ``self.validate`` is true, mismatched or empty arrays raise
        ``HermesMessageException``.
        """
        flux_list = []
        flux_error_list = []
        wavelength_list = []
        if 'flux' in datum.value and 'wavelength' in datum.value:
            flux_list = datum.value['flux']
            wavelength_list = datum.value['wavelength']
            flux_error_list = datum.value.get('flux_error', datum.value.get('error', []))
        else:
            # Legacy shape: a dict of rows. Pull flux / wavelength / error
            # fields from each row into parallel lists.
            for entry in datum.value.values():
                if 'flux' in entry:
                    flux_list.append(entry['flux'])
                if 'wavelength' in entry:
                    wavelength_list.append(entry['wavelength'])
                if 'error' in entry:
                    flux_error_list.append(entry['error'])
                if 'flux_error' in entry:
                    flux_error_list.append(entry['flux_error'])

        if self.validate:
            if len(flux_list) != len(wavelength_list):
                msg = f"Spectroscopy Datum {datum.id} has mismatched flux and wavelength values"
                logger.error(msg)
                raise HermesMessageException(msg)
            if len(flux_list) == 0 or len(wavelength_list) == 0:
                msg = (
                    f"Spectroscopy Datum {datum.id} has spectrum data in unknown format. "
                    f"Please implement a custom HermesDataConverter to support your data format."
                )
                logger.error(msg)
                raise HermesMessageException(msg)
            if flux_error_list and len(flux_error_list) != len(flux_list):
                msg = f"Spectroscopy Datum {datum.id} must have the same number of flux and flux error datapoints"
                logger.error(msg)
                raise HermesMessageException(msg)

        # Fall back to the DataProduct's ``extra_data`` for metadata keys
        # that are not on the ReducedDatum value dict. This matters when
        # a spectroscopy ReducedDatum was produced by the processor from
        # a HERMES-delivered file; the file-level metadata lives on the
        # DataProduct, not on each datum.
        try:
            dp_extras = json.loads(datum.data_product.extra_data)
        except (json.JSONDecodeError, ValueError, AttributeError):
            dp_extras = {}
        telescope = datum.value.get('telescope') or dp_extras.get('telescope')
        instrument = datum.value.get('instrument') or dp_extras.get('instrument')
        reducer = datum.value.get('reducer') or dp_extras.get('reducer')
        observer = datum.value.get('observer') or dp_extras.get('observer')

        spectroscopy_table_row = {
            'target_name': datum.target.name,
            'date_obs': datum.timestamp.isoformat(),
            'telescope': telescope,
            'instrument': instrument,
            'reducer': reducer,
            'observer': observer,
            'flux': flux_list,
            'wavelength': wavelength_list,
            'flux_units': convert_astropy_flux_unit_to_hermes(datum.value.get('flux_units')),
            'wavelength_units': convert_astropy_wavelength_unit_to_hermes(datum.value.get('wavelength_units')),
        }
        if flux_error_list:
            spectroscopy_table_row['flux_error'] = flux_error_list

        return spectroscopy_table_row


def convert_astropy_brightness_unit_to_hermes(brightness_unit):
    """Map an astropy brightness-unit string to the HERMES-expected spelling."""
    if not brightness_unit:
        return brightness_unit
    if brightness_unit.upper() == 'AB' or brightness_unit.upper() == 'ABFLUX':
        return 'AB mag'
    return brightness_unit


def convert_astropy_flux_unit_to_hermes(flux_unit):
    """Map an astropy flux-unit string to the HERMES-expected spelling."""
    if not flux_unit:
        return flux_unit
    if flux_unit == 'erg / (Angstrom s cm2)':
        return 'erg / s / cm² / Å'
    return flux_unit


def convert_astropy_wavelength_unit_to_hermes(wavelength_unit):
    """Map an astropy wavelength-unit string to the HERMES-expected spelling."""
    if not wavelength_unit:
        return wavelength_unit
    if wavelength_unit.lower() == 'angstrom' or wavelength_unit == 'AA':
        return 'Å'
    if wavelength_unit.lower() in ['micron', 'micrometer', 'um']:
        return 'µm'
    if wavelength_unit.lower() == 'hertz':
        return 'Hz'
    return wavelength_unit


# ---------------------------------------------------------------------------
# Human-authored message container
# ---------------------------------------------------------------------------

class BuildHermesMessage:
    """Human-authored parts of a HERMES message (title, authors, topic, etc.).

    Assembled by ``HermesSharingBackend.share`` from ``form_data`` fields
    and then passed into ``publish_to_hermes`` to be combined with the
    machine-produced target/photometry/spectroscopy tables.
    """

    def __init__(self, title='', submitter='', authors='', message='', topic='hermes.test', **kwargs):
        self.title = title
        self.submitter = submitter
        self.authors = authors
        self.message = message
        self.topic = topic
        # Any additional keyword arguments are preserved and emitted under
        # the message's ``data.extra_data`` key (see ``create_hermes_alert``).
        self.extra_info = kwargs


# ---------------------------------------------------------------------------
# Publishing functions (HTTP calls to HERMES)
# ---------------------------------------------------------------------------

def publish_to_hermes(message_info, datums, targets=None, *, user=None, **kwargs):
    """POST a fully-assembled HERMES message to ``/api/v0/submit_message/``.

    Credential lookup: ``tom_hermes.credentials.resolve_hermes_credentials(user)``.
    Reads the HERMES API key first from the User's ``HermesProfile`` and
    falls back to ``settings.HERMES_CONFIGURATION['HERMES_API_TOKEN']``.

    No side effect on the local DB: the published datums keep whatever
    ``source_name`` they already had. (Earlier versions of this code
    stamped each shared datum with an ``AlertStreamMessage`` row whose
    ``exchange_status='published'``; that model has been removed in the
    larger refactor — both the model itself in ``tom_alerts`` and the
    ``ReducedDatum.message`` M2M field that referenced it. The round-trip
    safety guard in ``check_for_share_safe_datums`` now works on
    ``source_name`` directly: it excludes datums whose
    ``source_name == f'Hermes:{topic}'``, so a datum that originated from
    this same topic is filtered out by the caller before the share
    request reaches us.)

    Returns the ``requests.Response`` on success, or an error-shaped
    feedback dict ``{'message': 'ERROR: ...'}`` on setup/build failure.
    """
    if targets is None:
        targets = Target.objects.none()

    creds = resolve_hermes_credentials(user)
    if not creds.get('api_key'):
        return {'message': (
            'No HERMES API key available. Configure either per-user credentials on '
            "the user's HermesProfile page, or TOM-wide credentials at "
            "settings.HERMES_CONFIGURATION['HERMES_API_TOKEN']."
        )}
    if not creds.get('base_url'):
        return {'message': (
            'No HERMES BASE_URL configured. Set settings.HERMES_CONFIGURATION["HERMES_BASE_URL"].'
        )}

    stream_base_url = creds['base_url']
    submit_url = stream_base_url + 'api/v0/submit_message/'
    headers = {'Authorization': f"Token {creds['api_key']}"}

    # Build the HERMES-schema JSON body from the TOM models.
    try:
        alert = create_hermes_alert(message_info, datums, targets, **kwargs)
    except HermesMessageException as e:
        return {'message': 'ERROR: ' + str(e)}

    # Submit. If the POST or the status check raises, surface the HTTP
    # response to the caller so they can inspect it; don't crash the
    # request-handling view.
    response = None
    try:
        response = requests.post(url=submit_url, json=alert, headers=headers)
        response.raise_for_status()
    except Exception as ex:
        logger.error(repr(ex))
        if response is not None:
            logger.error(response.content)
        return response if response is not None else {'message': f'ERROR: {ex!r}'}

    # Log a one-line summary of the successful publish. Without this the
    # only operator-visible signal in the runserver log is the bare
    # ``POST /share/ ... 302`` from Django's request log; the
    # HERMES-assigned uuid (the message id we publish under) is otherwise
    # invisible to the operator.
    message_uuid = response.json().get('uuid', '<no-uuid>')
    logger.info(
        f'publish_to_hermes: published topic={message_info.topic} '
        f'message_uuid={message_uuid}'
    )
    return response


def preload_to_hermes(message_info, reduced_datums, targets, *, user=None):
    """POST a dry-run assembly to HERMES ``/api/v0/submit_message/preload/``, returning the preload key.

    Used by the HERMES UI preview flow: the caller assembles a message,
    preloads it (HERMES returns a server-side key that references the
    draft), and then redirects the User to the HERMES draft page keyed
    by that value. Returns the empty string on failure.
    """
    creds = resolve_hermes_credentials(user)
    if not creds.get('api_key') or not creds.get('base_url'):
        return ''

    preload_url = creds['base_url'] + 'api/v0/submit_message/preload/'
    headers = {'Authorization': f"Token {creds['api_key']}"}

    alert = create_hermes_alert(message_info, reduced_datums, targets)
    response = None
    try:
        response = requests.post(url=preload_url, json=alert, headers=headers)
        response.raise_for_status()
        return response.json()['key']
    except Exception as ex:
        logger.error(repr(ex))
        if response is not None:
            logger.error(response.content)

    return ''


def create_hermes_alert(message_info, datums, targets=None, **kwargs):
    """Assemble a HERMES-schema JSON body from a BuildHermesMessage + datums + targets.

    Walks ``datums`` once, converting each photometry/spectroscopy datum
    into a HERMES row and collecting the associated Target into a dict
    keyed by name (so every referenced Target appears exactly once in
    the target table). Then walks ``targets`` to add any Targets that
    have no datums but should still appear in the message (e.g., a
    target-only announcement).

    Raises ``HermesMessageException`` if the data converter rejects a
    spectroscopy datum (mismatched array lengths, etc.).
    """
    if targets is None:
        targets = Target.objects.none()

    hermes_photometry_data = []
    hermes_spectroscopy_data = []
    # dict[target_name -> hermes target-table row], used to de-duplicate
    # targets when the same target appears in multiple datums.
    hermes_target_dict: dict = {}

    hermes_data_converter = get_hermes_data_converter_class()(validate=True)
    for datum in datums:
        if datum.target.name not in hermes_target_dict:
            hermes_target_dict[datum.target.name] = hermes_data_converter.build_hermes_target_table_row(datum.target)
        if datum.data_type == 'photometry':
            hermes_photometry_data.append(hermes_data_converter.build_hermes_photometry_table_row(datum))
        elif datum.data_type == 'spectroscopy':
            hermes_spectroscopy_data.append(hermes_data_converter.get_hermes_spectroscopy(datum))

    # Add any targets that have no datums but should still be in the
    # message (target-only announcements, where the point is to broadcast
    # the Target).
    for target in targets:
        if target.name not in hermes_target_dict:
            hermes_target_dict[target.name] = hermes_data_converter.build_hermes_target_table_row(target)

    alert = {
        'topic': message_info.topic,
        'title': message_info.title,
        'submitter': message_info.submitter,
        'authors': message_info.authors,
        'data': {
            'targets': list(hermes_target_dict.values()),
            'photometry': hermes_photometry_data,
            'spectroscopy': hermes_spectroscopy_data,
            'extra_data': message_info.extra_info,
        },
        'message_text': message_info.message,
    }
    return alert


def get_hermes_topics(*, user=None, **kwargs):
    """Return the list of topics this User is permitted to publish to.

    Calls HERMES ``/api/v0/profile/`` using the User's resolved
    credentials and reads the ``writable_topics`` key from the response.
    Results are cached for a day.

    The cache key includes the User's id (or 'shared' when there is no
    User) so two different Users on the same TOM do not see each other's
    cached topic lists — a User whose Profile has different credentials
    gets their own cache entry.
    """
    cache_key = f'hermes_writable_topics:{user.id}' if (user and getattr(user, 'id', None)) else (
        'hermes_writable_topics:shared'
    )
    topics = cache.get(cache_key, [])
    if topics:
        return topics

    creds = resolve_hermes_credentials(user)
    if not creds.get('api_key') or not creds.get('base_url'):
        return []

    submit_url = creds['base_url'] + 'api/v0/profile/'
    headers = {'Authorization': f"Token {creds['api_key']}"}
    try:
        response = requests.get(url=submit_url, headers=headers)
        topics = response.json()['writable_topics']
        # 86400 seconds = 1 day. Matches the previous behavior. Long
        # enough that form-render is cheap; short enough that a
        # topic-permission change propagates within a day without
        # manual cache eviction.
        cache.set(cache_key, topics, 86400)
    except (KeyError, requests.exceptions.JSONDecodeError, requests.exceptions.RequestException):
        # Degrade gracefully: an empty list means the share-destination
        # form for HERMES simply shows no topics; the form still renders.
        pass
    return topics
