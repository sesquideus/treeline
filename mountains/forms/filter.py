from django import forms

from core.models import Country

class CommaSeparatedWidget(forms.TextInput):
    def value_from_datadict(self, data, files, name):
        value = data.get(name)
        if not value:
            return []
        return [v.strip() for v in value.split(",") if v.strip()]

    def decompress(self, value):
        # only needed if you render this field with initial/bound data
        if value:
            return ",".join(str(v) for v in value)
        return ""


class CodeModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    widget = CommaSeparatedWidget

    def label_from_instance(self, obj):
        return obj.code


class FilterForm(forms.Form):
    countries = CodeModelMultipleChoiceField(
        queryset=Country.objects.all(),
        to_field_name='code',
    )