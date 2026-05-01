from mountains.forms.namedpoint import NamedPointInlineForm
from mountains.models import Summit


class SummitAdminForm(NamedPointInlineForm):
    class Meta(NamedPointInlineForm.Meta):
        model = Summit


