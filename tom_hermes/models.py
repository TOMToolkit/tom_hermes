"""
Per-user HERMES credentials model.

### Why this model exists

HERMES authentication has historically been configured in the TOM operator's
``settings.DATA_SHARING['hermes']`` block, which is a TOM-wide shared
credential. For multi-user TOMs we want per-user credentials so each User
publishes and subscribes under their own HERMES identity.

### Where the credentials are read

``tom_hermes.sharing._resolve_hermes_credentials(user)`` reads this model
first. If the User has no ``HermesProfile`` (or the Profile has no HERMES
credentials), that function falls back to
``settings.DATA_SHARING['hermes']``. Both paths are fully supported
indefinitely: Profile = per-user, settings = TOM-wide shared credentials.
No deprecation warning attaches to the settings fallback.

### Encryption

The ``hermes_api_key`` secret is encrypted at rest using the
Fernet-backed session cipher that ``tom_common.models.EncryptableModelMixin``
/ ``EncryptedProperty`` provides. The cipher key is derived from the User's
login password via PBKDF2-HMAC and lives in the Django session, not in
settings. See ``tom_common.session_utils``. Reading an encrypted field
therefore requires an authenticated request context (``request.user``
with an active session).

### Follows the same pattern as tom_eso

The structure here mirrors ``tom_eso.models.ESOProfile``: the mixin
supplies the ``user`` OneToOneField, and each secret field is a pair of
``_<name>_encrypted`` BinaryField plus a ``<name> = EncryptedProperty(...)``
descriptor.
"""
from __future__ import annotations

import logging

from django.db import models

from tom_common.models import EncryptableModelMixin, EncryptedProperty

logger = logging.getLogger(__name__)


class HermesProfile(EncryptableModelMixin, models.Model):
    """Per-user HERMES credentials.

    The ``user`` OneToOneField is inherited from ``EncryptableModelMixin``
    and should not be redefined here. Each secret is stored as a
    ``_<name>_encrypted`` BinaryField with a matching
    ``<name> = EncryptedProperty('_<name>_encrypted')`` descriptor that
    transparently encrypts on write and decrypts on read (see the
    ``EncryptableModelMixin`` docstring for the setup sequence).
    """

    # LCO HERMES submit API key (hermes.lco.global/api/v0/submit_message/).
    # Used by ``publish_to_hermes`` when the User publishes data to HERMES.
    _hermes_api_key_encrypted = models.BinaryField(null=True, blank=True)
    hermes_api_key = EncryptedProperty('_hermes_api_key_encrypted')

    def __str__(self) -> str:
        return f'{self.user.username} HERMES Profile'
