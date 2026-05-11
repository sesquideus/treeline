from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from core.admin import ModelAdmin
from .note import NoteInline
from ..models import PointName, NamedPoint, Note


class PointNameInline(admin.TabularInline):
    model = PointName
    extra = 3


@admin.register(NamedPoint)
class NamedPointAdmin(GISModelAdmin):
    gis_widget_kwargs = {
        "attrs": {
            "default_lat": 48.5,
            "default_lon": 19.5,
            "default_zoom": 7,
        },
    }
    list_display = ['name', 'location_display', 'latitude', 'longitude', 'altitude', 'flags']

    inlines = [PointNameInline, NoteInline]

    fieldsets = (
        ('Identity', {
            'fields': ('name',)
        }),
        ('Position', {
            'fields': ('location', ('latitude', 'longitude', 'altitude'), 'countries')
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

    @admin.display(description="Location")
    def location_display(self, obj):
        if obj.location:
            sn = 'N' if obj.location.y >= 0 else 'S'
            we = 'E' if obj.location.x >= 0 else 'W'
            return f"{abs(obj.location.y):.6f}° {sn}, {abs(obj.location.x):.5f}° {we}"
        return "—"


class NamedPointInline(admin.TabularInline):
    model = NamedPoint


@admin.register(PointName)
class PointNameAdmin(admin.ModelAdmin):
    pass


