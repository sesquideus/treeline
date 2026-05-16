from django.contrib import admin

from ..models import Confluence


@admin.register(Confluence)
class ConfluenceAdmin(admin.ModelAdmin):
    list_display = ['point', 'point__location', 'point__altitude']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        },),
    )
