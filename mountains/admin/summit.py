from django.contrib import admin

from core.admin import ModelAdmin
from mountains import models


@admin.register(models.Summit)
class SummitAdmin(ModelAdmin):
    class Media:
        css = {
            'all': ('css/admin.css',)
        }

    fieldsets = (
        ('Point', {
            'fields': ('point',),
        }),
        ('Prominence', {
            'fields': ('key_col', 'prominence_parent',
                       'prominence_source', 'island_high_point'),
        }),
        ('Isolation', {
            'fields': (
                'isolation_name', 'isolation_parent', ('isolation_latitude', 'isolation_longitude'),
                'isolation_source',
            ),
        })
    )

    list_display = ['point', 'point__latitude', 'point__longitude', 'point__altitude',
                    'is_complete',
                    'key_col__point__name', 'key_col_altitude',
                    'prominence', 'prominence_parent_link', 'encirclement_parent_link',
                    'isolation', 'isolation_parent_link']

    def key_col_link(self, obj):
        return self.related_link(obj.key_col)

    def prominence_parent_link(self, obj):
        return self.related_link(obj.prominence_parent)

    def encirclement_parent_link(self, obj):
        return self.related_link(obj.encirclement_parent)

    def isolation_parent_link(self, obj):
        return self.related_link(obj.isolation_parent)

    @admin.display(description='Prominence')
    def prominence(self, obj):
        if (prom := obj.prominence()) is not None:
            return f"{prom:.1f} m"
        return None

    @admin.display(description='Isolation')
    def isolation(self, obj):
        if (iso := obj.isolation()) is not None:
            return f"{iso.km:.3f} km"
        return None

    @admin.display(description='Key col altitude')
    def key_col_altitude(self, obj):
        if obj.key_col is not None and (kca := obj.key_col.point.altitude) is not None:
            return f"{kca:.1f} m"
        return None

    @admin.display(boolean=True)
    def is_complete(self, obj):
        return obj.is_complete()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'point', 'key_col__point',
            'encirclement_parent__point', 'prominence_parent__point', 'isolation_parent__point'
        )


