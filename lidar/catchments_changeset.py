"""
New summits from a catchment run (`catchments.py fabdem` / `dmr5` output) as a changeset, for a
region the database holds little of: every top of at least --min-prominence in the given
countries, with its key col, prominence parent and line parent; existing summits are matched,
not duplicated.

    python3 lidar/catchments_changeset.py RUN.json KNOWN.json OSM.json ADMIN0.geojson OUT.json \
        --countries tw --min-prominence 100 --source 'FABDEM V1-2' --lang zh

- KNOWN.json: the database's summits around ([{pk, name, lat, lon, alt, prom}]). A top within 200 m
  of one, or within 1 km at its height (±1.5 m), is that summit.
- Names (--wikidata, CC0): the Wikidata item whose height the run took, else the nearest item
  within 1 km (or its stated imprecision, up to 2 km) at a height within 150 m (the best of
  distance and height together), each item to one
  top; its English label, with Wikidata as the name's source (PointName.source). Bulk OSM names would make the database a derivative
  of OSM under ODbL, which cannot carry FABDEM's non-commercial condition.
- Names (OSM, without --wikidata): the nearest OSM peak within 500 m, each to one top. The name is Latin script, as the
  database's are (Elbrus, K2): `name:en`, else `name` if it is Latin, else a romanisation;
  the local name goes in as a local name in --lang, `name:en` as English.
- Heights EGM2008 to 0.01 m, as the model has them; where the run took a height from Wikidata
  (`height_from`: burned in, or capped by `recap.py`), that height with Wikidata as its source,
  the item in a note, datum unknown. A child the model has level with its parent is linked one step
  further up, with a note (the database refuses a parent that is not strictly higher and more
  prominent).
- Summits are found by where they stand on amos too: new ones by key, existing ones by `at`.
- --local-lang uk --labels LABELS.json (`wikidata_labels.py`): the name (NamedPoint.name) is the
  local one in its own script, as the user wants Ukraine's summits named in Cyrillic: the Wikidata
  item's label in that language, else a GeoNames name in it (without a leading "гора", "Mount"
  of the kind, and any "(гора)" after); either also a local name with its source, the English
  label an English one. With neither, the top stays unnamed (its names in other languages kept),
  and the Latin name on offer is listed in REVIEW.json for a person; a top with no name stays unnamed.
  An item or GeoNames summit within 300 m of a summit TreeLine holds, or named as one within
  3 km is, is that summit's and names no new top; nor does a name a more prominent new top within
  3 km has already (two items for one mountain): those are listed in REVIEW.json.
- --review-heights: how far each new summit's height can be trusted (NamedPoint.altitude_review)
  by its Wikidata item (burned in, or the item that names it): within --agree m (20) of the
  model, O; farther, W, with a note giving both heights and the difference; no item with a
  height, none (not assessed).
- --parents-known: a prominence or line parent outside the tops kept (another country, beyond
  --exclude-box) is the summit TreeLine has there, when it has it (else none, as without).
- --assign ADMIN1.geojson:UA-43,UA-40:ua: tops in those first-level units (Natural Earth's
  admin-1, by `iso_3166_2`) count as that country instead of the one admin-0 gives them: Crimea
  and Sevastopol, which Natural Earth draws as Russia's, are Ukraine's in TreeLine.
- --osm-peaks OSM.json (`osm_peaks.py`): OpenStreetMap first, for a database offered under the ODbL
  on a model whose licence allows it (GEDTM30, CC BY): the OSM node whose `ele` the run burned in,
  else the nearest named peak within 500 m (at a height within 150 m, if it gives one), each node
  to one top, none a summit TreeLine holds; its `name` is the name, its `name:xx` the names, with
  OpenStreetMap as their source (the name also in --local-lang where it has no `name:uk` and is in
  that script). Then Wikidata's and GeoNames' as above. A height burned from OSM names OSM as its
  source; with --review-heights, OSM, Wikidata and the model are compared: any two over --agree
  apart, W with a note; all within, O; the model alone, none.
- --no-known-lines: no line parents at all for the summits TreeLine holds.
- --keep-lines: a summit TreeLine holds that has a line parent (`line_source` in KNOWN.json) keeps
  it: the run's goes only to the summits without one (DMR 5.0's on the Slovak border stay).
- --review REVIEW.json [--compare-osm OSM.json]: the W summits, and the Wikidata items that look
  misplaced (no top of their height near their coordinate, one farther; or OSM has the item,
  by its wikidata tag, far from Wikidata's coordinate). OSM goes into this file only, for a
  person to compare: never into the changeset.
"""
import argparse
import os
import json
import math
import re

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from burn_match import OSM_KINDS, osm_ele as osm_height


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


CYRILLIC = re.compile(r"^[\u0400-\u04ff0-9'’ʼ .,()-]+$")
# a word for the kind of feature before the name proper ("гора Стіг" is Стіг): left off
KIND = re.compile(r'^(гора|г\.|вершина|полонина гора|vârful|varful|vf\.)\s+(?=\S)', re.I)
CYRILLIC_LANGS = {'uk', 'ru', 'be', 'bg', 'sr', 'mk'}
#: the script of the local language (--local-lang): Cyrillic or Latin, set in main()
SCRIPT = [CYRILLIC]


def local_form(name, script=None):
    """ `name` as the summit's own name in the local script, or None: without a leading word for
    the kind of feature or a disambiguation in brackets after it ("Стіг (гора)", "Vârful Omu"). """
    script = script or SCRIPT[0]
    if not name:
        return None
    n = re.sub(r'\s*\([^)]*\)\s*$', '', name.strip())
    n = KIND.sub('', n).strip()
    if not n or not script.match(n) or len(n) > NAME_MAX:
        return None
    return n[0].upper() + n[1:]


