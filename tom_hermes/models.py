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

Secrets (``hermes_api_key``, ``hop_password``) are encrypted at rest using
the Fernet-backed session cipher that ``tom_common.models.EncryptableModelMixin``
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

    # Hopskotch SCRAM username — per-user identity for kafka.scimma.org.
    # Not a secret, so stored as a plain CharField. Paired with the
    # encrypted ``hop_password`` descriptor below.
    hop_username = models.CharField(
        max_length=255, null=True, blank=True, verbose_name='Hopskotch Username',
    )

    # Hopskotch SCRAM password — the secret half of the kafka.scimma.org
    # credential pair. Used by the ``hop-client`` authentication path
    # that the stream consumer sets up.
    _hop_password_encrypted = models.BinaryField(null=True, blank=True)
    hop_password = EncryptedProperty('_hop_password_encrypted')

    # Default topics the User is allowed to publish to. Populated from
    # HERMES ``/api/v0/profile/`` once per form render (see
    # ``tom_hermes.sharing.get_hermes_topics``). JSONField rather than a
    # M2M because the topic list is a per-user HERMES state, not a set of
    # TOM-side relations.
    default_topics = models.JSONField(
        default=list, blank=True, verbose_name='Default HERMES Topics',
    )

    def __str__(self) -> str:
        return f'{self.user.username} HERMES Profile'
