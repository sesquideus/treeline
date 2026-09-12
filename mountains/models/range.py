import json

from django.apps import apps
from django.contrib.gis.db import models
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import CharField, Count, Q, Value
from django.db.models.functions import Concat, Length, Substr
from django.urls import reverse

from cairn.models import AdminModel
from mountains.models.base import GeoModel


class RangeQuerySet(models.QuerySet):
    """
    Chainable loading for the range tree.

    Navigation *from one node* — ancestors, the subtree, the summits under it — lives on
    `Range` itself rather than here. Those methods all need an instance to start from, and
    `mountains/test_querysets.py` builds every registered queryset method with no arguments,
    so an argument-taking `with_*` could not be exercised by the registry as it stands.
    """

    def with_system(self):
        """
        The classification a range belongs to. Every listing prints it — a "Vysoké Tatry"
        that does not say which system named it is not an answer — and without this it is
        one query per row.
        """
        return self.select_related('system')

    def with_parent(self):
        """
        The containing range, for a one-line "X, part of Y". Deliberately one level only:
        a full breadcrumb comes from `Range.ancestors()`, which costs one query however
        deep the node sits, where `select_related` would need one join per level and the
        systems here run to nine.
        """
        return self.select_related('parent')

    def roots(self):
        """
        The top of each system's tree. There is one root per system in a tidy import, but
        nothing enforces that — a subtree whose head was never attached surfaces here too,
        which is exactly the report this is for.
        """
        return self.filter(parent__isnull=True)

    def with_direct_summit_count(self):
        """
        How many summits name *this* range as their deepest one — which is not how many
        summits lie in it: a summit recorded on a sub-range counts there and not here, by
        the invariant that a summit stores only its deepest range. The subtree total needs
        a path-prefix subquery per row and has no caller yet.
        """
        return self.annotate(direct_summit_count=Count('memberships', distinct=True))


class RangeSystem(AdminModel):
    """
    One classification of mountains into ranges — the Slovak geomorphological division,
    SOIUSA, the AVE.

    Systems are not reconcilable with one another: they cut the same rock in different
    places, name the cuts differently, and disagree about how many levels there are. So
    each gets its own tree, and a summit gets one range *per system*.
    """

    class Meta:
        ordering = ['code']

    code = models.SlugField(max_length=32, unique=True,
                            help_text="URL-safe identifier: 'sk-geomorf', 'soiusa', 'ave'")
    name = models.CharField(max_length=255, unique=True)

    #: Where the division was published. Provenance sits on the system rather than on each
    #: range because it is the *system* that is somebody's work; the individual ranges all
    #: come from the same document.
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='range_systems')

    def __str__(self):
        return self.name


