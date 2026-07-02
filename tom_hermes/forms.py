from __future__ import annotations

from crispy_forms.layout import Fieldset, Layout, Div
from django import forms

from tom_dataservices.forms import BaseQueryForm


class HermesForm(BaseQueryForm):
    """Query form for ``HermesDataService``.
    """

    target_name = forms.CharField(
        required=False,
        label='Target Name',
        help_text='Includes partial matches',
    )
    ra = forms.FloatField(required=False, label="RA (deg)")
    dec = forms.FloatField(required=False, label="Dec (deg)")
    radius = forms.FloatField(required=False, label="Search Radius (deg)")
    uuid = forms.CharField(required=False, label='Message UUID')

    def get_layout(self):
        """Return the crispy Layout for the advanced form.
        """
        return Layout(
            # Group cone search components
            Fieldset(
                'Cone Search',
                Div(
                    Div(
                        'ra',
                        'radius',
                        css_class='col',
                    ),
                    Div(
                        'dec',
                        css_class='col',
                    ),
                    css_class="row",
                )
            ),
            Fieldset('UUID', 'uuid'),
        )

    def simple_fields(self):
        """Return List of fields to be included in the simple form."""
        return ['target_name']
