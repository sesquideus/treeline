"""
`p100.py`'s tops as a changeset: the ones the database lacks, with their key cols and parents.

    python3 lidar/southamerica/p100_changeset.py P100.json KNOWN.json OSM.json ADMIN0.geojson BURNED.json OUT.json

- Heights: a list height burned into the mosaic (andes.org.uk, or the database's own) where the
  top is one of those, else Copernicus GLO-30 (EGM2008).
- Names: the nearest OSM peak within 500 m, each name given to one top only, and none a database
  summit within 2 km already has.
- Parents: a database summit by where it stands (numbers are not shared with another database),
  a new one by its key. Countries from Natural Earth.
"""
import json
import math
import re
import sys
import unicodedata

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

P = json.load(open(sys.argv[1]))
known = json.load(open(sys.argv[2]))
osm = [e for e in json.load(open(sys.argv[3]))['elements'] if e.get('tags', {}).get('name')]
fs = [f for f in json.load(open(sys.argv[4]))['features']]
burned = json.load(open(sys.argv[5]))
OUT = sys.argv[6]
known_by_pk = {s['pk']: s for s in known}
LANG = dict(br='pt', gy='en', fk='en', gs='en', tt='en', sr='nl', fr='fr')


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110.574, (a[1] - b[1]) * 111.32 * k)


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"['’`.,]", '', s)
    s = re.sub(r'[-_/]', ' ', s)
    s = ' '.join(s.split())
    for _ in range(2):
        s = re.sub(r'^(cerros?|nevados?|volcan|pico|cumbre|monte|mount|mt|morro|alto|sierra|loma)\s+', '', s)
    return s


geoms = [shape(f['geometry']) for f in fs]
codes = [f['properties']['ISO_A2_EH'].lower() for f in fs]
tree = STRtree(geoms)


def countries(lat, lon):
    p = Point(lon, lat)
    hit = [codes[i] for i in tree.query(p.buffer(0.01), predicate='intersects')]
    if not hit:
        hit = [codes[tree.nearest(p)]]
    hit = sorted(set(hit))
    return ['ar', 'cl'] if '-99' in hit else hit


def ele(e):
    try:
        return float(re.sub(r'[^\d.]', '', e['tags'].get('ele', '')) or 'nan')
    except ValueError:
        return float('nan')


def grid(items, cell, ll):
    """Items by grid cell of `cell` degrees, for lookups within a cell's width."""
    g = {}
    for it in items:
        lat, lon = ll(it)
        g.setdefault((math.floor(lat / cell), math.floor(lon / cell)), []).append(it)
    return g


def around(g, cell, at):
    i, j = math.floor(at[0] / cell), math.floor(at[1] / cell)
    return [it for di in (-1, 0, 1) for dj in (-1, 0, 1) for it in g.get((i + di, j + dj), [])]


KCELL = 0.02          # degrees: wider than the 1 km any lookup of a known summit reaches
kgrid = grid(known, KCELL, lambda k: (k['lat'], k['lon']))


def known_near(at, limit_km, h=None):
    best = min(around(kgrid, KCELL, at), key=lambda k: km(at, (k['lat'], k['lon'])), default=None)
    if best is None:
        return None
    d = km(at, (best['lat'], best['lon']))
    if d <= limit_km and (h is None or (best['alt'] is not None and abs(best['alt'] - h) <= 1.5)):
        return best
    return None


# A top within 1 km of a database summit of its height is that summit, placed a little off
# (Aucanquilcha, 550 m): not a new one.
for x in P:
    if 'db' not in x:
        k = known_near((x['lat'], x['lon']), 1.0, x['h'])
        if k:
            x['db'] = dict(pk=k['pk'], name=k['name'], alt=k['alt'])
            print(f'  {x["lat"]:.5f},{x["lon"]:.5f} {x["h"]:.1f} m is {k["name"]} ({km((x["lat"], x["lon"]), (k["lat"], k["lon"])):.2f} km off)')
new = [x for x in P if 'db' not in x]
key = lambda x: f'glo:{x["lat"]:.6f},{x["lon"]:.6f}'
by_pos = {(x['lat'], x['lon']): x for x in P}

