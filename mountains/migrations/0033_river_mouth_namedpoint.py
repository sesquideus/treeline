# Hand-written; see the Migration docstring for why `makemigrations` is not enough.

import django.db.models.deletion
from django.db import migrations, models


def mouths_into_points(apps, schema_editor):
    """
    Turn each river's `mouth` + `mouth_altitude` into a `NamedPoint`.

    Deduplicated by position *and altitude*, because a mouth is a place and two rivers can
    end at the same one — Kňazov potok joins Lomniansky potok exactly where Lomniansky potok
    joins Neresnica. One row for that junction is the whole reason this is a ForeignKey.

    Altitude is part of the key, not just position, so that nothing silently changes a
    recorded number. Two cases in the current data need it:

      * Kňazov potok and Lomniansky potok share that junction but recorded its altitude
        0.5 m apart. Matching on position alone would hand one of them the other's figure.
      * Melčický potok's mouth coordinates are identical to its own *source* coordinates —
        a data error — and the point already sitting there is its source, 548 m higher.
        Reusing it would move the mouth half a kilometre uphill.

    Both therefore get their own point, preserving exactly what was recorded, and both are
    reported below for someone to reconcile by hand.
    """
    River = apps.get_model('mountains', 'River')
    NamedPoint = apps.get_model('mountains', 'NamedPoint')

    rivers = list(River.objects.filter(mouth__isnull=False).order_by('pk'))
    missing = [r.pk for r in rivers if r.mouth_altitude is None]
    if missing:
        # `NamedPoint.altitude` is NOT NULL, so there is nowhere to put a mouth without one.
        # Refusing beats inventing a number: the migration is one transaction, so this rolls
        # the new column back and leaves the schema untouched.
        raise RuntimeError(
            f'rivers {missing} have a mouth position but no altitude, which a NamedPoint '
            f'cannot represent. Give them one, or clear the position, then migrate again.'
        )

    existing = {}
    for point in NamedPoint.objects.exclude(location__isnull=True).only('pk', 'location',
                                                                        'altitude'):
        key = (round(point.location.x, 6), round(point.location.y, 6), point.altitude)
        existing.setdefault(key, point.pk)

    # Positions that turn up more than once with disagreeing altitudes: each keeps its own
    # point, and the clash is printed rather than resolved.
    seen_positions, clashes = {}, []

    for river in rivers:
        position = (round(river.mouth.x, 6), round(river.mouth.y, 6))
        key = position + (river.mouth_altitude,)
        if position in seen_positions and seen_positions[position] != river.mouth_altitude:
            clashes.append((river.pk, position, seen_positions[position],
                            river.mouth_altitude))
        seen_positions.setdefault(position, river.mouth_altitude)

        if key not in existing:
            point = NamedPoint.objects.create(location=river.mouth,
                                              altitude=river.mouth_altitude)
            existing[key] = point.pk
        river.mouth_point_id = existing[key]
    River.objects.bulk_update(rivers, ['mouth_point'])

    for pk, position, first, second in clashes:
        print(f'  note: river {pk} ends at {position}, where another river also ends, but '
              f'they recorded {first} m and {second} m. Both kept their own point.')

    # The inserts above queue deferred foreign-key trigger events on `mountains_namedpoint`,
    # and PostgreSQL refuses DDL on a table that has any pending — the `RemoveField`s below
    # would fail with "cannot ALTER TABLE ... because it has pending trigger events". Firing
    # them now empties the queue; they would all have fired at COMMIT anyway, so this only
    # changes when they run, not whether.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def points_back_into_mouths(apps, schema_editor):
    """
    The reverse. Lossless for position and altitude; everything the point gained afterwards
    — its name, its countries, its notes — has nowhere to go and is dropped. The points
    themselves are left behind rather than deleted, because by then other things may
    reference them.
    """
    River = apps.get_model('mountains', 'River')
    rivers = list(River.objects.filter(mouth_point__isnull=False)
                  .select_related('mouth_point'))
    for river in rivers:
        river.mouth = river.mouth_point.location
        river.mouth_altitude = river.mouth_point.altitude
    River.objects.bulk_update(rivers, ['mouth', 'mouth_altitude'])


class Migration(migrations.Migration):
    """
    Move a river's mouth from a loose point + altitude onto a `NamedPoint`, so the junction
    can carry the countries it sits in and the name the river takes below it.

    `makemigrations` would write RemoveField + AddField and drop 967 positions and altitudes
    on the floor — the way 0029 lost `River.summit`. So the new column is added first, the
    values are carried across into real points, and only then are the old two removed.

    The rename at the end is what lets the field keep the name `mouth`: the column has to be
    added under a different name while the old one still exists.
    """

    dependencies = [
        ('mountains', '0032_river_watershed_high_point'),
    ]

    operations = [
        migrations.AddField(
            model_name='river',
            name='mouth_point',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='mouths', to='mountains.namedpoint'),
        ),
        migrations.RunPython(mouths_into_points, points_back_into_mouths, elidable=False),
        migrations.RemoveField(model_name='river', name='mouth'),
        migrations.RemoveField(model_name='river', name='mouth_altitude'),
        migrations.RenameField(model_name='river', old_name='mouth_point', new_name='mouth'),
    ]
