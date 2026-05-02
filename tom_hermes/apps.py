"""
Django AppConfig for ``tom_hermes``.

### What this file does

Declares every integration point this app participates in. Each method on
``TomHermesConfig`` is the hook that tom_base / tom_common calls when it
walks ``INSTALLED_APPS`` building up the app's plug-in registries. The
actual class references are returned as dotted-path strings, and each
host (``tom_dataservices``, ``tom_common.sharing``, etc.) resolves them
lazily — so the classes referenced here do not need to be importable at
Django's app-ready phase.

### Who calls what

- ``data_services()``     — by ``tom_dataservices.dataservices.get_data_service_classes()``
- ``sharing_backends()``  — by ``tom_common.sharing.get_sharing_backends()``
- ``profile_details()``   — by ``tom_common.templatetags.user_extras.show_app_profiles``
- ``include_url_paths()`` — by ``tom_common.urls`` during URLconf assembly
"""
from django.apps import AppConfig
from django.urls import include, path


class TomHermesConfig(AppConfig):
    """Single AppConfig for the ``tom_hermes`` Django app.

    All HERMES-specific integration points live here. If you add a new
    HERMES integration (e.g., a new DataService class, a new kind of
    profile partial), add it to the corresponding list-returning method
    below rather than writing new glue elsewhere.
    """

    # ``default = True`` tells Django (3.2+) to use this AppConfig when
    # ``tom_hermes`` is referenced by its dotted package path in
    # INSTALLED_APPS. Explicit and forward-compatible; replaces the older
    # ``default_app_config`` attribute on the package's ``__init__.py``.
    default = True
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tom_hermes'

    def data_services(self):
        """Advertise ``HermesDataService`` to the tom_dataservices discovery.

        tom_dataservices calls this method on every installed AppConfig
        and aggregates the returned lists into its registry of DataService
        classes. The class is resolved lazily (dotted-path string), so
        ``tom_hermes.dataservices.hermes`` does not need to import cleanly
        at Django app-ready time.
        """
        return [{'class': f'{self.name}.dataservices.hermes.HermesDataService'}]

    def sharing_backends(self):
        """Advertise ``HermesSharingBackend`` to the tom_common.sharing discovery.

        ``tom_common.sharing.get_sharing_backends()`` calls this method on
        every installed AppConfig and aggregates the returned lists into
        its registry of SharingBackend classes. The class is resolved
        lazily (dotted-path string).
        """
        return [{'class': f'{self.name}.sharing.HermesSharingBackend'}]

    def profile_details(self):
        """Register the user-profile page fragment showing HERMES credential status.

        tom_common iterates installed AppConfigs at user-profile render
        time and calls this method; each returned dict contributes one
        bootstrap card to the profile page. ``partial`` is the template
        path; ``context`` is the dotted path to a templatetag callable
        that returns a context dict for the partial.
        """
        return [{
            'partial': 'tom_hermes/partials/hermes_user_profile.html',
            'context': f'{self.name}.templatetags.hermes_extras.hermes_profile_data',
        }]

    def include_url_paths(self):
        """URL patterns this app contributes to the project URLconf.

        ``tom_common.urls`` iterates installed AppConfigs and extends the
        URLconf with whatever each returns. The list returned here is
        concatenated into the project-level urlpatterns, so it must be a
        list of ``django.urls.path()`` / ``re_path()`` / ``include()``
        entries. All our URLs live under ``/hermes/``.
        """
        return [path('hermes/', include(f'{self.name}.urls'))]
