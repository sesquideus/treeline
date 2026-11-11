"""
Whatever parent is still unsourced falls back to "guess".

"guess" is the source for a value with nothing better behind it, and that is what these
are: after 0036 credited the Alpine prominence parents to Peaklist, 41 prominence parents
and 53 isolation parents remained with no source at all — the Andes, the Caucasus, the
Carpathians, scattered Slovak summits. Saying "guess" is honest about them, and it is
what lets the constraints that a parent must carry a source go in after this.

Listed by pk, as in 0036, and only rows still unsourced are touched; the reverse clears
only rows that still say "guess".

The opposite case goes too: Vysoká in the Kremnické vrchy cites Freemap for a prominence it
has no parent to measure against, so the citation describes nothing and is removed. The
reverse puts it back.
"""

from django.db import migrations

SOURCE = 'guess'

PROMINENCE = [
    14, 18, 19, 20, 29, 30, 34, 42, 46, 53, 63, 65, 68, 69, 73, 74, 76, 77, 78, 80, 94,
    125, 288, 465, 517, 564, 647, 668, 673, 1670, 1978, 2124, 2157, 2161, 2204, 2218,
    2533, 2607, 2630, 2755, 2767,
]

#: Vysoká (Kremnické vrchy): a prominence source with no prominence parent.
SOURCE_WITHOUT_PARENT = 650
CITED_FOR_IT = 'manual measurement from Freemap Slovakia'

ISOLATION = [
    14, 17, 18, 19, 20, 22, 29, 30, 33, 34, 36, 42, 46, 53, 56, 57, 63, 65, 68, 69, 73,
    74, 76, 77, 254, 315, 332, 333, 455, 519, 523, 595, 2081, 2138, 2139, 2216, 2234,
    2259, 2318, 2332, 2347, 2354, 2478, 2481, 2486, 2543, 2705, 2728, 2806, 2866, 2883,
    2991, 2992,
]


def cite_guess(apps, schema_editor):
    Source = apps.get_model('mountains', 'Source')
    Summit = apps.get_model('mountains', 'Summit')
    prominence = Summit.objects.filter(pk__in=PROMINENCE, prominence_parent__isnull=False,
                                       prominence_source__isnull=True)
    isolation = Summit.objects.filter(pk__in=ISOLATION, isolation_parent__isnull=False,
                                      isolation_source__isnull=True)
    if prominence.exists() or isolation.exists():   # a fresh database has none of this
        # .get(): refuse, rather than invent a source the data is not from
        guess = Source.objects.get(name=SOURCE)
        prominence.update(prominence_source=guess)
        isolation.update(isolation_source=guess)
    (Summit.objects
        .filter(pk=SOURCE_WITHOUT_PARENT, prominence_parent__isnull=True)
        .update(prominence_source=None))


def uncite_guess(apps, schema_editor):
    Source = apps.get_model('mountains', 'Source')
    Summit = apps.get_model('mountains', 'Summit')
    guess = Source.objects.filter(name=SOURCE)
    Summit.objects.filter(pk__in=PROMINENCE, prominence_source__in=guess).update(prominence_source=None)
    Summit.objects.filter(pk__in=ISOLATION, isolation_source__in=guess).update(isolation_source=None)
    (Summit.objects
        .filter(pk=SOURCE_WITHOUT_PARENT, prominence_parent__isnull=True,
                prominence_source__isnull=True)
        .update(prominence_source=Source.objects.filter(name=CITED_FOR_IT).first()))


class Migration(migrations.Migration):
    dependencies = [
        ('mountains', '0036_cite_peaklist_for_alpine_prominence'),
    ]

    operations = [
        migrations.RunPython(cite_guess, uncite_guess),
    ]
