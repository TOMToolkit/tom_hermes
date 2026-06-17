"""
HermesSharingBackend — the SharingBackend plug-in for HERMES.

### What lives here

- ``SharingBackend`` — abstract base class for sharing destinations. This class
  originated in ``tom_common.sharing`` and was removed from ``tom_base`` along
  with its registry (``get_sharing_backends()``). The definition has been
  inlined here as a stop-gap so ``HermesSharingBackend`` continues to compile
  and tests continue to run. When the host-side ``SharingBackend`` returns
  upstream, delete this inlined copy and restore the import from
  ``tom_common.sharing``.
- ``HermesSharingBackend`` — subclass of ``SharingBackend``. Once the host
  registry returns, will be advertised by ``TomHermesConfig.sharing_backends()``
  and dispatched to by ``tom_dataproducts.views.DataShareView``.

### What does NOT live here

- The HTTP and data-conversion code — lives in ``tom_hermes.sharing.publisher``.
- Credential resolution — lives in ``tom_hermes.credentials``.

"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

<<<<<<< HEAD
# from tom_common.sharing import SharingBackend
=======
from django.conf import settings
>>>>>>> e177c2d (bulk commit of unreviewed changes)

from tom_hermes.credentials import resolve_hermes_credentials
from tom_hermes.sharing.publisher import (
    BuildHermesMessage,
    get_hermes_topics,
    publish_to_hermes,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.db.models import QuerySet


class SharingBackend(ABC):
    """Base class (abstract) for a data-sharing destination.

    Each subclass represents one destination family: one class for HERMES,
    one for TOM-to-TOM, and so on. Instances are short-lived and created
    per ``share()`` invocation by the consumer code.
    """

    # ``name`` is:
    #   - the key in the dict returned by ``get_sharing_backends()``;
    #   - used as the prefix of the form's ``share_destination`` value, which is
    #     formatted as the string ``'<name>:<sub-destination>'``
    #     (e.g. ``'hermes:gw.lvk.public'``, or ``'tom:tom_b'``);
    #   - the value that ``DataShareView.post()`` parses out of
    #     ``share_destination`` to look up this backend's class and
    #     dispatch to its ``share()`` method.
    # Required: set to a short, unique, machine-readable string in every subclass.
    name: str = ''

    # ``verbose_name`` is the human-readable label shown as the heading above
    # this backend's destinations in the share-destination dropdown.
    # Required: set in every subclass.
    verbose_name: str = ''

    @classmethod
    def get_destination_choices(cls, user: User | None = None) -> list:
        """Return the (value, label) pairs that populate this backend's options in the share-destination dropdown.

        Called by ``DataShareForm.__init__`` at form-render time. Each
        returned ``value`` is a string formatted as
        ``'<cls.name>:<sub-destination>'`` so ``DataShareView.post()`` can
        parse the prefix and look up this class in the registry. The
        returned ``label`` is what the user sees.

        Implementations typically call the destination (e.g., to list
        topics), or read ``settings.DATA_SHARING``, to enumerate configured
        sub-destinations.
        """
        raise NotImplementedError

    @abstractmethod
    def share(self, form_data: dict, *,
              reduced_datums: QuerySet | None = None,
              targets: QuerySet | None = None,
              data_products: QuerySet | None = None,
              user: User | None = None,
              **kwargs) -> dict:
        """Execute the share operation.

        Called by ``DataShareView.post()`` (and by the shims in
        ``tom_dataproducts.sharing``) after a successful form submission.

        Returns a feedback dict with at least
        ``{'status': 'success'|'error', 'message': str}`` (older code
        returns just ``{'message': str}``; both are tolerated by the
        sharing feedback handler).
        """

    def validate_credentials(self, user: User | None = None) -> bool:
        """Check that the user has credentials configured for this backend.

        Called by ``DataShareForm.clean()`` when the form is about to
        submit. Default returns True (the backend needs no per-user
        credentials). Subclasses override to check e.g. that a
        ``HermesProfile`` API key is set for the current user.
        """
        return True


# class HermesSharingBackend(SharingBackend):
#     """SharingBackend that publishes TOM data to HERMES.

