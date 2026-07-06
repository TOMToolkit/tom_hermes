from __future__ import annotations

import logging

from django import template
from django.conf import settings

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

    context = {
        'user': user,
        'hermes_profile': profile,
        'profile_data_list': [{}],  # Not quite sure what this does
        'hermes_api_key': profile.hermes_api_key
    }

    return context


def share_button(context):
    """
    Returns the app specific context for making a target detail button.
    """
    # get the default topic from HERMES_CONFIGURATION (use tomtoolkit.test) if it's not set
    hermes_config = getattr(settings, 'HERMES_CONFIGURATION', {})
    hermes_topic = hermes_config.get('DEFAULT_TOPIC', 'tomtoolkit.test')

    context = {
        'button_text': 'Submit to HERMES',
        'hermes_topic': hermes_topic,
    }
    return context
