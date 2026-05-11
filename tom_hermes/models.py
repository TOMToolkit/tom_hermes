from __future__ import annotations

import logging

from django.db import models

from tom_common.models import EncryptableModelMixin, EncryptedProperty

logger = logging.getLogger(__name__)


class HermesProfile(EncryptableModelMixin, models.Model):
    """Per-user HERMES data.

    The ``user`` OneToOneField is inherited from ``EncryptableModelMixin``
    and should not be redefined here; associates the model instance with
    it's User.

    Uses the standard idiom for encrytped data in TOMToolkit.
    """

    # LCO HERMES submit API key (hermes.lco.global/api/v0/submit_message/).
    # Used by ``publish_to_hermes`` when the User publishes data to HERMES.
    _hermes_api_key_encrypted = models.BinaryField(null=True, blank=True)
    hermes_api_key = EncryptedProperty('_hermes_api_key_encrypted')

    def __str__(self) -> str:
        return f'{self.user.username} HERMES Profile'
