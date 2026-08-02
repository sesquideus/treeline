from django import forms
from django.forms import CharField

from core.fields import CountryMultipleChoiceField
from core.models import Country


class FilterForm(forms.Form):
    countries = CountryMultipleChoiceField(
        queryset=Country.objects.all(),
        to_field_name='code',
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    name = CharField(label="Name contains", required=False, help_text="Name")