<<<<<<< HEAD
#     Registered by ``tom_hermes.apps.TomHermesConfig.sharing_backends()`` and
#     discovered by ``tom_common.sharing.get_sharing_backends()``. Its
#     ``name = 'hermes'`` is the prefix in the share-destination form field
#     value (``'hermes:<topic>'``); the sub-destination half is the HERMES
#     topic the message is published to.
=======
    Currently dormant: the host-side registry (``tom_common.sharing.get_sharing_backends()``
    and the ``DataShareView`` dispatch path) has been removed from tom_base.
    The class is kept intact, with the upstream ``SharingBackend`` inlined above,
    so that when the registry returns we re-add the AppConfig advertisement
    and the dispatch path lights back up unchanged.

    ``name = 'hermes'`` is the prefix in the share-destination form field
    value (``'hermes:<topic>'``); the sub-destination half is the HERMES
    topic the message is published to.
>>>>>>> e177c2d (bulk commit of unreviewed changes)

#     The ``share`` method is the main entry point: it picks the right HERMES
#     topic out of ``form_data``, assembles a ``BuildHermesMessage`` from
#     the form's title/author/message fields, filters the supplied datums
#     against the already-published-to-this-topic check, and calls
#     ``publish_to_hermes``.
#     """

#     # ``name = 'hermes'`` because the share-destination form field encodes
#     # a choice from this backend as ``'hermes:<topic>'``. DataShareView.post()
#     # parses the 'hermes' prefix and looks up this class in the registry.
#     name = 'hermes'

#     # ``verbose_name = 'HERMES'`` is the heading shown above this backend's
#     # topics in the share-destination dropdown.
#     verbose_name = 'HERMES'

#     @classmethod
#     def get_destination_choices(cls, user=None) -> list:
#         """Return the list of ``('hermes:<topic>', '<topic>')`` tuples for the share-destination dropdown.

#         Topics come from HERMES via ``get_hermes_topics(user=user)`` —
#         which reads the User's credentials, calls HERMES ``/profile/``,
#         and caches the response for a day per User. If the User's TOM
#         operator has configured ``USER_TOPICS`` in
#         ``settings.DATA_SHARING['hermes']``, that list is intersected
#         with what HERMES says the User can write to (so the TOM
#         operator can restrict the subset of HERMES topics shown in the
#         TOM UI).
#         """
#         topics = get_hermes_topics(user=user)
#         if not topics:
#             # No writable topics from HERMES means we cannot publish; the
#             # backend renders no dropdown entries.
#             return []

#         # Optional operator-side restriction: show only the intersection
#         # of the HERMES-provided topics and the operator's configured list.
#         cfg = getattr(settings, 'DATA_SHARING', {}).get('hermes', {})
#         user_topics = cfg.get('USER_TOPICS', [])
#         if user_topics:
#             topics = [t for t in user_topics if t in topics]

#         return [(f'{cls.name}:{topic}', topic) for topic in topics]

#     def validate_credentials(self, user=None) -> bool:
#         """Return True if the User has a HERMES API key available from either
#         the User's HermesProfile or settings.py.
#         """
#         api_key_available = bool(resolve_hermes_credentials(user).get('api_key'))
#         return api_key_available

#     def share(self, form_data: dict, *,
#               reduced_datums=None, targets=None, data_products=None,
#               user=None, **kwargs) -> dict:
#         """Publish the supplied data to HERMES under the topic named in ``form_data['share_destination']``.

#         Input normalization: the caller supplies some combination of
#         ``reduced_datums`` / ``targets`` / ``data_products``. This method
#         resolves all three into (a) a queryset of ReducedDatums to publish
#         and (b) a queryset of Targets to mention. When ``data_products``
#         is provided, we walk each DataProduct to collect its ReducedDatums
#         and its Target. When only ``targets`` is provided (target-only
#         announcement), we leave datums empty.

#         Filtering: ``check_for_share_safe_datums`` (imported lazily from
#         tom_dataproducts) drops any datum already published to this exact
#         HERMES topic so we don't produce duplicate messages.

#         Returns the ``publish_to_hermes`` response on success or an
#         error-shaped feedback dict on failure.
#         """
#         # Parse the destination: 'hermes:hermes.test' -> topic = 'hermes.test'.
#         share_destination: str = (form_data or {}).get('share_destination', '')
#         prefix, sep, topic = share_destination.partition(':')
#         if not sep or not topic:
#             return {'message': (
#                 f"ERROR: share_destination '{share_destination}' is not in the expected "
#                 "'hermes:<topic>' form."
#             )}

#         # Pull DEFAULT_AUTHORS out of the operator's settings block as the
#         # final fallback for the authors field. Per-user preferences would
#         # live on the HermesProfile; not implemented yet.
#         cfg = getattr(settings, 'DATA_SHARING', {}).get('hermes', {})
#         authors = (form_data or {}).get('share_authors', cfg.get('DEFAULT_AUTHORS', None))

