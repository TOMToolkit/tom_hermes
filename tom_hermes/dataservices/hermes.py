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

import requests
from django.apps import apps as django_apps
from django.core.cache import cache

from tom_dataservices.dataservices import DataService

from tom_hermes import __version__
from tom_hermes.alertstreams.ingester import ingest_hermes_alert
from tom_hermes.credentials import resolve_hermes_credentials
from tom_hermes.forms import HermesForm

from tom_targets.models import Target

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
        """Translate cleaned form data into HERMES ``/query`` URL parameters.

        Only keys the user actually filled in are included; HERMES is
        expected to treat absent keys as "do not filter on this field."
        The built dict is cached on ``self.query_parameters`` so that
        ``query_service()`` does not have to re-derive it.
        """
        query_parameters: dict = {}
        print(parameters)
        if parameters.get('target_name'):
            query_parameters['name'] = parameters['target_name']
        if parameters.get('uuid'):
            query_parameters['referenced_by_uuid'] = parameters['uuid']
        if parameters.get('ra') and parameters.get('dec') and parameters.get('radius'):
            query_parameters['cone_search'] = f'{parameters.get('ra')}, {parameters.get('dec')}, '
            f'{parameters.get('radius')}'
        self.query_parameters = query_parameters
        return query_parameters

    def query_service(self, data, **kwargs):
        """Send the query to HERMES and cache the response on ``self.query_results``.

        Required abstract method (``DataService.query_service`` in
        ``tom_dataservices.dataservices``). Results are cached on
        ``self.query_results`` so later methods (``query_targets``,
        ``to_target``) can reuse them without re-querying.
        """
        print(data)
        print("===========================================")
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
        """Return the list of per-message dicts the framework's results partial iterates over.

        The framework calls this after ``query_service()`` has populated
        ``self.query_results``. 
        — HERMES uses cursor-based pagination and puts the rows under
        ``'messages'``. We return just the first page; walking the
        cursors is a follow-up when a dataset demands it.

        Also tolerates two alternative shapes for forward-compat / tests:
        DRF-style ``{'results': [...]}`` and bare list. Unknown shapes
        return ``[]``.
        """
        # call query_service if we haven't already
        if not self.query_results:
            self.query_service(query_parameters, **kwargs)

        targets_results = self.query_results['results']
        print(self.query_results['results'])
        print("======================================")
        return targets_results

        # figure out what the query_results are
        # NOTE: all calls to isinstance in this module are code smell
        if isinstance(self.query_results, dict):
            # The canonical HERMES /query response shape.
            if 'messages' in self.query_results:
                rows = self.query_results['messages']
            # DRF pagination — accepted for forward-compat / test mocks.
            elif 'results' in self.query_results:
                rows = self.query_results['results']
            else:
                return []
        elif isinstance(self.query_results, list):
            rows = self.query_results
        else:
            return []

        # prepare the rows for what the template expects 
        for row in rows:
            _flatten_hermes_archive_row(row)
        return rows

    def create_target_from_query(self, target_result, **kwargs):
        """Create a new target from a single instance of the target results.
        :param target_result: dictionary describing target details based on query result
        :returns: target object
        :rtype: `Target`
        """

        # Need to move to query_targets
        message_uuid = target_result['uuid']
        full_message = self._fetch_full_message(message_uuid) or {}
        target_table = full_message.get('message', {}).get('data', {}).get('targets', [])
        print(full_message)
        print("==========================================")
        print(target_table)

        

        # target = Target(
        #     name=target_result['name']
        #     )
        return None

    def to_target(self, target_result=None, **kwargs):
        """Create TOM database rows for one selected query-result row.

        Called by the ``tom_dataservices`` framework
        (``CreateTargetFromQueryView``) once per row the user selects in
        the results partial. The archive-query result is metadata-only
        (no ``data.photometry`` / ``data.targets`` / ``data.spectroscopy``),
        so we first GET the full message body from HERMES by uuid, then
        delegate to ``ingest_hermes_alert`` — the same function the
        Hopskotch stream handler calls. So, the DataService and the
        alerthandel funnel into the same code path.


        Returns the ``(Target, extras_dict, aliases_list)`` tuple the
        framework expects. Returns ``(None, {}, [])`` if the full message
        cannot be fetched (log the empty save and skip the row if we
        can't get beyond the meta-data the query returned.
        """
        # if not target_result:
        #     raise ValueError('to_target requires a target_result (HERMES message dict).')

        # # Fetch the full message body with the  uuid meta-data returned
        # # by the original query.
        # message_uuid = target_result.get('uuid') or (
        #     target_result.get('annotations') or {}).get('con_text_uuid')
        # if not message_uuid:
        #     logger.warning(f'to_target: no uuid on target_result; skipping. '
        #                    f'keys={list(target_result.keys())}')
        #     return None, {}, []
        # full_body = self._fetch_full_message(message_uuid)
        # if full_body is None:
        #     return None, {}, []

        # # get the message data into the form the ingest_hermes_alert expect
        # published_message = full_body.get('message') if isinstance(full_body, dict) else None
        # if not published_message:
        #     logger.warning(
        #         'HERMES message response has no "message" key; cannot ingest. Keys=%s',
        #         list(full_body.keys()) if isinstance(full_body, dict) else '<non-dict>',
        #     )
        #     return None, {}, []

        # # ingest_hermes_alert creates Targets + ReducedDatums + DataProducts
        # # and returns a summary
        # summary = ingest_hermes_alert(alert=published_message, metadata=None)
        
        # # unpack the summary for the DataService
        # primary_target = summary['targets'][0] if summary.get('targets') else None
        # extras = summary.get('target_extras', {})
        # aliases = summary.get('aliases', [])
        # return primary_target, extras, aliases

    def _fetch_full_message(self, message_uuid: str):
        """GET the full HERMES message body by uuid; return the JSON dict or ``None``.

        Called by ``to_target`` for archive-ingest. Uses the same auth
        headers as ``query_service`` (built from ``self.user``'s
        HermesProfile via ``resolve_hermes_credentials``).
        """
        template = self.get_urls(url_type='message_url_template')
        if not template:
            logger.warning('_fetch_full_message: no message_url_template configured.')
            return None
        message_url = template.format(uuid=message_uuid)
        try:
            response = requests.get(message_url, headers=self.build_headers(), timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning('Could not fetch HERMES message %s: %s', message_uuid, exc)
            return None

    def get_simple_form_partial(self):
        """Path to the simplified search partial (free-text + dates)."""
        return 'tom_hermes/partials/hermes_simple_form.html'

    def get_advanced_form_partial(self):
        """Path to the advanced partial, which adds the topic multi-select."""
        return 'tom_hermes/partials/hermes_advanced_form.html'

    # NOTE: unclear if we really need this:
    def get_additional_context_data(self):
        """Return extra template context used by the results partial.

        ``tom_nonlocalizedevents_installed`` lets the results template
        show a "Create NonLocalizedEvent" action when that app is
        available; otherwise that UI is hidden.
        """
        return {
            'tom_nonlocalizedevents_installed':
                django_apps.is_installed('tom_nonlocalizedevents'),
            'version': __version__,
        }
