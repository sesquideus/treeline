from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from cairn.admin import ModelAdmin
from cairn.admin.modeladmin import ModelInline

from core.admin import GeometryModelAdmin

from ..forms.range import INDENT, RangeAdminForm, RangeChoiceField
from ..models import Range, RangeSystem, SummitRange


@admin.register(RangeSystem)
class RangeSystemAdmin(ModelAdmin):
    list_display = ['code', 'name', 'source:link', 'range_count']
    search_fields = ['code', 'name']
    fieldsets = (
        ('Identity', {'fields': ('code', 'name')}),
        ('Source', {'fields': ('source',)}),
    )

    def get_queryset(self, request):
        return (super().get_queryset(request)
                .select_related('source')
                .annotate(range_count=Count('ranges')))

    @admin.display(description='Ranges', ordering='range_count')
    def range_count(self, obj):
        """A link, not a number: every count in this admin is a way into the list behind it."""
        url = reverse('admin:mountains_range_changelist')
        return format_html('<a href="{}?system__id__exact={}">{}</a>',
                           url, obj.pk, obj.range_count)


class ChildRangeInline(ModelInline):
    """The immediate children, so a range's page reads as a node in the tree."""
    model = Range
    fk_name = 'parent'
    extra = 0
    fields = ['name', 'level_name']
    show_change_link = True
    verbose_name_plural = 'Sub-ranges'

    def has_add_permission(self, request, obj):
        # Children are added through the `add child` link on the changelist, which carries
        # the system across; an inline row here would have to ask for it again.
        return False


class SummitsInRangeInline(ModelInline):
    """
    The summits pinned exactly here. Not the summits *in* the range — those include every
    sub-range's, and are `Range.all_summits()`.
    """
    model = SummitRange
    fk_name = 'range'
    extra = 5
    fields = ['summit', 'source']
    autocomplete_fields = ['summit']
    verbose_name_plural = 'Summits recorded at this range'


@admin.register(Range)
class RangeAdmin(GeometryModelAdmin):
    form = RangeAdminForm
    list_display = ['tree_name', 'level_name', 'system:link', 'parent:link',
                    'summit_count', 'has_area', 'add_child']
    list_display_links = ['tree_name']
    list_filter = ['system', 'level_name']
    search_fields = ['name']            # also what the summit inline autocompletes against
    list_select_related = ['system', 'parent']
    # The whole tree on one page: paginated at the default 100 a mid-sized system splits
    # across pages and stops reading as a tree at all.
    list_per_page = 500
    inlines = [ChildRangeInline, SummitsInRangeInline]

    fieldsets = (
        ('Identity', {'fields': ('system', 'parent', 'name', 'level_name')}),
        ('Area', {
            # Collapsed: an OpenLayers map is 400px of chrome, and it must not sit between
            # the user and the name field while they are entering two hundred nodes.
            'classes': ('collapse',),
            'fields': ('area',),
        }),
    )

    def get_queryset(self, request):
        return (super().get_queryset(request)
                .with_system().with_parent().with_direct_summit_count())

    @admin.display(description='Name')
    def tree_name(self, obj):
        """
        The tree itself. `Meta.ordering` is ('system', 'path'), and the C collation on
        `path` keeps every subtree contiguous, so indenting by depth is all it takes.

        Deliberately not sortable — there is no `admin_order_field` — because one click on
        the header would scatter the hierarchy with no obvious way back.
        """
        return format_html('<span class="range-indent">{}{}</span>',
                           mark_safe(INDENT * obj.depth()), obj.name)

    @admin.display(description='Summits', ordering='direct_summit_count')
    def summit_count(self, obj):
        """Summits pinned exactly here, from the annotation — never a count() per row."""
        url = reverse('admin:mountains_summit_changelist')
        return format_html('<a href="{}?range={}">{}</a>', url, obj.pk, obj.direct_summit_count)

    @admin.display(description='Area', boolean=True)
    def has_area(self, obj):
        """Whether, not what: serialising a MultiPolygon per row would be absurd here."""
        return obj.area is not None

    @admin.display(description='')
    def add_child(self, obj):
        """
        The tree-building loop. Django pre-fills an add form from the query string
        (`get_changeform_initial_data`), so this lands on a blank range with its system and
        parent already set: type the name, save, click the next one.

        It is also what makes the narrowed parent select below work at add time — the add
        form is always reached with the system known.
        """
        url = reverse('admin:mountains_range_add')
        return format_html('<a class="addlink" href="{}?system={}&parent={}">add child</a>',
                           url, obj.system_id, obj.pk)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'parent':
            kwargs['form_class'] = RangeChoiceField
            queryset = Range.objects.with_system().order_by('system__code', 'path')
            if system_id := self._current_system(request):
                queryset = queryset.filter(system_id=system_id)
            kwargs['queryset'] = queryset
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @staticmethod
    def _current_system(request):
        """
        The system being worked in: `?system=` on the add form, put there by `add_child`, or
        the edited range's own system on a change form. Reaching the add form bare gives the
        unfiltered list ordered by system — degraded but correct, and `Range.clean()` refuses
        a cross-system parent anyway.
        """
        if system_id := request.GET.get('system'):
            return system_id
        match = request.resolver_match
        if match and (object_id := match.kwargs.get('object_id')):
            return (Range.objects.filter(pk=object_id)
                    .values_list('system_id', flat=True).first())
        return None


class SummitRangeInline(ModelInline):
    """
    A summit's ranges, one per system, on the summit's own page — where a single record is
    checked and corrected. Bulk entry is the changelist action, not this.
    """
    model = SummitRange
    fk_name = 'summit'
    extra = 1
    fields = ['range', 'source']
    autocomplete_fields = ['range']
    verbose_name_plural = 'Ranges'


class RangeSystemFilter(admin.SimpleListFilter):
    """Which classification to look through, when a summit sits in more than one."""
    title = 'classification'
    parameter_name = 'system'

    def lookups(self, request, model_admin):
        return RangeSystem.objects.values_list('pk', 'name')

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(range_memberships__system=self.value())


class RangeFilter(admin.SimpleListFilter):
    """
    Filter by a range *and everything under it* — one prefix match on `path`.

    Only the top few levels are offered: the whole tree would be an unusable sidebar, and
    the deep levels are reachable by clicking a range's summit count instead.
    """
    title = 'range'
    parameter_name = 'range'           # matches the ?range= link from RangeAdmin.summit_count

    def lookups(self, request, model_admin):
        ranges = (Range.objects.with_system()
                  .filter(path__regex=r'^([0-9]+\.){1,3}$')
                  .order_by('system__code', 'path'))
        return [(r.pk, f'{INDENT * r.depth()}{r.name}') for r in ranges]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        root = Range.objects.filter(pk=self.value()).first()
        if root is None:
            return queryset.none()
        # One membership per system means at most one row can match a single subtree, so
        # this cannot duplicate summits; distinct() is free insurance against a future
        # filter that joins the same table again.
        return queryset.filter(range_memberships__range__path__startswith=root.path).distinct()


class RangeAssignedFilter(admin.SimpleListFilter):
    """The progress bar for the whole exercise: how much of the database is still unplaced."""
    title = 'range assignment'
    parameter_name = 'assigned'

    def lookups(self, request, model_admin):
        return [('yes', 'Assigned'), ('no', 'Unassigned')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(range_memberships__isnull=False).distinct()
        if self.value() == 'no':
            return queryset.filter(range_memberships__isnull=True)
        return queryset
