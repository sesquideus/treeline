from django import forms
from django.contrib import admin

from core.models import Country
from mountains.models import Col, Source, NamedPoint


class NamedPointInlineForm(forms.ModelForm):
    name = forms.CharField(max_length=64)
    latitude = forms.FloatField()
    longitude = forms.FloatField()
    altitude = forms.FloatField()
    country = forms.ModelMultipleChoiceField(
        queryset=Country.objects.all(),
        required=False,
        widget=admin.widgets.FilteredSelectMultiple('countries', is_stacked=False),
    )
    source = forms.ModelChoiceField(
        queryset=Source.objects.all(),
        required=False,
    )

    class Meta:
        model = Col
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.point_id:
            p = self.instance.point
            self.fields['name'].initial = p.name
            self.fields['latitude'].initial = p.latitude
            self.fields['longitude'].initial = p.longitude
            self.fields['altitude'].initial = p.altitude
            self.fields['country'].initial = p.country.none()
            self.fields['source'].initial = p.source_id

    def save(self, commit=True):
        obj = super().save(commit=False)
        if obj.point_id:
            point = obj.point
        else:
            point = NamedPoint()

        point.name = self.cleaned_data['name']
        point.latitude = self.cleaned_data['latitude']
        point.longitude = self.cleaned_data['longitude']
        point.altitude = self.cleaned_data['altitude']
        point.source = self.cleaned_data['source']

        if commit:
            point.save()
            # M2M must be set after save
            point.country.set(self.cleaned_data['country'])
            obj.point = point
            obj.save()
        return obj