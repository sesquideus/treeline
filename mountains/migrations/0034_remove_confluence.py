# Hand-written: the data step has to run before the field it reads is dropped.

from django.contrib.gis.db.models.functions import Distance
from django.db import migrations


def confluences_into_rivers(apps, schema_editor):
    """
    Carry each col's confluence over to `confluence_river` before the link is dropped.

    `Confluence` was a first attempt at recording where water goes; a river's `mouth` is now
    a `NamedPoint` and says the same thing better. But 56 cols name a confluence and *no*
    river, and dropping the field would strand them, so each is matched to the river whose
    mouth sits on that confluence.

    Matched on position first, since confluence points are named "Poprad -> Dunajec" and
    river names repeat — name alone pairs some of them with a river 487 km away. 50 m is
    deliberately tight, because beyond that the nearest mouth is as likely to be a different
    junction, and a wrong river is worse than a missing one.

    A second pass then accepts a looser 1 km when the *name* agrees as well: a confluence
    called "Varínka -> Váh" whose nearest mouth is Varínka's, 90 m off, is that junction
    whatever the tolerance says. Neither signal is trusted alone; together they are enough.

    Whatever is left over is printed for someone to settle by hand.
    """
    Col = apps.get_model('mountains', 'Col')
    River = apps.get_model('mountains', 'River')

    rivers = River.objects.exclude(mouth__isnull=True)
    stranded = (Col.objects
                .filter(confluence__isnull=False, confluence_river__isnull=True)
                .select_related('confluence__point', 'point'))

    carried, unresolved = [], []
    for col in stranded:
        point = col.confluence.point
        if point is None or point.location is None:
            unresolved.append((col, None, None))
            continue
        nearest = (rivers.annotate(distance=Distance('mouth__location', point.location))
                   .order_by('distance').select_related('source').first())
        if nearest is None:
            unresolved.append((col, point, None))
            continue

        close = nearest.distance.m <= 50
        named = (point.name or '').split('->')[0].strip()
        agrees = (named and nearest.source is not None
                  and nearest.source.name == named and nearest.distance.m <= 1000)
        if close or agrees:
            col.confluence_river_id = nearest.pk
            carried.append(col)
        else:
            unresolved.append((col, point, nearest))

    Col.objects.bulk_update(carried, ['confluence_river'])

    # The updates above queue deferred foreign-key trigger events on `mountains_col`, and
    # PostgreSQL refuses DDL on a table with any pending — the `RemoveField` below would
    # fail with "cannot ALTER TABLE ... because it has pending trigger events". Firing them
    # now empties the queue; they would all have fired at COMMIT anyway.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    print(f'  carried {len(carried)} cols from confluence to confluence_river')
    if unresolved:
        print(f'  {len(unresolved)} could not be matched to a river mouth within 50 m '
              f'and now have neither; they were:')
        for col, point, nearest in unresolved:
            where = point.name if point is not None and point.name else '—'
            how_far = (f'nearest mouth {nearest.distance.m / 1000:.1f} km away'
                       if nearest is not None and point is not None else 'no position')
            print(f'    col {col.pk}: confluence "{where}" — {how_far}')


class Migration(migrations.Migration):
    """
    Remove `Confluence`, superseded by a river's `mouth` being a `NamedPoint`.

    The 52 confluence points themselves are left in place rather than deleted: they are real
    named places with countries on them, and they keep their own pages at `/point/<pk>/`.
    Only the `Confluence` wrapper and the link from `Col` go.

    Not reversible in any useful sense — unapplying restores the table and the column, but
    the rows are gone, so the reverse is a schema-only rollback.
    """

    dependencies = [
        ('mountains', '0033_river_mouth_namedpoint'),
    ]

    operations = [
        migrations.RunPython(confluences_into_rivers, migrations.RunPython.noop,
                             elidable=False),
        migrations.RemoveField(model_name='col', name='confluence'),
        migrations.DeleteModel(name='Confluence'),
    ]
