"""
What `compare.py` found, as a changeset for `manage.py apply_lidar_changeset`: plain JSON
that needs none of the lidar tooling, so it can be applied on the server too.

    uv run --group lidar python lidar/changeset.py comparison.json DB.json OUT.json

Three kinds of change:

- `summits` — the P100s missing from the DB: summit, key col and prominence parent. The
  parent is a DB summit within 150 m of the lidar walk's parent, or another new summit.
- `point_names` — Freemap `name:xx` tags in a language the point has no name in.
- `col_names` — key cols the DB leaves unnamed and Freemap names.

Countries come from OpenStreetMap's border: the nearest stretch of Slovakia's border to a
point outside it says which neighbour it is in.
"""
import json
import math
import os
import sys

import osmium
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point

OSM = os.path.expanduser('~/.cache/treeline/lidar/osm')
SLOVAKIA = 14296
NEIGHBOURS = {16239: 'at', 21335: 'hu', 49715: 'pl', 51684: 'cz', 60199: 'ua'}
LANGUAGE = {'sk': 'sk', 'hu': 'hu', 'pl': 'pl', 'cz': 'cs', 'at': 'de', 'ua': 'uk'}
TM = Transformer.from_crs(4326, 3046, always_xy=True)


def dist(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * 111320 * k, (a[0] - b[0]) * 110574)


class Borders(osmium.SimpleHandler):
    """Slovakia's border ways, each with the neighbour whose border it also is."""

    def __init__(self):
        super().__init__()
        self.members = {}           # way id -> set of country relation ids
        self.lines = {}

    def relation(self, r):
        if r.id == SLOVAKIA or r.id in NEIGHBOURS:
            for m in r.members:
                if m.type == 'w':
                    self.members.setdefault(m.ref, set()).add(r.id)


class Lines(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__()
        self.wanted, self.lines = wanted, {}

    def way(self, w):
        if w.id in self.wanted:
            try:
                coords = [TM.transform(n.lon, n.lat) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) > 1:
                self.lines[w.id] = LineString(coords)


def countries_of(points, inside):
    """For each (lat, lon): ['sk'], a neighbour, or both within 30 m of the border."""
    b = Borders()
    b.apply_file(os.path.join(OSM, 'slovakia-latest.osm.pbf'))
    border = {w for w, rels in b.members.items() if SLOVAKIA in rels}
    lines = Lines(border)
    lines.apply_file(os.path.join(OSM, 'slovakia-latest.osm.pbf'), locations=True)
    ids = list(lines.lines)
    tree = shapely.STRtree([lines.lines[i] for i in ids])
    out = []
    for ll, is_in in zip(points, inside):
        here = Point(*TM.transform(ll[1], ll[0]))
        k = tree.nearest(here)
        way = ids[k]
        gap = lines.lines[way].distance(here)
        neighbour = next((NEIGHBOURS[r] for r in b.members[way] if r in NEIGHBOURS), None)
        if gap <= 30 and neighbour:
            out.append(['sk', neighbour])
        elif is_in or neighbour is None:
            out.append(['sk'])
        else:
            out.append([neighbour])
    return out


# Freemap name:xx tags that are not names of the point: a range ("Kleine Fatra" on Malý
# Kriváň, "Große Fatra" on Veľký Kriváň, which is not even in it), a common noun ("Höhe"),
# three spellings of one name nobody can choose between here, an exonym nobody uses.
EXCLUDE = {
    ('Malý Kriváň', 'de', 'Kleine Fatra'), ('Veľký Kriváň', 'de', 'Große Fatra'),
    ('Čierna hora', 'de', 'Höhe'), ('Lomnický štít', 'en', 'Lomnicky Peak'),
    ('Vápenná', 'de', 'Rachsthurn'), ('Vápenná', 'de', 'Rachsturn'), ('Vápenná', 'de', 'Rachsturm'),
}


def fold(name):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', (name or '').lower())
                   if unicodedata.category(c) != 'Mn' and c.isalnum())


def language_of(osm, countries):
    """The language of an OSM point's plain `name`: the name:xx it repeats, else the country's."""
    for lang, names in (osm.get('langs') or {}).items():
        if osm['name'] in names:
            return lang
    return LANGUAGE.get(countries[0], 'sk')