def review_height(model, stated, agree=20.0):
    """ (altitude_review, difference) for a height the model reads `model` m and a source states
    `stated` m: 'O' within `agree` m, else 'W'; (None, None) with nothing to compare. """
    if model is None or stated is None:
        return None, None
    d = stated - model
    return ('O' if abs(d) <= agree else 'W'), d


def review_many(said, model, model_name, agree=20.0):
    """
    altitude_review from the heights the sources state (`said`: {source: m}) and the model's: None
    with no source beside the model; else ('O', None, heights, spread) when every two agree within
    `agree` m, or ('W', note, heights, spread) when any two do not, the note giving them all.
    """
    if not said:
        return None
    heights = dict(said, **{model_name: round(model, 1)})
    spread = max(heights.values()) - min(heights.values())
    if spread <= agree:
        return 'O', None, heights, spread
    note = (f'Heights differ by up to {spread:.0f} m: '
            + ', '.join(f'{k} {v:g} m' for k, v in heights.items()) + '.')
    return 'W', note, heights, spread


def latin_name(tags):
    for k in ('name:en', 'name', 'name:zh-Latn-pinyin', 'name:zh_pinyin', 'name:ja-Latn', 'name:ko-Latn', 'int_name'):
        v = (tags.get(k) or '').strip()
        if v and LATIN.match(v) and len(v) <= NAME_MAX:
            return v
    return None


def osm_ele(e):
    try:
        return float(re.sub(r'[^\d.]', '', e.get('tags', {}).get('ele', '')) or 'nan')
    except ValueError:
        return float('nan')


def misplaced(p, tops_near, osm_of=None):
    """
    Why a Wikidata item looks misplaced, or None: no top of the run within 500 m reads its height
    within 40 m while one within 3 km reads it within 20 m (the coordinate is off), or none within
    3 km does (the height or the coordinate is off); or OSM has the item (by its wikidata tag)
    over 500 m from Wikidata's coordinate. `tops_near`: the run's tops around, {top, h}.
    """
    at = (p['lat'], p['lon'])
    why = []
    c = sorted((km(at, tuple(t['top'])), t) for t in tops_near)
    close = [(d, t) for d, t in c if d <= 0.5 and abs(p['elev'] - t['h']) <= 40]
    if not close:
        agree = [(d, t) for d, t in c if d <= 3.0 and abs(p['elev'] - t['h']) <= 20]
        if agree:
            d, t = agree[0]
            why.append(f'no top of its height within 500 m; one {d:.1f} km away reads {t["h"]:.0f} m '
                       f'({t["top"][0]:.5f}, {t["top"][1]:.5f})')
        else:
            near = c[0] if c else None
            why.append('no top within 3 km reads its height within 20 m' +
                       (f'; the nearest, {near[0] * 1000:.0f} m away, reads {near[1]["h"]:.0f} m' if near else ''))
    if osm_of:
        d = km(at, (osm_of['lat'], osm_of['lon']))
        if d > 0.5:
            why.append(f'OSM has this item {d:.1f} km away ({osm_of["lat"]:.5f}, {osm_of["lon"]:.5f}, '
                       f'ele {osm_of.get("tags", {}).get("ele", "-")})')
    return why or None


