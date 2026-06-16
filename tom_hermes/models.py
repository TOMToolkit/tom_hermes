from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.db import models

from tom_common.encryption import EncryptedModelField

logger = logging.getLogger(__name__)


class HermesProfile(models.Model):
    """Per-user HERMES data.

    Uses the EncryptedModelField from TOMToolkit.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    hermes_api_key = EncryptedModelField(null=True, blank=True)

    def __str__(self) -> str:
        return f'{self.user.username} HERMES Profile'