# names: each OSM peak to its nearest new top within 500 m (preferring one at its height), and
# not a name a database summit nearby already carries
by_key = {key(x): x for x in new}
NCELL = 0.01          # degrees: wider than the 500 m an OSM name may lie from its top
ngrid = grid(new, NCELL, lambda x: (x['lat'], x['lon']))
claims = {}
for e in osm:
    best = None
    for x in around(ngrid, NCELL, (e['lat'], e['lon'])):
        d = km((x['lat'], x['lon']), (e['lat'], e['lon']))
        if d <= 0.5:
            h = ele(e)
            score = d + (0 if h != h else abs(h - x['h']) / 400)
            if best is None or score < best[0]:
                best = (score, x)
    if best:
        claims.setdefault(key(best[1]), []).append((best[0], e))
taken = {}
for s in known:
    if s['name']:
        taken.setdefault(norm(s['name']), []).append((s['lat'], s['lon']))
NAME_MAX = 64 - len(' (99)')      # NamedPoint.name, with room for a number when it is taken


def fitting(n):
    """
    A name the database can hold: without a description in quotes ("Punto más alto del
    Departamento de …", Paraguay), else its first alternative ("A / B" across a border), else
    none.
    """
    n = re.sub(r'\s*"[^"]*"\s*$', '', n).strip()
    if len(n) > NAME_MAX:
        n = n.split(' / ')[0].strip()
    return n if len(n) <= NAME_MAX else None


named = {}
for k, cs in claims.items():
    cs.sort(key=lambda c: c[0])
    for _, e in cs:
        n = fitting(e['tags']['name'])
        if not n:
            continue
        if len(norm(n)) < 3:
            continue
        x = by_key[k]
        if any(km((x['lat'], x['lon']), q) < 2 for q in taken.get(norm(n), [])):
            continue
        named[k] = n
        break

burn = grid([(b['at'], b['now']) for b in burned], NCELL, lambda b: b[0])
summits, stats = [], dict(named=0, list=0, island=0, edge=0)
for x in sorted(new, key=lambda x: -x['prom']):
    cs = countries(x['lat'], x['lon'])
    lang = next((LANG[c] for c in cs if c in LANG), 'es')
    name = named.get(key(x))
    rec = dict(key=key(x), lat=x['lat'], lon=x['lon'], name=name,
               names=[dict(lang=lang, name=name, local=True)] if name else [], countries=cs)
    notes = [f'Copernicus GLO-30 at 1″ (flood of all South America), P {x["prom"]:.0f} m'
             + ('.' if x['why'] == 'around' else ': added as the parent of a summit in the area.')]
    lst = next((h for at, h in around(burn, NCELL, (x['lat'], x['lon'])) if km(at, (x['lat'], x['lon'])) < 0.15 and abs(h - x['h']) < 0.01), None)
    if lst is not None:
        rec.update(altitude=float(lst), datum=None, source='andes.org.uk')
        stats['list'] += 1
    else:
        rec.update(altitude=round(x['h'], 1), datum='EGM2008')
    if name:
        stats['named'] += 1
    if x['island']:
        stats['island'] += 1
        rec.update(island=True, prominence=rec['altitude'], parent=None)
    else:
        rec['prominence'] = round(rec['altitude'] - x['col_h'], 1)
        rec['col'] = dict(lat=x['col'][0], lon=x['col'][1], name=None, names=[], altitude=round(x['col_h'], 1),
                          datum='EGM2008', countries=countries(*x['col']), notes=[])
        if x.get('edge'):
            stats['edge'] += 1
            notes.append('The key col lies on the edge of the data used (57° S–13° N, 82°–34° W): the real one may be beyond it.')
        p = by_pos.get(tuple(x['parent'])) if x.get('parent') else None
        if p is None and x.get('parent'):
            # a database summit beyond the circle: the walk stopped there, so it is not listed
            k = known_near(tuple(x['parent']), 0.2) or known_near(tuple(x['parent']), 1.0, None)
            p = k and dict(db=dict(pk=k['pk'], name=k['name'], alt=k['alt']))
        if p is None:
            rec['parent'] = None
        elif 'db' in p:
            # by number and where it stands: numbers are not shared with another database
            k = known_by_pk[p['db']['pk']]
            rec['parent'] = dict(at=[k['lat'], k['lon']])
        else:
            rec['parent'] = dict(new=key(p))
    rec['notes'] = notes
    summits.append(rec)

json.dump(dict(sources=dict(lidar='Copernicus GLO-30', names='OpenStreetMap'), summits=summits),
          open(OUT, 'w'), ensure_ascii=False, indent=1)
print(len(summits), 'new summits', stats, sorted({c for s in summits for c in s['countries']}))
