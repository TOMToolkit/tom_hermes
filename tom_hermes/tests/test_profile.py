from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

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
