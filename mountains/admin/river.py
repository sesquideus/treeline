from django.contrib import admin
from django.contrib.gis.db.models import PointField

from core.admin.world import PointModelAdmin
from core.fields import PointFormField
from ..models import River


@admin.register(River)
class RiverAdmin(PointModelAdmin):
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, PointField):
            return PointFormField(label=db_field.verbose_name.title(), required=False)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    list_display = ['source__name', 'source__location', 'source__altitude', 'summit_link',
                    'mouth', 'mouth_altitude', 'parent_link', 'is_complete']
    fieldsets = (
        ('Identity', {
            'fields': (
                'source',
                'summit',
            )
        }),
        ('Mouth', {
            'fields': ('mouth', 'mouth_altitude', 'parent')
        })
    )
    search_fields = ['source__name']

    @admin.display(boolean=True)
    def is_complete(self, obj):
        return obj.complete

    def get_queryset(self, request):
        return self.model.objects.with_db_status().with_source().with_parent()