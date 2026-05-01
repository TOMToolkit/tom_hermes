"""
HERMES credential lookup: per-user profile, falling back to TOM-wide settings.

### What lives here

- ``resolve_hermes_credentials(user)`` — the one function that answers
  the question "what HERMES credentials should I use for this user?"

### Where it lives

At the app root rather than inside ``tom_hermes.sharing`` because
credentials are not a sharing-only concern. Today only ``sharing`` calls
it; forward-looking, ``alertstreams`` will want it for per-user Hopskotch
subscription (the ``HermesProfile`` already has ``hop_username`` /
``hop_password`` fields for that), and ``dataservices`` may want it if
the HERMES archive endpoint ever requires authentication.

### Lookup order

1. ``HermesProfile`` for ``user`` (per-user credentials, encrypted at rest).
2. ``settings.DATA_SHARING['hermes']`` (TOM-wide shared credentials).

Both paths are fully supported: the TOM-wide path is the canonical
mechanism when a TOM operator authenticates all users with a single set
of HERMES credentials. No deprecation warning fires when falling through
to the settings path.

### Callers

- ``tom_hermes.sharing.publisher.publish_to_hermes``
- ``tom_hermes.sharing.publisher.preload_to_hermes``
- ``tom_hermes.sharing.publisher.get_hermes_topics``
- ``tom_hermes.sharing.backend.HermesSharingBackend.validate_credentials``
"""
from __future__ import annotations

from django.conf import settings

from tom_hermes.models import HermesProfile


def resolve_hermes_credentials(user=None) -> dict:
    """Return the HERMES credentials dict to use for this User.

    Returns a dict with keys ``'api_key'``, ``'base_url'``, ``'hop_username'``,
    ``'hop_password'``. Values default to ``None`` if neither the user's
    Profile nor ``settings.DATA_SHARING['hermes']`` provides them;
    ``'base_url'`` falls back to ``'https://hermes.lco.global/'``.

    Reading an ``EncryptedProperty`` off ``HermesProfile`` requires the
    Django session cipher — which is bound to an authenticated request.
    Background callers (management commands, signal handlers) pass
    ``user=None`` (or an ``AnonymousUser``) and the function falls
    straight through to the settings lookup.
    """
    result = {
        'api_key': None,
        'base_url': None,
        'hop_username': None,
        'hop_password': None,
    }

    # Per-user path: only if the request carries an authenticated User and
    # that User has a populated HermesProfile. Gating on is_authenticated
    # keeps AnonymousUser (and plain ``None``) from reaching the Profile
    # lookup, which would fail because related filters require a saved User.
    if user is not None and getattr(user, 'is_authenticated', False):
        profile = HermesProfile.objects.filter(user=user).first()
        if profile is not None:
            # Lazy import to avoid pulling tom_common.session_utils at
            # module load time (it depends on Django session middleware
            # being configured).
            from tom_common.session_utils import get_encrypted_field
            api_key = get_encrypted_field(user, profile, 'hermes_api_key')
            hop_pw = get_encrypted_field(user, profile, 'hop_password')
            if api_key:
                result['api_key'] = api_key
            if profile.hop_username:
                result['hop_username'] = profile.hop_username
            if hop_pw:
                result['hop_password'] = hop_pw

    # TOM-wide fallback: settings.DATA_SHARING['hermes']. Always consulted
    # for base_url (which is rarely per-user). Consulted for api_key only
    # if the Profile did not supply one.
    cfg = getattr(settings, 'DATA_SHARING', {}).get('hermes', {})
    if not result['api_key'] and cfg.get('HERMES_API_KEY'):
        result['api_key'] = cfg['HERMES_API_KEY']
    result['base_url'] = cfg.get('BASE_URL', 'https://hermes.lco.global/')
    return result
