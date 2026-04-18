from django.contrib import admin

from core.admin import ModelAdmin
from mountains import models


@admin.register(models.Summit)
class SummitAdmin(ModelAdmin):
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        }),
        ('Key Prominence', {
            'fields': ('key_col', ('encirclement_parent', 'prominence_parent'),
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
                    'key_col', 'prominence_parent_link', 'encirclement_parent_link', 'distance_to_key_col',
                    'prominence', 'isolation_parent_link', 'isolation']

    def key_col_link(self, obj):
        return self.related_link(obj.key_col)

    def prominence_parent_link(self, obj):
        return self.related_link(obj.prominence_parent)

    def encirclement_parent_link(self, obj):
        return self.related_link(obj.encirclement_parent)

    def isolation_parent_link(self, obj):
        return self.related_link(obj.isolation_parent)

    @admin.display(boolean=True)
    def is_complete(self, obj):
        return obj.is_complete()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'point', 'key_col__point', 'encirclement_parent__point', 'prominence_parent__point', 'isolation_parent__point'
        )


