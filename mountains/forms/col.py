from mountains.forms.namedpoint import NamedPointInlineForm
from mountains.models import Col


class ColAdminForm(NamedPointInlineForm):
    class Meta(NamedPointInlineForm.Meta):
        model = Col