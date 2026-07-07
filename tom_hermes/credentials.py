"""HERMES credential lookup: per-user profile, falling back to TOM-wide settings.
"""
from __future__ import annotations

from django.conf import settings

from tom_hermes.models import HermesProfile


def resolve_hermes_credentials(user=None) -> dict:
    """Return the HERMES credentials dict to use for this User.

    Params: user
    Return: dict:: {'api_key','base_url'}
    """
    result = {
        'api_key': None,
        'base_url': None,
    }

    # first, try (to get creds) from HermesProfile
    if user is not None and getattr(user, 'is_authenticated', False):
        profile = HermesProfile.objects.filter(user=user).first()
        if profile is not None:
            api_key = profile.hermes_api_key
            if api_key:
                result['api_key'] = api_key

    # second, fall back to the TOM-wide HERMES_CONFIGURATION settings dict
    cfg = getattr(settings, 'HERMES_CONFIGURATION', {})
    if not result['api_key'] and cfg.get('HERMES_API_TOKEN'):
        result['api_key'] = cfg['HERMES_API_TOKEN']
    result['base_url'] = cfg.get('BASE_URL', 'https://hermes.lco.global/')

    return result
