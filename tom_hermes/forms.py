"""
Forms for tom_hermes.

### What lives here

- ``HermesProfileForm`` — ModelForm for editing the per-user ``HermesProfile``
  credential entries. Handles the two encrypted fields (``hermes_api_key``,
  ``hop_password``) with extra ``CharField``s, since ``EncryptedProperty``
  descriptors are not plain model fields and ``ModelForm`` does not produce
  form fields for them automatically. Pattern copied from
  ``tom_eso.forms.ESOProfileForm``.
- ``HermesForm`` — the DataService query form rendered by
  ``HermesDataService``. Inherits from ``tom_dataservices.forms.BaseQueryForm``
  so the framework's save-query / run-query flow treats it as a normal
  DataService form.
"""
from __future__ import annotations

from crispy_forms.layout import Fieldset, Layout
from django import forms

from tom_common.session_utils import get_encrypted_field, set_encrypted_field
from tom_dataservices.forms import BaseQueryForm

from tom_hermes.models import HermesProfile


class HermesProfileForm(forms.ModelForm):
    """Edit-form for a ``HermesProfile``.

    ``ModelForm`` handles the plaintext fields (``hop_username``,
    ``default_topics``) automatically. The two encrypted secrets
    (``hermes_api_key``, ``hop_password``) are not model fields in the
    ModelForm sense — they are ``EncryptedProperty`` descriptors backed
    by BinaryFields — so we declare a ``CharField`` for each and wire
    the read/write through ``get_encrypted_field`` /
    ``set_encrypted_field`` from ``tom_common.session_utils``.

    The password-style fields are left blank on re-render and only update
    the underlying value when the user submits a non-empty string, so a
    round-trip through the form does not require the user to re-type
    their credentials.
    """

    # Encrypted field #1: LCO HERMES submit API key. Rendered as a password
    # input so the value is not shoulder-surfable while typing.
    hermes_api_key = forms.CharField(
        required=False,
        label='HERMES API Key',
        widget=forms.PasswordInput(render_value=False),
        help_text='Your HERMES API key (found on your HERMES profile page). Leave blank to keep unchanged.',
    )

    # Encrypted field #2: Hopskotch SCRAM password. The username is a plain
    # CharField on the model, so it is rendered by ModelForm automatically.
    hop_password = forms.CharField(
        required=False,
        label='Hopskotch Password',
        widget=forms.PasswordInput(render_value=False),
        help_text='Your Hopskotch SCRAM password. Leave blank to keep unchanged.',
    )

    class Meta:
        model = HermesProfile
        # hermes_api_key and hop_password are declared above, not model fields;
        # ModelForm happily treats them as form fields but does not try to
        # read/write them through the model descriptor (which would require
        # the session cipher).
        fields = ['hop_username', 'default_topics', 'hermes_api_key', 'hop_password']

    def __init__(self, *args, **kwargs):
        # The view passes in the logged-in User so we can read the session
        # cipher; without it, we cannot decrypt existing secrets to populate
        # the form's initial values.
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        # Populate initial values of the encrypted fields from the Profile.
        # If the Profile has not been saved yet (no pk), there is nothing
        # to decrypt.
        if self.instance and self.instance.pk and self.user is not None:
            self.fields['hermes_api_key'].initial = get_encrypted_field(
                self.user, self.instance, 'hermes_api_key',
            )
            self.fields['hop_password'].initial = get_encrypted_field(
                self.user, self.instance, 'hop_password',
            )

    def save(self, commit=True):
        """Save the ``HermesProfile``, writing encrypted fields only if the user provided new values.

        Extends ``ModelForm.save`` to handle the two EncryptedProperty
        descriptors separately. We call ``super().save(commit=False)`` to
        get an unsaved instance, then call ``set_encrypted_field`` for
        each non-empty encrypted value. Finally we call ``instance.save()``
        ourselves so the BinaryFields (written by ``set_encrypted_field``)
        land on disk together with the plain-field changes.
        """
        instance = super().save(commit=False)
        user = instance.user or self.user

        # Only overwrite an encrypted field when the user provided a
        # non-empty value; a blank string means "keep whatever is already saved."
        for encrypted_field_name in ('hermes_api_key', 'hop_password'):
            new_value = self.cleaned_data.get(encrypted_field_name)
            if new_value:
                success = set_encrypted_field(user, instance, encrypted_field_name, new_value)
                if not success:
                    # set_encrypted_field returns False when the session
                    # cipher is missing — surface it as a non-field form
                    # error so the user sees a useful message rather than
                    # silently losing the edit.
                    self.add_error(
                        None,
                        f'Could not save encrypted {encrypted_field_name} due to a server error. '
                        'Please ensure you are logged in correctly.',
                    )

        if commit and not self.errors:
            instance.save()
        return instance


class HermesForm(BaseQueryForm):
    """Query form for ``HermesDataService``.

    Rendered by ``tom_dataservices.views.DataServiceQueryCreateView`` when
    the user picks "HERMES Messaging Service" in the Data Services nav.
    The framework uses ``BaseQueryForm.save()`` to persist a
    ``DataServiceQuery`` row, and this form's ``cleaned_data`` is fed to
    ``HermesDataService.build_query_parameters``.
    """

    # Free-text search against HERMES message contents. Matches the
    # ``search`` query parameter on the /query endpoint.
    search = forms.CharField(
        required=False,
        label='Free-text search',
        help_text='Search HERMES message text (title, body).',
    )

    # Topic multi-select. Choices are populated at ``__init__`` time by
    # calling ``HermesDataService.get_topic_choices(user=self.user)`` (which
    # hits HERMES once per user per hour and caches). Leaving this empty
    # means "any topic". Rendered as checkboxes because a list of topic
    # strings is easier to scan than a shift-click multi-select box.
    topics = forms.MultipleChoiceField(
        required=False,
        label='Topics',
        help_text='Limit results to one or more topics. Leave empty for any topic.',
        widget=forms.CheckboxSelectMultiple,
    )

    # Date filters. HTML date input widgets for a consistent UI; HERMES
    # accepts ISO date strings.
    published_after = forms.CharField(
        required=False, label='Published after',
        widget=forms.TextInput(attrs={'type': 'date'}),
    )
    published_before = forms.CharField(
        required=False, label='Published before',
        widget=forms.TextInput(attrs={'type': 'date'}),
    )

    def __init__(self, *args, **kwargs):
        # BaseQueryForm.__init__ pops ``user`` off kwargs and stashes it on
        # ``self.user``. The view (DataServiceQueryCreateView /
        # DataServiceQueryUpdateView) passes request.user via get_form_kwargs.
        super().__init__(*args, **kwargs)
        # Populate the topic multi-select choices from HERMES using the
        # user's own credentials so the form shows the topics *that user*
        # is allowed to read. Import lazily to avoid a circular import at
        # module load (dataservices.hermes imports tom_hermes.forms).
        from tom_hermes.dataservices.hermes import HermesDataService
        self.fields['topics'].choices = HermesDataService.get_topic_choices(user=self.user)

    def get_layout(self):
        """Return the crispy Layout for the form.

        Groups fields into "Search text" / "Topics" / "Time" fieldsets so
        the simple and advanced partials can share the underlying form
        class (the partials pick which fields to render).
        """
        return Layout(
            Fieldset('Search text', 'search'),
            Fieldset('Topics', 'topics'),
            Fieldset('Time', 'published_after', 'published_before'),
        )
