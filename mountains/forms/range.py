from django import forms
from django.core.exceptions import ValidationError

from mountains.models import Range

INDENT = ' ' * 4


def indented(range):
    """A range's name, pushed right by its depth, so a flat list reads as a tree."""
    return f'{INDENT * range.depth()}{range.name}'


class RangeChoiceField(forms.ModelChoiceField):
    """
    A range select whose options are indented by depth.

    This is why `parent` is a plain select and not an autocomplete: autocomplete options come
    from `AutocompleteJsonView`, which is told only which field is being filled and never
    which object is being edited — so it can neither restrict itself to one system nor show
    the shape of the tree. A few hundred options in a `<select>` that draws the hierarchy is
    the better trade at this size.
    """

    def label_from_instance(self, obj):
        return indented(obj)


class RangeAdminForm(forms.ModelForm):
    """
    Model validation is in `Range.clean()`; this form exists so the admin's parent dropdown
    is narrowed and ordered, and so the same messages arrive on the right field.
    """

    class Meta:
        model = Range
        fields = '__all__'


class AssignRangeForm(forms.Form):
    """The range chooser on the summit changelist's bulk-assign page."""

    range = RangeChoiceField(
        queryset=Range.objects.with_system().order_by('system__code', 'path'),
        label='Assign to range',
        help_text='Only the deepest range is stored; the ranges above it come from its path.',
    )
