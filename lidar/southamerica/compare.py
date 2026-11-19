"""South America P600 (Copernicus) against the DB, the andes.org.uk 6000 m list and OSM names."""
import json, math, os, re, sys
import numpy as np
from shapely import wkt
from shapely.geometry import Point
D = sys.argv[1]
P = json.load(open(os.path.expanduser(sys.argv[2])))
L = []
db = json.load(open(f'{D}/db_sa.json'))
osm = json.load(open(f'{D}/osm_sa.json'))['elements']
chile = wkt.loads(open(f'{D}/chile.wkt').read())
olat = np.array([e['lat'] for e in osm]); olon = np.array([e['lon'] for e in osm])
def km(a, b): return math.hypot((a[0]-b[0])*110.574, (a[1]-b[1])*111.32*math.cos(math.radians(a[0])))
def kms(lat, lon):
    return np.hypot((olat - lat) * 110.574, (olon - lon) * 111.32 * math.cos(math.radians(lat)))
def ele(e):
    try: return float(re.sub(r'[^\d.]', '', e['tags'].get('ele', '')) or 'nan')
    except ValueError: return float('nan')
plat = np.array([x['lat'] for x in P]); plon = np.array([x['lon'] for x in P])
def nearest_p(lat, lon, r):
    d = np.hypot((plat - lat) * 110.574, (plon - lon) * 111.32 * math.cos(math.radians(lat)))
    i = int(np.argmin(d))
    return (P[i], float(d[i])) if d[i] <= r else (None, None)
# names: the nearest named OSM peak within 1.5 km, preferring one whose ele is near
for x in P:
    d = kms(x['lat'], x['lon'])
    near = np.flatnonzero(d < 1.5)
    if len(near):
        best = min(near, key=lambda i: (d[i] + (0 if ele(osm[i]) != ele(osm[i]) else abs(ele(osm[i]) - x['h']) / 200)))
        e = osm[best]
        x['name'] = e['tags']['name']; x['osm_ele'] = e['tags'].get('ele'); x['osm_km'] = round(float(d[best]), 2)
    x['chile'] = chile.contains(Point(x['lon'], x['lat']))
    x['near_chile_km'] = None
# the 6000 m list: located by OSM, matched to a P600 within 3 km
for y in L:
    if 'osm_lat' in y:
        x, d = nearest_p(y['osm_lat'], y['osm_lon'], 3)
    else:
        # placed by name alone: an OSM namesake with a Copernicus summit of the right height by it
        x = d = None
        for e in osm:
            if y['name'].split(' (')[0].lower() in e['tags']['name'].lower():
                c, dd = nearest_p(e['lat'], e['lon'], 3)
                if c and abs(c['h'] - (y['height'] or 6000)) < 150:
                    x, d = c, dd
                    break
    y['p600'] = [x['lat'], x['lon']] if x else None
    y['p600_km'] = round(d, 2) if x else None
    if x:
        x['list'] = dict(rank=y['rank'], name=y['name'], height=y['height'], approx=y['approx'])
for s in db:
    x, d = nearest_p(s['lat'], s['lon'], 3)
    s['p600'] = x and dict(lat=x['lat'], lon=x['lon'], h=x['h'], prom=x['prom'], col_h=x.get('col_h'), parent=x.get('parent'), km=round(d, 2))
    if x:
        x['db'] = dict(pk=s['pk'], name=s['name'], alt=s['alt'])
json.dump(dict(p600=P, list=L, db=db), open(f'{D}/sa_cmp.json', 'w'), ensure_ascii=False, indent=0)
named = sum(1 for x in P if 'name' in x)
print(len(P), 'P600;', named, 'named;', sum(1 for x in P if x['chile']), 'in Chile;',
      sum(1 for y in L if y['p600']), 'of', len(L), 'list peaks matched;', sum(1 for s in db if s['p600']), 'of', len(db), 'DB summits')