def write_review(path, warned, peak_of, all_tops, inside, osm, local_label, claimed, latin_only=(), duplicate=()):
    """ REVIEW.json: the W summits, with the OSM peak nearest each for comparison, and the
    Wikidata items in the area kept that look misplaced (`misplaced`). """
    TCELL = 0.05
    tgrid = grid(list(all_tops.values()), TCELL, lambda t: tuple(t['top']))
    ogrid = grid(osm, 0.01, lambda e: (e['lat'], e['lon']))
    osm_by_q = {e['tags']['wikidata']: e for e in osm if e.get('tags', {}).get('wikidata')}
    for w in warned:
        o = min(((km((w['lat'], w['lon']), (e['lat'], e['lon'])), e) for e in around(ogrid, 0.01, (w['lat'], w['lon']))),
                key=lambda t: t[0], default=None)
        if o and o[0] <= 0.5:
            w['osm'] = dict(name=o[1]['tags'].get('name'), ele=o[1]['tags'].get('ele'), km=round(o[0], 3))
    odd = []
    for q, p in sorted(peak_of.items()):
        if not inside(p['lat'], p['lon']):
            continue
        why = misplaced(p, [t for t in around(tgrid, TCELL, (p['lat'], p['lon']))], osm_by_q.get(q))
        if why:
            odd.append(dict(qid=q, label=p.get('label'), local_label=local_label(q), lat=p['lat'], lon=p['lon'],
                            precision=p['precision'], elev=p['elev'],
                            database_has_it=claimed(p['lat'], p['lon'], [p.get('label'), local_label(q)]), why=why))
    json.dump(dict(note='OSM appears here for comparison only; nothing of it is in the changeset.',
                   warnings=sorted(warned, key=lambda w: -abs(w['difference'])), misplaced_wikidata=odd,
                   unnamed_latin_only=list(latin_only), unnamed_repeated_name=list(duplicate)),
              open(path, 'w'), ensure_ascii=False, indent=1)
    print(len(warned), 'W summits and', len(odd), 'Wikidata items that look misplaced ->', path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run')
    ap.add_argument('known')
    ap.add_argument('osm')
    ap.add_argument('admin0')
    ap.add_argument('out')
    ap.add_argument('--countries', help='ISO codes, comma separated: the tops kept')
    ap.add_argument('--exclude', help='ISO codes, comma separated: the tops left out (the rest kept)')
    ap.add_argument('--exclude-box', action='append', default=[],
                    help='S,N,W,E: tops in this box left out too (French Guiana, which counts as France)')
    ap.add_argument('--wikidata', help="names from Wikidata (wikidata_peaks.py's PEAKS.json, CC0) instead of OSM")
    ap.add_argument('--geonames', help='a directory of GeoNames (CC BY 4.0): summits.json, summit-alternates.json, '
                                       'country-languages.tsv, languages-wikidata.json, treeline-languages.json')
    ap.add_argument('--match', default='0.2,1.0,1.5',
                    help="a summit TreeLine holds: within KM, or FAR km at a height within DH m (KM,FAR,DH)")
    ap.add_argument('--fill', help='FILL.json: a second changeset giving summits TreeLine holds without a key col '
                                   "the run's (applied after OUT.json, whose new summits it may name as parents)")
    ap.add_argument('--coarse', help="directory of the GLO-90 filler tiles: summits there are noted less precise")
    ap.add_argument('--min-prominence', type=float, default=100.0)
    ap.add_argument('--source', required=True, help='the elevation data, as the Source is to be named')
    ap.add_argument('--lang', help="the local names' language (ISO 639-1); not with --wikidata")
    ap.add_argument('--datum', default='EGM2008', help="the heights' vertical datum, as VerticalDatum.code")
    ap.add_argument('--assign', action='append', default=[],
                    help='ADMIN1.geojson:ISO_3166_2,...:CODE: those first-level units count as country CODE '
                         '(Crimea and Sevastopol, which Natural Earth gives Russia, as Ukraine: ...:UA-43,UA-40:ua)')
    ap.add_argument('--local-lang', help="the name in this language's script (uk: Cyrillic), from --labels or GeoNames")
    ap.add_argument('--labels', help="wikidata_labels.py's LABELS.json: the items' labels in --local-lang")
    ap.add_argument('--review-heights', action='store_true', help="altitude_review by the Wikidata item's height")
    ap.add_argument('--agree', type=float, default=20.0, help='m: heights this close agree (O), farther W')
    ap.add_argument('--parents-known', action='store_true',
                    help='parents outside the tops kept: the summit TreeLine has there')
    ap.add_argument('--osm-peaks', help="osm_peaks.py's OSM.json: the name and height first (ODbL: for a database "
                                        'under the ODbL, on a model that allows it)')
    ap.add_argument('--no-known-lines', action='store_true', help='no line parents for summits TreeLine holds')
    ap.add_argument('--keep-lines', action='store_true',
                    help="a summit TreeLine holds with a line parent keeps it (KNOWN.json's line_source)")
    ap.add_argument('--review', help='REVIEW.json: the W summits and the Wikidata items that look misplaced')
    ap.add_argument('--compare-osm', help='OSM peaks (Overpass JSON), compared in REVIEW.json only')
    ap.add_argument('--reference', help='JSON of the countries and languages the database may lack '
                                        '({countries: [{code, name, full_name, english_name}], languages: [...]})')
    a = ap.parse_args()

    SCRIPT[0] = CYRILLIC if (a.local_lang or 'uk') in CYRILLIC_LANGS else LATIN
    run = json.load(open(a.run))
    known = json.load(open(a.known))
    osm = [e for e in json.load(open(a.osm))['elements'] if e.get('tags', {}).get('name')]
    fs = json.load(open(a.admin0))['features']
    keep = set(a.countries.split(',')) if a.countries else None
    drop = set(a.exclude.split(',')) if a.exclude else set()
    near_km, far_km, far_dh = (float(v) for v in a.match.split(','))
    line_cutoff = run['stats'].get('line_cutoff')

    geoms = [shape(f['geometry']) for f in fs]
    # Disputed areas (Natural Earth's -99) under whoever holds them, as TreeLine records Taiwan:
    # Somaliland and Northern Cyprus have no ISO code, so user-assigned ones (xs, xc)
    held = {'KAS': 'in', 'SOL': 'xs', 'CYN': 'xc', 'ESB': 'gb', 'WSB': 'gb', 'USG': 'us', 'CNM': 'cy'}
    codes = [f['properties']['ISO_A2_EH'].lower() if f['properties']['ISO_A2_EH'] != '-99'
             else held.get(f['properties']['ADM0_A3'], '') for f in fs]
    tree = STRtree(geoms)

    assigned = []                            # (geometry, the code it replaces, the code it takes)
    for spec in a.assign:
        path, units, to = spec.rsplit(':', 2)
        units = set(units.split(','))
        for f in json.load(open(path))['features']:
            pr = f['properties']
            if pr.get('iso_3166_2') in units:
                assigned.append((shape(f['geometry']), (pr.get('iso_a2') or '').lower(), to))
                if not assigned[-1][1] or assigned[-1][1] == '-99':
                    held_by = next((codes[i] for i, g in enumerate(fs) if g['properties']['ADM0_A3'] == pr['adm0_a3']), '')
                    assigned[-1] = (assigned[-1][0], held_by, to)
        print(f'{len(assigned)} areas of {units} assigned to {to}')

    def countries(lat, lon):
        p = Point(lon, lat)
        hit = {codes[i] for i in tree.query(p.buffer(0.01), predicate='intersects')} - {''}
        hit = hit or {c for c in [codes[tree.nearest(p)]] if c}
        for geom, frm, to in assigned:
            if geom.intersects(p.buffer(0.01)):
                if geom.contains(p) or not hit - {frm}:
                    hit.discard(frm)
                hit.add(to)
        return sorted(hit)

    all_tops = {tuple(x['top']): x for x in run['summits']}
    tops = [x for x in run['summits'] if x['prom'] >= a.min_prominence]
    for x in tops:
        x['at'] = tuple(x['top'])
        x['countries'] = countries(*x['at'])
    boxes = [tuple(float(v) for v in b.split(',')) for b in a.exclude_box]
    tops = [x for x in tops if (keep is None or keep & set(x['countries'])) and not drop & set(x['countries'])
            and not any(b[0] <= x['at'][0] <= b[1] and b[2] <= x['at'][1] <= b[3] for b in boxes)]
    by_pos = {x['at']: x for x in tops}
    print(len(tops), f'tops of P >= {a.min_prominence:g} in', sorted(keep) if keep else f'all but {sorted(drop)}')

    KCELL = 0.02
    kgrid = grid(known, KCELL, lambda k: (k['lat'], k['lon']))

    def known_near(at, h=None, used=frozenset()):
        c = [(km(at, (k['lat'], k['lon'])), k) for k in around(kgrid, KCELL, at) if k['pk'] not in used]
        close = [t for t in c if t[0] <= near_km]
        if close:
            return min(close, key=lambda t: t[0])[1]
        # farther, at about its height: the most prominent such summit (not a subsidiary top of it)
        far = [t for t in c if t[0] <= far_km and h is not None and t[1]['alt'] is not None
               and abs(t[1]['alt'] - h) <= far_dh]
        return max(far, key=lambda t: (t[1].get('prom') or 0, -t[0]))[1] if far else None

    labels = json.load(open(a.labels)) if a.labels else {}

    def local_label(qid):
        return local_form(labels.get(qid, {}).get(a.local_lang)) if a.local_lang else None

    def norm_name(n):
        return re.sub(r'\s+', ' ', (n or '').lower().replace('ʼ', "'").replace('’', "'")).strip()

    def known_claims(lat, lon, names):
        """ Whether a summit TreeLine holds is this item's: within 300 m, or named so within 3 km. """
        names = {norm_name(n) for n in names if n} - {''}
        for k in around(kgrid, KCELL, (lat, lon)):
            d = km((lat, lon), (k['lat'], k['lon']))
            if d <= 0.3:
                return True
        if names:
            for k in around(k3grid, K3CELL, (lat, lon)):
                if norm_name(k['name']) in names and km((lat, lon), (k['lat'], k['lon'])) <= 3.0:
                    return True
        return False
    K3CELL = 0.05                            # wider than 3 km, at these latitudes and below 60°
    k3grid = grid(known, K3CELL, lambda k: (k['lat'], k['lon']))

    # each summit TreeLine holds stands for one top: the most prominent that finds it
    used = set()
    for x in sorted(tops, key=lambda x: -x['prom']):
        x['db'] = known_near(x['at'], x['h'], used)
        if x['db']:
            used.add(x['db']['pk'])
    new = [x for x in tops if x['db'] is None]
    key = lambda x: f'fab:{x["at"][0]:.6f},{x["at"][1]:.6f}'

    # names from Wikidata: the item whose height the run took, else the nearest item within 500 m (or
    # within its stated imprecision, up to 2 km) at a height within 150 m; each item to one top
    wd_of = {}
    if a.wikidata:
        WCELL = 0.02
        wgrid = grid(new, WCELL, lambda x: x['at'])
        for x in new:
            hf = x.get('height_from')
            if hf and hf.get('source', 'Wikidata') == 'Wikidata' and (hf.get('label') or local_label(hf.get('qid'))):
                wd_of[key(x)] = (0.0, hf['qid'], hf.get('label') or '')
        taken_q = {v[1] for v in wd_of.values()}
        pairs = []
        for p in json.load(open(a.wikidata)):
            if not (p.get('label') or local_label(p['qid'])) or p['qid'] in taken_q or p['elev'] is None:
                continue
            if a.local_lang and known_claims(p['lat'], p['lon'], [p.get('label'), local_label(p['qid'])]):
                continue
            reach = 1.0 if p['precision'] is not None and p['precision'] <= 0.001 else \
                min(2.0, max(0.5, (p['precision'] or 0.01) * 111))
            for x in around(wgrid, WCELL, (p['lat'], p['lon'])):
                if key(x) in wd_of:
                    continue
                d, dh = km(x['at'], (p['lat'], p['lon'])), abs(x['h'] - p['elev'])
                if d <= reach and dh <= 150:
                    pairs.append((d / reach + dh / 150, key(x), p['qid'], p['label'] or ''))
        pairs.sort()
        for score, k, q, label in pairs:
            if k not in wd_of and q not in taken_q:
                wd_of[k] = (score, q, label)
                taken_q.add(q)
        print(len(wd_of), 'of', len(new), 'new tops named from Wikidata')
    # names from GeoNames, every language it has, for any new top (beside Wikidata's): the nearest
    # mountain, peak, hill, volcano or nunatak within 1 km at a height within 150 m (if it gives one)
    gn_of, gn_alts, lang_code, local_langs, new_langs = {}, {}, {}, {}, {}
    tl_codes = None
    if a.geonames:
        G = [g for g in json.load(open(os.path.join(a.geonames, 'summits.json')))
             if g['code'] in ('MT', 'PK', 'HLL', 'VLC', 'NTK', 'PKU', 'MTU', 'HLLU')]
        gn_alts = json.load(open(os.path.join(a.geonames, 'summit-alternates.json')))
        for line in open(os.path.join(a.geonames, 'country-languages.tsv')):
            cc, langs = (line.rstrip('\n').split('\t') + [''])[:2]
            local_langs[cc] = [lg.split('-')[0] for lg in langs.split(',') if lg][:1]
        # each language's own country, for its flag: the most populous that lists it first
        home_of, home_any = {}, {}
        for line in open(os.path.join(a.geonames, 'countryInfo.txt'), encoding='utf-8'):
            if line.startswith('#'):
                continue
            c = line.rstrip('\n').split('\t')
            if len(c) > 15 and c[15]:
                spoken = [lg.split('-')[0] for lg in c[15].split(',') if lg]
                pop = int(c[7] or 0)
                if spoken and (spoken[0] not in home_of or pop > home_of[spoken[0]][1]):
                    home_of[spoken[0]] = (c[0].lower(), pop)
                for lg in spoken[1:]:           # listed, not first: only if no country lists it first
                    if lg not in home_any or pop > home_any[lg][1]:
                        home_any[lg] = (c[0].lower(), pop)
        tl = json.load(open(os.path.join(a.geonames, 'treeline-languages.json')))
        tl_codes, tl_by_en = {t['code'] for t in tl}, {t['english_name'].lower(): t['code'] for t in tl}

        def tl_match(english):
            """ TreeLine's code for a language by its English name, the words of one within the
            other's ('Modern Greek' is TreeLine's 'Greek', gr; 'Standard Tibetan' its Tibetan, xc). """
            e = english.lower()
            if e in tl_by_en:
                return tl_by_en[e]
            words = set(e.split())
            for name, code in tl_by_en.items():
                if set(name.split()) <= words or words <= set(name.split()):
                    return code
            return None
        wl = {}
        for b in json.load(open(os.path.join(a.geonames, 'languages-wikidata.json')))['results']['bindings']:
            c = b['code']['value']
            wl.setdefault(c, dict(en=b['en']['value'], native=b.get('native', {}).get('value')))

        def code_for(raw):
            """ TreeLine's code for a GeoNames language: its own where TreeLine has it under another
            (Greek is gr there), a new one if Wikidata knows it, else none (left out). """
            if raw in lang_code:
                return lang_code[raw]
            c = None
            if len(raw) == 2:
                if raw in tl_codes:
                    c = raw
                elif raw in wl and tl_match(wl[raw]['en']):
                    c = tl_match(wl[raw]['en'])
                elif raw in wl:
                    c = raw
                    new_langs.setdefault(c, dict(iso639_1=c, name=(wl[c]['native'] or wl[c]['en'])[:64],
                                                 english_name=wl[c]['en'][:64], countries={}))
            lang_code[raw] = c
            return c
        GCELL = 0.05
        ggrid = grid(G, GCELL, lambda g: (g['lat'], g['lon']))
        pairs = []
        claimed_g = {}

        def g_claimed(g):
            if g['id'] not in claimed_g:
                claimed_g[g['id']] = known_claims(g['lat'], g['lon'], [g['name']] + [
                    t['name'] for t in gn_alts.get(str(g['id']), []) if t['lang'] in (a.local_lang, 'en')])
            return claimed_g[g['id']]
        for x in new:
            for g in around(ggrid, GCELL, x['at']):
                if a.local_lang and g_claimed(g):
                    continue
                d, h = km(x['at'], (g['lat'], g['lon'])), g['elev']
                if d <= 1.0 and (h is None or abs(h - x['h']) <= 150):
                    pairs.append((d + (abs(h - x['h']) / 150 if h is not None else 0.5), key(x), g))
        pairs.sort(key=lambda t: t[0])
        used_g = set()
        for _, k, g in pairs:
            if k not in gn_of and g['id'] not in used_g:
                gn_of[k] = g
                used_g.add(g['id'])
        print(len(gn_of), 'of', len(new), 'new tops matched to GeoNames')

    # OSM's names and heights first (--osm-peaks): the node the run burned, else the nearest named peak
    osm_of, osm_claimed = {}, {}
    if a.osm_peaks:
        O = [e for e in json.load(open(a.osm_peaks)) if e.get('kind') in OSM_KINDS and e.get('name')]
        by_id = {e['id']: e for e in O}
        for x in new:
            hf = x.get('height_from')
            if hf and hf.get('source') == 'OpenStreetMap' and int(hf['qid'][4:]) in by_id:
                e = by_id[int(hf['qid'][4:])]
                if known_claims(e['lat'], e['lon'], [e['name'], e['other_names'].get(a.local_lang or 'en')]):
                    # its height burned, but its name is a summit's TreeLine holds: this top is not that
                    # summit (it was matched elsewhere), so it takes no name at all
                    osm_claimed[key(x)] = e
                else:
                    osm_of[key(x)] = e
        taken_o = {e['id'] for e in osm_of.values()}
        OCELL = 0.01
        ogrid = grid(new, OCELL, lambda x: x['at'])
        pairs = []
        for e in O:
            if e['id'] in taken_o:
                continue
            h = osm_height(e.get('ele'))
            for x in around(ogrid, OCELL, (e['lat'], e['lon'])):
                d = km(x['at'], (e['lat'], e['lon']))
                if key(x) not in osm_of and d <= 0.5 and (h is None or abs(h - x['h']) <= 150):
                    pairs.append((d / 0.5 + (abs(h - x['h']) / 150 if h is not None else 0.5), key(x), e['id']))
        pairs.sort()
        claimed_o = {}
        for _, k, i in pairs:
            if k in osm_of or i in taken_o:
                continue
            e = by_id[i]
            if i not in claimed_o:
                claimed_o[i] = known_claims(e['lat'], e['lon'], [e['name'], e['other_names'].get(a.local_lang or 'en')])
            if not claimed_o[i]:
                osm_of[k] = e
                taken_o.add(i)
        print(len(osm_of), 'of', len(new), 'new tops named from OSM')

    coarse = set()
    if a.coarse:
        for f in os.listdir(a.coarse):
            m = re.match(r'Copernicus_DSM_COG_30_([NS])(\d+)_00_([EW])(\d+)_00_DEM', f)
            if m:
                coarse.add(((1 if m[1] == 'N' else -1) * int(m[2]), (1 if m[3] == 'E' else -1) * int(m[4])))
    in_coarse = lambda at: (math.floor(at[0]), math.floor(at[1])) in coarse

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

    summits, stats, warned, latin_only = [], dict(named=0, island=0, stepped=0), [], []
    given, duplicate = {}, []                # names given so far, by grid cell; names refused as repeats
    peak_of = {p['qid']: p for p in json.load(open(a.wikidata)) if p['elev'] is not None} if a.wikidata else {}

    def known_beyond(at):
        """ The summit TreeLine holds at a top of the run outside the tops kept, or None. """
        q = all_tops.get(tuple(at))
        return known_near(tuple(at), q['h']) if q else None

    for x in sorted(new, key=lambda x: -x['prom']):
        tags = tags_of.get(key(x))
        name = latin_name(tags) if tags else None
        names = []
        wd = wd_of.get(key(x))
        g = gn_of.get(key(x))
        if wd or g:
            # every name on offer, each with its source; the ID name the best known one that can be
            # typed: Wikidata's English label, GeoNames' English (preferred first), its name, its ASCII
            cands = []
            if wd and wd[2].strip():
                names.append(dict(lang='en', name=wd[2].strip()[:64], local=False, source='Wikidata'))
                cands.append(wd[2].strip())
            if g:
                home = local_langs.get(x['countries'][0] if x['countries'] else '', [])
                alts = gn_alts.get(str(g['id']), [])
                for alt in sorted(alts, key=lambda t: (not t['preferred'], t['historic'], t['colloquial'])):
                    c = code_for(alt['lang'])
                    if not c or len(alt['name']) > 64:
                        continue
                    names.append(dict(lang=c, name=alt['name'], local=alt['lang'] in home,
                                      historical=alt['historic'], source='GeoNames'))
                    if c in new_langs:
                        cc = (x['countries'] or [''])[0]
                        new_langs[c]['countries'][cc] = new_langs[c]['countries'].get(cc, 0) + 1
                cands += [t['name'] for t in alts if t['lang'] == 'en' and t['preferred']]
                cands += [t['name'] for t in alts if t['lang'] == 'en' and not t['historic']]
                cands += [g['name'], g['ascii']]
            name = next((c.strip() for c in cands if c and LATIN.match(c.strip()) and len(c.strip()) <= NAME_MAX), None)
            if a.local_lang:
                # the name in the local script: Wikidata's label, else GeoNames' (preferred first);
                # GeoNames' names in that language as their own names, in its script only
                loc = local_label(wd[1]) if wd else None
                loc_src = 'Wikidata'
                gl = [n for n in names if n['lang'] == a.local_lang and n.get('source') == 'GeoNames']
                names = [n for n in names if n not in gl]
                gl = [dict(n, name=local_form(n['name'])) for n in gl if local_form(n['name'])]
                if not loc:
                    loc = next((n['name'] for n in gl if not n.get('historical')), None)
                    loc_src = 'GeoNames'
                if loc and (any(norm_name(o['name']) == norm_name(loc) and km(o['at'], x['at']) <= 3.0
                                for o in around(given, K3CELL, x['at']))
                            or known_claims(x['at'][0], x['at'][1], [loc]) and not wd):
                    # the name of a more prominent top near (or a summit TreeLine has): the same
                    # mountain under two items (Wikidata's bot copies of GeoNames), not this top's
                    duplicate.append(dict(key=key(x), lat=x['at'][0], lon=x['at'][1], h=round(x['h'], 1),
                                          prom=round(x['prom'], 1), name=loc, source=loc_src,
                                          wikidata=wd[1] if wd else None, geonames=g['id'] if g else None))
                    loc, name, names = None, None, []
                if loc:
                    given.setdefault((math.floor(x['at'][0] / K3CELL), math.floor(x['at'][1] / K3CELL)), []).append(
                        dict(name=loc, at=x['at']))
                    names = [dict(lang=a.local_lang, name=loc, local=True, source=loc_src)] + gl + names
                    name = loc
                    stats['local_name'] = stats.get('local_name', 0) + 1
                elif name:
                    # no name in the local script: none, rather than a romanisation (often of
                    # another language's form); its Latin name for a person to look at
                    latin_only.append(dict(key=key(x), lat=x['at'][0], lon=x['at'][1], h=round(x['h'], 1),
                                           prom=round(x['prom'], 1), latin=name, wikidata=wd[1] if wd else None,
                                           geonames=g['id'] if g else None))
                    name = None
            seen_n = set()
            names = [n for n in names if (n['lang'], n['name']) not in seen_n and not seen_n.add((n['lang'], n['name']))]
        elif tags:
            local = (tags.get(f'name:{a.lang}') or tags.get('name') or '').strip()
            if local and local != name and len(local) <= 64:
                names.append(dict(lang=a.lang, name=local, local=True))
            if tags.get('name:en') and tags['name:en'] != name:
                names.append(dict(lang='en', name=tags['name:en'].strip(), local=False))
        e = osm_of.get(key(x))
        if key(x) in osm_claimed:
            c = osm_claimed[key(x)]
            duplicate.append(dict(key=key(x), lat=x['at'][0], lon=x['at'][1], h=round(x['h'], 1), prom=round(x['prom'], 1),
                                  name=c['name'], source='OpenStreetMap', osm=c['id'],
                                  why='the name of a summit TreeLine holds within 3 km'))
            name, names = None, []
        elif e:
            # OSM's name first: `name` the name, its `name:xx` names (the languages TreeLine has)
            nm = e['name'].strip()
            if len(nm) > NAME_MAX or any(norm_name(o['name']) == norm_name(nm) and km(o['at'], x['at']) <= 3.0
                                         and o['at'] != x['at'] for o in around(given, K3CELL, x['at'])):
                duplicate.append(dict(key=key(x), lat=x['at'][0], lon=x['at'][1], h=round(x['h'], 1),
                                      prom=round(x['prom'], 1), name=nm, source='OpenStreetMap', osm=e['id']))
                name, names = None, []
            else:
                home = set(local_langs.get(x['countries'][0] if x['countries'] else '', [])) | {a.local_lang}
                on = [dict(lang=lg, name=v.strip(), local=lg in home, source='OpenStreetMap')
                      for lg, v in sorted(e['other_names'].items())
                      if len(lg) == 2 and v.strip() and len(v.strip()) <= 64 and (tl_codes is None or lg in tl_codes)]
                if a.local_lang and a.local_lang not in e['other_names'] and local_form(nm) == nm:
                    on.insert(0, dict(lang=a.local_lang, name=nm, local=True, source='OpenStreetMap'))
                if name and name != nm:
                    stats['osm_over_other'] = stats.get('osm_over_other', 0) + 1
                name = nm
                given.setdefault((math.floor(x['at'][0] / K3CELL), math.floor(x['at'][1] / K3CELL)), []).append(
                    dict(name=nm, at=x['at']))
                seen_n = set()
                names = [n for n in on + names
                         if (n['lang'], n['name']) not in seen_n and not seen_n.add((n['lang'], n['name']))]
                stats['osm_name'] = stats.get('osm_name', 0) + 1
        rec = dict(key=key(x), lat=x['at'][0], lon=x['at'][1], name=name, names=names,
                   countries=x['countries'], altitude=round(x['h'], 2), datum=a.datum,
                   notes=[f'{a.source} at 1″, P {x["prom"]:.0f} m.'])
        if in_coarse(x['at']) or ('col' in x and in_coarse(tuple(x['col']))):
            rec['notes'].append('Less precise: Copernicus GLO-90 (90 m) here, where GLO-30 and FABDEM are withheld.')
            stats['coarse'] = stats.get('coarse', 0) + 1
        hf = x.get('height_from')
        if hf and hf.get('source', 'Wikidata') not in ('Wikidata', 'OpenStreetMap'):
            # the database's own height, burned in (burn_match.py --known): a top the database has,
            # found here only if the matching missed it; the model's terrain, the height as burned
            rec['notes'].append(f'The height is the one TreeLine had for summit #{hf.get("db")} '
                                f'({hf.get("label") or "unnamed"}), burned in; the model read {hf["model"]:.0f} m.')
            stats['database_height'] = stats.get('database_height', 0) + 1
            hf = None
        if hf:
            # the height is Wikidata's (burned in before the flood, or capped after it): cite it,
            # datum unknown (Wikidata's heights come in mixed national systems); the col stays the model's
            how = ('raised into the model before the flood' if hf['how'] == 'burned'
                   else 'capped after the flood (the model read it high)')
            src = hf.get('source', 'Wikidata')
            rec.update(source=src, datum=None)
            whose = (f'OpenStreetMap node {hf["qid"][4:]}\'s `ele`' if src == 'OpenStreetMap'
                     else f'Wikidata\'s {hf["qid"]}')
            rec['notes'][0:1] = [f'{a.source} at 1″ for the terrain, P {x["prom"]:.0f} m; the height is {whose} '
                                 f'({hf.get("label") or "unlabelled"}), {how}; the model read {hf["model"]:.0f} m.']
            stats['wikidata' if src == 'Wikidata' else 'osm_height'] = \
                stats.get('wikidata' if src == 'Wikidata' else 'osm_height', 0) + 1
        if a.review_heights and a.osm_peaks:
            # how far the height can be trusted: OSM's, Wikidata's and the model's, each pair
            model = hf['model'] if hf else x['h']
            said = {}
            if e and osm_height(e.get('ele')) is not None:
                said[f'OpenStreetMap node {e["id"]}'] = osm_height(e.get('ele'))
            if wd and wd[1] in peak_of:
                said[f'Wikidata {wd[1]}'] = peak_of[wd[1]]['elev']
            r = review_many(said, model, a.source, a.agree)
            if r:
                rec['altitude_review'] = r[0]
                stats['review_' + r[0]] = stats.get('review_' + r[0], 0) + 1
                if r[0] == 'W':
                    rec['notes'].append(r[1])
                    warned.append(dict(key=rec['key'], name=name, lat=rec['lat'], lon=rec['lon'], heights=r[2],
                                       difference=round(r[3], 1), stored=rec['altitude'], burned=bool(hf),
                                       burned_from=hf['qid'] if hf else None, prominence=x['prom']))
            else:
                stats['review_none'] = stats.get('review_none', 0) + 1
        elif a.review_heights:
            # how far the height can be trusted: the model against the Wikidata item's height
            stated = qid = model = None
            if hf and hf['qid'] in peak_of:
                qid, model = hf['qid'], hf['model']
            elif wd and wd[1] in peak_of:
                qid, model = wd[1], x['h']
            if qid:
                stated = peak_of[qid]['elev']
            r, d = review_height(model, stated, a.agree)
            if r:
                rec['altitude_review'] = r
                stats['review_' + r] = stats.get('review_' + r, 0) + 1
            else:
                stats['review_none'] = stats.get('review_none', 0) + 1
            if r == 'W':
                rec['notes'].append(f'Heights differ by {abs(d):.0f} m: Wikidata {qid} states {stated:g} m, '
                                    f'{a.source} reads {model:.0f} m here ({d:+.0f} m).')
                warned.append(dict(key=rec['key'], name=name, lat=rec['lat'], lon=rec['lon'], qid=qid,
                                   label=peak_of[qid].get('label'), local_label=local_label(qid), wikidata=stated,
                                   wikidata_at=[peak_of[qid]['lat'], peak_of[qid]['lon']], model=round(model, 1),
                                   difference=round(d, 1), stored=rec['altitude'], burned=bool(hf), prominence=x['prom']))
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
            elif a.parents_known and x.get('parent'):
                kp = known_beyond(x['parent'])
                if kp:
                    rec['parent'] = dict(at=[kp['lat'], kp['lon']])
                    stats['parent_known'] = stats.get('parent_known', 0) + 1
                else:
                    stats['parent_missing'] = stats.get('parent_missing', 0) + 1
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
        if a.keep_lines and x['db'] and x['db'].get('line_source'):
            continue
        if a.no_known_lines and x['db']:
            continue
        if 'col' not in x:
            lines.append(dict(summit_at=where[x['at']], parent_at=None, cutoff=line_cutoff, source=a.source))
        elif x.get('line') and tuple(x['line']) in where:
            lines.append(dict(summit_at=where[x['at']], parent_at=where[tuple(x['line'])], cutoff=line_cutoff,
                              source=a.source))
        elif x.get('line') and a.parents_known and known_beyond(x['line']):
            kp = known_beyond(x['line'])
            lines.append(dict(summit_at=where[x['at']], parent_at=[kp['lat'], kp['lon']], cutoff=line_cutoff,
                              source=a.source))

    reference = json.load(open(a.reference)) if a.reference else {}
    if new_langs:
        # languages the names come in that TreeLine lacks: flagged by the country most of their
        # summits stand in
        langs = []
        taken_names = set()
        for c, d in sorted(new_langs.items()):
            if not d['countries']:
                continue
            nm = d['name'] if d['name'] not in taken_names else f"{d['name']} ({c})"
            taken_names.add(nm)
            flag = (home_of.get(c) or home_any.get(c) or (None,))[0] or max(d['countries'], key=d['countries'].get) or c
            langs.append(dict(iso639_1=c, name=nm, english_name=d['english_name'], country_code=flag, priority=100))
        reference = dict(reference or {})
        reference['languages'] = reference.get('languages', []) + langs
        print(len(langs), 'languages TreeLine lacks, in the reference')
    if a.fill:
        # Summits TreeLine holds with no key col (nor island): the run's key col and parent; never
        # replacing a col a person set
        relinks = []
        matched = {x['db']['pk']: x for x in tops if x['db']}
        kcols = grid([k for k in known if 'col_lat' in k], KCELL, lambda k: (k['col_lat'], k['col_lon']))
        todo = [x for x in tops if x['db'] and not x['db'].get('has_col') and not x['db'].get('island') and 'col' in x]
        done = set()
        while todo:
            x = todo.pop()
            k = x['db']
            if k['pk'] in done or 'col' not in x:
                continue
            done.add(k['pk'])
            # The col it is to get may be another summit's in TreeLine (an earlier import's box edge:
            # Denali's in Panama): that summit takes the run's own key col in the same batch
            for o in around(kcols, KCELL, tuple(x['col'])):
                if o['pk'] != k['pk'] and km(tuple(x['col']), (o['col_lat'], o['col_lon'])) <= 0.2 \
                        and o['pk'] in matched and o['pk'] not in done:
                    todo.append(matched[o['pk']])
            link = dict(summit_at=dict(lat=k['lat'], lon=k['lon']), summit_name=k['name'],
                        key_col=dict(new=dict(lat=x['col'][0], lon=x['col'][1], name=None, names=[],
                                              altitude=round(x['col_h'], 2), datum=a.datum,
                                              countries=countries(*x['col']),
                                              notes=[f'{a.source} at 1″: key col of {k["name"] or "a summit"}, '
                                                     f'P {x["h"] - x["col_h"]:.0f} m in the model.'])))
            if x.get('parent') and tuple(x['parent']) in where:
                pa = where[tuple(x['parent'])]
                link['parent_at'] = dict(lat=pa[0], lon=pa[1])
            elif x.get('parent'):
                # beyond the tops kept (South America for Denali): the summit TreeLine has there
                q = all_tops.get(tuple(x['parent']))
                kp = q and known_near(tuple(x['parent']), q['h'])
                if kp:
                    link['parent_at'] = dict(lat=kp['lat'], lon=kp['lon'])
            relinks.append(link)
        json.dump(dict(sources=dict(lidar=a.source, names='Wikidata' if a.wikidata else 'OpenStreetMap'),
                       relinks=relinks), open(a.fill, 'w'), ensure_ascii=False)
        print(len(relinks), 'summits TreeLine holds without a key col: given the run\'s ->', a.fill)
    if a.review:
        write_review(a.review, warned, peak_of, all_tops, lambda lat, lon: (
            (keep is None or keep & set(countries(lat, lon))) and not drop & set(countries(lat, lon))
            and not any(b[0] <= lat <= b[1] and b[2] <= lon <= b[3] for b in boxes)),
            json.load(open(a.compare_osm))['elements'] if a.compare_osm else [], local_label,
            lambda lat, lon, names: known_claims(lat, lon, names), latin_only, duplicate)
    json.dump(dict(sources=dict(lidar=a.source, names='Wikidata' if a.wikidata else 'OpenStreetMap'), reference=reference, summits=summits,
                   line_parents=lines),
              open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(len(summits), 'new summits', stats, f'{len(tops) - len(new)} already in the database;',
          len(lines), 'line parents ->', a.out)


if __name__ == '__main__':
    main()
