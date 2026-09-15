from django.contrib import admin
from django.contrib.gis.db.models import PointField

from core.admin.world import PointModelAdmin
from core.fields import PointFormField
from ..models import River


@admin.register(River)
class RiverAdmin(PointModelAdmin):
    list_display = ['source__name', 'flags', 'source_latitude', 'source_longitude', 'source_altitude',
                    'source_summit:link', 'watershed_high_point:link', 'branches_off:link',
                    'mouth', 'mouth_altitude:.1f', 'mouth_side', 'parent:link', 'is_complete']
    fieldsets = (
        ('Identity', {
            'fields': (
                'source',
                'branches_off',
            )
        }),
        ('Mouth', {
            'fields': (
                'mouth',
                'mouth_altitude',
                'parent',
                'mouth_side',
            ),
         }),
        # Not 'Summits': only the first of these is one. The high point of a basin is any
        # named point, catalogued as a summit or not.
        ('Summit and high point', {
            'fields': (
                'source_summit',
                'watershed_high_point',
            )
        }),
    )
    search_fields = ['source__name']

    @admin.display(boolean=True)
    def is_complete(self, obj):
        return obj.complete

    def get_queryset(self, request):
        return (self.model.objects.with_db_status().with_source().with_parent()
                .with_source_summit().with_watershed_high_point())

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, PointField):
            return PointFormField(label=db_field.verbose_name.title(), required=False)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def source_latitude(self, obj):
        return f"{obj.source.location.y:+.6f}°"

    def source_longitude(self, obj):
        return f"{obj.source.location.x:+.6f}°"

    def source_altitude(self, obj):
        return f"{obj.source.altitude:.1f} m"

    def flags(self, obj):
        return obj.source.flags()

