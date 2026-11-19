"""Place the andes.org.uk lists (6000 m, 5000 m) on the P600s: by OSM name, checked by height."""
import json, math, re, sys, unicodedata
import numpy as np
D = sys.argv[1]
P = json.load(open(sys.argv[2]))
osm = [e for e in json.load(open(f'{D}/osm_sa.json'))['elements'] if e.get('tags', {}).get('name')]
L6 = json.load(open(f'{D}/6000m.json')); L5 = json.load(open(f'{D}/5000m.json'))
for y in L6: y['list'] = '6000'
for y in L5: y['list'] = '5000'
L = L6 + L5
PRE = r'^(cerros?|co\.?|nevados?|nev\.?|volcan|vn\.?|vol\.?|pico|cumbre|monte|mt\.?|loma|cordon|cerro|apu|punta|morro|alto|sierra)\s+'
def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"['’`.,]", '', s); s = re.sub(r'[-_/]', ' ', s); s = ' '.join(s.split())
    for _ in range(2): s = re.sub(PRE, '', s)
    return s
def variants(name):
    out = set()
    for part in re.split(r'\(|\)|&|/|\bor\b|,', name):
        p = norm(part)
        if p and p not in ('un named', 'unnamed', 'pt', 'n', 's', 'e', 'w', 'main') and len(p) > 2: out.add(p)
    return out
onames = [variants(e['tags']['name']) | {norm(e['tags'][k]) for k in e['tags'] if k.startswith(('alt_name', 'old_name', 'name:')) and e['tags'][k]} for e in osm]
olat = np.array([e['lat'] for e in osm]); olon = np.array([e['lon'] for e in osm])
plat = np.array([x['lat'] for x in P]); plon = np.array([x['lon'] for x in P]); ph = np.array([x['h'] for x in P])
def near_p(lat, lon, r):
    d = np.hypot((plat - lat) * 110.574, (plon - lon) * 111.32 * math.cos(math.radians(lat)))
    return [(int(i), float(d[i])) for i in np.flatnonzero(d <= r)]
def ok_h(i, y):
    # GLO reads summits low (to −200 m on spires), rarely more than a few tens high
    return y['height'] is None or -60 <= y['height'] - ph[i] <= 250
import difflib
DIR = {'sur', 'norte', 'central', 'este', 'oeste', 'principal', 'chico', 'grande', 'n', 's', 'e', 'w', 'i', 'ii', 'sud'}
def fold(v):
    v = v.replace('qu', 'k').replace('hu', 'w').replace('gu', 'w').replace('c', 'k').replace('z', 's').replace('ll', 'y').replace('v', 'b')
    v = re.sub(r'(.)\1', r'\1', v)
    return re.sub(r'[ie]\b', 'i', re.sub(r'[ou]\b', 'u', v))
def core(v): return frozenset(t for t in v.split() if t not in DIR)
def tier(vs, on):
    if vs & on: return 0
    if any(core(a) and core(a) == core(b) for a in vs for b in on): return 1
    fa = {fold(' '.join(sorted(core(a)))) for a in vs} - {''}; fb = {fold(' '.join(sorted(core(b)))) for b in on} - {''}
    if fa & fb: return 1
    if any(len(a) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.88 for a in fa for b in fb if a[:2] == b[:2]): return 2
    return None
claims = {}
for k, y in enumerate(L):
    vs = variants(y['name'])
    cands = []
    if y.get('osm_lat') is not None:               # the 6000 m list's earlier OSM placement
        cands += [(i, d, 'osm') for i, d in near_p(y['osm_lat'], y['osm_lon'], 3)]
    cands = [(i, d, 'osm', 0) for i, d, _ in cands]
    for j, on in enumerate(onames):
        t = tier(vs, on)
        if t is not None:
            cands += [(i, d, 'name' if t == 0 else 'name~' if t == 1 else 'fuzzy', t) for i, d in near_p(olat[j], olon[j], 3)]
    cands = [c for c in cands if ok_h(c[0], y) and (c[3] == 0 or c[1] <= 1.0) and (c[3] < 2 or abs(y['height'] - ph[c[0]] - 7) < 80)]
    if not cands: continue
    i, d, how, t = min(cands, key=lambda c: (c[3], c[1] + abs((y['height'] or ph[c[0]]) - ph[c[0]] - 7) / 100))
    claims.setdefault(i, []).append((t * 1000 + abs((y['height'] or ph[i]) - ph[i] - 7) + d * 50, k, d, how))
for i, cs in claims.items():
    cs.sort()
    _, k, d, how = cs[0]
    y = L[k]
    y['p600'] = [P[i]['lat'], P[i]['lon']]; y['p600_km'] = round(d, 2); y['match'] = how
    P[i]['list'] = dict(list=y['list'], rank=y['rank'], name=y['name'], height=y['height'], approx=y['approx'])
    for _, k2, _, _ in cs[1:]:
        L[k2]['p600'] = None; L[k2]['lost_to'] = y['name']
json.dump(dict(p600=P, list=L), open(sys.argv[3], 'w'), ensure_ascii=False, indent=0)
for nm in ('6000', '5000'):
    ys = [y for y in L if y['list'] == nm]
    print(nm, len(ys), 'matched', sum(1 for y in ys if y.get('p600')), 'lost', sum(1 for y in ys if y.get('lost_to')))