class Range(GeoModel):
    """
    A node in one system's tree: a range, a sub-range, or whatever that system calls the
    rank it sits at.

    `GeoModel` because it draws on the map — but only once somebody has given it an `area`.
    `to_geojson()` returns None without one, and `FlatGeoJsonView` drops those, so a purely
    nominal hierarchy simply contributes nothing to the layer rather than breaking it.
    """

    class Meta:
        # `system` first so the trees do not interleave, then `path`, which — with the C
        # collation on that column — walks each tree depth-first and keeps every subtree
        # contiguous. Both are plain columns: the default ordering pays for no join.
        ordering = ['system', 'path']

        constraints = [
            # Two children of one parent cannot share a name inside one system.
            # `nulls_distinct=False` (PostgreSQL 15+; this database is 17) is what makes it
            # bite on roots as well: their `parent` is NULL, and under SQL's default NULL
            # semantics two roots with the same name would both slip through.
            models.UniqueConstraint(
                fields=['system', 'parent', 'name'],
                nulls_distinct=False,
                name='unique_range_name_per_parent',
            ),
            # The terminator is load-bearing: every subtree query is a prefix match on this
            # column, and a path that lost its final '.' silently over-matches ('3.4' also
            # prefixes '3.41.'). The empty string is allowed because a fresh row is INSERTed
            # before it has the pk its path is built from; that window is one statement wide
            # and inside `save()`'s transaction.
            models.CheckConstraint(
                condition=Q(path='') | Q(path__endswith='.'),
                name='range_path_is_dot_terminated',
            ),
        ]

    system = models.ForeignKey('RangeSystem', on_delete=models.PROTECT, related_name='ranges')

    # PROTECT, not CASCADE: deleting an interior node would take a whole subtree of curated
    # ranges with it, and no warning. Emptying the subtree first is the intended way.
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='children')

    name = models.CharField(max_length=255)

    #: What this system calls the rank: 'celok', 'podcelok', 'sottosezione', 'gruppo'.
    #: A label and not a number, because the systems skip levels — a SOIUSA 'supergruppo'
    #: is optional and absent from half the sections, so the fourth level of one branch is
    #: not the fourth level of the next, and an integer would assert an equivalence that
    #: does not exist. Depth, where something genuinely wants it, is `depth()`.
    level_name = models.CharField(max_length=64, null=True, blank=True)

    #: Denormalised chain of ancestor ids ending with this node's own, dot-terminated:
    #: '3.41.118.' is node 118, child of 41, child of root 3. It exists so that "everything
    #: under X" is one indexed prefix match instead of a recursive walk, and so that a
    #: summit can record only its deepest range and still answer which ranges contain it.
    #:
    #: `editable=False` — derived in `save()`, and no form may offer it.
    #:
    #: `db_collation='C'` is not a detail. This database is en_GB.UTF-8, under which glibc
    #: ignores the dot at the primary level, so `ORDER BY path` yields
    #:     3.  30.  3.41.  3.5.  4.
    #: and a subtree stops being contiguous. Under C it yields
    #:     3.  3.41.  3.5.  30.  4.
    #: `Meta.ordering` and the tree-shaped admin changelist both rest on that. Prefix
    #: *lookups* are index-backed either way — Django adds a `varchar_pattern_ops` index
    #: for any indexed varchar, and C is deterministic so it is not skipped. Changing this
    #: later rewrites the column and both its indexes.
    #:
    #: 255 is ~31 levels at seven-digit ids; the deepest system here is nine.
    path = models.CharField(max_length=255, default='', editable=False, db_index=True,
                            db_collation='C')

    #: The ground the range covers. MultiPolygon and not Polygon because ranges come apart —
    #: an island group, a range clipped by a border in the source document — and because a
    #: single polygon imports into a MultiPolygon field unchanged while the reverse fails.
    #:
    #: geography(MultiPolygon, 4326), like every other geometry here. For whoever derives
    #: membership from it: on a geography column PostGIS implements only `bboverlaps`,
    #: `coveredby`, `covers`, `intersects` and `dwithin` natively — every other spatial
    #: lookup, `__within` included, is silently cast to geometry by the backend and then
    #: measures in degrees. Use `point__location__coveredby=range.area`.
    area = models.MultiPolygonField(geography=True, dim=2, srid=4326, null=True, blank=True)

    objects = RangeQuerySet.as_manager()

    def __str__(self):
        return f'{self.name} ({self.level_name})' if self.level_name else self.name

    def full_name(self):
        """Name, rank and system — the unambiguous form, as `NamedPoint.full_name()` is."""
        return f'{self} — {self.system.code}'

    def get_absolute_url(self):
        return reverse('range-detail', kwargs={'pk': self.pk})

    def to_dict(self):
        return {
            'pk': self.pk,
            'name': self.name,
            'level': self.level_name,
            'system': self.system.code,
            'depth': self.depth(),
            'parent': {'pk': self.parent_id, 'name': self.parent.name} if self.parent_id else None,
        }

    def to_geojson(self):
        """
        The range as a polygon, or None where nobody has drawn one yet — which is most of
        them, and which `FlatGeoJsonView` filters out for us.

        `properties.type` is 'range', and `styleFor()` in `static/js/styles.js` must know
        that string or the layer renders invisibly.
        """
        if self.area is None:
            return None
        return {
            'type': 'Feature',
            'geometry': json.loads(self.area.geojson),
            'properties': {'type': 'range', **self.to_dict()},
        }

    # --- the path -----------------------------------------------------------------

    def _built_path(self):
        """This node's path as its parent's path plus its own id. Needs the pk."""
        parent_path = self.parent.path if self.parent_id else ''
        return f'{parent_path}{self.pk}.'

    def _owns(self, path):
        """
        Whether `path` is a well-formed path *for this node* — one ending in its own id.
        A hand-built `Range(pk=..., parent=...)` carries an empty or foreign path, and
        rewriting descendants from that prefix would match either the whole table or
        somebody else's subtree.
        """
        return bool(path) and path.rstrip('.').rsplit('.', 1)[-1] == str(self.pk)

    def save(self, *args, **kwargs):
        """
        Keep `path` — and every descendant's — true.

        Creation takes two statements because the path ends with a pk that only the INSERT
        can produce. Re-parenting takes two more: this node's row, then one UPDATE that
        swaps the old prefix for the new across the whole subtree at once. That UPDATE is a
        single statement whatever the subtree's size and in whatever order its rows sit,
        because each row's new path depends only on its own old one.

        Summit rows are never touched by any of this, and that is the point of the design:
        a summit records its deepest range, containment comes from that range's path, so
        moving a sub-range rewrites ranges only.
        """
        with transaction.atomic():
            previous = self.path
            if self.pk is not None and not previous:
                # Somebody constructed the instance rather than loading it, so it carries no
                # path — and `_state.adding` is True even though the row exists, so it is
                # not the thing to test. Read the stored path instead, or the subtree
                # rewrite below has no prefix to work from and silently skips. A forced-pk
                # creation lands here too and correctly finds nothing.
                previous = (type(self).objects.filter(pk=self.pk)
                            .values_list('path', flat=True).first() or '')

            super().save(*args, **kwargs)

            built = self._built_path()
            if built == previous:
                return                      # nothing moved, so no second write

            if previous and built.startswith(previous):
                # The new parent is this node or something below it. `clean()` refuses that,
                # but `save()` does not validate in this project, and the consequence here
                # is not a bad record but a subtree whose paths cannot be expressed at all.
                # Stop before writing anything.
                raise ValueError(
                    f'{self} cannot be moved inside its own subtree: that is a cycle.'
                )

            self.path = built
            super().save(update_fields=['path'])

            if self._owns(previous):
                # A move, not a creation. Every descendant still carries `previous` as its
                # prefix; `Substr` is 1-based, so `len(previous) + 1` is the first character
                # after it. Self is excluded because its row was just written above.
                type(self).objects.filter(path__startswith=previous).exclude(pk=self.pk).update(
                    path=Concat(Value(self.path),
                                Substr('path', len(previous) + 1),
                                output_field=CharField())
                )

    @classmethod
    def rebuild_paths(cls, system=None):
        """
        Recompute every path from the `parent` links, breadth-first — roots first, then each
        level from the one above, so a child is never written before the parent it derives
        from. One query and one `bulk_update` per level; nine, worst case.

        Two callers: an importer using `bulk_create()`, which does not run `save()` and so
        leaves every path empty, and a human repairing a tree corrupted by saving hand-built
        instances.
        """
        ranges = cls.objects.all()
        if system is not None:
            ranges = ranges.filter(system=system)

        by_parent = {}
        for node in ranges:
            by_parent.setdefault(node.parent_id, []).append(node)

        level, prefixes = by_parent.get(None, []), {}
        while level:
            for node in level:
                node.path = f'{prefixes.get(node.parent_id, "")}{node.pk}.'
            cls.objects.bulk_update(level, ['path'])
            prefixes = {node.pk: node.path for node in level}
            level = [child for node in level for child in by_parent.get(node.pk, [])]

    # --- navigation ---------------------------------------------------------------

    def ancestor_ids(self):
        """
        The containing ranges' ids, outermost first, straight out of the path and without
        touching the database. '3.41.118.' -> [3, 41]; the last two elements of the split
        are this node's own id and the empty string after the terminator.
        """
        return [int(part) for part in self.path.split('.')[:-2]] if self.path else []

    def ancestors(self):
        """
        The chain from the root down to this node's parent, in that order, in one query —
        the breadcrumb. Ordered by `Length('path')` rather than by `path`: each ancestor's
        path is a strict prefix of the next, which makes length an exact ordering, and one
        that does not depend on the column's collation.
        """
        return type(self).objects.filter(pk__in=self.ancestor_ids()).order_by(Length('path'))

    def depth(self):
        """How deep this node sits; a root is 0. Free — the path already says it."""
        return self.path.count('.') - 1 if self.path else 0

    def self_and_descendants(self):
        """
        This range and everything below it, by prefix. No `system` filter is needed: ids are
        global, so one system's prefix cannot reach into another's tree. Guarded against an
        unsaved node, whose empty path would prefix the entire table.
        """
        if not self.path:
            return type(self).objects.none()
        return type(self).objects.filter(path__startswith=self.path)

    def descendants(self):
        return self.self_and_descendants().exclude(pk=self.pk)

    def all_summits(self):
        """
        Every summit recorded at or below this range — *not* `self.memberships`, which holds
        only the rows pinned exactly here. One join through the join table and one prefix
        match; nothing recurses.
        """
        Summit = apps.get_model('mountains', 'Summit')
        if not self.path:
            return Summit.objects.none()
        return Summit.objects.filter(range_memberships__range__path__startswith=self.path)

    def high_point(self):
        """
        The highest summit at or below this range, or None. One query.

        It is the highest summit *in this database*, which in a sparse area is not the
        range's culminating point — worth repeating wherever this is printed, because a
        number that looks authoritative and is not is worse than a blank.
        """
        return self.all_summits().select_related('point').order_by('-point__altitude').first()

    # --- validation ---------------------------------------------------------------

    def _check_parent_cycle(self):
        """
        A range cannot contain itself. The path makes this O(1) where
        `Summit._check_prominence_cycle` has to walk: a candidate parent is a descendant
        exactly when its path starts with this node's.
        """
        if not self.parent_id:
            return
        if self.parent_id == self.pk:
            raise ValidationError({'parent': 'A range cannot be its own parent.'})
        if self.path and self.parent.path.startswith(self.path):
            raise ValidationError({
                'parent': "The parent cannot be one of this range's own descendants."
            })

    def _check_parent_system(self):
        """
        A tree belongs to one classification. Crossing them would put a SOIUSA gruppo under
        a Slovak celok and make `SummitRange.system` — copied from the range — disagree with
        the branch the range actually hangs on.
        """
        if not (self.parent_id and self.system_id):
            return
        if self.parent.system_id != self.system_id:
            raise ValidationError({
                'parent': (f'{self.parent} belongs to {self.parent.system}, '
                           f'but this range is in {self.system}.')
            })

    def _check_system_unchanged(self):
        """
        A saved range cannot change system. Moving one means moving its whole subtree and
        every membership underneath, possibly colliding with a summit's existing membership
        in the destination — a data migration, not an edit.
        """
        if self._state.adding or self.pk is None:
            return
        stored = (type(self).objects.filter(pk=self.pk)
                  .values_list('system_id', flat=True).first())
        if stored is not None and stored != self.system_id:
            raise ValidationError({
                'system': 'A range cannot be moved to another classification system.'
            })

    def clean(self):
        super().clean()
        self._check_parent_cycle()
        self._check_parent_system()
        self._check_system_unchanged()


