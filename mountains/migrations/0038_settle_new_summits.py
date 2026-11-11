"""
Five summits entered on the server after 0036 and 0037 were written, which the constraints
in 0039 would refuse.

Three had an isolation parent but no isolation source: the measurement was made on Freemap,
as their prominence was. Two in the Andes had a prominence source with no prominence parent
and no key col — the source described nothing yet — and lose it. The reverse restores both.
"""

from django.db import migrations

FREEMAP = 'manual measurement from Freemap Slovakia'

#: Isolation measured on Freemap: Großer Rachel, Hufberg, Kłodzka Góra.
ISOLATION_FROM_FREEMAP = [3050, 3051, 3054]

#: A prominence source with no parent to measure against, and what it cited.
PROMINENCE_WITHOUT_PARENT = {
    3062: 'Wikipedia',              # Aucanquilcha
    3068: 'Google Earth (manual)',  # Risco Plateado
}


def settle(apps, schema_editor):
    Source = apps.get_model('mountains', 'Source')
    Summit = apps.get_model('mountains', 'Summit')
    unsourced = Summit.objects.filter(pk__in=ISOLATION_FROM_FREEMAP,
                                      isolation_parent__isnull=False,
                                      isolation_source__isnull=True)
    if unsourced.exists():  # a fresh database has none of this
        # .get(): refuse, rather than invent a source the data is not from
        unsourced.update(isolation_source=Source.objects.get(name=FREEMAP))
    (Summit.objects
        .filter(pk__in=PROMINENCE_WITHOUT_PARENT, prominence_parent__isnull=True)
        .update(prominence_source=None))


def unsettle(apps, schema_editor):
    Source = apps.get_model('mountains', 'Source')
    Summit = apps.get_model('mountains', 'Summit')
    (Summit.objects
        .filter(pk__in=ISOLATION_FROM_FREEMAP, isolation_source__name=FREEMAP)
        .update(isolation_source=None))
    for pk, name in PROMINENCE_WITHOUT_PARENT.items():
        (Summit.objects
            .filter(pk=pk, prominence_parent__isnull=True, prominence_source__isnull=True)
            .update(prominence_source=Source.objects.filter(name=name).first()))


class Migration(migrations.Migration):
    dependencies = [
        ('mountains', '0037_cite_guess_for_unsourced_parents'),
    ]

    operations = [
        migrations.RunPython(settle, unsettle),
    ]
