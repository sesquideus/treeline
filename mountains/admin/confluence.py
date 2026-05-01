from django.contrib import admin

from ..models import Confluence


@admin.register(Confluence)
class ConfluenceAdmin(admin.ModelAdmin):
    list_display = ['point', 'point__latitude', 'point__longitude', 'point__altitude']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        },),
    )
