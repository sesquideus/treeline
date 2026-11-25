"""
Apply a changeset written by `lidar/changeset.py`: P100s found in the lidar and missing from
the database, Freemap names in languages a point has none in, and names for unnamed key cols.

    uv run python manage.py apply_lidar_changeset lidar/changesets/CHANGESET.json --dry-run
    uv run python manage.py apply_lidar_changeset lidar/changesets/CHANGESET.json

Plain JSON, no lidar tooling needed, so the same file goes on the server. Running it twice
changes nothing the second time: a summit is skipped when one already stands within 30 m, a
name when the point has it in that language, a col name when the col has been named since.
Points are found by pk and checked against the name the changeset saw, so a point renamed or
renumbered since is skipped with a warning rather than given someone else's name. A name proves
nothing for an unnamed point, and numbers part ways between two databases as soon as either
creates something the other does not: an unnamed point is taken by its number only where it
also stands where the changeset saw it (`was`, [lat, lon], within 50 m), and otherwise skipped. A name
another point already has is numbered — "Hoľa (2)" — for a person to qualify properly later.

The dry run is a real one, rolled back at the end, as in `import_peaks`.
"""
import json
import math

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Country, Language, VerticalDatum
from mountains.map_zooms import refresh_and_record_map_zooms
from mountains.models import Col, NamedPoint, Note, PointName, Source, Summit
from users.models import User

SAME_PLACE = D(m=30)
NEAR_ENOUGH = 50        # m: a point found by its number must stand this near where the changeset saw it


def metres(location, at):
    """Metres between a point's location and `at` ({'lat', 'lon'} or [lat, lon])."""
    lat, lon = (at['lat'], at['lon']) if isinstance(at, dict) else at
    k = math.cos(math.radians(lat))
    return math.hypot((location.x - lon) * 111320 * k, (location.y - lat) * 110574)


class DryRun(Exception):
    pass


