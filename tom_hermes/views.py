"""
Views for tom_hermes.

### What lives here

- ``HermesProfileUpdateView`` — ``UpdateView`` for editing a ``HermesProfile``.
  Rendered at ``/hermes/users/<pk>/update/`` (see ``urls.py``) and reached
  from the "Edit" icon on the HERMES card on the user profile page
  (see ``templates/tom_hermes/partials/hermes_user_profile.html``).

Pattern copied from ``tom_eso.views.ProfileUpdateView``.
"""
from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic.edit import UpdateView

from tom_hermes.forms import HermesProfileForm
from tom_hermes.models import HermesProfile


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
    template_name = 'tom_hermes/hermes_update_user_profile.html'

    # Custom form class handles the two encrypted fields (hermes_api_key, hop_password).
    form_class = HermesProfileForm

    def get_form_kwargs(self):
        """Extend ``UpdateView.get_form_kwargs`` to pass ``request.user`` into the form.

        The form needs the User to read/write encrypted fields (the session
        cipher is derived from the User's login).
        """
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        """Return the user-profile page URL so the user sees the updated card after saving."""
        return reverse_lazy('user-profile')
