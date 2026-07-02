"""
HermesDataService — DataService subclass that queries HERMES.

### Who calls what

- Discovered by ``tom_dataservices.dataservices.get_data_service_classes()``
  via ``TomHermesConfig.data_services()`` in ``apps.py``.
- Form rendered by ``tom_dataservices.views.DataServiceQueryCreateView``
  (URL: ``/dataservices/query/create/?data_service=Hermes``).
- Query executed by ``tom_dataservices.views.RunQueryView`` which calls
  ``build_query_parameters()`` then ``query_service()`` (and possibly
  ``query_targets()``).
- User selects rows from the results partial; the framework's
  ``CreateTargetFromQueryView`` posts each selection back, calling
  ``to_target()`` per row. ``to_target()`` here delegates to the shared
  ``tom_hermes.alertstreams.ingester.ingest_hermes_alert`` so stream-ingest
  and query-ingest share one implementation.

### Endpoint contract

``GET hermes.lco.global/api/v0/query?<params>`` returns JSON with the
HERMES message structure (the same structure used by messages delivered
via Hopskotch). See https://github.com/LCOGT/hermes for the server source.

The exact query-parameter names used below (``search``, ``topic``,
``published_after``, ``published_before``, ``page_size``) are best
guesses; verify them against the LCOGT/hermes source when refining.

### Future scope

Additional query modes for ``/nonlocalizedevents/``, ``/targets/``,
``/messages/`` endpoints. Either a second form class controlled by a
mode select field or a sibling ``HermesLCODataService`` class
registered alongside. Documented, not implemented here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

import requests
from django.core.cache import cache

from tom_dataservices.dataservices import DataService

from tom_hermes import __version__
from tom_hermes.credentials import resolve_hermes_credentials
from tom_hermes.forms import HermesForm

from tom_targets.models import Target
from tom_dataproducts.models import PhotometryReducedDatum

logger = logging.getLogger(__name__)


def _flatten_hermes_archive_row(row: dict) -> None:
    """Add flat top-level ``topic`` / ``title`` / ``published`` / ``submitter`` / ``uuid`` keys.

    HERMES archive responses nest these fields under ``metadata`` and
    ``annotations``. The results-table template reads flat top-level keys
    (easier to read in the template), so we promote the common fields
    here. The original nested ``metadata`` / ``annotations`` dicts are
    left untouched so downstream code can still reach them.

    ``setdefault`` is used so that if a future HERMES response ever
    provides the flat keys natively, our flattening is a no-op.
    """
    meta = row.get('metadata') or {}
    ann = row.get('annotations') or {}
    row.setdefault('topic', meta.get('topic', ''))
    row.setdefault('title', ann.get('title', '') or '')
    row.setdefault('submitter', ann.get('sender', '') or '')
    row.setdefault('uuid', ann.get('con_text_uuid', '') or '')
    # HERMES timestamps are integer milliseconds since epoch. Convert to
    # an ISO-8601 UTC string so the template can render it without a
    # custom filter.
    ts_ms = meta.get('timestamp')
    if ts_ms is not None and 'published' not in row:
        try:
            row['published'] = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            row['published'] = ''


_TOPICS_CACHE_KEY_PREFIX = 'tom_hermes:topics'  # namespace the cache data
_TOPICS_CACHE_TTL_SECONDS = 60 * 60  # one hour


class HermesDataService(DataService):
    """Query the HERMES ``/query`` wrapper and (optionally) ingest selected results.

    Targets ``hermes.lco.global/api/v0/query``, which proxies to the SCIMMA
    Hopskotch archive. The ingestion action (see ``to_target``) calls the
    same ``ingest_hermes_alert`` function that the Hopskotch stream handler
    in ``tom_hermes.alertstreams.ingester`` calls. This ensures a message
    ingested via the archive query writes the same rows to the TOM's
    database as the same message ingested from the live Hopskotch stream.
    """

    # Class attributes consumed by the tom_dataservices framework during
    # discovery and template rendering (nav entries, form headers, etc.).
    name = 'Hermes'
    verbose_name = 'HERMES Messaging Service'
    info_url = 'https://hermes.lco.global/about'
    base_url = 'https://hermes.lco.global'
    # Path to the custom results partial so the DataService results page
    # renders HERMES-shaped rows rather than the generic target table.
    query_results_table = 'tom_hermes/partials/hermes_query_results_table.html'
    app_version = __version__
    app_link = 'https://github.com/TOMToolkit/tom_hermes'

    def __init__(self, *args, user=None, **kwargs):
        """Accept and store the requesting User on the instance.

        The ``tom_dataservices`` base class does not thread ``user`` through
        ``__init__`` (it constructs services with no args). We accept it
        here so that callers — including the test suite and any view that
        passes ``user=request.user`` — can pre-populate the attribute that
        ``build_headers`` reads. Views that instantiate via the framework's
        zero-arg pattern can also set ``svc.user`` directly after
        construction.
        """
        super().__init__(*args, **kwargs)
        self.user = user

    @classmethod
    def get_form_class(cls):
        """Return the query form class the framework uses to render the query UI.

        Called by ``tom_dataservices.views.DataServiceQueryCreateView.get_form_class``.
        """
        return HermesForm

    def build_headers(self, *args, **kwargs):
        """Attach ``Authorization: Token <api_key>`` to every HERMES request
        this DataService makes.

        HERMES requires auth on `/api/v0/query/` and `/api/v0/topics/`;
        So, resolve the creds get the api_key.
        """
        creds = resolve_hermes_credentials(getattr(self, 'user', None))
        api_key = creds.get('api_key')
        if not api_key:
            return {}
        return {'Authorization': f'Token {api_key}'}

    @classmethod
    def urls(cls, **kwargs) -> dict:
        """Return the dict of URLs this DataService uses, keyed by purpose.

        Retrieved via ``DataService.get_urls(url_type='<key>')`` so URL
        construction stays in one place. The ``/topics/`` path is a best
        guess; verify against the LCOGT/hermes source at implementation
        time.
        """
        base = cls.base_url  # base_url is class attribute

        urls_by_purpose = {
            'base_url': base,
            'info_url': cls.info_url,  # also class attribute

            'query_url': f'{base}/api/v0/query',  # Generic message search (wraps archive-api), returns msg meta-data
            # 'query_url': f'{base}/api/v0/query',  # Generic message search (wraps archive-api), returns msg meta-data
            'target_url': f'{base}/api/v0/targets/',

            'topics_url': f'{base}/api/v0/topics/',  # for topic verification


            # the archive query response is message metadata; use this url
            # to ask HERMES for the message content if needed.
            'message_url_template': f'{base}/api/v0/query/message/{{uuid}}/',  # returns full message
        }
        return urls_by_purpose

    @classmethod
    def get_topic_choices(cls, user=None) -> list:
        """Return the list of ``(value, label)`` pairs for the advanced form's topic multi-select.

        Hits ``/api/v0/topics/`` with ``Authorization: Token <api_key>``
        taken from ``tom_hermes.credentials.resolve_hermes_credentials(user)``.

        Cached for an hour, per user (don't spam HERMES and insulate users from each other).

        Called by ``HermesForm.__init__``.
        """
        # Per-user cache key. Anonymous / background callers (no user) get a
        # 'shared' key, which reads from settings credentials if any.
        user_id = getattr(user, 'id', None) if user else None
        cache_key = f'{_TOPICS_CACHE_KEY_PREFIX}:{user_id or "shared"}'
        choices = cache.get(cache_key)
        if choices is None:
            creds = resolve_hermes_credentials(user)
            api_key = creds.get('api_key')
            if not api_key:
                # No credentials: the /topics/ endpoint requires auth so there
                # is nothing we can do. Render the form with empty topics;
                # the user can still submit a search without a topic filter.
                return []

            headers = {'Authorization': f'Token {api_key}'}
            try:
                response = requests.get(
                    cls.get_urls(url_type='topics_url'), headers=headers, timeout=10,
                )
                response.raise_for_status()
                payload = response.json()

                # figure out what the payload looks like (we don't know a priori)
                # (we are trying to get_topic_choices from the payload).)
                if isinstance(payload, dict):
                    if 'topics' in payload:
                        topics = payload['topics']
                    elif 'results' in payload:
                        topics = payload['results']
                    else:
                        logger.warning(
                            'Unexpected HERMES topics response (dict without topics/results key): %r',
                            list(payload.keys()),
                        )
                        topics = []
                elif isinstance(payload, list):
                    topics = payload
                else:
                    logger.warning('Unexpected HERMES topics response type: %s', type(payload).__name__)
                    topics = []
                # Topic entries can be bare strings or dicts with a 'name'
                # key depending on the server version. Normalize to strings.
                topics = [t['name'] if isinstance(t, dict) and 'name' in t else t for t in topics]
                choices = [(t, t) for t in topics if isinstance(t, str)]
                cache.set(cache_key, choices, _TOPICS_CACHE_TTL_SECONDS)
            except requests.RequestException as exc:
                logger.warning('Could not fetch HERMES topics for form: %s', exc)
                choices = []
            except (TypeError, KeyError, ValueError) as exc:
                logger.warning('Unexpected HERMES topics response shape: %s', exc)
                choices = []
        # hopefully we've figured out what the topic choices are
        return choices

    def build_query_parameters(self, parameters, **kwargs):
        """Translate cleaned form data into HERMES ``/target`` URL parameters.
        """
        query_parameters: dict = {}
        if parameters.get('exact_name'):
            query_parameters['name_exact'] = parameters['exact_name']
        if parameters.get('target_name'):
            query_parameters['name'] = parameters['target_name']
        if parameters.get('uuid'):
            query_parameters['referenced_by_uuid'] = parameters['uuid']
        if parameters.get('ra') and parameters.get('dec') and parameters.get('radius'):
            query_parameters['cone_search'] = (f'{parameters.get("ra")}, {parameters.get("dec")}, '
                                               f'{parameters.get("radius")}')
        self.query_parameters = query_parameters
        return query_parameters

    def query_service(self, data, **kwargs):
        """Send the query to HERMES and cache the response on ``self.query_results``.

        Required abstract method
        """
        response = requests.get(
            self.get_urls(url_type='target_url'),
            params=data,
            headers=self.build_headers(),
            timeout=30,
        )
        response.raise_for_status()
        self.query_results = response.json()
        return self.query_results

    def query_targets(self, query_parameters, **kwargs) -> list:
        """
        Specialized query to retrieve targets
        """
        # call query_service if we haven't already
        if not self.query_results:
            self.query_service(query_parameters, **kwargs)

        targets_results = self.query_results['results']

        return targets_results

    def create_target_from_query(self, target_result, **kwargs):
        """Create a new target from a single instance of the target results.
        :param target_result: dictionary describing target details based on query result
        :returns: target object
        :rtype: `Target`
        """

        if target_result.get('right_ascension') and target_result.get('declination'):
            target_type = 'SIDEREAL'
        else:
            target_type = 'NON_SIDEREAL'
        target = Target(
            name=target_result['name'],
            type=target_type,
            ra=target_result.get('right_ascension'),
            dec=target_result.get('declination'),
            pm_ra=target_result.get('pm_ra'),
            pm_dec=target_result.get('pm_dec'),
            epoch_of_elements=target_result.get('epoch_of_elements'),
            mean_anomaly=target_result.get('mean_anomaly'),
            arg_of_perihelion=target_result.get('argument_of_the_perihelion'),
            eccentricity=target_result.get('eccentricity'),
            lng_asc_node=target_result.get('longitude_of_the_ascending_node'),
            inclination=target_result.get('orbital_inclination'),
            semimajor_axis=target_result.get('semimajor_axis'),
            epoch_of_perihelion=target_result.get('epoch_of_perihelion'),
            )
        return target

    def build_query_parameters_from_target(self, target, **kwargs):
        """
        This is a method that builds query parameters based on an existing target object that will be recognized by
        `query_service()` using an exact name match.

        :param target: A target object to be queried
        :return: query_parameters (usually a dict) that can be understood by `query_service()`
        """
        query_parameters = self.build_query_parameters(parameters={'exact_name': target.name})
        return query_parameters

    def query_aliases(self, query_parameters=None, target=None, **kwargs) -> List:
        """
        Set up and run a specialized query for retrieving target names from Hermes. For some reason these names are
        retrievable from the full message Target table, but not the target API itself.

        :param query_parameters: This is the output from build_query_parameters()
        :return: A list of target names
        :rtype: List
        """

        # Get the full message for a specific target via the UUIDs associated with that target.
        target_parameters = self.build_query_parameters_from_target(target)
        target_results = self.query_service(target_parameters)['results']
        alias_results = []
        for target_result in target_results:
            for message in target_result['messages']:
                uuid = message['uuid']
                full_message = self._fetch_full_message(uuid)
                target_table = full_message.get('message', {}).get('data',{}).get('targets',[])
                # Find the appropriate target in the target table and return aliases
                for target_obj in target_table:
                    if target_obj['name'] == target_result['name']:
                        alias_results += target_obj.get('aliases', [])
        return alias_results

    def query_photometry(self, query_parameters, **kwargs):
        """Set up and run a specialized query for a DataService’s photometry service.
        :returns: photometry_results
        :rtype: Usually a List of Dictionaries
        """

        target_results = self.query_service(query_parameters)['results']
        photometry_results = []
        for target_result in target_results:
            for message in target_result['messages']:
                uuid = message['uuid']
                full_message = self._fetch_full_message(uuid)
                message_phot = full_message.get('message', {}).get('data', {}).get('photometry', [])
                for phot in message_phot:
                    if phot['target_name'] == target_result['name']:
                        photometry_results.append(phot)
        return photometry_results

    def create_reduced_datums_from_query(self, target, data=[], data_type='photometry', **kwargs):
        """
        Create and save new reduced_datums of the appropriate data_type from the query results
        Be sure to use `ReducedDatum.objects.get_or_create()` when creating new objects.

        :param target: Target Object to be associated with the reduced data
        :param data: List of data dictionaries of the appropriate `data_type`
        :param data_type: An appropriate data type as listed in tom_dataproducts.models.DATA_TYPE_CHOICES
        :return: List of Reduced Datums (either retrieved or created)
        """
        reduced_datums = []
        for datum in data:
            if data_type == 'photometry':
                reduced_datum, __ = PhotometryReducedDatum.objects.get_or_create(
                    target=target,
                    source_name=self.name,
                    telescope=datum.get('telescope'),
                    instrument=datum.get('instrument'),
                    brightness=datum.get('brightness'),
                    limit=datum.get('limiting_brightness'),
                    brightness_error= datum.get('brightness_error'),
                    bandpass=datum.get('bandpass'),
                    unit=datum.get('brightness_unit') or datum.get('limiting_brightness_unit'),
                    exposure_time=datum.get('exposure_time'),
                )
                reduced_datums.append(reduced_datum)
        return reduced_datums

    def _fetch_full_message(self, message_uuid: str):
        """GET the full HERMES message body by uuid; return the JSON dict or an empty dict.
        """
        template = self.get_urls(url_type='message_url_template')
        if not template:
            logger.warning('_fetch_full_message: no message_url_template configured.')
            return {}
        message_url = template.format(uuid=message_uuid)
        try:
            response = requests.get(message_url, headers=self.build_headers(), timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning('Could not fetch HERMES message %s: %s', message_uuid, exc)
            return {}
