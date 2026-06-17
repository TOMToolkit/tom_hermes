"""
Views for tom_hermes.

### What lives here

- ``HermesProfileUpdateView`` — ``UpdateView`` for editing a ``HermesProfile``.
  Rendered at ``/hermes/users/<pk>/update/`` (see ``urls.py``) and reached
  from the "Edit" icon on the HERMES card on the user profile page
  (see ``templates/tom_hermes/partials/hermes_user_profile.html``).
- ``TargetHermesPreloadView`` — POST handler for the "Open in Hermes 🗗"
  button on a single Target's data-share dialog. Stashes a draft on
  HERMES (``/api/v0/submit_message/preload/``) and redirects the user to
  the HERMES UI to review and submit.
- ``TargetGroupingHermesPreloadView`` — same workflow but for a
  ``TargetList`` (group of Targets).

The two preload views previously lived in ``tom_targets.views`` (in
``tom_base``) and were the only thing pinning ``tom_base`` to a hard
``import tom_hermes`` — they have been moved here so the dependency
direction stays plugin → host. Callers from ``tom_base`` reach them
through the ``tom_hermes:target-preload`` / ``tom_hermes:target-grouping-preload``
URL names (see ``tom_hermes/urls.py``).
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import View
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import UpdateView

from tom_dataproducts.models import ReducedDatum
from tom_targets.models import Target, TargetList

from tom_hermes.credentials import resolve_hermes_credentials
from tom_hermes.models import HermesProfile
from tom_hermes.sharing.publisher import BuildHermesMessage, preload_to_hermes


class HermesProfileUpdateView(LoginRequiredMixin, UpdateView):
    """Edit view for the logged-in user's ``HermesProfile`` credentials.

    The ``HermesProfile`` fields are displayed on the user-profile page via
    the ``show_app_profiles`` inclusion tag in
    ``tom_common.templates.tom_common.user_profile.html``, which in turn
    calls each registered ``profile_details`` integration point (see
    ``tom_hermes.apps.TomHermesConfig.profile_details``). The "Edit" icon
    on the HERMES card links to this view.
    """

    model = HermesProfile
<<<<<<< HEAD
    fields = ['hermes_api_key']
    template_name = 'tom_hermes/hermes_update_user_profile.html'

=======
    fields = []
    template_name = 'tom_hermes/hermes_update_user_profile.html'

    # Custom form class handles the encrypted hermes_api_key field.
    form_class = HermesProfileForm
>>>>>>> e177c2d (bulk commit of unreviewed changes)

    def get_success_url(self):
        """Return the user-profile page URL so the user sees the updated card after saving."""
        return reverse_lazy('user-profile')


class TargetHermesPreloadView(SingleObjectMixin, View):
    """Stash a draft HERMES message for one Target's selected datums and redirect to HERMES.

    Reached via POST from the "Open in Hermes 🗗" button on the share-data
    dialog for a Target (see ``target_share.html`` and
    ``photometry_datalist_for_target.html`` in ``tom_base``).

    The flow: read the form's selected ``share-box`` ReducedDatum pks,
    build a ``BuildHermesMessage`` envelope from the form fields, call
    ``preload_to_hermes`` (which POSTs the draft to HERMES and gets back
    a key), then redirect the user to ``<base_url>submit-message?id=<key>``
    so they finalize the submission on HERMES.

    Distinct from ``HermesSharingBackend.share`` (the registry-dispatch
    path used by ``DataShareView``, currently dormant pending the
    return of ``tom_common.sharing``) — that path, when active, posts
    the message definitively; this path bounces the user out to HERMES
    for review.
    """

    model = Target
    # Set ``app_label`` for Django-Guardian permissions so this works with
    # custom Target models that override ``Meta.app_label``.
    permission_required = f'{Target._meta.app_label}.change_target'

    def post(self, request, *args, **kwargs):
        target = self.get_object()
        # Per-user credentials first (HermesProfile), TOM-wide settings as
        # fallback. Same lookup the rest of tom_hermes uses.
        creds = resolve_hermes_credentials(user=request.user)
        if not creds.get('api_key'):
            return HttpResponseBadRequest(
                'No HERMES API key configured for this user '
                '(check the HermesProfile or DATA_SHARING settings).'
            )

        # The share_destination field is encoded as 'hermes:<topic>'; the
        # topic is the part after the colon.
        topic = request.POST.get('share_destination', '').split(':')[-1]
        title = request.POST.get('share_title') or f'Updated data for {target.name}'

        # DEFAULT_AUTHORS is a TOM-wide convenience fallback only — there is
        # no per-user authors field on HermesProfile yet.
        cfg = getattr(settings, 'DATA_SHARING', {}).get('hermes', {})
        hermes_message = BuildHermesMessage(
            title=title,
            topic=topic,
            submitter=request.POST.get('submitter'),
            message=request.POST.get('share_message', ''),
            authors=cfg.get('DEFAULT_AUTHORS'),
        )
        reduced_datums = ReducedDatum.objects.filter(
            pk__in=request.POST.getlist('share-box', [])
        )
        preload_key = preload_to_hermes(
            hermes_message, reduced_datums, [target], user=request.user
        )
        load_url = creds['base_url'] + f'submit-message?id={preload_key}'
        return HttpResponseRedirect(load_url)


class TargetGroupingHermesPreloadView(SingleObjectMixin, View):
    """Stash a draft HERMES message for a TargetList and redirect to HERMES.

    Reached via POST from the "Open in Hermes 🗗" button on the
    target-grouping share dialog (see ``target_group_share.html`` in
    ``tom_base``). Same flow as ``TargetHermesPreloadView`` but operates
    on a ``TargetList`` and the user-selected subset of its Targets.

    The form's ``dataSwitch`` checkbox controls whether the message
    includes the targets' photometry; when off, only the target table
    is published.
    """

    model = TargetList
    # The permission is checked against the underlying Target model — TargetList
    # is a grouping construct, not a permission-bearing object.
    permission_required = f'{Target._meta.app_label}.change_target'

    def post(self, request, *args, **kwargs):
        targetlist = self.get_object()
        creds = resolve_hermes_credentials(user=request.user)
        if not creds.get('api_key'):
            return HttpResponseBadRequest(
                'No HERMES API key configured for this user '
                '(check the HermesProfile or DATA_SHARING settings).'
            )

        topic = request.POST.get('share_destination', '').split(':')[-1]
        title = request.POST.get('share_title') or f'Updated targets for group {targetlist.name}.'

        cfg = getattr(settings, 'DATA_SHARING', {}).get('hermes', {})
        hermes_message = BuildHermesMessage(
            title=title,
            topic=topic,
            submitter=request.POST.get('submitter'),
            message=request.POST.get('share_message', ''),
            authors=cfg.get('DEFAULT_AUTHORS'),
        )

        targets = Target.objects.filter(
            pk__in=request.POST.getlist('selected-target', [])
        )
        # The 'dataSwitch' checkbox on the form controls whether photometry
        # is included with the announcement; off means a target-only message.
        if request.POST.get('dataSwitch', '') == 'on':
            reduced_datums = ReducedDatum.objects.filter(target__in=targets, data_type='photometry')
        else:
            reduced_datums = ReducedDatum.objects.none()

        preload_key = preload_to_hermes(
            hermes_message, reduced_datums, targets, user=request.user
        )
        load_url = creds['base_url'] + f'submit-message?id={preload_key}'
        return HttpResponseRedirect(load_url)
