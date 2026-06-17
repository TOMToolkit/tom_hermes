"""
Tests for the per-user ``HermesProfile`` plumbing.

Covers the model contract, the view's auth requirement, and the form's
blank-means-keep behaviour. With the new ``EncryptedProperty`` descriptor
(cipher derived from ``settings.SECRET_KEY`` rather than per-user material)
round-trip read/write through the descriptor is exercised directly.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tom_hermes.forms import HermesProfileForm
from tom_hermes.models import HermesProfile


class HermesProfileModelTests(TestCase):
    def test_one_to_one_with_user(self):
        # HermesProfile.user is a OneToOneField; creating a second profile
        # for the same user must fail.
        user = User.objects.create_user(username='u1', password='pw')
        HermesProfile.objects.create(user=user)
        with self.assertRaises(Exception):  # IntegrityError in sqlite
            HermesProfile.objects.create(user=user)

    def test_str_contains_username(self):
        # The __str__ representation appears in admin and on error pages.
        user = User.objects.create_user(username='alice', password='pw')
        profile = HermesProfile.objects.create(user=user)
        self.assertIn('alice', str(profile))

    def test_round_trip_encrypted_value(self):
        # Assigning to the EncryptedProperty encrypts on write; reading
        # decrypts; the value survives a save / refresh_from_db round-trip.
        user = User.objects.create_user(username='alice', password='pw')
        profile = HermesProfile.objects.create(user=user)
        profile.hermes_api_key = 'sekret-key-value'
        profile.save()
        profile.refresh_from_db()
        self.assertEqual(profile.hermes_api_key, 'sekret-key-value')


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
        # The update form has the hermes_api_key field rendered as a password input.
        self.assertContains(response, 'name="hermes_api_key"')


class HermesProfileFormSaveTests(TestCase):
    """Blank submissions on encrypted fields must leave the stored value alone."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pw')
        self.profile = HermesProfile.objects.create(user=self.user)

    def test_blank_encrypted_field_does_not_change_stored_value(self):
        # Pre-seed a value so we can confirm a blank submission preserves it.
        self.profile.hermes_api_key = 'existing-key'
        self.profile.save()

        form = HermesProfileForm(
            data={'hermes_api_key': ''},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid(), msg=form.errors)
        form.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.hermes_api_key, 'existing-key')

    def test_non_blank_encrypted_field_writes_through(self):
        form = HermesProfileForm(
            data={'hermes_api_key': 'new-key'},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid(), msg=form.errors)
        form.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.hermes_api_key, 'new-key')
