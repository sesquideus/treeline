"""
New summits from a catchment run (`catchments.py fabdem` / `dmr5` output) as a changeset, for a
region the database holds little of: every top of at least --min-prominence in the given
countries, with its key col, prominence parent and line parent; existing summits are matched,
not duplicated.

    python3 lidar/catchments_changeset.py RUN.json KNOWN.json OSM.json ADMIN0.geojson OUT.json \
        --countries tw --min-prominence 100 --source 'FABDEM V1-2' --lang zh

- KNOWN.json: the database's summits around ([{pk, name, lat, lon, alt}]). A top within 200 m
  of one, or within 1 km at its height (±1.5 m), is that summit.
- Names: the nearest OSM peak within 500 m, each to one top. The name is Latin script, as the
  database's are (Elbrus, K2): `name:en`, else `name` if it is Latin, else a romanisation;
  the local name goes in as a local name in --lang, `name:en` as English.
- Heights EGM2008 to 0.01 m. A child the model has level with its parent is linked one step
  further up, with a note (the database refuses a parent that is not strictly higher and more
  prominent).
- Summits are found by where they stand on amos too: new ones by key, existing ones by `at`.
"""
import argparse
import json
import math
import re

from shapely.geometry import Point, shape
from shapely.strtree import STRtree


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110.574, (a[1] - b[1]) * 111.32 * k)


def grid(items, cell, ll):
    g = {}
    for it in items:
        lat, lon = ll(it)
        g.setdefault((math.floor(lat / cell), math.floor(lon / cell)), []).append(it)
    return g


def around(g, cell, at):
    i, j = math.floor(at[0] / cell), math.floor(at[1] / cell)
    return [it for di in (-1, 0, 1) for dj in (-1, 0, 1) for it in g.get((i + di, j + dj), [])]


LATIN = re.compile(r"^[\x00-\x7fÀ-ɏḀ-ỿ'’ .,()-]+$")
NAME_MAX = 64 - len(' (99)')


