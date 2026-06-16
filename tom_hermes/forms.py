"""
Forms for tom_hermes.

### What lives here

- ``HermesProfileForm`` — ModelForm for editing the per-user ``HermesProfile``
  credential entries. Handles the encrypted ``hermes_api_key`` with an
  extra ``CharField``, since ``EncryptedProperty`` descriptors are not
  plain model fields and ``ModelForm`` does not produce form fields for
  them automatically. Pattern copied from ``tom_eso.forms.ESOProfileForm``.
- ``HermesForm`` — the DataService query form rendered by
  ``HermesDataService``. Inherits from ``tom_dataservices.forms.BaseQueryForm``
  so the framework's save-query / run-query flow treats it as a normal
  DataService form.
"""
from __future__ import annotations

from crispy_forms.layout import Fieldset, Layout
from django import forms

from tom_dataservices.forms import BaseQueryForm


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
