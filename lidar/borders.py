"""
Which countries a point lies in, by the borders as OpenStreetMap has them (ODbL, as surveyed: metres,
not the few hundred metres of Natural Earth's 1:10m outline, which put Polish summits 2 km from the
border into Ukraine).

The outlines are GeoJSON files in $TREELINE_DATA/borders (one feature per country, property `code`,
ISO 3166-1 alpha-2), made either from an extract on disk (`osm_borders.py`) or fetched here from
OpenStreetMap, one country's admin_level=2 relation at a time:

    uv run --group lidar python lidar/borders.py fetch PL SK HU ...     # writes borders/pl.geojson, ...
    uv run --group lidar python lidar/borders.py planet planet.osm.pbf DIR   # every country at once

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
NATURAL_EARTH = _path('naturalearth', 'ne_10m_admin_0_countries.geojson')
#: TreeLine's view where OSM's outlines overlap: Crimea and Sevastopol are Ukraine's, so Russia's
#: outline is less Ukraine's (OSM puts them in both)
LESS = {'RU': ['UA']}
#: what TreeLine records apart that OSM has no country outline for: carved from the country whose
#: outline holds it along Natural Earth's line (its ADM0_A3), the code TreeLine gave it
CARVE = {'XC': ('CY', 'CYN'), 'XS': ('SO', 'SOL')}


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


def assemble(members, ways):
    """
    A boundary relation's (multi)polygon: `members` its [(role, way id)], `ways` {way id: [(lon,
    lat), ...]}; outer rings less inner ones. None where the outer ways do not close.
    """
    from shapely.geometry import LineString
    lines = {'outer': [], 'inner': []}
    for role, ref in members:
        if role in lines and len(ways.get(ref, [])) > 1:
            lines[role].append(LineString(ways[ref]))
    if not lines['outer']:
        return None
    outer = unary_union(list(polygonize(unary_union(lines['outer']))))
    inner = unary_union(list(polygonize(unary_union(lines['inner'])))) if lines['inner'] else None
    geom = outer.difference(inner) if inner is not None and not inner.is_empty else outer
    return None if geom.is_empty else geom


def write(code, geom, folder=DIR, **props):
    """borders/<code>.geojson, one feature, written whole or not at all."""
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, code.lower() + '.geojson')
    json.dump(dict(type='FeatureCollection', features=[dict(
        type='Feature', properties=dict(code=code.upper(), **props), geometry=mapping(geom))]),
        open(out + '.part', 'w'))
    os.replace(out + '.part', out)
    return out


def fetch(code, folder=DIR):
    """
    A country's admin_level=2 relation, assembled into a (multi)polygon: its id from Overpass (a
    small query; Overpass gives up on a whole country's geometry), the relation with its ways and
    nodes from the OSM API (relation/ID/full).
    """
    import xml.etree.ElementTree as ET
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
    geom = assemble([(m.get('role'), m.get('ref')) for m in rel.iter('member') if m.get('type') == 'way'], ways)
    if geom is None:
        raise RuntimeError(f'{code}: the outer ways do not close')
    out = write(code, geom, folder, name=name, relation=rid, source='OpenStreetMap (ODbL), API relation/full')
    print(f'{code}: {name} {geom.area:.3f} deg² -> {out}', flush=True)


def planet(pbf, folder):
    """
    Every country's outline from a planet (or any) extract, in three passes that keep only what
    the borders need: the admin_level=2 relations with an ISO 3166-1 code, their ways, those
    ways' nodes (pyosmium's own area assembly would index every node of the planet). Several
    relations with one code (a country and its overseas parts) are joined.
    """
    import osmium
    from collections import defaultdict
    rels = []
    for r in osmium.FileProcessor(pbf, osmium.osm.RELATION).with_filter(osmium.filter.KeyFilter('admin_level')):
        t = r.tags
        code = t.get('ISO3166-1') or t.get('ISO3166-1:alpha2')
        if t.get('boundary') != 'administrative' or t.get('admin_level') != '2' or not code or len(code) != 2:
            continue
        rels.append((code.upper(), r.id, t.get('name'),
                     [(m.role, m.ref) for m in r.members if m.type == 'w']))
    print(len(rels), 'relations', flush=True)
    wids = {ref for *_, ms in rels for _, ref in ms}
    refs = {}
    for w in osmium.FileProcessor(pbf, osmium.osm.WAY).with_filter(osmium.filter.IdFilter(wids)):
        refs[w.id] = [n.ref for n in w.nodes]
    nids = {n for ns in refs.values() for n in ns}
    print(len(refs), 'ways,', len(nids), 'nodes', flush=True)
    loc = {}
    for n in osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(nids)):
        loc[n.id] = (n.location.lon, n.location.lat)
    ways = {i: [loc[n] for n in ns if n in loc] for i, ns in refs.items()}
    by_code = defaultdict(list)
    for code, rid, name, ms in rels:
        g = assemble(ms, ways)
        if g is None:
            print(f'  {code} ({name}, r{rid}): the outer ways do not close', flush=True)
            continue
        by_code[code].append((rid, name, g))
    geoms = {code: unary_union([g for *_, g in parts]) for code, parts in by_code.items()}
    props = {code: dict(name=parts[0][1], relation=[rid for rid, *_ in parts],
                        source=f'OpenStreetMap (ODbL), {os.path.basename(pbf)}') for code, parts in by_code.items()}
    adjust(geoms, props)
    for code, geom in sorted(geoms.items()):
        write(code, geom, folder, **props[code])
    print(len(by_code), 'countries ->', folder, flush=True)


def adjust(geoms, props, natural_earth=NATURAL_EARTH):
    """ TreeLine's view of disputed land on OSM's outlines ({code: geometry}, changed in place):
    `LESS`, then `CARVE`. """
    for code, less in LESS.items():
        if code in geoms:
            for other in less:
                if other in geoms:
                    geoms[code] = geoms[code].difference(geoms[other])
                    props[code]['less'] = props[code].get('less', []) + [other]
    ne = None
    for code, (holder, a3) in CARVE.items():
        if holder not in geoms:
            continue
        if ne is None:
            ne = {f['properties']['ADM0_A3']: shape(f['geometry']) for f in json.load(open(natural_earth))['features']}
        part = geoms[holder].intersection(ne[a3])
        if part.is_empty:
            continue
        geoms[code] = part
        geoms[holder] = geoms[holder].difference(part)
        props[code] = dict(name=a3, source=f"{props[holder]['source']}, carved from {holder} along Natural Earth's line")
        props[holder]['less'] = props[holder].get('less', []) + [code]


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'planet':
        planet(sys.argv[2], sys.argv[3])
        sys.exit()
    if len(sys.argv) < 3 or sys.argv[1] != 'fetch':
        sys.exit(__doc__)
    for c in sys.argv[2:]:
        if os.path.exists(os.path.join(DIR, c.lower() + '.geojson')):
            print(f'{c}: have it')
            continue
        fetch(c)
        time.sleep(10)                               # a public server