def latin_name(tags):
    for k in ('name:en', 'name', 'name:zh-Latn-pinyin', 'name:zh_pinyin', 'name:ja-Latn', 'name:ko-Latn', 'int_name'):
        v = (tags.get(k) or '').strip()
        if v and LATIN.match(v) and len(v) <= NAME_MAX:
            return v
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run')
    ap.add_argument('known')
    ap.add_argument('osm')
    ap.add_argument('admin0')
    ap.add_argument('out')
    ap.add_argument('--countries', required=True, help='ISO codes, comma separated: the tops kept')
    ap.add_argument('--min-prominence', type=float, default=100.0)
    ap.add_argument('--source', required=True, help='the elevation data, as the Source is to be named')
    ap.add_argument('--lang', required=True, help="the local names' language (ISO 639-1)")
    ap.add_argument('--datum', default='EGM2008', help="the heights' vertical datum, as VerticalDatum.code")
    ap.add_argument('--reference', help='JSON of the countries and languages the database may lack '
                                        '({countries: [{code, name, full_name, english_name}], languages: [...]})')
    a = ap.parse_args()

    run = json.load(open(a.run))
    known = json.load(open(a.known))
    osm = [e for e in json.load(open(a.osm))['elements'] if e.get('tags', {}).get('name')]
    fs = json.load(open(a.admin0))['features']
    keep = set(a.countries.split(','))
    line_cutoff = run['stats'].get('line_cutoff')

    geoms = [shape(f['geometry']) for f in fs]
    codes = [f['properties']['ISO_A2_EH'].lower() for f in fs]
    tree = STRtree(geoms)

    def countries(lat, lon):
        p = Point(lon, lat)
        hit = sorted({codes[i] for i in tree.query(p.buffer(0.01), predicate='intersects')})
        return hit or [codes[tree.nearest(p)]]

    tops = [x for x in run['summits'] if x['prom'] >= a.min_prominence]
    for x in tops:
        x['at'] = tuple(x['top'])
        x['countries'] = countries(*x['at'])
    tops = [x for x in tops if keep & set(x['countries'])]
    by_pos = {x['at']: x for x in tops}
    print(len(tops), f'tops of P >= {a.min_prominence:g} in', sorted(keep))

    KCELL = 0.02
    kgrid = grid(known, KCELL, lambda k: (k['lat'], k['lon']))

    def known_near(at, h=None):
        c = around(kgrid, KCELL, at)
        best = min(c, key=lambda k: km(at, (k['lat'], k['lon'])), default=None)
        if best is None:
            return None
        d = km(at, (best['lat'], best['lon']))
        if d <= 0.2 or (d <= 1.0 and h is not None and best['alt'] is not None and abs(best['alt'] - h) <= 1.5):
            return best
        return None

    for x in tops:
        x['db'] = known_near(x['at'], x['h'])
    new = [x for x in tops if x['db'] is None]
    key = lambda x: f'fab:{x["at"][0]:.6f},{x["at"][1]:.6f}'

    # names: each OSM peak to the nearest new top within 500 m, the nearest claim winning
    NCELL = 0.01
    ngrid = grid(new, NCELL, lambda x: x['at'])
    claims = {}
    for e in osm:
        best = min(((km(x['at'], (e['lat'], e['lon'])), x) for x in around(ngrid, NCELL, (e['lat'], e['lon']))),
                   key=lambda c: c[0], default=None)
        if best and best[0] <= 0.5:
            claims.setdefault(key(best[1]), []).append((best[0], e))
    taken = {(k['name'] or '').lower() for k in known}
    tags_of = {}
    for k, cs in claims.items():
        cs.sort(key=lambda c: c[0])
        for _, e in cs:
            if latin_name(e['tags']) and latin_name(e['tags']).lower() not in taken:
                tags_of[k] = e['tags']
                break

    summits, stats = [], dict(named=0, island=0, stepped=0)
    for x in sorted(new, key=lambda x: -x['prom']):
        tags = tags_of.get(key(x))
        name = latin_name(tags) if tags else None
        names = []
        if tags:
            local = (tags.get(f'name:{a.lang}') or tags.get('name') or '').strip()
            if local and local != name and len(local) <= 64:
                names.append(dict(lang=a.lang, name=local, local=True))
            if tags.get('name:en') and tags['name:en'] != name:
                names.append(dict(lang='en', name=tags['name:en'].strip(), local=False))
        rec = dict(key=key(x), lat=x['at'][0], lon=x['at'][1], name=name, names=names,
                   countries=x['countries'], altitude=round(x['h'], 2), datum=a.datum,
                   notes=[f'{a.source} at 1″, P {x["prom"]:.0f} m.'])
        if name:
            stats['named'] += 1
        if 'col' not in x:
            stats['island'] += 1
            rec.update(island=True, prominence=rec['altitude'], parent=None)
        else:
            rec['prominence'] = round(x['h'] - x['col_h'], 2)
            rec['col'] = dict(lat=x['col'][0], lon=x['col'][1], name=None, names=[], altitude=round(x['col_h'], 2),
                              datum=a.datum, countries=countries(*x['col']), notes=[])
            rec['parent'] = None
            p = by_pos.get(tuple(x['parent'])) if x.get('parent') else None
            if p is not None:
                rec['parent'] = dict(at=[p['db']['lat'], p['db']['lon']]) if p['db'] else dict(new=key(p))
        summits.append(rec)

    # level with its parent in the model: one step further up (see the docstring)
    by_key = {r['key']: r for r in summits}
    for rec in summits:
        parent, tie = rec.get('parent'), None
        for _ in range(50):
            if not parent or 'new' not in parent:
                break
            q = by_key[parent['new']]
            if q['altitude'] > rec['altitude'] and q['prominence'] > rec['prominence']:
                break
            tie = tie or q
            parent = q.get('parent')
        if tie is not None:
            rec['parent'] = parent
            rec['notes'].append(f'{a.source} has this level with {tie["name"] or "the unnamed summit at " + tie["key"][4:]} '
                                f'({tie["altitude"]} m, P {tie["prominence"]} m), its prominence parent in the flood; '
                                'linked one step further up instead.')
            stats['stepped'] += 1

    # line parents: by where both stand (new summits are created first in the same changeset)
    where = {x['at']: ([x['db']['lat'], x['db']['lon']] if x['db'] else [x['at'][0], x['at'][1]]) for x in tops}
    lines = []
    for x in tops:
        if line_cutoff is None:
            break
        if 'col' not in x:
            lines.append(dict(summit_at=where[x['at']], parent_at=None, cutoff=line_cutoff, source=a.source))
        elif x.get('line') and tuple(x['line']) in where:
            lines.append(dict(summit_at=where[x['at']], parent_at=where[tuple(x['line'])], cutoff=line_cutoff,
                              source=a.source))

    reference = json.load(open(a.reference)) if a.reference else {}
    json.dump(dict(sources=dict(lidar=a.source, names='OpenStreetMap'), reference=reference, summits=summits,
                   line_parents=lines),
              open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(len(summits), 'new summits', stats, f'{len(tops) - len(new)} already in the database;',
          len(lines), 'line parents ->', a.out)


if __name__ == '__main__':
    main()
