from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy

from mountains.forms.namedpoint import NamedPointInlineForm
from mountains.models import Summit


class SummitAdminForm(NamedPointInlineForm):
    class Meta(NamedPointInlineForm.Meta):
        model = Summit


class SummitSearchInput(forms.TextInput):
    """
    A search box with suggestions, in place of a dropdown holding every summit there is.

    `static/js/summit-search.js` reads `data-search-url` and fills the list; without it the
    box is still a plain text field that accepts a name typed by hand, which is why the
    field below resolves names rather than relying on something hidden being set.
    """

    input_type = 'search'

    def __init__(self, attrs=None):
        super().__init__({'data-search-url': reverse_lazy('summit-search'),
                          'autocomplete': 'off',
                          'placeholder': 'type a few letters of the name',
                          **(attrs or {})})


class SummitSearchField(forms.ModelChoiceField):
    """
    A summit named in words rather than chosen from a list of two and a half thousand.

    What arrives is whatever is in the box, so it is resolved here: a primary key, because
    `?summit1=58` and the view's own initial values are pks and both should keep working;
    otherwise the name, exactly if that matches and as a substring if it does not. No summit
    in the database shares a name with another, so a name identifies one — and when it
    somehow does not, that is said plainly instead of one of them being picked.

    The 69 summits with no name at all cannot be reached by typing, only by pk. They have
    nothing to type.
    """

    widget = SummitSearchInput

    def __init__(self, **kwargs):
        super().__init__(queryset=Summit.objects.select_related('point', 'key_col__point'),
                         **kwargs)

    def prepare_value(self, value):
        """What goes into the box: the name, not the pk the view hands us as initial."""
        if isinstance(value, Summit):
            return value.point.name or value.pk
        if value not in self.empty_values and str(value).isdigit():
            summit = self.queryset.filter(pk=value).first()
            if summit:
                return summit.point.name or value
        return value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        text = str(value).strip()
        if text.isdigit() and (summit := self.queryset.filter(pk=text).first()):
            return summit
        # Exactly, before loosely: `Kriv` is a whole name and also the start of two others,
        # and a shorter name must not be unreachable by typing all of it.
        matches = (list(self.queryset.filter(point__name__iexact=text)[:2])
                   or list(self.queryset.filter(point__name__icontains=text)[:2]))
        if not matches:
            raise ValidationError('No summit is called “%(name)s”.',
                                  code='invalid_choice', params={'name': text})
        if len(matches) > 1:
            raise ValidationError('More than one summit matches “%(name)s” — '
                                  'type more of the name.',
                                  code='ambiguous', params={'name': text})
        return matches[0]


class CompareForm(forms.Form):
    summit1 = SummitSearchField(label='First summit')
    summit2 = SummitSearchField(label='Second summit')
