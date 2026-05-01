from django.contrib import admin

from core.admin import ModelAdmin
from .note import NoteInline
from ..models import PointName, NamedPoint, Note


class PointNameInline(admin.TabularInline):
    model = PointName
    extra = 3


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

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, Note) and not obj.pk:
                obj.author = request.user
            obj.save()
        formset.save_m2m()


class NamedPointInline(admin.TabularInline):
    model = NamedPoint


@admin.register(PointName)
class PointNameAdmin(admin.ModelAdmin):
    pass


