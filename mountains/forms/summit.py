from django import forms

from mountains.forms.namedpoint import NamedPointInlineForm
from mountains.models import Summit


class SummitAdminForm(NamedPointInlineForm):
    class Meta(NamedPointInlineForm.Meta):
        model = Summit


class CompareForm(forms.Form):
    summit1 = forms.ModelChoiceField(Summit.objects.all(), label='First summit')
    summit2 = forms.ModelChoiceField(Summit.objects.all(), label='Second summit')
