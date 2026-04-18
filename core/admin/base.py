from django.contrib import admin
from django.utils.safestring import mark_safe

from core.models import AdminModel


class LinkableMixin:
    """
    Mixin for admins and inlines

    Adds the ability to link to a related admin object.
    """

    @staticmethod
    def related_link(obj: AdminModel,
                     *,
                     na: str = "&mdash;") -> str:
        """
        Create a hyperlink to the admin detail page of a related object,
        or a default if not available.
        """
        if obj is None:
            return mark_safe(na)
        else:
            return mark_safe('<a href="{url}">{title}</a>'.format(
                url=obj.admin_change_url(),
                title=str(obj),
            ))



class ModelAdmin(LinkableMixin, admin.ModelAdmin):
    """
    Enhanced base class for model admins in AMOS, especially derived from AdminModel.
    """


class ModelInline(LinkableMixin, admin.TabularInline):
    """
    Enhanced base class for tabular inlines.
    """
