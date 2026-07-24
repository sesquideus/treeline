from django import forms
from django.forms import ModelMultipleChoiceField, CharField

from core.models import Country


class FilterForm(forms.Form):
    countries = ModelMultipleChoiceField(
        queryset=Country.objects.all(),
        to_field_name='code',
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    name = CharField(label="Name contains", required=False, help_text="Name")