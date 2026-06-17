"""
HERMES credential lookup: per-user profile, falling back to TOM-wide settings.

### What lives here

- ``resolve_hermes_credentials(user)`` — the one function that answers
  the question "what HERMES credentials should I use for this user?"

### Where it lives

At the app root rather than inside ``tom_hermes.sharing`` because
credentials are not a sharing-only concern. Today both ``sharing``
and ``dataservices`` call it. A future per-user Hopskotch
subscription path would add Kafka SCRAM credentials to the
``HermesProfile`` and to the dict this function returns; today the
SCRAM creds for ``readstreams`` come from
``settings.ALERT_STREAMS[...]['OPTIONS']`` only.

### Lookup order

1. ``HermesProfile`` for ``user`` (per-user credentials, encrypted at rest).
2. ``settings.DATA_SHARING['hermes']`` (TOM-wide shared credentials).

Both paths are fully supported: the TOM-wide path is the canonical
mechanism when a TOM operator authenticates all users with a single set
of HERMES credentials.

"""
from __future__ import annotations

from django.conf import settings

from tom_hermes.models import HermesProfile


def resolve_hermes_credentials(user=None) -> dict:
    """Return the HERMES credentials dict to use for this User.

    Returns a dict with keys ``'api_key'`` and ``'base_url'``. Values
    default to ``None`` if neither the user's Profile nor
    ``settings.DATA_SHARING['hermes']`` provides them; ``'base_url'``
    falls back to ``'https://hermes.lco.global/'``.

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

    # second, try settings.DATA_SHARING
    cfg = getattr(settings, 'DATA_SHARING', {}).get('hermes', {})
    if not result['api_key'] and cfg.get('HERMES_API_KEY'):
        result['api_key'] = cfg['HERMES_API_KEY']
    result['base_url'] = cfg.get('BASE_URL', 'https://hermes.lco.global/')
    return result