#         # Resolve the data to publish. The caller may have passed in any
#         # combination of the three querysets; reconcile them here.
#         datums, resolved_targets, title_name = self._resolve_inputs(
#             form_data=form_data,
#             reduced_datums=reduced_datums,
#             targets=targets,
#             data_products=data_products,
#         )

#         # Default the title to something sensible if the form did not
#         # provide one (the share-data view has a field for this, but the
#         # per-target continuous-share path calls share() with a minimal
#         # form_data and relies on this fallback).
#         tom_name = getattr(settings, 'TOM_NAME', 'TOM Toolkit')
#         default_title = f'Updated data for {title_name} from {tom_name}.' if title_name else (
#             f'Updated data from {tom_name}.'
#         )
#         message_info = BuildHermesMessage(
#             title=(form_data or {}).get('share_title', default_title),
#             submitter=(form_data or {}).get('submitter'),
#             authors=authors,
#             message=(form_data or {}).get('share_message', None),
#             topic=topic,
#         )

#         # Filter out datums we have already published to this topic. Lazy
#         # import because tom_dataproducts.sharing depends on tom_common
#         # via other paths and we want to avoid import-time coupling.
#         from tom_dataproducts.sharing import check_for_share_safe_datums
#         filtered_datums = check_for_share_safe_datums(self.name, datums, topic=topic)

#         datum_count = filtered_datums.count() if hasattr(filtered_datums, 'count') else len(filtered_datums)
#         target_count = resolved_targets.count() if hasattr(resolved_targets, 'count') else len(resolved_targets)
#         if datum_count <= 0 and target_count <= 0:
#             return {'message': (
#                 'ERROR: No valid data or targets to share. (Check Sharing Protocol. Note that '
#                 'only photometry and spectroscopy data types are supported for sharing with HERMES.)'
#             )}

#         return publish_to_hermes(message_info, filtered_datums, resolved_targets, user=user)

    # @staticmethod
    # def _resolve_inputs(form_data, reduced_datums, targets, data_products):
    #     """Normalize the (data_products, reduced_datums, targets) inputs into (datums, targets, title_name).

    #     Called only by ``share()``. Keeps the dispatch logic out of the
    #     main method so ``share()`` reads top-to-bottom without nested
    #     conditionals.
    #     """
    #     # Lazy imports: ReducedDatum / Target live in apps other than
    #     # tom_hermes; importing at module top couples load-order to those
    #     # apps even when this path isn't hit.
    #     from tom_dataproducts.models import ReducedDatum
    #     from tom_targets.models import Target

    #     # Case 1: a TargetList share. The form carries the TargetList
    #     # itself (set by the TargetListShareView), and targets is the
    #     # selected Target queryset.
    #     target_list = (form_data or {}).get('target_list') if form_data else None
    #     if target_list is not None and targets is not None:
    #         title_name = f'{target_list.name} target list'
    #         datums = reduced_datums if reduced_datums is not None else ReducedDatum.objects.none()
    #         return datums, targets, title_name

    #     # Case 2: data_products supplied (single DataProduct share).
    #     if data_products is not None and data_products.exists():
    #         product = data_products.first()
    #         datums = ReducedDatum.objects.filter(data_product=product)
    #         resolved_targets = Target.objects.filter(pk=product.target.pk)
    #         title_name = product.target.name if product.target else ''
    #         return datums, resolved_targets, title_name

    #     # Case 3: reduced_datums supplied. Infer the Target from the first
    #     # datum and publish the explicitly-listed datums.
    #     if reduced_datums is not None and reduced_datums.exists():
    #         first = reduced_datums.first()
    #         resolved_targets = Target.objects.filter(pk=first.target.pk)
    #         # HERMES only handles photometry and spectroscopy; restrict
    #         # the datums to those types rather than letting the message
    #         # build fail later.
    #         datums = reduced_datums.filter(data_type__in=['photometry', 'spectroscopy'])
    #         return datums, resolved_targets, first.target.name

    #     # Case 4: only targets supplied (target-only announcement).
    #     if targets is not None and targets.exists():
    #         first = targets.first()
    #         return ReducedDatum.objects.none(), targets, first.name

    #     # Case 5: nothing — share() will return an error feedback dict.
    #     return ReducedDatum.objects.none(), Target.objects.none(), ''
