from django.contrib import admin

from core.admin import ModelAdmin
from .note import NoteInline
from ..models import PointName, NamedPoint


class PointNameInline(admin.TabularInline):
    model = PointName


@admin.register(NamedPoint)
class NamedPointAdmin(ModelAdmin):
    list_display = ['name', 'latitude', 'longitude', 'altitude', 'flags']

    inlines = [PointNameInline, NoteInline]

    fieldsets = (
        ('Identity', {
            'fields': ('name',)
        }),
        ('Position', {
            'fields': (('latitude', 'longitude', 'altitude'), 'country')
        }),
        ('Source', {
            'fields': ('source',)
        }),
    )


class NamedPointInline(admin.TabularInline):
    model = NamedPoint


@admin.register(PointName)
class PointNameAdmin(admin.ModelAdmin):
    pass

