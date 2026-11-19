import json, sys, collections
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
SP = sys.argv[1]
fs = [f for f in json.load(open(f'{SP}/ne/admin0.geojson'))['features'] if f['properties']['CONTINENT'] in ('South America', 'North America', 'Seven seas (open ocean)', 'Antarctica', 'Europe')]
geoms = [shape(f['geometry']) for f in fs]
codes = [f['properties']['ISO_A2_EH'].lower() for f in fs]
tree = STRtree(geoms)
def countries(lat, lon, buf=0.01):
    p = Point(lon, lat)
    hit = [codes[i] for i in tree.query(p.buffer(buf), predicate='intersects')]
    if not hit:
        i = tree.nearest(p); hit = [codes[i]]
    return sorted(set(hit))
P = json.load(open(f'{SP}/andes/sa_lists.json'))['p600']
cnt = collections.Counter()
for x in P:
    x['countries'] = countries(x['lat'], x['lon'])
    if x.get('col'): x['col_countries'] = countries(*x['col'])
    cnt[tuple(x['countries'])] += 1
json.dump(P, open(f'{SP}/andes/p600_c.json', 'w'), ensure_ascii=False)
print(cnt.most_common())
print({c: f['properties']['NAME'] for c, f in zip(codes, fs) if c in {k for t in cnt for k in t}})
