"""
The terrain models on disk: one record per source, with where its integer-centimetre tiles are (under
`data.ROOT`), what they are (resolution, grid, height datum, surveys, licence), and how its heights reach
EGM2008 (`heights`: the PROJ compound CRS of the source's heights over ETRS89, None where no free grid
converts them). `tileindex.py` indexes these, `coverage_map.py` maps them.

A source lives in the first of its `dirs` that exists: a download in progress sits in its own folder and
moves into intcm/ when done.
"""
import glob
import os

from data import ROOT

DHHN = 'DHHN2016 (EPSG:7837)'


def _de(st, name, years, crs='ETRS89 / UTM 32N (EPSG:25832)', tiles='1 km tiles', dirs=None, licence='dl-de/by-2-0'):
    return dict(key='DE-' + st, label=f'DGM1 {name}', country='DE', dirs=dirs or [f'intcm/DE/{st}'],
                pattern='tiles/*.tif', res=1.0, crs=crs, tiles=tiles, datum=DHHN, heights='EPSG:4258+7837',
                years=years, licence=licence, lidar=True)


SOURCES = [
    dict(key='SK', label='DMR 5.0', country='SK', dirs=['intcm/SK'], pattern='dmr5_etrs89.tif', res=1.0,
         crs='ETRS89 / UTM 34N (EPSG:3046)', tiles='one mosaic', datum='ETRS89 ellipsoidal (EPSG:4937)',
         heights='EPSG:4937', years='2017–2023', licence='CC BY 4.0 (ÚGKK SR)', lidar=True),
    dict(key='CZ', label='DMR 5G', country='CZ', dirs=['intcm/CZ'], pattern='*.tif', res=1.0,
         crs='ETRS89 / TM33N (EPSG:3045)', tiles='2 km tiles, triangulated from the points', datum='Bpv (EPSG:8357)',
         heights='EPSG:4258+8357', years='2009–2013', licence='CC BY 4.0 (ČÚZK)', lidar=True),
    dict(key='AT', label='ALS-DGM', country='AT', dirs=['intcm/AT'], pattern='*.tif', res=1.0,
         crs='ETRS89 / LAEA (EPSG:3035)', tiles='50 km tiles', datum='EVRF2000 Austria (EPSG:9274)',
         heights='EPSG:4258+9274', years='edition 2025-09', licence='CC BY 4.0 (BEV)', lidar=True),
    dict(key='PL-EVRF', label='NMT EVRF2007', country='PL', dirs=['intcm/PL/EVRF'], pattern='sheets/*.tif', res=1.0,
         crs='PUWG 1992 (EPSG:2180); PL-2000-named sheets EPSG:2176–2178', tiles='1:5000 sheets',
         datum='PL-EVRF2007-NH (EPSG:9651)', heights='EPSG:4258+9651', years='2018–2026', licence='free (GUGiK)',
         lidar=True),
    dict(key='PL-KRON86', label='NMT KRON86 (older)', country='PL', dirs=['intcm/PL/KRON86'], pattern='tiles/*.tif',
         res=1.0, crs='PUWG 1992 (EPSG:2180)', tiles='10 km tiles (WCS)', datum='PL-KRON86-NH (EPSG:9650)',
         heights='EPSG:4258+9650', years='2010–2015', licence='free (GUGiK)', lidar=True),
    _de('BY', 'Bayern', '2008–2024', licence='CC BY 4.0 (LDBV)'),
    _de('BW', 'Baden-Württemberg', '2000–2023', tiles='1 km tiles (2 km cells, XYZ)'),
    _de('HE', 'Hessen', '2008–2022', tiles='1 km squares (WCS)'),
    _de('NI', 'Niedersachsen', '2013–2024', licence='CC BY 4.0 (LGLN)'),
    _de('ST', 'Sachsen-Anhalt', '2008–2023', tiles='2 km tiles'),
    _de('SN', 'Sachsen', '2015–2023', crs='ETRS89 / UTM 33N (EPSG:25833)', tiles='2 km tiles'),
    _de('BB', 'Brandenburg', '2008–2023', crs='ETRS89 / UTM 33N (EPSG:25833)'),
    _de('NW', 'Nordrhein-Westfalen', '2022', licence='dl-de/zero-2.0'),
    _de('TH', 'Thüringen', '2014–2025', dirs=['intcm/DE/TH', 'DGM1_TH_cm']),
    dict(key='CH', label='swissALTI3D', country='CH', dirs=['intcm/CH'], pattern='tiles/*.tif', res=0.5,
         crs='CH1903+ / LV95 (EPSG:2056)', tiles='1 km tiles', datum='LN02 (EPSG:5728)', heights='EPSG:4258+5728',
         years='2019–2026', licence='swisstopo open data (© swisstopo)', lidar=True),
    dict(key='IT-TN', label='LiDAR PAT Trentino', country='IT', dirs=['intcm/IT/TN'], pattern='tiles/*.tif', res=0.5,
         crs='ETRS89 / UTM 32N (EPSG:25832)', tiles='500 m tiles', datum='ITALGEO2005 orthometric', heights=None,
         years='2013–2018', licence='CC BY 4.0 (PAT)', lidar=True),
    dict(key='IT-BZ', label='DTM Südtirol', country='IT', dirs=['intcm/IT/BZ'], pattern='tiles/*.tif', res=2.5,
         crs='ETRS89 / UTM 32N (EPSG:25832)', tiles='5 km blocks (WCS)', datum='provincial geoid, orthometric',
         heights=None, years='2004–2005', licence='CC0 (Provinz Bozen)', lidar=True),
    dict(key='IT-FVG', label='DTM PCR Friuli Venezia Giulia', country='IT', dirs=['intcm/IT/FVG'],
         pattern='tiles/*.tif', res=1.0, crs='RDN2008 / TM33 (EPSG:6708)', tiles='CTRN 1:5000 quarter sheets',
         datum='orthometric (Genova 1942)', heights=None, years='2006–2010', licence='IODL 2.0 (unconfirmed)',
         lidar=True),
    dict(key='IT-LOM', label='DTM5 Lombardia', country='IT', dirs=['intcm/IT/LOM'], pattern='tiles/*.tif', res=5.0,
         crs='WGS 84 / UTM 32N (EPSG:32632)', tiles='10 km blocks', datum='Genova 1942 orthometric', heights=None,
         years='2015 (not lidar in the mountains)', licence='CC BY 4.0 or BY-NC-ND (unconfirmed)', lidar=False),
    dict(key='IT-MASE', label='MASE PST lidar', country='IT', dirs=['intcm/IT/MASE', 'IT_MASE_cm'],
         pattern='tiles/*.tif', res=1.0, crs='WGS 84 lon/lat (EPSG:4326), 0.00001°', tiles='0.01° tiles',
         datum='orthometric (Genova 1942)', heights=None, years='2008–2012', licence='CC BY 4.0 (MASE)', lidar=True),
    dict(key='IT-MASE05', label='MASE PST lidar, South Tyrol 0.5 m', country='IT', dirs=['intcm/IT/MASE', 'IT_MASE_cm'],
         pattern='tiles05/*.tif', res=0.5, crs='WGS 84 lon/lat (EPSG:4326), 0.000005°', tiles='0.01° tiles, partial',
         datum='orthometric (Genova 1942)', heights=None, years='2013–2014 (not accepted by MASE)',
         licence='CC BY 4.0 (MASE)', lidar=True),
    dict(key='SI', label='LSS DMR1 (ARSO)', country='SI', dirs=['intcm/SI', 'SI_ARSO_cm'], pattern='tiles/*.tif',
         res=1.0, crs='D96 / TM (EPSG:3794)', tiles='1 km tiles', datum='SVS2000 Trieste (PROJ: si_gurs_SLO-VTP2024)',
         heights=None, years='2011–2015', licence='ARSO open data', lidar=True),
]
BY_KEY = {s['key']: s for s in SOURCES}
# Where two sources cover the same ground, the one a run takes: the finer first, then by this order
# (the newer survey before the older; a country's own survey before a national or foreign one).
PRIORITY = ['IT-MASE05', 'CH', 'IT-TN', 'SK', 'CZ', 'AT', 'PL-EVRF', 'PL-KRON86', 'SI', 'IT-FVG', 'IT-MASE']


def priority(key):
    """ Sort key: finer resolution first, then PRIORITY, then the catalogue's order. """
    s = BY_KEY[key]
    return (s['res'], PRIORITY.index(key) if key in PRIORITY else len(PRIORITY), SOURCES.index(s))


def folder(s):
    """ The source's folder: the first of its `dirs` that exists (else the first). """
    for d in s['dirs']:
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            return p
    return os.path.join(ROOT, s['dirs'][0])


def files(s):
    return sorted(glob.glob(os.path.join(folder(s), s['pattern'])))
