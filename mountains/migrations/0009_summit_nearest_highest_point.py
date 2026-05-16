
import django.contrib.gis.db.models.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mountains', '0008_populate_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='summit',
            name='nearest_highest_point',
            field=django.contrib.gis.db.models.fields.PointField(blank=True, geography=True, null=True, srid=4326),
        ),
    ]
