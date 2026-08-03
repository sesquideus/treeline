from django import forms
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
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
    """
    A latitude and a longitude input that fill both halves from one pasted coordinate pair.

    `static/js/point-widget.js` splits the "48.123456, 19.12345" pair Google Maps copies —
    and the hemisphere form `NamedPointAdmin.location_display` prints — across the two inputs
    on Ctrl+V, leaving anything else to paste normally. The 📋 button does the same on click
    where the browser grants clipboard reads.
    """
    template_name = 'core/widgets/point.html'

    class Media:
        css = {'all': ('css/point-widget.css',)}
        js = ('js/point-widget.js',)

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


class RangeWidget(forms.MultiWidget):
    """A "min" and a "max" number input on one line, separated by a dash."""
    template_name = 'core/widgets/range.html'

    def __init__(self):
        widgets = [
            forms.NumberInput(attrs={'step': 'any', 'placeholder': 'min'}),
            forms.NumberInput(attrs={'step': 'any', 'placeholder': 'max'}),
        ]
        super().__init__(widgets=widgets)

    def decompress(self, value):
        if value:
            return list(value)
        return [None, None]


class RangeFormField(forms.MultiValueField):
    """
    An inclusive numeric range, cleaned to a `(low, high)` tuple where either end may be None.

    Both ends are optional — a one-sided bound is ordinary input, not an "incomplete value"
    error, so the subfields are explicitly `required=False` (`require_all_fields=False` alone
    does not relax them). An empty range cleans to `None` rather than `(None, None)`, so a
    view can skip it with a walrus the way it does for the other filters.
    """
    def __init__(self, *, min_value=None, **kwargs):
        kwargs.setdefault('required', False)
        fields = (
            forms.FloatField(required=False, min_value=min_value),
            forms.FloatField(required=False, min_value=min_value),
        )
        super().__init__(fields=fields, widget=RangeWidget(), require_all_fields=False, **kwargs)

    def compress(self, data_list):
        if not data_list:
            return None
        low, high = data_list
        if low is None and high is None:
            return None
        if low is not None and high is not None and low > high:
            raise ValidationError("The minimum cannot be greater than the maximum.")
        return low, high