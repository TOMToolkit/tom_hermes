"""
Template tags for tom_hermes.
"""
from __future__ import annotations

import logging

from django import template
from django.forms.models import model_to_dict

from tom_hermes.models import HermesProfile

logger = logging.getLogger(__name__)

register = template.Library()


@register.inclusion_tag('tom_hermes/partials/hermes_user_profile.html')
def hermes_profile_data(user) -> dict:
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
    profile_dict = model_to_dict(user.hermesprofile, exclude=exclude_fields)

    context = {
        'user': user,
        'hermes_profile': profile,
        'profile_data_list': profile_dict,
        'hermes_api_key': profile.hermes_api_key
    }

    return context