def main():
    report = json.load(open(sys.argv[1]))
    db = json.load(open(sys.argv[2]))
    summits = db['summits']
    by_pk = {s['pk']: s for s in summits}
    out = dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
               summits=[], point_names=[], col_names=[])

    # Missing P100s, most prominent first, so a new parent exists before its children.
    missed = sorted(report['missed'], key=lambda e: -(e['lidar']['prom'] or 0))
    places = [e['lidar']['ll'] for e in missed] + [e['lidar']['col'] for e in missed]
    inside = [not any(f.startswith('outside Slovakia') for f in e['lidar']['flags']) for e in missed]
    countries = countries_of(places, inside + [True] * len(missed))
    keys = {}
    for i, e in enumerate(missed):
        x = e['lidar']
        key = f'lidar:{x["ll"][0]:.6f},{x["ll"][1]:.6f}'
        keys[tuple(x['ll'])] = key
        osm, osm_col = x.get('osm'), x.get('osm_col')
        c_summit, c_col = countries[i], countries[len(missed) + i]
        copernicus_col = any('key col outside the lidar' in f for f in x['flags'])
        names = []
        if osm and osm['name']:
            names.append(dict(lang=language_of(osm, c_summit), name=osm['name'], local=True))
            names += [dict(lang=lang, name=n, local=False) for lang, ns in (osm.get('langs') or {}).items()
                      for n in ns if n != osm['name']]
        col_names = []
        if osm_col and osm_col['name'] and not osm_col['name'].isdigit():
            col_names.append(dict(lang=language_of(osm_col, c_col), name=osm_col['name'], local=True))
        parent = None
        if x.get('parent'):
            near = min(summits, key=lambda s: dist((s['point']['lat'], s['point']['lon']), x['parent']))
            if dist((near['point']['lat'], near['point']['lon']), x['parent']) <= 150:
                parent = dict(pk=near['pk'], name=near['point']['name'])
            else:
                new = next((k for ll, k in keys.items() if dist(ll, x['parent']) <= 150), None)
                if new:
                    parent = dict(new=new)
        notes = []
        if copernicus_col:
            notes.append('Key col from Copernicus GLO-30 (30 m surface model with the forest canopy in it), '
                         'outside the lidar: less precise than lidar.')
        out['summits'].append(dict(
            key=key, lat=x['ll'][0], lon=x['ll'][1],
            name=osm['name'] if osm and osm['name'] else None, names=names,
            altitude=x['bpv'] if x['bpv'] is not None else x['egm'],
            datum='Bpv' if x['bpv'] is not None else 'EGM2008',
            countries=c_summit, prominence=x['prom'], parent=parent, notes=notes,
            col=dict(lat=x['col'][0], lon=x['col'][1],
                     name=col_names[0]['name'] if col_names else None, names=col_names,
                     altitude=x['col_bpv'] if x['col_bpv'] is not None else x['col_egm'],
                     datum='Bpv' if x['col_bpv'] is not None else 'EGM2008',
                     countries=c_col, notes=notes if copernicus_col else [])))

    # Names in other languages, and unnamed key cols Freemap names.
    for key in ('wrong', 'amiss', 'good', 'doubtful'):
        for e in report[key]:
            d = e.get('db')
            if not d:
                continue
            s = by_pk[d['pk']]
            for what, items, point in (('summit', e.get('langs', []), s['point']),
                                       ('col', e.get('col_langs', []), s['key_col'])):
                for l in items:
                    if l['status'] != 'missing' or point is None:
                        continue
                    known = {fold(point['name'])} | {fold(n) for n in point.get('names', [])}
                    for name in l['osm']:
                        # Nothing new: "Babia hora" in Czech is the Slovak name again.
                        if fold(name) in known or (point['name'], l['lang'], name) in EXCLUDE:
                            continue
                        out['point_names'].append(dict(point=point['pk'], point_name=point['name'],
                                                       lang=l['lang'], name=name, what=what))
            oc = e['lidar'].get('osm_col')
            kc = s['key_col']
            if e.get('col_name') == 'osm only' and kc and oc and oc['name'] and not oc['name'].isdigit():
                out['col_names'].append(dict(point=kc['pk'], summit=d['name'], name=oc['name'],
                                             lang=language_of(oc, kc.get('countries') or ['sk']),
                                             names=[dict(lang=lang, name=n) for lang, ns in (oc.get('langs') or {}).items()
                                                    for n in ns if n != oc['name']]))
    json.dump(out, open(sys.argv[3], 'w'), ensure_ascii=False, indent=1)
    print(f'{len(out["summits"])} summits, {len(out["point_names"])} names, {len(out["col_names"])} col names'
          f' -> {sys.argv[3]}')


if __name__ == '__main__':
    main()
