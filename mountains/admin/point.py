from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.gis.db.models import PointField

from core.fields import PointFormField
from .note import NoteInline
from ..models import PointName, NamedPoint, Note


class PointNameInline(admin.TabularInline):
    model = PointName
    extra = 3


@admin.register(NamedPoint)
class NamedPointAdmin(GISModelAdmin):
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, PointField):
            return PointFormField(label=db_field.verbose_name.title(), required=False)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    gis_widget_kwargs = {
        "attrs": {
            "default_lat": 48.5,
            "default_lon": 19.5,
            "default_zoom": 7,
        },
    }
    list_display = ['__str__', 'location_display', 'altitude', 'altitude_datum',
                    'altitude_egm2008', 'altitude_review', 'flags']
    list_filter = ['altitude_review', ('altitude_datum', admin.EmptyFieldListFilter), 'altitude_datum']
    list_select_related = ['altitude_datum']
    readonly_fields = ['altitude_egm2008']
    inlines = [PointNameInline, NoteInline]
    search_fields = ['name']
    autocomplete_search_fields = ['name']
    fieldsets = (
        ('Identity', {
            'fields': ('name',)
        }),
        ('Position', {
            'fields': (('location', 'altitude'), ('altitude_datum', 'altitude_egm2008'),
                       'countries')
        }),
        ('Source', {
            'fields': ('source', 'altitude_review')
        }),
    )

    def save_related(self, request, form, formsets, change):
        """
        A new point with no datum chosen gets the one its countries imply. Only on adding:
        an existing point left without one is unknown on purpose.
        """
        super().save_related(request, form, formsets, change)
        point = form.instance
        if not change and point.altitude_datum_id is None:
            point.altitude_datum = point.default_datum()
            if point.altitude_datum is not None:
                point.save(update_fields=['altitude_datum'])

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
        return None


class NamedPointInline(admin.TabularInline):
    model = NamedPoint


@admin.register(PointName)
class PointNameAdmin(admin.ModelAdmin):
    autocomplete_fields = ['point']
    list_display = ['name', 'point', 'language', 'local', 'historical']
    list_filter = ['historical', 'local', 'language']
    list_select_related = ['point', 'language']
    search_fields = ['name', 'point__name']


