from django import forms
from django.forms import CharField

from core.fields import CountryMultipleChoiceField, RangeFormField
from core.models import Country


class FilterForm(forms.Form):
    countries = CountryMultipleChoiceField(
        queryset=Country.objects.all(),
        to_field_name='code',
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    name = CharField(label="Name contains", required=False, help_text="Name")


class SummitFilterForm(FilterForm):
    """
    The mountain list's filters: the shared ones plus the three numeric ranges.

    Kept apart from `FilterForm` because the col list shares that form and applies none of
    these — a rendered filter that the view ignores is worse than a missing one. Units match
    the columns they filter, so isolation is kilometres (as `distance` prints it) while the
    two altitude-derived metrics are metres.
    """
    altitude = RangeFormField(label="Altitude (m)")
    prominence = RangeFormField(label="Prominence (m)", min_value=0)
    isolation = RangeFormField(label="Isolation (km)", min_value=0)
