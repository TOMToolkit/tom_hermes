"""
Tests for the HERMES preload views.

Both views POST a draft message to HERMES via ``preload_to_hermes`` and
redirect the user to the HERMES UI to review and submit. We mock
``preload_to_hermes`` at the view-module seam so we never hit the
network — the assertions are about (a) the credential gate, (b) the
arguments handed to ``preload_to_hermes``, and (c) the redirect URL.

Credentials resolve through ``resolve_hermes_credentials``: a per-user
``HermesProfile`` first, then ``settings.HERMES_CONFIGURATION``. The
``override_settings`` blocks below exercise the settings fallback; the
empty-config cases exercise the gate (and stay deterministic regardless
of whether HERMES_API_TOKEN is set in the environment).
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from tom_dataproducts.models import ReducedDatum
from tom_targets.tests.factories import SiderealTargetFactory


class TargetHermesPreloadViewTests(TestCase):
    """POST ``/hermes/targets/<pk>/preload/`` — single-target preload."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pw')
        self.client.force_login(self.user)
        self.target = SiderealTargetFactory.create()

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'BASE_URL': 'https://h.example/'})
    def test_post_redirects_to_hermes_with_preload_key(self):
        # ``preload_to_hermes`` is mocked at the view-module seam so we don't
        # hit HERMES. It returns the key the view should embed in the redirect.
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='ABC123') as mock_preload:
            response = self.client.post(url, data={
                'share_destination': 'tomtoolkit.test',
                'share_title': 'Title',
                'share_message': 'Body',
                'submitter': self.user.username,
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://h.example/submit-message?id=ABC123')
        # Verify preload_to_hermes received the right Targets list and the
        # form-derived envelope. authors is empty: the view no longer reads a
        # DEFAULT_AUTHORS (not part of HERMES_CONFIGURATION yet). Datums are
        # empty because this target has no ReducedDatum rows.
        message_info, datums, targets = mock_preload.call_args.args
        self.assertEqual(message_info.title, 'Title')
        self.assertEqual(message_info.topic, 'tomtoolkit.test')
        self.assertEqual(message_info.authors, '')
        self.assertEqual(targets, [self.target])
        self.assertEqual(list(datums), [])

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'BASE_URL': 'https://h.example/'})
    def test_default_title_when_share_title_blank(self):
        # When the form omits share_title, the view substitutes a default.
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='K') as mock_preload:
            self.client.post(url, data={'share_destination': 'tomtoolkit.test'})
        message_info = mock_preload.call_args.args[0]
        self.assertEqual(message_info.title, f'Updated data for {self.target.name}')

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'BASE_URL': 'https://h.example/'})
    def test_all_target_reduced_data_is_included(self):
        # The detail-page button has no per-datum picker, so the view shares
        # *every* ReducedDatum on the target (photometry and spectroscopy
        # alike) and nothing belonging to other targets.
        rd1 = ReducedDatum.objects.create(target=self.target, data_type='photometry',
                                          value={'magnitude': 18.0, 'filter': 'r'})
        rd2 = ReducedDatum.objects.create(target=self.target, data_type='spectroscopy',
                                          value={'flux': [1, 2], 'wavelength': [3, 4]})
        other_target = SiderealTargetFactory.create()
        ReducedDatum.objects.create(target=other_target, data_type='photometry',
                                    value={'magnitude': 19.0, 'filter': 'g'})
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='K') as mock_preload:
            self.client.post(url, data={'share_destination': 'tomtoolkit.test'})
        datums = mock_preload.call_args.args[1]
        self.assertEqual({d.pk for d in datums}, {rd1.pk, rd2.pk})

    @override_settings(HERMES_CONFIGURATION={})
    def test_no_credentials_returns_400(self):
        # With no HermesProfile and no HERMES_CONFIGURATION token, the
        # credential gate returns 400 instead of trying to call HERMES.
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        response = self.client.post(url, data={'share_destination': 'tomtoolkit.test'})
        self.assertEqual(response.status_code, 400)

    def test_anonymous_user_is_blocked(self):
        # LoginRequiredMixin gates the view: an unauthenticated POST is
        # redirected to login and never reaches the HERMES preload.
        self.client.logout()
        url = reverse('tom_hermes:target-preload', args=[self.target.pk])
        response = self.client.post(url, data={'share_destination': 'tomtoolkit.test'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('submit-message', response['Location'])


class TargetGroupingHermesPreloadViewTests(TestCase):
    """POST ``/hermes/targetgrouping/<pk>/preload/`` — TargetList preload."""

    def setUp(self):
        from tom_targets.models import TargetList
        self.user = User.objects.create_user(username='alice', password='pw')
        self.client.force_login(self.user)
        self.t1 = SiderealTargetFactory.create()
        self.t2 = SiderealTargetFactory.create()
        self.targetlist = TargetList.objects.create(name='Group A')
        self.targetlist.targets.add(self.t1, self.t2)

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'BASE_URL': 'https://h.example/'})
    def test_post_redirects_to_hermes_target_list(self):
        url = reverse('tom_hermes:target-grouping-preload', args=[self.targetlist.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='XYZ') as mock_preload:
            response = self.client.post(url, data={
                'share_destination': 'hermes:tomtoolkit.test',
                'share_title': '',
                'selected-target': [str(self.t1.pk), str(self.t2.pk)],
                # dataSwitch off — target-only announcement (no photometry).
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://h.example/submit-message?id=XYZ')
        message_info, datums, targets = mock_preload.call_args.args
        self.assertEqual(message_info.title, f'Updated targets for group {self.targetlist.name}.')
        self.assertEqual({t.pk for t in targets}, {self.t1.pk, self.t2.pk})
        # dataSwitch off → empty datum queryset.
        self.assertEqual(list(datums), [])

    @override_settings(HERMES_CONFIGURATION={'HERMES_API_TOKEN': 'k', 'BASE_URL': 'https://h.example/'})
    def test_dataswitch_on_includes_photometry(self):
        # With ``dataSwitch=on`` and selected targets, the view pulls every
        # photometry ReducedDatum on those targets and hands them to preload.
        ReducedDatum.objects.create(target=self.t1, data_type='photometry',
                                    value={'magnitude': 18.0, 'filter': 'r'})
        # Spectroscopy on the same target must NOT be included — preload only
        # publishes photometry from this view.
        ReducedDatum.objects.create(target=self.t1, data_type='spectroscopy',
                                    value={'flux': [1, 2], 'wavelength': [3, 4]})
        url = reverse('tom_hermes:target-grouping-preload', args=[self.targetlist.pk])
        with patch('tom_hermes.views.preload_to_hermes', return_value='K') as mock_preload:
            self.client.post(url, data={
                'share_destination': 'hermes:t',
                'selected-target': [str(self.t1.pk)],
                'dataSwitch': 'on',
            })
        datums = mock_preload.call_args.args[1]
        types = {d.data_type for d in datums}
        self.assertEqual(types, {'photometry'})

    @override_settings(HERMES_CONFIGURATION={})
    def test_no_credentials_returns_400(self):
        url = reverse('tom_hermes:target-grouping-preload', args=[self.targetlist.pk])
        response = self.client.post(url, data={'share_destination': 'hermes:t'})
        self.assertEqual(response.status_code, 400)
