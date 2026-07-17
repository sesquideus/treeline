from django.contrib import admin
from django.contrib.gis.db.models import PointField
from django.contrib.gis.geos import Point

from cairn.admin import ModelAdmin
from cairn.admin.modeladmin import admin_action

from core.fields import PointFormField
from mountains import models
from mountains.models import NamedPoint, Summit, Col


@admin.register(models.Summit)
class SummitAdmin(ModelAdmin):
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, PointField):
            return PointFormField(label=db_field.verbose_name.title(), required=False)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    class Media:
        css = {
            'all': ('css/admin.css',)
        }

    fieldsets = (
        ('Point', {
            'fields': ('point', 'location_display'),
        }),
        ('Prominence', {
            'fields': ('key_col', 'prominence_parent',
                       'prominence_source', 'island_high_point'),
        }),
        ('Isolation', {
            'fields': (
                'isolation_name', 'isolation_parent',
                'nearest_higher_point',
                'isolation_source',
            ),
        }),
        ('Slope parent', {
            'fields': (
                'slope_parent', 'horizon_parent',
            )
        }),
    )

    list_display = ['point', 'point_latitude', 'point_longitude', 'point_altitude', 'flags',
                    'is_complete',
                    'key_col_altitude', 'key_col:link',
                    'prominence', 'prominence_parent:link',
                    'isolation', 'isolation_parent:link',
                    'nhp_latitude', 'nhp_longitude',
                    'slope_parent:link', 'horizon_parent:link']

    actions = ['compute_slope_parent', 'compute_horizon_parent', 'compute_horizon_parent_std', 'compute_points']
    search_fields = ['point__name']
    list_select_related = ['point', 'key_col__point',
                           'prominence_parent__point', 'slope_parent__point', 'horizon_parent__point']
    readonly_fields = ['location_display']

    def get_queryset(self, request):
        return super().get_queryset(request).with_isolation().with_prominence().with_prominence_parent().with_isolation_parent().with_key_col().with_slope_parent().with_horizon_parent()

    @admin.display(description="Latitude")
    def point_latitude(self, obj):
        return f"{obj.point.location.y:+.6f}°"

    @admin.display(description="Longitude")
    def point_longitude(self, obj):
        return f"{obj.point.location.x:+.6f}°"

    @admin.display(description="Altitude")
    def point_altitude(self, obj):
        return f"{obj.point.altitude:.1f} m"

    def nhp_latitude(self, obj):
        if obj.nearest_higher_point:
            return f"{obj.nearest_higher_point.y:+.6f}°"
        return ""

    def nhp_longitude(self, obj):
        if obj.nearest_higher_point:
            return f"{obj.nearest_higher_point.x:+.6f}°"
        return ""

    def flags(self, obj):
        return obj.point.flags()

    @admin_action(description='Compute slope')
    def compute_slope_parent(self, request, queryset):
        all_summits = list(
            self.model.objects.select_related('point')
            .exclude(point__isnull=True)
            #            .filter(Q(slope_source__isnull=True) | Q(slope_source=source))
        )

        for summit in queryset.select_related('point'):
            if summit.point.name == 'Mount Everest':
                continue

            best = max(
                [s for s in all_summits if s.pk != summit.pk],
                key=lambda x: summit.point.slope_to(x.point),
            )

            if summit.slope_parent_id != best.pk and summit.point.altitude < best.point.altitude:
                summit.slope_parent = best
                summit.save(update_fields=['slope_parent'])
                yield summit.point.name

    def _compute_horizon_parent(self, all_summits, summit, refraction: float = 0.0):
        all_summits = list(
            self.model.objects.select_related('point').exclude(point__isnull=True)
        )

        best = max(
            [s for s in all_summits if s.pk != summit.pk],
            key=lambda x: summit.point.angle_to(x.point, refraction),
        )

        if summit.horizon_parent_id != best.pk and summit.point.angle_to(best.point, refraction) > 0:
            summit.horizon_parent = best
            summit.save(update_fields=['horizon_parent'])
            yield summit.point.nam

    @admin_action(description='Compute horizon parent')
    def compute_horizon_parent(self, request, queryset):
        all_summits = list(
            self.model.objects.select_related('point').exclude(point__isnull=True)
        )

        for summit in queryset.select_related('point'):
            best = max(
                [s for s in all_summits if s.pk != summit.pk],
                key=lambda x: summit.point.angle_to(x.point),
            )

            if summit.horizon_parent_id != best.pk and summit.point.angle_to(best.point) > 0:
                summit.horizon_parent = best
                summit.save(update_fields=['horizon_parent'])
                yield summit.point.name

    @admin_action(description='Compute horizon parent (std)')
    def compute_horizon_parent_std(self, request, queryset):
        all_summits = list(
            self.model.objects.select_related('point').exclude(point__isnull=True)
        )

        for summit in queryset.select_related('point'):
            best = max(
                [s for s in all_summits if s.pk != summit.pk],
                key=lambda x: summit.point.angle_to(x.point, refraction=0.14),
            )

            if summit.horizon_parent_std_id != best.pk and summit.point.angle_to(best.point, refraction=0.14) > 0:
                summit.horizon_parent_std = best
                summit.save(update_fields=['horizon_parent_std'])
                yield summit.point.name

    @admin.display(description="Location", ordering="point__location")
    def location_display(self, obj):
        if obj.point and obj.point.location:
            return obj.point.location
        return None

    @admin.display(description='Prominence')
    def prominence(self, obj):
        if (prom := obj.compute_prominence()) is not None:
            return f"{prom:.1f} m"
        return None

    @admin.display(description='Isolation')
    def isolation(self, obj):
        if (iso := obj.compute_isolation()) is not None:
            return f"{iso.km:.3f}\u00A0km"
        return None

    @admin.display(description='distance to NHN')
    def nhn_distance(self, obj):
        if (dist := obj.compute_distance_to_nhn()) is not None:
            return f"{dist.km:.3f}\uAA0Akm"
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
            'prominence_parent__point', 'isolation_parent__point'
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'horizon_parent':
            kwargs['queryset'] = Summit.objects.with_point()
        if db_field.name == 'slope_parent':
            kwargs['queryset'] = Summit.objects.with_point()
        elif db_field.name == 'prominence_parent':
            kwargs['queryset'] = Summit.objects.with_prominence()
        elif db_field.name == 'isolation_parent':
            kwargs['queryset'] = Summit.objects.with_isolation()
        elif db_field.name == 'key_col':
            kwargs['queryset'] = Col.objects.with_point().order_by('-point__altitude')

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
