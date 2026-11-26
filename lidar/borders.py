"""
Which countries a point lies in, by the borders as OpenStreetMap has them (ODbL, as surveyed: metres,
not the few hundred metres of Natural Earth's 1:10m outline, which put Polish summits 2 km from the
border into Ukraine).

The outlines are GeoJSON files in $TREELINE_DATA/borders (one feature per country, property `code`,
ISO 3166-1 alpha-2), made either from an extract on disk (`osm_borders.py`) or fetched here from
OpenStreetMap, one country's admin_level=2 relation at a time:

    uv run --group lidar python lidar/borders.py fetch PL SK HU ...     # writes borders/pl.geojson, ...

A point within TOL metres of a border line stands on it and is in both countries: OSM's line on a
ridge is itself tens of metres off at places, and a summit on the ridge belongs to both. Where no
OSM outline is on disk the caller's fallback decides (Natural Earth in `catchments_changeset.py`),
and those points are counted, so a run can say where the borders were coarse.
"""
import glob
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

from shapely import affinity
from shapely.geometry import Point, shape, mapping
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

try:
    from data import path as _path
except ImportError:                                  # imported as lidar.borders
    from lidar.data import path as _path

DIR = _path('borders', env='TREELINE_BORDERS')
TOL = 100.0                                          # metres: on the line, in both countries
OVERPASS = 'https://overpass-api.de/api/interpreter'
OSM_API = 'https://api.openstreetmap.org/api/0.6'
AGENT = 'treeline-client/0.1'


class Borders:
    def __init__(self, folder=DIR, tol=TOL):
        self.tol = tol
        self.codes, self.geoms = [], []
        for f in sorted(glob.glob(os.path.join(folder, '*.geojson'))):
            for feat in json.load(open(f))['features']:
                self.codes.append(feat['properties']['code'].lower())
                self.geoms.append(shape(feat['geometry']))
        self.tree = STRtree(self.geoms) if self.geoms else None
        self.edges = [g.boundary for g in self.geoms]
        self.coarse = 0                              # points no outline on disk covered

    def near(self, lat, lon):
        """The ellipse of radius `tol` metres around a point, in degrees."""
        r = self.tol / 111320.0
        return affinity.scale(Point(lon, lat).buffer(r, 16), 1 / max(math.cos(math.radians(lat)), 1e-6), 1)

    def countries(self, lat, lon):
        """
        (inside, known, edge): the countries whose outline holds the point or whose border runs within
        `tol` of it, the countries whose outline is near enough to have a say (empty: no outline on
        disk reaches here, and the caller's fallback must decide), and whether a border is that near.
        """
        if self.tree is None:
            return set(), set(), False
        p, area = Point(lon, lat), self.near(lat, lon)
        inside, known, edge = set(), set(), False
        for i in self.tree.query(area):
            known.add(self.codes[i])
            on = self.edges[i].intersects(area)
            edge = edge or on
            if on or self.geoms[i].contains(p):
                inside.add(self.codes[i])
        return inside, known, edge

    def decide(self, lat, lon, fallback):
        """
        The point's countries: the OSM outlines that hold it (or whose line passes within `tol`),
        and on the side of a border no outline on disk covers, `fallback(exclude)` (coarse: a set of
        codes, never one of `exclude`). A country whose outline is on disk is decided by that outline
        alone, anywhere: the coarse map never adds it, and never keeps a point in it.
        """
        inside, known, edge = self.countries(lat, lon)
        outlined = set(self.codes)
        res = set(inside)
        if not inside or edge:
            res |= set(fallback(outlined))
        if not inside:
            self.coarse += 1
        return res


def get(url, data=None, timeout=600):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': AGENT})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            print(f'  {url[:60]}: attempt {attempt + 1} failed: {e}', flush=True)
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f'{url}: no answer')


def fetch(code, folder=DIR):
    """
    A country's admin_level=2 relation, assembled into a (multi)polygon: its id from Overpass (a
    small query; Overpass gives up on a whole country's geometry), the relation with its ways and
    nodes from the OSM API (relation/ID/full).
    """
    import xml.etree.ElementTree as ET
    from shapely.geometry import LineString
    q = (f'[out:json][timeout:120];relation["boundary"="administrative"]["admin_level"="2"]'
         f'["ISO3166-1"="{code.upper()}"];out ids tags;')
    rels = json.loads(get(OVERPASS, urllib.parse.urlencode({'data': q}).encode()))['elements']
    if len(rels) != 1:
        raise RuntimeError(f'{code}: {len(rels)} relations')
    rid, name = rels[0]['id'], rels[0]['tags'].get('name')
    root = ET.fromstring(get(f'{OSM_API}/relation/{rid}/full'))
    nodes = {n.get('id'): (float(n.get('lon')), float(n.get('lat'))) for n in root.iter('node')}
    ways = {w.get('id'): [nodes[nd.get('ref')] for nd in w.iter('nd')] for w in root.iter('way')}
    rel = next(r for r in root.iter('relation') if r.get('id') == str(rid))
    lines = {'outer': [], 'inner': []}
    for m in rel.iter('member'):
        if m.get('type') == 'way' and m.get('role') in lines and len(ways.get(m.get('ref'), [])) > 1:
            lines[m.get('role')].append(LineString(ways[m.get('ref')]))
    outer = unary_union(list(polygonize(unary_union(lines['outer']))))
    inner = unary_union(list(polygonize(unary_union(lines['inner'])))) if lines['inner'] else None
    geom = outer.difference(inner) if inner is not None and not inner.is_empty else outer
    if geom.is_empty:
        raise RuntimeError(f'{code}: the outer ways do not close')
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, code.lower() + '.geojson')
    json.dump(dict(type='FeatureCollection', features=[dict(
        type='Feature', properties=dict(code=code.upper(), name=name, relation=rid,
                                        source='OpenStreetMap (ODbL), API relation/full'),
        geometry=mapping(geom))]), open(out + '.part', 'w'))
    os.replace(out + '.part', out)
    print(f'{code}: {name} {geom.area:.3f} deg² -> {out}', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] != 'fetch':
        sys.exit(__doc__)
    for c in sys.argv[2:]:
        if os.path.exists(os.path.join(DIR, c.lower() + '.geojson')):
            print(f'{c}: have it')
            continue
        fetch(c)
        time.sleep(10)                               # a public server
