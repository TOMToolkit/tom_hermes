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
                ('user', models.OneToOneField(
                    on_delete=models.deletion.CASCADE, to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                # inherits from an abstract mixin, so Django emits ``abstract: False``.
                'abstract': False,
            },
        ),
    ]
