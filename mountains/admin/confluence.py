from django.contrib import admin

from cairn.admin import ModelAdmin

from ..models import Confluence


@admin.register(Confluence)
class ConfluenceAdmin(ModelAdmin):
    list_display = ['point', 'point__location', 'point__altitude:.1f']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        },),
    )
