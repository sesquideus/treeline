from django.db import migrations

DATUMS = [
    ('Bpv', 'Baltic 1957 (Baltský výškový systém po vyrovnaní)', 'EPSG:4258+8357', 'normal',
     'Slovakia and Czechia. Converted through GKÚ (sk_gku_Slovakia_ETRS89h_to_Baltic1957) '
     'or ČÚZK (cz_cuzk_CR-2005) grids, by location.'),
    ('PL-EVRF2007', 'PL-EVRF2007-NH', 'EPSG:4258+9651', 'normal',
     'Poland since 2019/2020; geoportal.gov.pl. Grid pl_gugik_geoid2021-PL-EVRF2007-NH.'),
    ('PL-KRON86', 'PL-KRON86-NH (Baltic 1986)', 'EPSG:4258+9650', 'normal',
     'Poland before 2020; older Polish maps. Grid pl_gugik_geoid2011-PL-KRON86-NH.'),
    ('EVRF2007', 'European Vertical Reference Frame 2007', 'EPSG:4258+5621', 'normal', ''),
    ('EGM2008', 'EGM2008 geoid', 'EPSG:4326+3855', 'orthometric',
     "TreeLine's canonical system. Grid us_nga_egm08_25."),
    ('EGM96', 'EGM96 geoid', 'EPSG:4326+5773', 'orthometric',
     'Older GPS units, SRTM.'),
    ('ETRS89-h', 'ETRS89 ellipsoidal height', 'EPSG:4937', 'ellipsoidal',
     'GNSS without a geoid; the Slovak lidar DMR 5.0.'),
]


def seed(apps, schema_editor):
    VerticalDatum = apps.get_model('core', 'VerticalDatum')
    for code, name, crs, kind, notes in DATUMS:
        VerticalDatum.objects.update_or_create(
            code=code, defaults=dict(name=name, crs=crs, kind=kind, notes=notes))


class Migration(migrations.Migration):
    dependencies = [('core', '0004_vertical_datum')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
