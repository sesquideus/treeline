from django import forms
from django.contrib.gis.geos import Point
from django.utils.html import format_html

from core.templatetags.countries import flag


class CountryMultipleChoiceField(forms.ModelMultipleChoiceField):
    """
    Country checkboxes labelled with the flag alongside the country's own name.

    The label shows `name` — the endonym, as `Country.__str__` does — with `english_name`
    kept as the `title` for the ones where the two differ (Česko / Czechia). A code with no
    flag (anything other than two letters) falls back to showing the code.
    """
    def label_from_instance(self, country):
        emblem = flag(country.code) or format_html(
            '<span class="flag-missing">{}</span>', country.code.upper()
        )
        return format_html(
            '<span class="country-choice" title="{}">{}<span class="country-name">{}</span></span>',
            country.english_name or country.name,
            emblem,
            country.name,
        )


class PointWidget(forms.MultiWidget):
    def __init__(self):
        widgets = [
            forms.NumberInput(attrs={'step': 'any', 'placeholder': 'Latitude',}),
            forms.NumberInput(attrs={'step': 'any', 'placeholder': 'Longitude'}),
        ]
        super().__init__(widgets=widgets)

    def decompress(self, value):
        if value:
            return [value.y, value.x]  # lat, lon
        return [None, None]


class PointFormField(forms.MultiValueField):
    def __init__(self, *args, **kwargs):
        fields = (
            forms.FloatField(),
            forms.FloatField(),
        )
        super().__init__(fields=fields, widget=PointWidget(), require_all_fields=False, *args, **kwargs)

    def compress(self, data_list):
        if data_list and data_list[0] is not None and data_list[1] is not None:
            return Point(data_list[1], data_list[0], srid=4326)  # Point(lon, lat)
        return None