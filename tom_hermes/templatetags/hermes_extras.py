"""
Template tags for tom_hermes.

### What lives here

- ``hermes_profile_data`` — inclusion tag registered under the
  ``profile_details`` integration point (see
  ``tom_hermes.apps.TomHermesConfig.profile_details``). Called by
  ``tom_common.templatetags.user_extras.show_app_profiles`` when the user
  profile page renders its collection of app-specific cards. Produces the
  context dict consumed by ``tom_hermes/partials/hermes_user_profile.html``.
"""
from __future__ import annotations

import logging

from django import template

from tom_common.session_utils import get_encrypted_field

from tom_hermes.forms import HermesProfileForm
from tom_hermes.models import HermesProfile

logger = logging.getLogger(__name__)

register = template.Library()


@register.inclusion_tag('tom_hermes/partials/hermes_user_profile.html')
def hermes_profile_data(user) -> dict:
    """Return the context dict for the HERMES card on the user-profile page.

    Lookup:
    1. Fetch the user's ``HermesProfile``, creating one if it does not
       exist yet. This matches the tom_eso pattern: a fresh TOM user
       should see the HERMES card immediately (with blank values), not
       have to navigate to a setup page before the card appears.
    2. Build a ``profile_data_list`` of ``{label, value}`` dicts — one per
       displayable field. Encrypted fields are decrypted via
       ``get_encrypted_field``; their values are masked to ``'[set]'`` /
       ``'[not set]'`` because we never want to render the raw credential
       on the profile page.

    The rendered partial shows each row of ``profile_data_list`` in a
    bootstrap definition list and an "Edit" icon that points at
    ``tom_hermes:hermes-profile-update``.
    """
    try:
        profile: HermesProfile = user.hermesprofile
    except HermesProfile.DoesNotExist:
        # First time the user visits the profile page after HermesProfile
        # was added to the schema: create an empty Profile so the card
        # appears immediately with "not set" placeholders.
        profile = HermesProfile.objects.create(user=user)

    profile_data_list: list = []

    # Plain (non-encrypted) fields: render the value directly.
    for field_name in ('hop_username',):
        field = profile._meta.get_field(field_name)
        value = getattr(profile, field_name) or '[not set]'
        profile_data_list.append({'label': field.verbose_name, 'value': value})

    # default_topics is a list; render its length so the card shows at a
    # glance whether any topics are configured without revealing them all.
    topics = profile.default_topics or []
    profile_data_list.append({
        'label': profile._meta.get_field('default_topics').verbose_name,
        'value': f'{len(topics)} configured' if topics else '[none]',
    })

    # Encrypted fields: never render the actual value on the profile page.
    # We only indicate whether a value is set, to protect the secret from
    # shoulder-surfing and from any future page-capture / logging.
    for encrypted_field_name in ('hermes_api_key', 'hop_password'):
        label = HermesProfileForm.base_fields[encrypted_field_name].label
        # get_encrypted_field can return None when the session cipher is
        # unavailable; we treat that the same as "not set" for display.
        decrypted = get_encrypted_field(user, profile, encrypted_field_name)
        profile_data_list.append({
            'label': label,
            'value': '[set]' if decrypted else '[not set]',
        })

    return {
        'user': user,
        'hermes_profile': profile,
        'profile_data_list': profile_data_list,
    }