class SummitRange(AdminModel):
    """
    Which range a summit sits in, in one system.

    A plain join model with explicit `related_name`s rather than a
    `ManyToManyField(through=...)`, following `PointName` — the one M2M in this project is
    `NamedPoint.countries`, and it carries nothing.

    A summit records only the **deepest** range it belongs to; everything above comes from
    `range.path`, which is why re-parenting a sub-range never touches a summit row.
    "Deepest" means deepest *known*: a summit whose sub-range nobody has worked out yet sits
    on the celok, and that is a complete record, not a leaf requirement.
    """

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['summit', 'system'],
                name='one_range_per_system_per_summit',
            ),
        ]

    summit = models.ForeignKey('Summit', on_delete=models.CASCADE,
                               related_name='range_memberships')
    # PROTECT: a range holding summits must be emptied before it can go, rather than taking
    # somebody's curation with it silently.
    range = models.ForeignKey('Range', on_delete=models.PROTECT, related_name='memberships')

    #: Copied from `range.system`, and from nothing else. It exists for one reason: a unique
    #: constraint cannot reach through a join, so "one range per system per summit" is only
    #: enforceable in the database if the system is a column on this row. `editable=False`
    #: because nobody may set it — `save()` does, from the range, every time.
    system = models.ForeignKey('RangeSystem', on_delete=models.PROTECT, related_name='+',
                               editable=False)

    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='range_memberships')

    def save(self, *args, **kwargs):
        # Derived unconditionally, so no writer can put the two out of step. `bulk_create()`
        # bypasses this and hits a NOT NULL violation, which is the loud failure it should be.
        self.system_id = self.range.system_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.range_id and self.system_id and self.system_id != self.range.system_id:
            raise ValidationError({
                'range': (f'{self.range} is in {self.range.system}, but this membership '
                          f'records {self.system}.')
            })

    def __str__(self):
        return f'{self.summit} in {self.range} ({self.system.code})'
