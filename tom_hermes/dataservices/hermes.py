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


# Topic list is stable over minutes; cache for an hour. The cache key is
# prefixed with this module's name so other DataServices cannot accidentally
# read or write this entry. The per-user suffix (added in get_topic_choices)
# keeps users from seeing each other's authenticated topic lists.
_TOPICS_CACHE_KEY_PREFIX = 'tom_hermes:topics'
_TOPICS_CACHE_TTL_SECONDS = 60 * 60


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

    @classmethod
    def get_form_class(cls):
        """Return the query form class the framework uses to render the query UI.

        Called by ``tom_dataservices.views.DataServiceQueryCreateView.get_form_class``.
        """
        return HermesForm

    def build_headers(self, *args, **kwargs):
        """Attach ``Authorization: Token <api_key>`` to every HERMES request this DataService makes.

        HERMES requires auth on `/api/v0/query/` and `/api/v0/topics/`; sending an anonymous
        request produces a 403. Credentials come from ``resolve_hermes_credentials(self.user)``
        — ``self.user`` is set by ``tom_dataservices.views.RunQueryView`` and siblings when
        they instantiate the DataService. When there is no User (background callers) or
        the user has no configured HERMES key, returns an empty dict and lets HERMES
        produce its own 403, which the view surfaces as a query-feedback banner.
        """
        creds = resolve_hermes_credentials(self.user)
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
        base = cls.base_url

        urls_by_purpose = {
            'base_url': base,
            'info_url': cls.info_url,
            # Generic message search (wraps archive-api):
            'query_url': f'{base}/api/v0/query',
            # TO VERIFY against LCOGT/hermes source:
            'topics_url': f'{base}/api/v0/topics/',
            # Per-message content fetch. The archive-query response is
            # metadata-only; UC3 ingestion needs the full message body
            # (topic / title / data.photometry / data.spectroscopy /
            # data.targets etc.), so ``to_target`` follows up by GETting
            # this URL with the message uuid substituted in.
            #
            # Path verified against LCOGT/hermes `hermes_base/urls.py`
            # on branch `dev` (2026-04-23):
            #     path('api/v0/query/message/<str:uuid>/',
            #          views.MessageApiView.as_view(), name='query-message')
            # Not `/api/v0/messages/<uuid>/` — that's the DRF router's
            # generic MessageViewSet, which is keyed by pk (int), not uuid.
            'message_url_template': f'{base}/api/v0/query/message/{{uuid}}/',
        }
        return urls_by_purpose

    @classmethod
    def get_topic_choices(cls, user=None) -> list:
        """Return the list of ``(value, label)`` pairs for the advanced form's topic multi-select.

        Unlike the sharing path (which filters to *writable* topics via
        ``/api/v0/profile/``), the query path wants any topic the user is
        permitted to *read* — including topics they cannot publish to.
        Hits ``/api/v0/topics/`` with ``Authorization: Token <api_key>``
        taken from ``tom_hermes.credentials.resolve_hermes_credentials(user)``.

        Cached for an hour, per user, so that rendering the form does not
        hit HERMES on every page load and so two users do not see each
        other's authenticated topic lists. Called by ``HermesForm.__init__``.
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
                # Exact structure of the response is to be determined at
                # implementation time — probably ``list[str]`` or
                # ``{'results': [...]}`` per DRF conventions. Handle both.
                payload = response.json()
                # HERMES /api/v0/topics/ returns ``{'topics': [...]}``. Handle
                # both DRF-pagination shape (``{'results': [...]}``) and bare
                # list too so the code tolerates a future endpoint change.
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
        return choices

    def build_query_parameters(self, parameters, **kwargs):
        """Translate cleaned form data into HERMES ``/query`` URL parameters.

        Only keys the user actually filled in are included; HERMES is
        expected to treat absent keys as "do not filter on this field."
        The built dict is cached on ``self.query_parameters`` so that
        ``query_service()`` does not have to re-derive it.
        """
        params: dict = {}
        if parameters.get('search'):
            params['search'] = parameters['search']
        if parameters.get('topics'):
            # HERMES ``/query`` expects repeatable ``topic=`` parameters to
            # carry the multi-select. requests handles list values that way
            # by default.
            params['topic'] = parameters['topics']
        if parameters.get('published_after'):
            params['published_after'] = parameters['published_after']
        if parameters.get('published_before'):
            params['published_before'] = parameters['published_before']
        params['page_size'] = 25
        self.query_parameters = params
        return params

    def query_service(self, data, **kwargs):
        """Send the query to HERMES and cache the response on ``self.query_results``.

        Required abstract method (``DataService.query_service`` in
        ``tom_dataservices.dataservices``). Results are cached on
        ``self.query_results`` so later methods (``query_targets``,
        ``to_target``) can reuse them without re-querying.
        """
        response = requests.get(
            self.get_urls(url_type='query_url'),
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
        ``self.query_results``. Verified live response shape (2026-04-23):
        ``{'messages': [...], 'next': '<cursor>', 'prev': '<cursor>'}``
        — HERMES uses cursor-based pagination and puts the rows under
        ``'messages'``. We return just the first page; walking the
        cursors is a follow-up when a dataset demands it.

        Also tolerates two alternative shapes for forward-compat / tests:
        DRF-style ``{'results': [...]}`` and bare list. Unknown shapes
        return ``[]``.
        """
        # If query_service has not been called yet, call it so there is
        # something to return. The parent DataService's query_photometry /
        # query_spectroscopy implementations use the same pattern.
        if not self.query_results:
            self.query_service(query_parameters, **kwargs)

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

        # HERMES archive rows look like
        # ``{'metadata': {'topic', 'timestamp'}, 'annotations': {'title', 'sender',
        # 'con_text_uuid', 'file_name', ...}}``. The results partial
        # (hermes_query_results_table.html) renders ``result.topic``,
        # ``result.title``, ``result.published``, ``result.submitter``,
        # ``result.uuid`` — flat top-level keys. Promote the nested values
        # here so the template stays simple; leave the original nested
        # structure in place so downstream code (to_target) can reach it.
        for row in rows:
            _flatten_hermes_archive_row(row)
        return rows

    def to_target(self, target_result=None, **kwargs):
        """Create TOM database rows for one selected query-result row.

        Called by the ``tom_dataservices`` framework
        (``CreateTargetFromQueryView``) once per row the user selects in
        the results partial. The archive-query result is metadata-only
        (no ``data.photometry`` / ``data.targets`` / ``data.spectroscopy``),
        so we first GET the full message body from HERMES by uuid, then
        delegate to ``ingest_hermes_alert`` — the same function the
        Hopskotch stream handler calls. This ensures archive-ingest and
        stream-ingest write the same rows to the TOM's database for the
        same message.

        Returns the ``(Target, extras_dict, aliases_list)`` tuple the
        framework expects. Returns ``(None, {}, [])`` if the full message
        cannot be fetched — the framework will log the empty save and
        skip the row rather than crashing.
        """
        if not target_result:
            raise ValueError('to_target requires a target_result (HERMES message dict).')

        # Fetch the full message body by uuid. The archive /query endpoint
        # gave us only metadata + annotations; ingest_hermes_alert needs
        # data.photometry / data.targets / data.spectroscopy.
        message_uuid = target_result.get('uuid') or (
            target_result.get('annotations') or {}).get('con_text_uuid')
        if not message_uuid:
            logger.warning('to_target: no uuid on target_result; skipping. keys=%s',
                           list(target_result.keys()))
            return None, {}, []
        full_body = self._fetch_full_message(message_uuid)
        if full_body is None:
            return None, {}, []

        # The /api/v0/query/message/<uuid>/ response has three top-level
        # keys — ``metadata``, ``annotations``, ``message`` — where
        # ``message`` is the published body (the one ``publish_to_hermes``
        # originally POSTed) with keys like ``topic`` / ``title`` /
        # ``data.targets`` / ``data.photometry`` / ``data.spectroscopy``.
        # ``ingest_hermes_alert`` expects *that* shape, so unwrap here.
        published_message = full_body.get('message') if isinstance(full_body, dict) else None
        if not published_message:
            logger.warning(
                'HERMES message response has no "message" key; cannot ingest. Keys=%s',
                list(full_body.keys()) if isinstance(full_body, dict) else '<non-dict>',
            )
            return None, {}, []

        # ingest_hermes_alert creates Targets + ReducedDatums + DataProducts
        # and returns a summary we repackage for the framework's caller.
        summary = ingest_hermes_alert(alert=published_message, metadata=None)
        primary_target = summary['targets'][0] if summary.get('targets') else None
        extras = summary.get('target_extras', {})
        aliases = summary.get('aliases', [])
        return primary_target, extras, aliases

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
