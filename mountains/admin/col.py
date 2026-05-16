from django.contrib import admin

from cairn.admin import ModelAdmin
from django.utils.safestring import mark_safe

from ..models import Col, NamedPoint


@admin.register(Col)
class ColAdmin(ModelAdmin):
    list_display = ['point',
                    'point_latitude', 'point_longitude', 'point_altitude',
                    'confluence_river_link']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        }),
        ('Confluence', {
            'fields': (
                'confluence_river',
            )
        }),
    )
    search_fields = ['point__name', 'key_for__point__name']

    def point_latitude(self, obj):
        if obj.point.location:
            return f"{obj.point.location.y:+.6f}°"
        else:
            return mark_safe("&mdash;")

    def point_longitude(self, obj):
        if obj.point.location:
            return f"{obj.point.location.x:+.6f}°"
        else:
            return mark_safe("&mdash;")

    def point_altitude(self, obj):
        return f"{obj.point.altitude:+.1f} m"


    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'point':
            kwargs['queryset'] = NamedPoint.objects.select_related('col')

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
