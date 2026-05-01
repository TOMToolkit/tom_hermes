# First migration for tom_hermes. Creates the HermesProfile table.
#
# Hand-authored (rather than makemigrations-generated) so this change can
# land alongside the initial HermesProfile model in the same commit without
# requiring a Django environment during the refactor. The next time someone
# runs ``makemigrations tom_hermes`` and the model has changed, that will
# produce a follow-on migration (``0002_...``) against this as its parent.
#
# Mirrors the shape of ``tom_eso.migrations.0001_initial`` because both
# profiles extend ``tom_common.models.EncryptableModelMixin``, which
# contributes the ``user`` OneToOneField.

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HermesProfile',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False, verbose_name='ID',
                )),
                ('_hermes_api_key_encrypted', models.BinaryField(blank=True, null=True)),
                ('hop_username', models.CharField(
                    blank=True, max_length=255, null=True, verbose_name='Hopskotch Username',
                )),
                ('_hop_password_encrypted', models.BinaryField(blank=True, null=True)),
                ('default_topics', models.JSONField(
                    blank=True, default=list, verbose_name='Default HERMES Topics',
                )),
                ('user', models.OneToOneField(
                    on_delete=models.deletion.CASCADE, to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                # Matches the ``ESOProfile`` pattern: the model is concrete but
                # inherits from an abstract mixin, so Django emits ``abstract: False``.
                'abstract': False,
            },
        ),
    ]
