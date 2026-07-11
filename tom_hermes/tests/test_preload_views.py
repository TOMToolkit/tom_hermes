from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from tom_dataproducts.models import PhotometryReducedDatum, SpectroscopyReducedDatum
from tom_targets.tests.factories import SiderealTargetFactory
from tom_hermes.publisher import BuildHermesMessage, create_hermes_message


class TargetHermesPreloadViewTests(TestCase):
    """POST ``/hermes/targets/<pk>/preload/`` — single-target preload."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pw')
        self.client.force_login(self.user)
        self.target = SiderealTargetFactory.create()

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'HERMES_BASE_URL': 'https://h.example/'})
    def test_post_redirects_to_hermes_with_preload_key(self):
        # ``preload_to_hermes`` is mocked at the view-module seam so we don't
        # hit HERMES. It returns the key the view should embed in the redirect.
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='ABC123') as mock_preload:
            response = self.client.post(url, data={
                'hermes_topic': 'tomtoolkit.test',
                'message_title': 'Title',
                'message': 'Body',
                'submitter': self.user.username,
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://h.example/submit-message?id=ABC123')
        message_info, datums, targets = mock_preload.call_args.args
        self.assertEqual(message_info.title, 'Title')
        self.assertEqual(message_info.topic, 'tomtoolkit.test')
        self.assertEqual(message_info.authors, '')
        self.assertEqual(targets, [self.target])

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'HERMES_BASE_URL': 'https://h.example/'})
    def test_default_title_when_message_title_blank(self):
        # When the form omits message_title, the view substitutes a default.
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='K') as mock_preload:
            self.client.post(url, data={'hermes_topic': 'tomtoolkit.test'})
        message_info = mock_preload.call_args.args[0]
        self.assertEqual(message_info.title, f'Updated data for {self.target.name}')

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'HERMES_BASE_URL': 'https://h.example/'})
    def test_all_target_reduced_data_is_included(self):
        # Create Reduced Datums
        rd1 = PhotometryReducedDatum.objects.create(target=self.target, brightness=18.0, bandpass='r')
        rd2 = SpectroscopyReducedDatum.objects.create(target=self.target, flux=[1, 2], wavelength=[3, 4])
        other_target = SiderealTargetFactory.create()
        PhotometryReducedDatum.objects.create(target=other_target, brightness=19.0, bandpass='g')
        # Build Message
        message_info = BuildHermesMessage()
        message = create_hermes_message(message_info, targets=[self.target])
        data = message['data']
        # Make sure target data made it into message
        self.assertEqual({d['bandpass'] for d in data['photometry']}, {rd1.bandpass})
        for d in data['spectroscopy']:
            self.assertEqual(d['flux'], rd2.flux)

    @override_settings(HERMES_CONFIGURATION={})
    def test_no_credentials_returns_400(self):
        # With no HermesProfile and no HERMES_CONFIGURATION token, the
        # credential gate returns 400 instead of trying to call HERMES.
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        response = self.client.post(url, data={'hermes_topic': 'tomtoolkit.test'})
        self.assertEqual(response.status_code, 400)

    def test_anonymous_user_is_blocked(self):
        # LoginRequiredMixin gates the view: an unauthenticated POST is
        # redirected to login and never reaches the HERMES preload.
        self.client.logout()
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        response = self.client.post(url, data={'hermes_topic': 'tomtoolkit.test'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('submit-message', response['Location'])
