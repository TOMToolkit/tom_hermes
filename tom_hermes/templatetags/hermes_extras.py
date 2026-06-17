"""
Template tags for tom_hermes.
"""
from __future__ import annotations

import logging

from django import template
from django.forms.models import model_to_dict

<<<<<<< HEAD
=======
from tom_hermes.forms import HermesProfileForm
>>>>>>> e177c2d (bulk commit of unreviewed changes)
from tom_hermes.models import HermesProfile

logger = logging.getLogger(__name__)

register = template.Library()


@register.inclusion_tag('tom_hermes/partials/hermes_user_profile.html')
def hermes_profile_data(user) -> dict:
<<<<<<< HEAD
=======
    """Return the context dict for the HERMES card on the user-profile page.

    Lookup:
    1. Fetch the user's ``HermesProfile``, creating one if it does not
       exist yet. This matches the tom_eso pattern: a fresh TOM user
       should see the HERMES card immediately (with blank values), not
       have to navigate to a setup page before the card appears.
    2. Build a ``profile_data_list`` of ``{label, value}`` dicts — one per
       displayable field. Encrypted fields are decrypted by reading the
       ``EncryptedProperty`` descriptor; their values are rendered as a
       masked password input the user can click to reveal, falling back
       to ``[not set]`` when the field is empty.

    The rendered partial shows each row of ``profile_data_list`` in a
    bootstrap definition list and an "Edit" icon that points at
    ``tom_hermes:hermes-profile-update``.
>>>>>>> e177c2d (bulk commit of unreviewed changes)
    """
    Return the context dict for the HERMES card on the user-profile page.
    """

    # Get the user's Hermes Profile. Make one if none found.
    try:
        profile: HermesProfile = user.hermesprofile
    except HermesProfile.DoesNotExist:
        profile = HermesProfile.objects.create(user=user)

    # Need to include hermes_api_key separately since it's an encrypted field
    exclude_fields = ['user', 'id', 'hermes_api_key']
    profile_dict = model_to_dict(user.demoprofile, exclude=exclude_fields)

<<<<<<< HEAD
    context = {
=======
    # Encrypted fields: render as a masked password input the user can
    # click to reveal, mirroring the tom_eso pattern (PR #44). When the
    # value is unset we fall back to a plain ``[not set]`` marker — there
    # is nothing to mask. ``is_password`` flags the masked-input rendering
    # for the partial template; the actual decrypted value is sent down
    # to the browser only when there is one to reveal on click.
    for encrypted_field_name in ('hermes_api_key',):
        label = HermesProfileForm.base_fields[encrypted_field_name].label
        # Reading the EncryptedProperty decrypts; empty fields come back
        # as '' so the falsy check below routes them to "not set".
        decrypted = getattr(profile, encrypted_field_name)
        if decrypted:
            profile_data_list.append({
                'label': label,
                'value': decrypted,
                'is_password': True,
            })
        else:
            profile_data_list.append({
                'label': label,
                'value': '[not set]',
            })

    return {
>>>>>>> e177c2d (bulk commit of unreviewed changes)
        'user': user,
        'hermes_profile': profile,
        'profile_data_list': profile_dict,
        'hermes_api_key': profile.hermes_api_key
    }

    return context
