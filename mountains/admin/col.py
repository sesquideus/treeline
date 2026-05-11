from django.contrib import admin

from core.admin import ModelAdmin
from ..models import Col


@admin.register(Col)
class ColAdmin(ModelAdmin):
    list_display = ['point',
                    'point__latitude', 'point__longitude', 'point__altitude',
                    'confluence_link']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        }),
        ('Confluence', {
            'fields': (
                'confluence',
            )
        }),
    )

    def confluence_link(self, obj):
        return self.related_link(obj.confluence)