class Command(BaseCommand):
    help = 'Apply a lidar changeset (lidar/changeset.py): missing P100s, names, col names.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true', help='do everything, then roll back')
        parser.add_argument('--author', help='username the notes are written by (default: the first superuser)')
        parser.add_argument('--refresh-map', action='store_true',
                            help='rebuild the map zoom table at the end (about 25 s); otherwise run '
                                 '`manage.py refresh_map_zooms` once after the last changeset')

    def handle(self, path, dry_run=False, author=None, refresh_map=False, **options):
        data = json.load(open(path))
        self.lidar = Source.objects.get_or_create(name=data['sources']['lidar'])[0]
        self.osm = Source.objects.get_or_create(name=data['sources']['names'])[0]
        self.author = (User.objects.get(username=author) if author
                       else User.objects.filter(is_superuser=True).order_by('pk').first())
        if self.author is None:
            raise CommandError('no superuser to write the notes as; pass --author')
        self.counts = dict(summits=0, names=0, col_names=0, renamed=0, splits=0, moved=0, relinked=0,
                           removed=0, summits_removed=0, merged=0, isolations=0, promoted=0,
                           line_parents=0, skipped=0)
        # A col this changeset names is no longer unnamed on a second run: that is not a change.
        self.ours = {c['point']: c['name'] for c in data.get('col_names', [])}
        try:
            with transaction.atomic():
                self.reference(data.get('reference', {}))
                self.languages = {lang.iso639_1: lang for lang in Language.objects.all()}
                self.datums = {d.code: d for d in VerticalDatum.objects.all()}
                self.countries = {c.code: c for c in Country.objects.all()}
                # Splits and moves first, then relinks (which may free a col), then new summits
                # (which may take one), names, and last the cols nothing needs any more.
                new = self.new = {}
                for split in data.get('splits', []):
                    self.split(split)
                for fix in data.get('points', []):
                    self.move(fix)
                self.relink(data.get('relinks', []))
                for s in data.get('summits', []):
                    self.summit(s, new)
                for n in data.get('point_names', []):
                    self.point_name(n)
                for c in data.get('col_names', []):
                    self.col_name(c)
                for r in data.get('renames', []):
                    self.replace_name(r)
                for at in data.get('remove_cols', []):
                    self.remove_col(at)
                for r in data.get('remove_summits', []):
                    self.remove_summit(r)
                for m in data.get('merges', []):
                    self.merge(m)
                for iso in data.get('isolations', []):
                    self.isolation(iso)
                for pr in data.get('promotions', []):
                    self.promote(pr)
                for lp in data.get('line_parents', []):
                    self.line_parent(lp)
                if dry_run:
                    raise DryRun
        except DryRun:
            self.stdout.write(self.style.WARNING('dry run: rolled back'))
        else:
            # The map's zoom table is rebuilt whole (about 25 s at 240 000 summits, minutes at
            # millions), so only when asked: after a batch of changesets, once. Until then the map
            # shows the table as last built.
            if refresh_map:
                refresh_and_record_map_zooms()
                self.stdout.write('map zoom table rebuilt')
            else:
                self.stdout.write('map zoom table not rebuilt: run `manage.py refresh_map_zooms` after the last changeset')
        c = self.counts
        self.stdout.write(self.style.SUCCESS(
            f'{c["summits"]} summits, {c["names"]} names, {c["col_names"]} col names, '
            f'{c["renamed"]} renamed, {c["splits"]} split, {c["moved"]} moved, {c["relinked"]} relinked, '
            f'{c["removed"]} cols removed, {c["summits_removed"]} summits removed, {c["merged"]} merged, {c["isolations"]} isolations, {c["promoted"]} promoted, '
            f'{c["line_parents"]} line parents; '
            f'{c["skipped"]} skipped'))

    def reference(self, ref):
        """
        Countries and languages a changeset's points need and a database may lack: created when
        missing, left as they are when present (a person may have renamed them since).
        """
        for c in ref.get('countries', []):
            if not Country.objects.filter(code=c['code']).exists():
                Country.objects.create(code=c['code'], name=c['name'], full_name=c['full_name'],
                                       english_name=c['english_name'])
                self.stdout.write(f'  + country {c["code"]} {c["english_name"]}')
        for lang in ref.get('languages', []):
            if not Language.objects.filter(iso639_1=lang['iso639_1']).exists():
                Language.objects.create(**lang)
                self.stdout.write(f'  + language {lang["iso639_1"]} {lang["english_name"]}')
        for datum in ref.get('datums', []):
            # A height system the heights come in: {code, name, crs, kind, notes}. Where PROJ
            # has no grid for it, EGM2008 heights stay null (core/functions/heights.py).
            if not VerticalDatum.objects.filter(code=datum['code']).exists():
                VerticalDatum.objects.create(**datum)
                self.stdout.write(f'  + vertical datum {datum["code"]}')

    # ------------------------------------------------------------------ summits

    def unique(self, name, what):
        """
        `name`, or `name (2)`, `name (3)`… when another point has it: NamedPoint.name is
        unique, and the right qualifier ("Hoľa (Čierna hora)") is for a person to choose.
        """
        if not name or not NamedPoint.objects.filter(name=name).exists():
            return name
        n = 2
        while NamedPoint.objects.filter(name=f'{name} ({n})').exists():
            n += 1
        self.stdout.write(f'  ! the name "{name}" is taken: {what} is "{name} ({n})" for now')
        return f'{name} ({n})'

    def rename(self, point, name, what):
        """Name a point an earlier run had to leave unnamed."""
        if point.name is None and name:
            point.name = self.unique(name, what)
            point.full_clean()
            point.save(update_fields=['name'])
            self.counts['renamed'] += 1

    def point(self, d, what):
        """A new NamedPoint, numbered if its name is taken; the plain name also a PointName."""
        name = self.unique(d['name'], what)
        # A height from elsewhere than the changeset's own data (a published list) names its
        # source, and its datum may be unknown.
        point = NamedPoint(name=name, location=Point(d['lon'], d['lat'], srid=4326),
                           altitude=round(d['altitude'], 2),
                           altitude_datum=self.datums[d['datum']] if d.get('datum') else None,
                           source=Source.objects.get_or_create(name=d['source'])[0] if d.get('source') else self.lidar)
        point.full_clean()
        point.save()
        point.countries.set([self.countries[c] for c in d['countries'] if c in self.countries])
        for n in d['names']:
            self.add_name(point, n['lang'], n['name'], local=n.get('local', False),
                          historical=n.get('historical', False), source=n.get('source'))
        for text in d.get('notes', []):
            Note.objects.create(point=point, text=text, author=self.author)
        return point

    def summit(self, s, new):
        here = Point(s['lon'], s['lat'], srid=4326)
        old = Summit.objects.filter(point__location__dwithin=(here, SAME_PLACE)).select_related('point').first()
        if old:
            self.rename(old.point, s['name'], 'the summit')
            # names a later changeset found: added (one it already has is not added twice)
            for n in s.get('names', []):
                self.add_name(old.point, n['lang'], n['name'], local=n.get('local', False),
                              historical=n.get('historical', False), source=n.get('source'))
            if old.key_col and old.key_col.point and s.get('col'):
                self.rename(old.key_col.point, s['col'].get('name'), 'its key col')
            self.stdout.write(f'  = {s["name"] or s["key"]}: already there as {old}')
            new[s['key']] = old
            self.counts['skipped'] += 1
            return
        c = s.get('col')
        if s.get('island'):
            # An island's high point: its key col is the sea.
            col = None
        elif 'existing' in c:
            # A col the DB has, freed by a relink: this summit is the one it is the key col of.
            col = Col.objects.filter(point__pk=c['existing']['point'], point__name=c['existing']['name'],
                                     key_for__isnull=True).select_related('point').first()
            col = col and self.checked(col, col.point, c['existing']['name'], c['existing'].get('was'), 'col')
            if col is None:
                self.stdout.write(f'  ! {s["name"] or s["key"]}: col {c["existing"]["name"]} not found free: skipped')
                self.counts['skipped'] += 1
                return
        else:
            col = (Col.objects.filter(point__location__dwithin=(Point(c['lon'], c['lat'], srid=4326), SAME_PLACE),
                                      key_for__isnull=True).first()
                   or Col.objects.create(point=self.point(c, 'its key col')))
        parent = None
        if s['parent'] and 'at' in s['parent']:
            parent = Summit.objects.filter(point__location__dwithin=(
                Point(s['parent']['at'][1], s['parent']['at'][0], srid=4326), SAME_PLACE)).first()
        elif s['parent'] and 'pk' in s['parent']:
            parent = Summit.objects.filter(pk=s['parent']['pk'], point__name=s['parent']['name']).select_related('point').first()
            parent = parent and self.checked(parent, parent.point, s['parent']['name'], s['parent'].get('was'), 'summit')
            if parent is None:
                self.stdout.write(f'  ! parent {s["parent"]["name"]} (#{s["parent"]["pk"]}) not found: left out')
        elif s['parent'] and 'new' in s['parent']:
            parent = new.get(s['parent']['new'])
        # The prominence source goes with the parent: the database will not have one alone.
        summit = Summit(point=self.point(s, 'the summit'), key_col=col, prominence_parent=parent,
                        prominence_source=self.lidar if parent else None, island_high_point=bool(s.get('island')))
        try:
            summit.full_clean()
        except ValidationError as e:
            # A parent that breaks an invariant is left for a person. On the border it happens:
            # the lidar alone overrates a neighbour whose key col is across it.
            self.stdout.write(f'  ! {summit.point}: {"; ".join(e.messages)} Saved without a parent.')
            summit.prominence_parent = summit.prominence_source = None
            summit.full_clean()
        summit.save()
        new[s['key']] = summit
        self.counts['summits'] += 1
        self.stdout.write(f'  + {summit.point} {s["altitude"]:.1f} m, P {s["prominence"]:.1f}, '
                          f'key col {col.point if col else "the sea"}, parent {summit.prominence_parent}')

    # ------------------------------------------------------------------ names

    def add_name(self, point, lang, name, local=False, historical=False, source=None):
        language = self.languages.get(lang)
        if language is None:
            self.stdout.write(f'  ! no language "{lang}" in the database: "{name}" for {point} left out')
            self.counts['skipped'] += 1
            return False
        if PointName.objects.filter(point=point, language=language, name=name).exists():
            self.counts['skipped'] += 1
            return False
        source = Source.objects.get_or_create(name=source)[0] if source else self.osm
        PointName.objects.create(point=point, language=language, name=name, local=local,
                                 historical=historical, source=source)
        self.counts['names'] += 1
        return True

    def checked(self, obj, point, name, at, what):
        """
        `obj` found by its number, kept only if it is the one the changeset meant: where `at`
        says (if given), and never by number alone when unnamed. Numbers are not shared with
        another database once either has created anything the other has not; a name proves the
        identity, a missing name proves nothing.
        """
        if obj is None:
            return None
        if at is not None:
            if metres(point.location, at) > NEAR_ENOUGH:
                self.stdout.write(f'  ! {what} #{point.pk} is not where the changeset saw it: skipped')
                return None
        elif name is None:
            self.stdout.write(f'  ! {what} #{point.pk}: unnamed, and no position to tell it by: skipped')
            return None
        return obj

    def found(self, pk, name, quiet=False, at=None):
        point = NamedPoint.objects.filter(pk=pk).first()
        mine = self.ours.get(pk)
        if point is not None and name is None and mine and point.name and \
                (point.name == mine or point.name.startswith(f'{mine} (')):
            return point if not quiet else None
        if point is None or point.name != name:
            self.stdout.write(f'  ! point #{pk} is no longer "{name}": skipped')
            self.counts['skipped'] += 1
            return None
        if self.checked(point, point, name, at, 'point') is None:
            self.counts['skipped'] += 1
            return None
        return point

    def point_name(self, n):
        point = self.found(n['point'], n['point_name'])
        if point and self.add_name(point, n['lang'], n['name']):
            self.stdout.write(f'  + {point}: {n["lang"]} {n["name"]}')

    def replace_name(self, r):
        """
        A wrong name off a summit (one OSM gave a rock or a sign, say) and the right one on: the
        summit standing at `at` ([lat, lon]) named `old` becomes `name`; `old` goes from its names.
        """
        point = self.summit_point_at(r['at'])
        if point is None or point.name != r['old']:
            done = point is not None and point.name is not None and \
                (point.name == r['name'] or point.name.startswith(f'{r["name"]} ('))
            if not done:
                self.stdout.write(f'  ! no summit "{r["old"]}" at {r["at"]}: skipped')
            self.counts['skipped'] += 1
            return
        PointName.objects.filter(point=point, name=r['old']).delete()
        point.name = None
        point.save(update_fields=['name'])
        self.rename(point, r['name'], f'the summit at {r["at"]}')
        self.add_name(point, r['lang'], r['name'], local=True)
        self.stdout.write(f'  ~ "{r["old"]}" renamed {point.name}')

    def col_name(self, c):
        point = self.found(c['point'], None, quiet=True, at=c.get('was'))
        if point is None:
            return
        self.rename(point, c['name'], f'the key col of {c["summit"]}')
        self.add_name(point, c['lang'], c['name'], local=True)
        for n in c['names']:
            self.add_name(point, n['lang'], n['name'])
        self.counts['col_names'] += 1
        self.stdout.write(f'  + key col of {c["summit"]}: {c["name"]}')

    # ------------------------------------------------------------------ splits

    def split(self, sp):
        """
        A DB summit whose key col belongs to a higher top beside it. The higher top becomes a
        summit of its own and takes over that key col and parent; the old summit gets its own
        key col, the saddle between the two, and the new top as its parent. Its children more
        prominent than it now is move to the new top: they no longer hang off a lesser peak.
        """
        old = Summit.objects.filter(pk=sp['summit'], point__name=sp['summit_name']).select_related(
            'point', 'key_col__point', 'prominence_parent').first()
        old = old and self.checked(old, old.point, sp['summit_name'], sp.get('summit_was'), 'summit')
        if old is None:
            self.stdout.write(f'  ! {sp["summit_name"]} (#{sp["summit"]}) not found as such: split skipped')
            self.counts['skipped'] += 1
            return
        t = sp['top']
        if Summit.objects.filter(point__location__dwithin=(Point(t['lon'], t['lat'], srid=4326), SAME_PLACE)).exists():
            self.counts['skipped'] += 1
            return
        own_col = Col.objects.create(point=self.point(dict(sp['col'], name=None, names=[]), 'the new col'))
        if t['altitude'] <= old.point.altitude:
            # The other top is no higher than the summit as the DB has it (a tie, within the
            # lidar's reach): the summit stays as it is, and the other top hangs off it.
            top = Summit(point=self.point(dict(t, name=None, names=[]), 'the new top'), key_col=own_col,
                         prominence_parent=old, prominence_source=self.lidar)
            top.full_clean()
            top.save()
            self.counts['splits'] += 1
            self.stdout.write(f'  + {old.point}: the other top {top.point} is no higher, so it hangs off it, '
                              f'key col {own_col.point}, P {top.compute_prominence():.1f}')
            return
        key_col, parent, source = old.key_col, old.prominence_parent, old.prominence_source
        Summit.objects.filter(pk=old.pk).update(key_col=own_col, prominence_parent=None, prominence_source=None)
        top = Summit(point=self.point(dict(t, name=None, names=[]), 'the new top'), key_col=key_col,
                     prominence_parent=parent, prominence_source=(source or self.lidar) if parent else None)
        top.full_clean()
        top.save()
        old.refresh_from_db()
        old.prominence_parent, old.prominence_source = top, self.lidar
        old.full_clean()
        old.save()
        mine = old.compute_prominence()
        moved = []
        for child in Summit.objects.filter(prominence_parent=old).select_related('point'):
            if child.compute_prominence() >= mine:
                child.prominence_parent = top
                child.full_clean()
                child.save(update_fields=['prominence_parent'])
                moved.append(str(child.point))
        self.counts['splits'] += 1
        self.stdout.write(f'  + {old.point}: new top {top.point} takes its key col {key_col.point if key_col else None}; '
                          f'its own key col {own_col.point}, P {mine:.1f}'
                          + (f'; moved to the new top: {", ".join(moved)}' if moved else ''))

    # ------------------------------------------------------------------ corrections

    def move(self, fix):
        """
        A point put where the lidar has it: an old col read off a map, a summit label placed
        on the slope. With an altitude, at the lidar's height too.
        """
        if 'summit_at' in fix:
            # By where the summit stands: numbers are not shared with another database.
            point = self.summit_point_at(fix['summit_at'])
            if point is None:
                self.stdout.write(f'  ! no summit at {fix["summit_at"]}: not moved')
                self.counts['skipped'] += 1
                return
        else:
            point = self.found(fix['point'], fix['name'], at=fix.get('was'))
        if point is None:
            return
        # Without a position only the height changes; without an altitude only the position:
        # the height, its datum and its source stay. A height from elsewhere than the lidar (a
        # published list) names its `source`, and its `datum` may be null: unknown.
        here = Point(fix['lon'], fix['lat'], srid=4326) if 'lat' in fix else point.location
        height = fix.get('altitude')
        datum = self.datums[fix['datum']] if fix.get('datum') else None
        source = Source.objects.get_or_create(name=fix['source'])[0] if fix.get('source') else self.lidar
        if point.location.distance(here) < 1e-7 and (height is None or (
                point.altitude == height and point.altitude_datum == datum and point.source == source)):
            self.counts['skipped'] += 1
            return
        was = f'{point.location.y:.6f}, {point.location.x:.6f}, {point.altitude} m'
        point.location = here
        if height is not None:
            point.altitude, point.altitude_datum, point.source = height, datum, source
        point.full_clean()
        point.save()
        self.counts['moved'] += 1
        self.stdout.write(f'  + {point.name or point}: {was} -> {here.y:.6f}, {here.x:.6f}, {point.altitude} m'
                          + (f' ({point.source})' if point.source != self.lidar else ''))

    def relink(self, links):
        """
        New key cols and parents for a group of summits at once. A key col belongs to one
        summit, so cols trading places are let go of first and then handed out.

        A link may carry the summit's new isolation too (`isolation`: parent, nhp, and a
        `source` if not the lidar), checked together with the rest: two summits trading places
        in height can change neither their parents nor their isolations one at a time, since
        each would point at the lower one for a moment.
        """
        def near(model, at):
            here = Point(at['lon'], at['lat'], srid=4326)
            return (model.objects.filter(point__location__dwithin=(here, SAME_PLACE))
                    .annotate(d=Distance('point__location', here)).order_by('d').first())

        def by_pk(model, pk, name, at, what):
            obj = model.objects.filter(pk=pk, point__name=name).select_related('point').first()
            return obj and self.checked(obj, obj.point, name, at, what)
        todo, created = [], []
        for link in links:
            # A summit or col this tooling created has no pk in common with the server: by position.
            summit = (near(Summit, link['summit_at']) if 'summit_at' in link
                      else by_pk(Summit, link['summit'], link['summit_name'], link.get('summit_was'), 'summit'))
            k = link['key_col']
            if 'new' in k:
                col = near(Col, k['new']) or Col(point=None)
            elif 'at' in k:
                col = near(Col, k['at'])
            else:
                col = Col.objects.filter(point__pk=k['point'], point__name=k['name']).select_related('point').first()
                col = col and self.checked(col, col.point, k['name'], k.get('was'), 'col')
            if 'parent_at' in link:
                parent = near(Summit, link['parent_at'])
            elif 'parent' in link:
                parent = link['parent'] and by_pk(Summit, link['parent']['pk'], link['parent']['name'],
                                                  link['parent'].get('was'), 'summit')
            else:
                parent = summit.prominence_parent if summit else None
            wanted = 'parent_at' in link or ('parent' in link and link['parent'])
            if summit is None or col is None or (wanted and parent is None):
                self.stdout.write(f'  ! {link.get("summit_name")}: summit, key col or parent not found as such: skipped')
                self.counts['skipped'] += 1
                continue
            iso = link.get('isolation')
            if iso:
                iso = dict(iso, parent=self.summit_spec(iso['parent']), nhp=Point(iso['nhp'][1], iso['nhp'][0], srid=4326))
                if iso['parent'] is None:
                    self.stdout.write(f'  ! {link.get("summit_name")}: isolation parent not found as such: skipped')
                    self.counts['skipped'] += 1
                    continue
                if (summit.isolation_parent_id == iso['parent'].pk and summit.nearest_higher_point is not None
                        and summit.nearest_higher_point.distance(iso['nhp']) < 1e-7):
                    iso = None                       # it has it already
            if col.pk and summit.key_col_id == col.pk and summit.prominence_parent_id == (parent and parent.pk) \
                    and not iso:
                self.counts['skipped'] += 1
                continue
            if not col.pk:
                col = Col.objects.create(point=self.point(k['new'], f'the new key col of {summit.point}'))
                created.append(col)
            todo.append((summit, col, parent, iso))
        # Each summit's own col, to give back if its relink is refused: the cols are let go of
        # all at once below, so a refused summit would otherwise be left with none.
        before = {s.pk: s.key_col_id for s, _, _, _ in todo}
        Summit.objects.filter(pk__in=[s.pk for s, _, _, _ in todo]).update(key_col=None)
        refused = []
        for summit, col, parent, iso in todo:
            summit.refresh_from_db()
            if parent is not None:
                # It may itself have been relinked a moment ago: check cycles against that.
                parent.refresh_from_db()
            if before.get(summit.pk) != col.pk:
                # A line parent was found across the key col it had: no longer this one.
                summit.line_parent = summit.line_cutoff = summit.line_source = None
            summit.key_col, summit.prominence_parent = col, parent
            summit.prominence_source = self.lidar if parent else None
            if iso:
                if summit.isolation_parent_id != iso['parent'].pk:
                    summit.isolation_name = None
                summit.nearest_higher_point, summit.isolation_parent = iso['nhp'], iso['parent']
                summit.isolation_source = Source.objects.get_or_create(name=iso['source'])[0] if iso.get('source') else self.lidar
            try:
                summit.full_clean()
            except ValidationError as e:
                # Left as it was, for a person: the change would break an invariant here.
                self.stdout.write(f'  ! {summit.point}: {"; ".join(e.messages)} Not relinked.')
                refused.append(summit)
                self.counts['skipped'] += 1
                continue
            summit.save()
            self.counts['relinked'] += 1
            self.stdout.write(f'  + {summit.point}: key col {col.point}, parent {parent.point if parent else None}, '
                              f'P {summit.compute_prominence():.1f}')
        for summit in refused:
            old = before[summit.pk]
            if old is not None and not Summit.objects.filter(key_col_id=old).exists():
                Summit.objects.filter(pk=summit.pk).update(key_col_id=old)
            elif old is not None:
                self.stdout.write(f'  ! {summit.point}: its old key col went to another summit; left without one')
        for col in created:
            if not Summit.objects.filter(key_col=col).exists():
                point = col.point
                col.delete()
                point.delete()

    def remove_col(self, at):
        """A col this tooling made that nothing needs any more: one it duplicated, say."""
        here = Point(at['lon'], at['lat'], srid=4326)
        col = Col.objects.filter(point__location__dwithin=(here, SAME_PLACE), key_for__isnull=True,
                                 point__source=self.lidar).select_related('point').first()
        if col is None:
            self.counts['skipped'] += 1
            return
        self.stdout.write(f'  - col {col.point}: removed')
        point = col.point
        col.delete()
        point.delete()
        self.counts['removed'] += 1

    def remove_summit(self, r):
        """
        A summit this tooling made that is none: a top only because the data stops there (the
        ground rises on beyond a border). By position, unnamed and from the lidar only; refused
        while it is any summit's prominence parent or anything else refers to it. A summit it
        was the isolation parent of keeps its nearest higher point, which is real ground, and
        loses only the attribution, with a note saying why. Its key col goes too if nothing
        else needs it.
        """
        here = Point(r['at'][1], r['at'][0], srid=4326)
        s = (Summit.objects.filter(point__location__dwithin=(here, D(m=2)), point__name__isnull=True,
                                   point__source=self.lidar).select_related('point', 'key_col__point').first())
        if s is None:
            self.counts['skipped'] += 1           # gone already, or not what the changeset saw
            return
        point, key_col = s.point, s.key_col
        blockers = []
        # Derived rows (the map's zooms) go with the summit; isolation children are handled below.
        for obj, allowed in ((s, {('Summit', 'isolation_parent'), ('SummitMapZoom', 'summit')}),
                             (point, {('PointName', 'point'), ('Note', 'point')})):
            for rel in obj._meta.related_objects:
                if (rel.related_model.__name__, rel.field.name) in allowed or rel.related_model._meta.auto_created:
                    continue
                if rel.one_to_one and rel.related_model is Summit:
                    continue                         # the summit itself, on its point
                if rel.related_model._default_manager.filter(**{rel.field.name: obj}).exists():
                    blockers.append(f'{rel.related_model.__name__}.{rel.field.name}')
        if blockers:
            self.stdout.write(f'  ! unnamed summit at {r["at"]}: still referred to by {", ".join(blockers)}: not removed')
            self.counts['skipped'] += 1
            return
        for child in Summit.objects.filter(isolation_parent=s).select_related('point'):
            child.isolation_parent, child.isolation_name = None, None
            child.full_clean()
            child.save()
            Note.objects.create(point=child.point, author=self.author, text=(
                'Isolation parent cleared: it was an unnamed top at '
                f'{r["at"][0]:.5f}, {r["at"][1]:.5f} that is no summit ({r["why"]}). '
                'The nearest higher point stands; whose ground it is remains to be found.'))
            self.stdout.write(f'  - {child.point}: isolation parent cleared')
        s.delete()
        point.delete()
        self.counts['summits_removed'] += 1
        self.stdout.write(f'  - unnamed summit at {r["at"]}: removed ({r["why"]})')
        if key_col is not None and key_col.point.source == self.lidar and not Summit.objects.filter(key_col=key_col).exists():
            col_point = key_col.point
            key_col.delete()
            col_point.delete()
            self.counts['removed'] += 1

    # ------------------------------------------------------------------ duplicates

    def merge(self, m):
        """
        Two points for one place: `drop` goes, `keep` stays and takes over whatever referred to
        the other — parents, children, rivers, notes — and the names it did not have. With
        `take_position`, also the dropped point's position, height and source: the duplicate
        was the one corrected from the lidar. A col still some summit's key col is not dropped.
        Either side can be given by position (`at`, [lat, lon]): a summit standing there. With
        `drop_key_col`, the dropped summit's key col goes too, if the lidar made it and nothing
        else needs it (the dip between two tops of one hill).
        """
        drop = (self.summit_point_at(m['drop']['at']) if 'at' in m['drop']
                else NamedPoint.objects.filter(pk=m['drop']['point'], name=m['drop']['name']).first())
        # A point this tooling made has no pk in common with the server: by its name alone, or
        # by position (an unnamed top).
        if 'at' in m['keep']:
            keep = self.summit_point_at(m['keep']['at'])
        elif m['keep']['point'] is None:
            keep = NamedPoint.objects.filter(name=m['keep']['name']).first()
        else:
            keep = NamedPoint.objects.filter(pk=m['keep']['point'], name=m['keep']['name']).first()
        if keep is None and m.get('take_name') and drop is None:
            self.counts['skipped'] += 1          # done already: the name has moved
            return
        if drop is None:
            self.counts['skipped'] += 1           # gone already
            return
        if keep is None:
            self.stdout.write(f'  ! keep #{m["keep"]["point"]} "{m["keep"]["name"]}" not found as such: skipped')
            self.counts['skipped'] += 1
            return
        col = Col.objects.filter(point=drop).first()
        if col is not None and Summit.objects.filter(key_col=col).exists():
            self.stdout.write(f'  ! {drop} is still a key col: not dropped')
            self.counts['skipped'] += 1
            return
        # Whatever points at the duplicate, its summit or its col, now points at the keeper's.
        pairs = [(drop, keep)]
        dropped_summit = Summit.objects.filter(point=drop).first()
        if dropped_summit is not None:
            kept_summit = Summit.objects.filter(point=keep).first()
            if kept_summit is None:
                self.stdout.write(f'  ! {drop} is a summit and {keep} is not: skipped')
                self.counts['skipped'] += 1
                return
            pairs.append((dropped_summit, kept_summit))
        if col is not None:
            kept_col = Col.objects.filter(point=keep).first()
            if kept_col is not None:
                pairs.append((col, kept_col))
        # Names first: the same name in the same language would otherwise be there twice.
        for n in PointName.objects.filter(point=drop):
            if PointName.objects.filter(point=keep, language=n.language, name=n.name).exists():
                n.delete()
        for old, new in pairs:
            for rel in old._meta.related_objects:
                if rel.one_to_many and not rel.related_model._meta.auto_created:     # every FK to it
                    rel.related_model._default_manager.filter(**{rel.field.name: old}).update(**{rel.field.name: new})
        if m.get('take_position'):
            keep.location, keep.altitude = drop.location, drop.altitude
            keep.altitude_datum, keep.source = drop.altitude_datum, drop.source
        keep.full_clean()
        keep.save()
        key_col = dropped_summit.key_col if dropped_summit else None
        name, drop_pk = drop.name, drop.pk
        if dropped_summit is not None:
            dropped_summit.delete()
        if col is not None:
            col.delete()
        drop.delete()
        if m.get('drop_key_col') and key_col is not None and key_col.point.source == self.lidar \
                and not Summit.objects.filter(key_col=key_col).exists():
            # The dip between two tops of one summit: nothing else needs it.
            point = key_col.point
            key_col.delete()
            point.delete()
            key_col = None
        if m.get('take_name') and name:
            # The kept point had a numbered name only because the dropped one held the plain one.
            keep.name = name
            keep.full_clean()
            keep.save(update_fields=['name'])
        self.counts['merged'] += 1
        self.stdout.write(f'  - {m["drop"]["name"] or "unnamed"} #{drop_pk} merged into '
                          f'{keep.name or "unnamed"} #{keep.pk}'
                          + (f' (its key col {key_col.point} left free)' if key_col else ''))

    def summit_point_at(self, at):
        """The point of the summit standing exactly at `at` ([lat, lon]), or None."""
        here = Point(at[1], at[0], srid=4326)
        s = (Summit.objects.filter(point__location__dwithin=(here, D(m=2))).select_related('point')
             .annotate(d=Distance('point__location', here)).order_by('d').first())
        return s.point if s else None

    # ------------------------------------------------------------------ line parents

    def line_parent(self, lp):
        """
        A summit's line parent (`Summit.line_parent`), both found where they stand
        (`summit_at`, `parent_at`: [lat, lon], within 2 m) — numbers are not shared with
        another database. `parent_at` null records that there is none (an island high point).
        `cutoff`: the least prominence the walk counted; `source`, if given, the elevation data,
        else the changeset's lidar source. A summit or parent not found is skipped, and so is
        one already linked so.
        """
        summit_point = self.summit_point_at(lp['summit_at'])
        summit = summit_point and Summit.objects.select_related('point').get(point=summit_point)
        if summit is None:
            self.stdout.write(f'  ! line parent: no summit at {lp["summit_at"]}: skipped')
            self.counts['skipped'] += 1
            return
        parent = None
        if lp.get('parent_at'):
            parent_point = self.summit_point_at(lp['parent_at'])
            parent = parent_point and Summit.objects.select_related('point').get(point=parent_point)
            if parent is None:
                self.stdout.write(f'  ! line parent of {summit.point}: no summit at {lp["parent_at"]}: skipped')
                self.counts['skipped'] += 1
                return
        source = Source.objects.get_or_create(name=lp['source'])[0] if lp.get('source') else self.lidar
        if (summit.line_parent_id == (parent and parent.pk) and summit.line_cutoff == lp['cutoff']
                and summit.line_source_id == source.pk):
            self.counts['skipped'] += 1
            return
        summit.line_parent, summit.line_cutoff, summit.line_source = parent, lp['cutoff'], source
        try:
            summit.full_clean()
        except ValidationError as e:
            self.stdout.write(f'  ! {summit.point}: {"; ".join(e.messages)} Line parent not set.')
            self.counts['skipped'] += 1
            return
        summit.save()
        self.counts['line_parents'] += 1

    # ------------------------------------------------------------------ isolation

    def summit_spec(self, spec):
        """ A summit given as one this changeset made (`new`), by position (`at`), or by number. """
        if 'new' in spec:
            return self.new.get(spec['new'])
        if 'at' in spec:
            return Summit.objects.filter(point__location__dwithin=(
                Point(spec['at'][1], spec['at'][0], srid=4326), SAME_PLACE)).first()
        s = Summit.objects.filter(pk=spec['pk'], point__name=spec['name']).select_related('point').first()
        return s and self.checked(s, s.point, spec['name'], spec.get('was'), 'summit')

    def isolation(self, iso):
        """
        A summit's nearest higher point and isolation parent, from the lidar. Both summits may
        be ones this tooling made (a split's twin top), so either can be given by position.
        The description of the ground (`isolation_name`) is cleared when the parent changes:
        "northern ridge of X" no longer says anything once it is not X's.
        """
        s, parent = self.summit_spec(iso['summit']), self.summit_spec(iso['parent'])
        if s is None or parent is None:
            self.stdout.write(f'  ! isolation of {iso["summit"]}: summit or parent not found as such: skipped')
            self.counts['skipped'] += 1
            return
        nhp = Point(iso['nhp'][1], iso['nhp'][0], srid=4326)
        if (s.isolation_parent_id == parent.pk and s.nearest_higher_point is not None
                and s.nearest_higher_point.distance(nhp) < 1e-7):
            self.counts['skipped'] += 1
            return
        if s.isolation_parent_id != parent.pk:
            s.isolation_name = None
        # A measurement carried over from elsewhere keeps its own source.
        source = Source.objects.get_or_create(name=iso['source'])[0] if iso.get('source') else self.lidar
        s.nearest_higher_point, s.isolation_parent, s.isolation_source = nhp, parent, source
        try:
            s.full_clean()
        except ValidationError as e:
            self.stdout.write(f'  ! {s.point}: {"; ".join(e.messages)} Isolation not set.')
            self.counts['skipped'] += 1
            return
        s.save()
        self.counts['isolations'] += 1
        self.stdout.write(f'  + {s.point}: isolation {iso["metres"]:.0f} m, parent {parent.point}')

    # ------------------------------------------------------------------ names

    def promote(self, pr):
        """
        A name to the higher of two tops: the summit the name stood on was the lower one.
        The higher (`higher`, by position: a split's twin) takes the name, its names in other
        languages and notes, and whatever referred to the named summit or its point — other
        summits' parents, ranges, rivers — except the lower top's own link to it.
        """
        lower = Summit.objects.filter(pk=pr['lower']['pk'], point__name=pr['lower']['name']).select_related('point').first()
        lower = lower and self.checked(lower, lower.point, pr['lower']['name'], pr['lower'].get('was'), 'summit')
        at = Point(pr['higher']['at'][1], pr['higher']['at'][0], srid=4326)
        higher = Summit.objects.filter(point__location__dwithin=(at, SAME_PLACE)).select_related('point').first()
        if lower is None:
            if higher is not None and higher.point.name == pr['lower']['name']:
                self.counts['skipped'] += 1         # done already
            else:
                self.stdout.write(f'  ! {pr["lower"]["name"]} not found as such: skipped')
                self.counts['skipped'] += 1
            return
        if higher is None or higher.point.name is not None:
            self.stdout.write(f'  ! the higher top of {lower.point} not found unnamed: skipped')
            self.counts['skipped'] += 1
            return
        if higher.point.altitude <= lower.point.altitude:
            self.stdout.write(f'  ! {higher.point} is not higher than {lower.point}: skipped')
            self.counts['skipped'] += 1
            return
        for old, new in ((lower, higher), (lower.point, higher.point)):
            for rel in old._meta.related_objects:
                if rel.one_to_many and not rel.related_model._meta.auto_created:
                    qs = rel.related_model._default_manager.filter(**{rel.field.name: old})
                    if rel.related_model is Summit:
                        qs = qs.exclude(pk=lower.pk).exclude(pk=higher.pk)
                    qs.update(**{rel.field.name: new})
        name = lower.point.name
        lower.point.name = None
        lower.point.save(update_fields=['name'])
        higher.point.name = name
        higher.point.full_clean()
        higher.point.save(update_fields=['name'])
        self.counts['promoted'] += 1
        self.stdout.write(f'  + {name} is now the higher top, {higher.point}; the lower one is {lower.point}')
