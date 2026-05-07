"""
HERMES-message ingestion: writes TOM database rows (Targets, ReducedDatums, DataProducts) from a single HERMES message.

### Two caller contexts, one implementation

``ingest_hermes_alert(alert, metadata=None)`` is the one function that writes
data from a HERMES message into the TOM database. It is called from two
places:

1. ``hermes_alert_handler(alert, metadata)`` — the Hopskotch topic handler
   registered in ``settings.ALERT_STREAMS.TOPIC_HANDLERS``. ``alert`` is a
   ``hop.models.JSONBlob``-like object (``alert.content`` holds the message
   dict) and ``metadata`` is hop-client metadata carrying ``topic`` and
   headers (including the ``_id`` UUID).
2. ``tom_hermes.dataservices.hermes.HermesDataService.to_target`` — when a
   user selects a message from a DataService query result and the framework
   dispatches ``to_target`` per row. ``alert`` is the plain message dict as
   returned by HERMES ``/api/v0/query``; ``metadata`` is ``None``.

Keeping the two paths on one implementation guarantees that a message
ingested live off the stream writes the same rows to the database as the
same message ingested later from the archive.

### Idempotence

Per-row, via ``ReducedDatum.objects.get_or_create()`` keyed on
``(target, data_type, timestamp, value)`` and ``DataProduct.objects.get_or_create()``
keyed on ``product_id`` (the file URL, for downloaded spectroscopy
files). Re-ingesting the same message returns existing rows; nothing
is duplicated.

### Provenance

Each ReducedDatum carries ``source_name = f'Hermes:{topic}'`` and
``source_location = f'{hermes_base_url}/api/v0/query/message/{alert_id}/'``,
the per-message HERMES API URL. ``tom_dataproducts.sharing.check_for_share_safe_datums``
uses ``source_name`` to prevent re-publishing a datum to its origin topic.

### Return value

Returns a summary dict::

    {
        'targets':        [Target, ...],         # Targets created or matched
        'reduced_datums': [ReducedDatum, ...],   # newly-created rows only
        'data_products':  [DataProduct, ...],    # newly-downloaded spectroscopy files only
        'target_extras':  {...},                 # extras for the first target
        'aliases':        [str, ...],            # aliases for the first target
    }

Callers that want a single Target (the DataService framework's ``to_target``
contract) pick ``summary['targets'][0]`` and use ``target_extras`` /
``aliases`` directly.

### Provenance and history

Source of this module: previously ``tom_base/tom_dataproducts/alertstreams/hermes_ingester.py``
on branch ``fix/hermes_ingestion`` (merged into
``1430-migrate-hermes-broker-to-data-service`` at commit ``2df08f22``). Moved
here as part of consolidating HERMES-specific code into ``tom_hermes``. The
original ``hermes_alert_handler`` body has been split into a caller-agnostic
``ingest_hermes_alert`` plus a thin ``hermes_alert_handler`` wrapper so that
the stream-ingest and query-ingest paths share one implementation.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from urllib.parse import urljoin, urlparse

import requests
from dateutil.parser import parse
from django.conf import settings
from django.core.files import File

from tom_dataproducts.data_processor import run_data_processor
from tom_dataproducts.models import DataProduct, ReducedDatum
from tom_targets.models import Target, TargetList

logger = logging.getLogger(__name__)

# Spectroscopy file extensions that ``_ingest_hermes_spectroscopy_file``
# recognizes in a HERMES message's ``file_info`` list. First match wins.
HERMES_SPECTROSCOPY_FILE_EXTENSIONS = ('.fits.fz', '.fits', '.csv', '.txt', '.ascii')


def get_or_create_uuid_from_metadata(metadata) -> uuid.UUID:
    """Return the message UUID from hop-client metadata headers, or a fresh uuid4 if absent.

    The ``headers`` property of hop-client ``Metadata`` is a list of
    ``(key, bytes_value)`` tuples. HERMES messages carry their UUID in
    the header whose key is ``'_id'``; the value is the 16-byte big-endian
    UUID.

    If the metadata has no ``_id`` header we generate a fresh uuid4 so the
    caller still has an alert_id to embed in ``source_location``.
    """
    message_uuid_tuple = None
    if metadata.headers:
        message_uuid_tuple = next(
            (item for item in metadata.headers if item[0] == '_id'), None,
        )
    if message_uuid_tuple:
        return uuid.UUID(bytes=message_uuid_tuple[1])
    return uuid.uuid4()


def _resolve_alert_identity(alert_as_dict: dict, metadata) -> tuple:
    """Return ``(alert_id, topic)`` from either hop-client metadata or the message body.

    Called by ``ingest_hermes_alert`` at the top of the function so the
    rest of the code does not have to branch on the caller context.

    - Stream path (``metadata`` is not None): ``alert_id`` comes from the
      hop-client ``_id`` header; ``topic`` comes from ``metadata.topic``.
    - Archive / DataService path (``metadata`` is None): ``alert_id`` comes
      from the message body's ``uuid`` field if present, otherwise a fresh
      uuid4. ``topic`` comes from the message body's ``topic`` field if
      present, otherwise the literal string ``'<unknown>'``.
    """
    if metadata is not None:
        return get_or_create_uuid_from_metadata(metadata), metadata.topic
    # DataService / archive path: no hop-client headers, so pull identity
    # from the message body. HERMES messages carry their own ``uuid`` and
    # ``topic`` fields at the top level of the JSON body; see the HERMES
    # schema at https://hermes.lco.global/about.
    body_uuid = alert_as_dict.get('uuid')
    alert_id = uuid.UUID(body_uuid) if body_uuid else uuid.uuid4()
    topic = alert_as_dict.get('topic', '<unknown>')
    return alert_id, topic


def _empty_summary() -> dict:
    """Return the default shape of the ``ingest_hermes_alert`` return value.

    A single place that defines the keys so callers (especially
    ``HermesDataService.to_target``) can rely on the structure.
    """
    return {
        'targets': [],
        'reduced_datums': [],
        'data_products': [],
        'target_extras': {},
        'aliases': [],
    }


def ingest_hermes_alert(alert, metadata=None) -> dict:
    """Write TOM database rows for a single HERMES message, returning a summary of what was created.

    Ingestion steps:

    1. Normalize the input. ``alert`` may be a hop-client ``JSONBlob``-like
       object (has ``.content``) or a plain dict (DataService query result).
    2. Resolve the alert UUID and topic (see ``_resolve_alert_identity``).
    3. For each photometry row: resolve (or create) the Target by matching
       on name/aliases; look up or create the ``ReducedDatum`` with
       ``data_type='photometry'``. ``source_name`` and ``source_location``
       on each created datum carry the HERMES topic and the per-message
       API URL.
    4. For each spectroscopy row: if it carries a file URL in ``file_info``,
       download the file, save it as a DataProduct, and run the data
       processor to produce ReducedDatums; otherwise ingest the inline
       flux/wavelength arrays as a single spectroscopy ReducedDatum.
    5. Return a summary dict (see module docstring).

    Called from ``hermes_alert_handler`` (stream path) and from
    ``tom_hermes.dataservices.hermes.HermesDataService.to_target`` (archive
    / query-result path).
    """
    summary = _empty_summary()

    # Step 1: normalize. hop-client wraps the message in a JSONBlob whose
    # ``.content`` is the dict; the DataService path passes the dict directly.
    if hasattr(alert, 'content'):
        alert_as_dict = alert.content
    else:
        alert_as_dict = alert

    photometry_table = alert_as_dict.get('data', {}).get('photometry') or []
    spectroscopy_table = alert_as_dict.get('data', {}).get('spectroscopy') or []
    target_table = alert_as_dict.get('data', {}).get('targets', []) or []

    # Nothing to ingest; return the empty summary (the caller distinguishes
    # "no data" from "already ingested" by ``alert_stream_message`` being None).
    if not photometry_table and not spectroscopy_table:
        return summary

    # Step 2: resolve identity.
    alert_id, topic = _resolve_alert_identity(alert_as_dict, metadata)

    # Build the per-message HERMES API URL we save as
    # ``ReducedDatum.source_location`` so the provenance stays with the
    # datum. ``DATA_SHARING['hermes']['BASE_URL']`` is the TOM operator's
    # configured HERMES URL; defaults to the public instance if not set.
    if hasattr(settings, 'DATA_SHARING'):
        hermes_base_url = settings.DATA_SHARING.get('hermes', {}).get('BASE_URL', 'https://hermes.lco.global')
    else:
        hermes_base_url = 'https://hermes.lco.global'
    hermes_message_url = urljoin(hermes_base_url, f'/api/v0/query/message/{alert_id}/')

    # ``source_name`` namespaces the topic so consumers can tell
    # HERMES-sourced data from any other broker that may also write
    # ReducedDatums with a topic-shaped source_name.
    source_name = f'Hermes:{topic}'

    # Cache of target-name-string -> Target model instance, used across
    # photometry and spectroscopy iterations so we do not re-query for
    # each row that references the same target.
    target_cache: dict = {}
    # Track whether we created the first Target so we can surface its
    # extras/aliases in the summary (for the DataService to_target contract).
    first_target_created = False
    first_target_extras: dict = {}
    first_target_aliases: list = []

    def resolve_target(target_name):
        """Return a Target for ``target_name``, creating one from ``target_table`` if needed."""
        nonlocal first_target_created, first_target_extras, first_target_aliases
        if target_name in target_cache:
            return target_cache[target_name]

        # Find the target-table entry for this name (if any) so we can use
        # its aliases for matching and its fields for new-target creation.
        target_entry = next(
            (t for t in target_table if t.get('name', '') == target_name),
            {},
        ) if target_table else {}
        aliases = target_entry.get('aliases', [])

        # Try to match an existing Target in the TOM by name, then by each alias.
        query = Target.matches.match_name(target_name)
        if not query:
            for alias in aliases:
                query = Target.matches.match_name(alias)
                if query:
                    break
        if query:
            target_cache[target_name] = query[0]
            summary['targets'].append(query[0])
            return query[0]

        # No match: create a Target from the target_table entry, if we have one.
        if target_table:
            new_target = create_new_hermes_target(target_table, target_name)
            if new_target is not None:
                target_cache[target_name] = new_target
                summary['targets'].append(new_target)
                # Capture extras/aliases for the first created Target so the
                # DataService to_target contract can surface them.
                if not first_target_created:
                    first_target_created = True
                    first_target_aliases = list(aliases)
                    # target_table rows use any-other-keys as "extras" after
                    # the known keys are popped by create_new_hermes_target.
                    first_target_extras = {
                        k: v for k, v in target_entry.items()
                        if k not in ('name', 'ra', 'dec', 'pm_ra', 'pm_dec',
                                     'epoch', 'orbital_elements', 'aliases')
                    }
                return new_target
        return None

    # Step 4: photometry.
    for row in photometry_table:
        target = resolve_target(row['target_name'])
        if target is None:
            continue
        try:
            obs_date = parse(row['date_obs'])
        except ValueError:
            # Bad date string in a HERMES message; skip the row but do not
            # abort the whole ingestion — other rows may be valid.
            continue

        datum_key_fields = {
            'target': target,
            'data_type': 'photometry',
            'timestamp': obs_date,
            'value': get_hermes_phot_value(row),
        }
        datum_defaults = {
            'source_name': source_name,
            'source_location': hermes_message_url,
        }
        new_rd, was_created = ReducedDatum.objects.get_or_create(
            defaults=datum_defaults, **datum_key_fields,
        )
        if was_created:
            summary['reduced_datums'].append(new_rd)

    # Step 5: spectroscopy. Each row is either a reference to a downloadable
    # file (``file_info``) or inline flux/wavelength arrays.
    for row in spectroscopy_table:
        target = resolve_target(row['target_name'])
        if target is None:
            continue
        try:
            obs_date = parse(row['date_obs'])
        except ValueError:
            continue

        file_url = _get_spectroscopy_file_url(row.get('file_info') or [])
        if file_url:
            # Download path: fetch the file, save as DataProduct, run
            # processor to create ReducedDatums. The helper appends created
            # DataProducts and ReducedDatums to the summary so callers see them.
            _ingest_hermes_spectroscopy_file(
                file_url, row, target, source_name, hermes_message_url, summary=summary,
            )
            continue

        # Inline path: row carries flux and wavelength arrays directly.
        if row.get('flux') and row.get('wavelength'):
            value = {
                'flux': row['flux'],
                'flux_units': row.get('flux_units', ''),
                'wavelength': row['wavelength'],
                'wavelength_units': row.get('wavelength_units', ''),
            }
            # Copy any optional descriptive keys over if the message has them.
            for key in ('telescope', 'instrument', 'reducer', 'observer', 'spec_type',
                        'flux_type', 'classification', 'comments', 'exposure_time',
                        'setup', 'proprietary_period', 'proprietary_period_units'):
                if row.get(key):
                    value[key] = row[key]
            if row.get('flux_error'):
                value['flux_error'] = row['flux_error']

            datum_key_fields = {
                'target': target,
                'data_type': 'spectroscopy',
                'timestamp': obs_date,
                'value': value,
            }
            datum_defaults = {
                'source_name': source_name,
                'source_location': hermes_message_url,
            }
            new_rd, was_created = ReducedDatum.objects.get_or_create(
                defaults=datum_defaults, **datum_key_fields,
            )
            if was_created:
                summary['reduced_datums'].append(new_rd)

    # Surface the first-target extras/aliases we captured during resolution,
    # so the DataService to_target caller can repackage them into the
    # (Target, extras, aliases) tuple the framework expects.
    if first_target_created:
        summary['target_extras'] = first_target_extras
        summary['aliases'] = first_target_aliases

    return summary


def hermes_alert_handler(alert, metadata):
    """Hopskotch topic handler — thin wrapper around ``ingest_hermes_alert``.

    Registered via ``settings.ALERT_STREAMS.TOPIC_HANDLERS`` with the dotted
    path ``'tom_hermes.alertstreams.ingester.hermes_alert_handler'``. Called
    by ``tom_alertstreams.alertstreams.hopskotch.HopskotchAlertStream.listen()``
    for every message on a subscribed topic. Discards the summary returned
    by ``ingest_hermes_alert`` because the stream consumer does not need it.

    Requirements in the TOM's settings:

    - ``tom_alertstreams`` in ``INSTALLED_APPS``.
    - ``ALERT_STREAMS.TOPIC_HANDLERS`` mapping the HERMES topic(s) to this
      function's dotted path.
    """
    ingest_hermes_alert(alert=alert, metadata=metadata)


def _get_spectroscopy_file_url(file_info_list):
    """Return the URL of the first ``file_info`` entry that names a supported spectroscopy file, or None.

    Supported extensions are defined by ``HERMES_SPECTROSCOPY_FILE_EXTENSIONS``.
    """
    for entry in file_info_list:
        url = entry.get('url', '')
        filename = os.path.basename(urlparse(url).path)
        for ext in HERMES_SPECTROSCOPY_FILE_EXTENSIONS:
            if filename.lower().endswith(ext):
                return url
    return None


def _ingest_hermes_spectroscopy_file(url, spectroscopy_row, target, source_name, alert_url,
                                     summary: dict | None = None):
    """Download a spectroscopy file from ``url``, save as a DataProduct, run the data processor.

    Skips the download if a DataProduct with this URL as its ``product_id``
    already exists (so re-ingesting the same message is idempotent even
    for the file-download path).

    ``source_name`` and ``alert_url`` are stamped onto the
    ``extra_data`` of the DataProduct so the SpectroscopyProcessor can
    read them back and apply them to the ReducedDatums it creates.

    When ``summary`` is provided (the refactored call path from
    ``ingest_hermes_alert``), the newly-created DataProduct and
    ReducedDatums are appended to ``summary['data_products']`` /
    ``summary['reduced_datums']`` so the caller sees everything that was
    created.
    """
    filename = os.path.basename(urlparse(url).path)

    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as ex:
        logger.error('Failed to download spectroscopy file from %s: %r', url, ex)
        return

    # Scalar spectroscopy metadata from the HERMES row, saved on the
    # DataProduct's ``extra_data`` so the SpectroscopyProcessor can read it
    # back when it parses the file.
    spectroscopy_keys = [
        'date_obs', 'flux_units', 'wavelength_units', 'telescope', 'instrument',
        'reducer', 'observer', 'spec_type', 'flux_type', 'classification',
        'comments', 'exposure_time', 'setup', 'proprietary_period',
        'proprietary_period_units',
    ]
    spectroscopy_data = {
        key: spectroscopy_row[key]
        for key in spectroscopy_keys
        if key in spectroscopy_row and spectroscopy_row[key]
    }
    # source_name / source_location travel with the ReducedDatums produced
    # by the processor (see the SpectroscopyProcessor that reads extra_data).
    spectroscopy_data['source_name'] = source_name
    spectroscopy_data['source_location'] = alert_url

    try:
        dp, dp_created = DataProduct.objects.get_or_create(
            product_id=url, target=target, data_product_type='spectroscopy',
            defaults={'extra_data': json.dumps(spectroscopy_data)},
        )
        if not dp_created:
            # Already ingested this file; skip.
            return

        _, ext = os.path.splitext(filename)
        # Write to a NamedTemporaryFile so Django's File wrapper can read
        # from a stable path; delete the temp file afterward.
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmpfile:
            tmpfile.write(response.content)
            tmpfile_path = tmpfile.name
        try:
            with open(tmpfile_path, 'rb') as f:
                dp.data.save(filename, File(f), save=True)
        finally:
            os.unlink(tmpfile_path)

        if summary is not None:
            summary['data_products'].append(dp)

        # Only run the processor for a newly-created DataProduct; this is
        # what actually turns the file's contents into ReducedDatum rows.
        reduced_datums = run_data_processor(dp)
        if summary is not None:
            summary['reduced_datums'].extend(reduced_datums)
    except Exception as ex:
        logger.error('Failed to ingest spectroscopy file from %s: %r', url, ex)


def get_hermes_phot_value(phot_data):
    """Translate a HERMES photometry row into the ``value`` dict a ReducedDatum expects.

    HERMES expresses brightness either as ``brightness`` (a detection) or
    ``limiting_brightness`` (a non-detection / upper limit); the ReducedDatum
    ``value`` dict has the corresponding ``magnitude`` or ``limit`` key.
    Optional descriptive keys (observer, comments, exposure_time, catalog)
    are copied through when present.
    """
    data_dictionary = {
        'error': phot_data.get('brightness_error', ''),
        'filter': phot_data['bandpass'],
        'telescope': phot_data.get('telescope', ''),
        'instrument': phot_data.get('instrument', ''),
        'unit': phot_data['brightness_unit'],
    }

    if phot_data.get('brightness', None):
        data_dictionary['magnitude'] = phot_data['brightness']
    elif phot_data.get('limiting_brightness', None):
        data_dictionary['limit'] = phot_data['limiting_brightness']
        if phot_data.get('limiting_brightness_error'):
            data_dictionary['limit_error'] = phot_data['limiting_brightness_error']
        if phot_data.get('limiting_brightness_unit'):
            data_dictionary['limit_unit'] = phot_data['limiting_brightness_unit']

    for key in ('observer', 'comments', 'exposure_time', 'catalog'):
        if phot_data.get(key):
            data_dictionary[key] = phot_data[key]

    return data_dictionary


def create_new_hermes_target(target_table, target_name=None, target_list_name=None):
    """Create a Target from a HERMES ``target_table`` entry and save it, returning the Target.

    When ``target_name`` is ``None``, every row in ``target_table`` is
    ingested (so callers can bulk-create) and the last-created Target is
    returned. When ``target_name`` is a string, only the matching row is
    ingested and that Target is returned.

    Handles both sidereal (``ra``/``dec``) and non-sidereal
    (``orbital_elements``) target shapes. Any remaining keys on the
    target row after the known fields are popped are passed as
    ``Target.save(extras=...)`` so they persist as TargetExtras.

    When ``target_list_name`` is given, the Target is added to a
    ``TargetList`` with that name (created if missing).
    """
    target = None
    for hermes_target in target_table:
        if target_name == hermes_target['name'] or target_name is None:

            new_target = {'name': hermes_target.pop('name')}
            if 'ra' in hermes_target and 'dec' in hermes_target:
                new_target['type'] = 'SIDEREAL'
                new_target['ra'] = hermes_target.pop('ra')
                new_target['dec'] = hermes_target.pop('dec')
                new_target['pm_ra'] = hermes_target.pop('pm_ra', None)
                new_target['pm_dec'] = hermes_target.pop('pm_dec', None)
                new_target['epoch'] = hermes_target.pop('epoch', None)
            elif 'orbital_elements' in hermes_target:
                orbital_elements = hermes_target.pop('orbital_elements')
                new_target['type'] = 'NON_SIDEREAL'
                new_target['epoch_of_elements'] = orbital_elements.pop('epoch_of_elements', None)
                new_target['mean_anomaly'] = orbital_elements.pop('mean_anomaly', None)
                new_target['arg_of_perihelion'] = orbital_elements.pop('argument_of_the_perihelion', None)
                new_target['eccentricity'] = orbital_elements.pop('eccentricity', None)
                new_target['lng_asc_node'] = orbital_elements.pop('longitude_of_the_ascending_node', None)
                new_target['inclination'] = orbital_elements.pop('orbital_inclination', None)
                new_target['semimajor_axis'] = orbital_elements.pop('semimajor_axis', None)
                new_target['epoch_of_perihelion'] = orbital_elements.pop('epoch_of_perihelion', None)
                new_target['perihdist'] = orbital_elements.pop('perihelion_distance', None)
            aliases = hermes_target.pop('aliases', [])
            target = Target(**new_target)
            target.full_clean()
            # The remaining hermes_target keys become TargetExtras; aliases
            # become TargetNames via the save()'s ``names=`` kwarg.
            target.save(names=aliases, extras=hermes_target)
            if target_list_name:
                target_list, list_created = TargetList.objects.get_or_create(name=target_list_name)
                if list_created:
                    logger.debug('New target_list created: %s', target_list_name)
                target_list.targets.add(target)
    return target
