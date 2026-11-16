import re

from django.contrib import admin
from django.db.models import Q

from cairn.admin import ModelAdmin
from django.utils.safestring import mark_safe

from ..models import Col, NamedPoint


@admin.register(Col)
class ColAdmin(ModelAdmin):
    list_display = ['point',
                    'flags',
                    'point_latitude', 'point_longitude', 'point_altitude',
                    'key_for:link',
                    'confluence_river:link']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        }),
        ('Drainage', {
            'fields': (
                'confluence_river',
                ('river_a', 'river_b'),
            ),
            'description': 'The confluence is where the waters of the two sides meet: the '
                           'mouth of that river. Each side may name the most minor catalogued '
                           'river its water reaches; both the same river means the col is a '
                           'spur inside its basin, and then the confluence stays empty.',
        }),
    )
    search_fields = ['point__name', 'key_for__point__name']
    autocomplete_fields = ['point', 'confluence_river', 'river_a', 'river_b']

    def get_search_results(self, request, queryset, search_term):
        """
        By name as usual, and by what an unnamed col can still be told apart by.

        A new col usually has no name and is nobody's key col yet, so the name search can
        never find it, including from the key-col box on a summit, which searches here.
        So a number also matches the altitude, read the way it is typed: "943" is anything
        from 943.0 to 943.9, "943.4" that decimetre. A whole number also matches the pk, and
        "unnamed" lists every col without a name.
        """
        results, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        term = search_term.strip()
        extra = Q()
        if term.lower() == 'unnamed':
            extra = Q(point__name__isnull=True) | Q(point__name='')
        elif re.fullmatch(r'\d+(\.\d)?', term):
            value = float(term)
            if '.' in term:     # stored as floats: 943.4 may be 943.39999…
                extra = Q(point__altitude__gte=value - 0.05, point__altitude__lt=value + 0.05)
            else:
                extra = Q(point__altitude__gte=value, point__altitude__lt=value + 1) | Q(pk=int(term))
        if extra:
            results = results | queryset.filter(extra)
        return results, may_have_duplicates

    def flags(self, obj):
        return obj.point.flags()

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
