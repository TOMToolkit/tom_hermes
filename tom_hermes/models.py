from __future__ import annotations

import logging

<<<<<<< HEAD
from django.contrib.auth.models import User
=======
from django.conf import settings
>>>>>>> e177c2d (bulk commit of unreviewed changes)
from django.db import models

from tom_common.encryption import EncryptedModelField

logger = logging.getLogger(__name__)


class HermesProfile(models.Model):
    """Per-user HERMES data.
<<<<<<< HEAD

    Uses the EncryptedModelField from TOMToolkit.
=======
>>>>>>> e177c2d (bulk commit of unreviewed changes)
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

<<<<<<< HEAD
    user = models.OneToOneField(User, on_delete=models.CASCADE)
=======
>>>>>>> e177c2d (bulk commit of unreviewed changes)
    hermes_api_key = EncryptedModelField(null=True, blank=True)

    def __str__(self) -> str:
        return f'{self.user.username} HERMES Profile'
