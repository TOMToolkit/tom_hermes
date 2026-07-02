"""
HERMES publisher: HTTP calls, message assembly, and the data converter.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils.module_loading import import_string

from tom_targets.models import Target
from tom_dataproducts.models import PhotometryReducedDatum, SpectroscopyReducedDatum

from tom_hermes.credentials import resolve_hermes_credentials

logger = logging.getLogger(__name__)


class HermesMessageException(Exception):
    """Raised when a ReducedDatum cannot be translated into a valid HERMES row.
    """


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
    """

    def __init__(self, validate=True):
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
        else:  # Build non-sidereal Target
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
            'telescope': datum.telescope,
            'instrument': datum.instrument,
            'bandpass': datum.bandpass,
        }
        brightness_unit = convert_astropy_brightness_unit_to_hermes(datum.unit)

        if brightness := datum.brightness:
            phot_table_row['brightness'] = brightness
            if brightness_unit:
                phot_table_row['brightness_unit'] = brightness_unit
        else:
            phot_table_row['limiting_brightness'] = datum.limit
            if brightness_unit:
                phot_table_row['limiting_brightness_unit'] = brightness_unit
        error_value = datum.brightness_error
        if error_value is not None and isinstance(error_value, (int, float)):
            phot_table_row['brightness_error'] = error_value
        return phot_table_row

    def build_hermes_spectroscopy_table_row(self, datum):
        """Return a HERMES spectroscopy-table row for a TOM SpectroscopyReducedDatum.
        """
        spectroscopy_table_row = {
            'target_name': datum.target.name,
            'date_obs': datum.timestamp.isoformat(),
            'telescope': datum.telescope,
            'instrument': datum.instrument,
            'reducer': datum.value.get('reducer'),
            'observer': datum.value.get('observer'),
            'flux': datum.flux,
            'flux_error': datum.error,
            'wavelength': datum.wavelength,
            'flux_units': convert_astropy_flux_unit_to_hermes(datum.flux_unit),
            'wavelength_units': convert_astropy_wavelength_unit_to_hermes(datum.wavelength_unit),
        }

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


class BuildHermesMessage:
    """Human-authored parts of a HERMES message (title, authors, topic, etc.).
    """

    def __init__(self, title='', submitter='', authors='', message='', topic='hermes.test', **kwargs):
        self.title = title
        self.submitter = submitter
        self.authors = authors
        self.message = message
        self.topic = topic
        # Any additional keyword arguments are preserved and emitted under
        # the message's ``data.extra_data`` key (see ``create_hermes_message``).
        self.extra_info = kwargs


def publish_to_hermes(message_info, datums=None, targets=None, *, user=None, **kwargs):
    """POST a fully-assembled HERMES message to ``/api/v0/submit_message/``.
    """
    if targets is None:
        targets = Target.objects.none()
    if datums is None:
        datums = []

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
        message = create_hermes_message(message_info, datums, targets, **kwargs)
    except HermesMessageException as e:
        return {'message': 'ERROR: ' + str(e)}

    # Submit. If the POST or the status check raises, surface the HTTP
    # response to the caller so they can inspect it; don't crash the
    # request-handling view.
    response = None
    try:
        response = requests.post(url=submit_url, json=message, headers=headers)
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
    """
    creds = resolve_hermes_credentials(user)
    if not creds.get('api_key') or not creds.get('base_url'):
        return ''

    preload_url = creds['base_url'] + 'api/v0/submit_message/preload/'
    headers = {'Authorization': f"Token {creds['api_key']}"}

    message = create_hermes_message(message_info, reduced_datums, targets)
    response = None
    try:
        response = requests.post(url=preload_url, json=message, headers=headers)
        response.raise_for_status()
        return response.json()['key']
    except Exception as ex:
        logger.error(repr(ex))
        if response is not None:
            logger.error(response.content)

    return ''


def create_hermes_message(message_info, datums=None, targets=None, **kwargs):
    """Assemble a HERMES-schema JSON body from a BuildHermesMessage + datums + targets.
    """
    if targets is None:
        targets = Target.objects.none()
    if datums is None:
        datums = []

    hermes_photometry_data = []
    hermes_spectroscopy_data = []
    hermes_target_dict: dict = {}

    # First pull in targets associated with submitted data and build data tables
    hermes_data_converter = get_hermes_data_converter_class()(validate=True)
    for datum in datums:
        if datum.target.name not in hermes_target_dict:
            hermes_target_dict[datum.target.name] = hermes_data_converter.build_hermes_target_table_row(datum.target)
        if isinstance(datum, PhotometryReducedDatum):
            hermes_photometry_data.append(hermes_data_converter.build_hermes_photometry_table_row(datum))
        elif isinstance(datum, SpectroscopyReducedDatum):
            hermes_spectroscopy_data.append(hermes_data_converter.build_hermes_spectroscopy_table_row(datum))

    # Next pull in submitted targets and build data tables
    for target in targets:
        if target.name not in hermes_target_dict:
            hermes_target_dict[target.name] = hermes_data_converter.build_hermes_target_table_row(target)
            # Build Phot Table
            phot_data = PhotometryReducedDatum.objects.filter(target=target)
            for datum in phot_data:
                hermes_photometry_data.append(hermes_data_converter.build_hermes_photometry_table_row(datum))
            # Build Spec Table
            spec_data = SpectroscopyReducedDatum.objects.filter(target=target)
            for datum in spec_data:
                hermes_spectroscopy_data.append(hermes_data_converter.build_hermes_spectroscopy_table_row(datum))

    # Finally put it all together in a message
    message = {
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
    return message


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
