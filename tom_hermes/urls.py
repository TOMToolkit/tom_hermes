from django.urls import path

from tom_hermes.views import (
    HermesProfileUpdateView,
    TargetHermesPreloadView,
)

app_name = 'tom_hermes'

urlpatterns = [
    path('users/<int:pk>/update/', HermesProfileUpdateView.as_view(), name='hermes-profile-update'),
    path('targets/<int:pk>/preload/', TargetHermesPreloadView.as_view(), name='target-preload'),
]
