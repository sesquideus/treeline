from django.contrib import admin, messages
from django.contrib.gis.db.models import PointField
from django.contrib.gis.geos import Point
from django.db import transaction
from django.template.response import TemplateResponse
from django.utils.html import format_html_join

from cairn.admin import ModelAdmin
from cairn.admin.modeladmin import NA, admin_action

from core.fields import PointFormField
from mountains.slope_horizon import compute as compute_parents
from mountains import models
from mountains.forms.range import AssignRangeForm
from mountains.models import NamedPoint, Summit, SummitRange, Col
from mountains.models.summit import PROMINENCE_BANDS, PROMINENCE_UNKNOWN
from .range import (RangeAssignedFilter, RangeFilter, RangeSystemFilter,
                    SummitRangeInline)


class ProminenceBandFilter(admin.SimpleListFilter):
    """
    The map's prominence bands, as a sidebar.

    Not a plain field lookup, because prominence is not a column: it is the drop to the key
    col, or the altitude of an island high point, and it only exists as an annotation. The
    band definitions are shared with `static/js/styles.js` so that filtering here selects
    exactly the summits the map paints that colour.
    """
    title = 'prominence'
    parameter_name = 'prominence'

    def lookups(self, request, model_admin):
        # Unknown last, after the seven that tile the numbers — it is an absence, not a band.
        return [(band.key, band.label) for band in (*PROMINENCE_BANDS, PROMINENCE_UNKNOWN)]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.in_prominence_band(self.value())


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
        ('Isolation', {
            'fields': (
                'nearest_higher_point',
                ('isolation_name', 'isolation_parent',),
                'isolation_source',
            ),
        }),
        ('Prominence', {
            'fields': (
                'key_col', 'prominence_parent',
                'prominence_source', 'island_high_point'
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
                    'slope_parent:link', 'horizon_parent:link',
                    'range_display']

    actions = ['compute_slope_parent', 'compute_horizon_parent', 'compute_horizon_parent_std',
               'compute_points', 'assign_to_range']
    search_fields = ['point__name']
    autocomplete_fields = ['point', 'key_col', 'prominence_parent', 'isolation_parent', 'slope_parent', 'horizon_parent']
    inlines = [SummitRangeInline]
    # `RelatedOnly` for the countries: a filter that offers the five countries no summit
    # sits in is five ways to reach an empty list.
    list_filter = [ProminenceBandFilter,
                   ('point__countries', admin.RelatedOnlyFieldListFilter),
                   RangeAssignedFilter, RangeSystemFilter, RangeFilter,
                   ('horizon_parent', admin.EmptyFieldListFilter)]
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

    # Every summit is a candidate parent, the selection says whose parents are worked out; by
    # branch and bound (mountains/slope_horizon.py), seconds even for all of them.
    def _compute(self, kind, queryset):
        changed = compute_parents(kind, queryset)
        yield from (self.model.objects.filter(pk__in=changed).select_related('point')
                    .values_list('point__name', flat=True))

    @admin_action(description='Compute slope')
    def compute_slope_parent(self, request, queryset):
        yield from self._compute('slope', queryset)

    @admin_action(description='Compute horizon parent')
    def compute_horizon_parent(self, request, queryset):
        yield from self._compute('horizon', queryset)

    @admin_action(description='Compute horizon parent (std)')
    def compute_horizon_parent_std(self, request, queryset):
        yield from self._compute('horizon_std', queryset)

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
        ).prefetch_related('range_memberships__range', 'range_memberships__system')

    @admin.display(description='Range')
    def range_display(self, obj):
        """Every system's range for this summit. Prefetched above; never a query per row."""
        memberships = obj.range_memberships.all()
        if not memberships:
            return NA
        # `related_link` is cairn's, the same helper the `:link` directive uses, and it
        # returns SafeString — so format_html_join passes it through unescaped.
        return format_html_join(', ', '{}',
                                ((self.related_link(m.range),) for m in memberships))

    @admin.action(description='Assign to a range…')
    def assign_to_range(self, request, queryset):
        """
        Bulk-assign, via a confirmation page carrying the range chooser.

        Not `@admin_action` like the four above: that decorator reports a count and returns
        None, and an action with an intermediate page has to return a response.
        """
        if 'apply' in request.POST:
            form = AssignRangeForm(request.POST)
            if form.is_valid():
                target = form.cleaned_data['range']
                assigned, moved = self._assign(queryset, target)
                self.message_user(
                    request,
                    f'{target}: {assigned} assigned, {moved} moved from another range.',
                    messages.SUCCESS)
                return None                     # None sends the admin back to the changelist
        else:
            form = AssignRangeForm()

        return TemplateResponse(request, 'admin/mountains/summit/assign_range.html', {
            **self.admin_site.each_context(request),
            'title': 'Assign summits to a range',
            'opts': self.model._meta,
            'form': form,
            'summits': queryset.select_related('point')[:50],
            'total': queryset.count(),
            # The whole original POST goes back out as hidden inputs. Re-emitting only the
            # ticked pks would drop `select_across`, and "select all 1680" would then quietly
            # assign one page of them.
            'post_data': request.POST,
            'media': self.media + form.media,
        })

    @staticmethod
    def _assign(queryset, target):
        """
        Three statements however many summits were selected.

        `unique(summit, system)` means a summit already placed in this system has to be
        *updated*: a plain create raises IntegrityError the first time anyone corrects a
        mistake. Memberships in other systems are left alone.
        """
        summit_ids = list(queryset.values_list('pk', flat=True))
        with transaction.atomic():
            existing = set(SummitRange.objects
                           .filter(summit_id__in=summit_ids, system=target.system)
                           .values_list('summit_id', flat=True))
            SummitRange.objects.filter(summit_id__in=existing, system=target.system) \
                               .update(range=target)
            SummitRange.objects.bulk_create([
                SummitRange(summit_id=pk, system=target.system, range=target)
                for pk in summit_ids if pk not in existing
            ])
        return len(summit_ids) - len(existing), len(existing)

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
