from django.db import migrations
from django.contrib.gis.geos import Point


def forwards(apps, schema_editor):
    NamedPoint = apps.get_model("mountains", "NamedPoint")
    qs = NamedPoint.objects.filter(
        location__isnull=True,
        latitude__isnull=False,
        longitude__isnull=False,
    )
    for p in qs.iterator():
        p.location = Point(p.longitude, p.latitude, srid=4326)
        p.save(update_fields=["location"])


def backwards(apps, schema_editor):
    NamedPoint = apps.get_model("mountains", "NamedPoint")
    NamedPoint.objects.update(location=None)


class Migration(migrations.Migration):
    dependencies = [
        ('mountains', '0007_namedpoint_location'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]