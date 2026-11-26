"""
Country outlines from an OpenStreetMap extract: the admin_level=2 boundary relations assembled into
(multi)polygons, as GeoJSON with ISO3166-1 codes. Natural Earth's 1:10m outline is a few hundred
metres off in the mountains; these follow the border as surveyed.

    uv run --group lidar python lidar/osm_borders.py ukraine-latest.osm.pbf OUT.geojson [UA ...]

Only the codes named (all admin_level=2 relations complete in the extract when none are) are kept.
"""
import json
import sys

import osmium
import shapely.wkb
from shapely.geometry import mapping

src, out, want = sys.argv[1], sys.argv[2], {c.upper() for c in sys.argv[3:]}
wkb = osmium.geom.WKBFactory()
feats = []
for o in osmium.FileProcessor(src).with_areas().with_filter(osmium.filter.KeyFilter('boundary')):
    if not o.is_area() or o.from_way():
        continue
    t = o.tags
    if t.get('boundary') != 'administrative' or t.get('admin_level') != '2':
        continue
    code = t.get('ISO3166-1') or t.get('ISO3166-1:alpha2')
    if not code or (want and code.upper() not in want):
        continue
    try:
        g = shapely.wkb.loads(wkb.create_multipolygon(o), hex=True)
    except Exception as e:
        print(code, 'not assembled:', e, flush=True)
        continue
    feats.append(dict(type='Feature', properties=dict(code=code.upper(), name=t.get('name'), relation=o.orig_id()),
                      geometry=mapping(g)))
    print(code, t.get('name'), round(g.area, 3), 'deg²', flush=True)
json.dump(dict(type='FeatureCollection', features=feats), open(out, 'w'))
