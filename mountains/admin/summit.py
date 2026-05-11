from cairn.admin.modeladmin import admin_action
from django.contrib import admin

from core.admin import ModelAdmin
from mountains import models
from mountains.models import NamedPoint, Summit, Col


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
        }),
        ('Slope parent', {
            'fields': (
                'slope_parent', 'horizon_parent',
            )
        }),
    )

    list_display = ['point', 'point__latitude', 'point__longitude', 'point__altitude',
                    'is_complete',
                    'key_col__point__name', 'key_col_altitude',
                    'prominence', 'prominence_parent_link',
                    'isolation', 'isolation_parent_link',
                    'slope_parent_link', 'horizon_parent_link']

    actions = ['compute_slope_parent', 'compute_horizon_parent']
    search_fields = ['point__name']
    list_select_related = ['point', 'key_col__point', 'prominence_parent__point', 'slope_parent__point', 'horizon_parent__point']

    def get_queryset(self, request):
        return super().get_queryset(request).with_isolation().with_prominence().with_prominence_parent().with_isolation_parent().with_key_col().with_slope_parent().with_horizon_parent()

    def key_col_link(self, obj):
        return self.related_link(obj.key_col)

    def prominence_parent_link(self, obj):
        return self.related_link(obj.prominence_parent)

    def encirclement_parent_link(self, obj):
        return self.related_link(obj.encirclement_parent)

    def isolation_parent_link(self, obj):
        return self.related_link(obj.isolation_parent)

    def slope_parent_link(self, obj):
        return self.related_link(obj.slope_parent)

    def horizon_parent_link(self, obj):
        return self.related_link(obj.horizon_parent)

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

    @admin_action(description='Compute horizon parent')
    def compute_horizon_parent(self, request, queryset):
        all_summits = list(
            self.model.objects.select_related('point')
            .exclude(point__isnull=True)
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


    @admin.display(description='Prominence')
    def prominence(self, obj):
        if (prom := obj.compute_prominence()) is not None:
            return f"{prom:.1f} m"
        return None

    @admin.display(description='Isolation')
    def isolation(self, obj):
        if (iso := obj.compute_isolation()) is not None:
            return f"{iso.km:.3f} km"
        return None

    @admin.display(description='distance to NHN')
    def nhn_distance(self, obj):
        if (dist := obj.compute_distance_to_nhn()) is not None:
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
            kwargs['queryset'] = Col.objects.with_point()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)