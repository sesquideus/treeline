from django import forms
from django.contrib.gis.geos import Point

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