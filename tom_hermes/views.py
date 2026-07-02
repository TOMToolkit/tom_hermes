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
from tom_hermes.publisher import BuildHermesMessage, preload_to_hermes


class HermesProfileUpdateView(LoginRequiredMixin, UpdateView):
    """Edit view for the logged-in user's ``HermesProfile`` credentials.
    """

    model = HermesProfile
    fields = ['hermes_api_key']
    template_name = 'tom_hermes/hermes_update_user_profile.html'

    def get_success_url(self):
        """Return the user-profile page URL so the user sees the updated card after saving."""
        return reverse_lazy('user-profile')


class TargetHermesPreloadView(LoginRequiredMixin, SingleObjectMixin, View):
    """Redirect to HERMES with a draft message for the Target (from TargetDetail page).
    """
    model = Target

    def post(self, request, *args, **kwargs):
        target: Target = self.get_object()

        # get the HERMES credentials from the profile or settings
        creds = resolve_hermes_credentials(user=request.user)
        if not creds.get('api_key'):
            return HttpResponseBadRequest(
                'No HERMES API key configured (set Hermes API key in User Profile '
                "or HERMES_CONFIGURATION['HERMES_API_TOKEN'] in settings.py)."
            )

        # get topic and title for the draft message from the post
        topic = request.POST.get('hermes_topic', '').split(':')[-1]
        title = request.POST.get('share_title') or f'Updated data for {target.name}'

        hermes_message = BuildHermesMessage(
            title=title,
            topic=topic,
            submitter=request.POST.get('submitter', ''),
            message=request.POST.get('message', ''),
            authors='',  # user will have to fill in authors on HERMES page (for now)
        )

        # Pre-load message and get key for redirect
        preload_key = preload_to_hermes(
            hermes_message, [], [target], user=request.user
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
