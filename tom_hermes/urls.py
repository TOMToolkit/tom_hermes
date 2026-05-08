"""
URL patterns for tom_hermes.

This module is included into the project URLconf by
``TomHermesConfig.include_url_paths`` (mounted under ``/hermes/``). Every
URL pattern added here is namespaced under ``tom_hermes:`` so templates
can refer to e.g. ``{% url 'tom_hermes:hermes-profile-update' pk=... %}``.

### Naming convention

URL pattern names use dashes (``hermes-profile-update``); Python callables
use underscores (``HermesProfileUpdateView``). This matches the convention
in tom_eso and elsewhere in tomtoolkit.
"""
from django.urls import path

from tom_hermes.views import (
    HermesProfileUpdateView,
    TargetGroupingHermesPreloadView,
    TargetHermesPreloadView,
)

app_name = 'tom_hermes'

urlpatterns = [
    # Edit form for the logged-in user's HermesProfile. Reached from the
    # "Edit" icon on the HERMES card on the user-profile page.
    path(
        'users/<int:pk>/update/',
        HermesProfileUpdateView.as_view(),
        name='hermes-profile-update',
    ),
    # "Open in Hermes 🗗" button on a single Target's data-share dialog.
    # Stashes a draft on HERMES and redirects the user to the HERMES UI.
    path(
        'targets/<int:pk>/preload/',
        TargetHermesPreloadView.as_view(),
        name='target-preload',
    ),
    # Same but for a TargetList (group of Targets).
    path(
        'targetgrouping/<int:pk>/preload/',
        TargetGroupingHermesPreloadView.as_view(),
        name='target-grouping-preload',
    ),
]
