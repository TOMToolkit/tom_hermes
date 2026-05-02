"""
Tests for the per-user ``HermesProfile`` plumbing.

Keeps the end-to-end encrypted-field read/write out of scope — that path
requires the Django session cipher set up by the login signal, which is
complicated to stand up in a test. Instead we test the model contract,
the view's auth requirement, and the form's blank-means-keep behavior
with ``set_encrypted_field`` mocked.
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tom_hermes.forms import HermesProfileForm
from tom_hermes.models import HermesProfile


class HermesProfileModelTests(TestCase):
    def test_one_to_one_with_user(self):
        # HermesProfile inherits ``user`` from EncryptableModelMixin as a
        # OneToOneField; creating a second profile for the same user
        # must fail.
        user = User.objects.create_user(username='u1', password='pw')
        HermesProfile.objects.create(user=user)
        with self.assertRaises(Exception):  # IntegrityError in sqlite
            HermesProfile.objects.create(user=user)

    def test_str_contains_username(self):
        # The __str__ representation appears in admin and on error pages.
        user = User.objects.create_user(username='alice', password='pw')
        profile = HermesProfile.objects.create(user=user)
        self.assertIn('alice', str(profile))


class HermesProfileViewTests(TestCase):
    """The profile-edit view requires login and renders the bootstrap form."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='supersecret')
        self.client = Client()
        self.profile = HermesProfile.objects.create(user=self.user)
        # The URL's ``<pk>`` is the HermesProfile pk, not the User pk
        # (the view is an UpdateView of HermesProfile).
        self.url = reverse('tom_hermes:hermes-profile-update',
                           args=[self.profile.pk])

    def test_anonymous_is_redirected(self):
        # LoginRequiredMixin → 302 to the login page.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_authenticated_user_gets_form(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # The update form has a hop_username field rendered as an <input>.
        self.assertContains(response, 'name="hop_username"')


class HermesProfileFormSaveTests(TestCase):
    """Blank submissions on encrypted fields must leave the stored value alone."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pw')
        self.profile = HermesProfile.objects.create(user=self.user)

    def test_blank_encrypted_field_does_not_call_set_encrypted_field(self):
        # Submit with hop_username filled in but API key / password blank.
        # set_encrypted_field is mocked so no session cipher is needed.
        form = HermesProfileForm(
            data={'hop_username': 'alice', 'default_topics': '[]',
                  'hermes_api_key': '', 'hop_password': ''},
            instance=self.profile,
            user=self.user,
        )
        self.assertTrue(form.is_valid(), msg=form.errors)
        with patch('tom_hermes.forms.set_encrypted_field') as set_mock:
            form.save()
        set_mock.assert_not_called()
        # The plain field did get saved.
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.hop_username, 'alice')

    def test_non_blank_encrypted_field_calls_set_encrypted_field(self):
        form = HermesProfileForm(
            data={'hop_username': 'alice', 'default_topics': '[]',
                  'hermes_api_key': 'new-key', 'hop_password': ''},
            instance=self.profile,
            user=self.user,
        )
        self.assertTrue(form.is_valid(), msg=form.errors)
        with patch('tom_hermes.forms.set_encrypted_field', return_value=True) as set_mock:
            form.save()
        # Only hermes_api_key is set; hop_password was blank and should be skipped.
        self.assertEqual(set_mock.call_count, 1)
        call_args = set_mock.call_args.args
        # Args: (user, instance, field_name, value)
        self.assertEqual(call_args[2], 'hermes_api_key')
        self.assertEqual(call_args[3], 'new-key')
