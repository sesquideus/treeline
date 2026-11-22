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
"""
import argparse
import os
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
    ap.add_argument('--reference', help='JSON of the countries and languages the database may lack '
                                        '({countries: [{code, name, full_name, english_name}], languages: [...]})')
    a = ap.parse_args()

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

    def countries(lat, lon):
        p = Point(lon, lat)
        hit = sorted({codes[i] for i in tree.query(p.buffer(0.01), predicate='intersects')} - {''})
        return hit or [c for c in [codes[tree.nearest(p)]] if c]

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
            if hf and hf.get('label'):
                wd_of[key(x)] = (0.0, hf['qid'], hf['label'])
        taken_q = {v[1] for v in wd_of.values()}
        pairs = []
        for p in json.load(open(a.wikidata)):
            if not p.get('label') or p['qid'] in taken_q or p['elev'] is None:
                continue
            reach = 1.0 if p['precision'] is not None and p['precision'] <= 0.001 else \
                min(2.0, max(0.5, (p['precision'] or 0.01) * 111))
            for x in around(wgrid, WCELL, (p['lat'], p['lon'])):
                if key(x) in wd_of:
                    continue
                d, dh = km(x['at'], (p['lat'], p['lon'])), abs(x['h'] - p['elev'])
                if d <= reach and dh <= 150:
                    pairs.append((d / reach + dh / 150, key(x), p['qid'], p['label']))
        pairs.sort()
        for score, k, q, label in pairs:
            if k not in wd_of and q not in taken_q:
                wd_of[k] = (score, q, label)
                taken_q.add(q)
        print(len(wd_of), 'of', len(new), 'new tops named from Wikidata')
    # names from GeoNames, every language it has, for any new top (beside Wikidata's): the nearest
    # mountain, peak, hill, volcano or nunatak within 1 km at a height within 150 m (if it gives one)
    gn_of, gn_alts, lang_code, local_langs, new_langs = {}, {}, {}, {}, {}
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
        for x in new:
            for g in around(ggrid, GCELL, x['at']):
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

    summits, stats = [], dict(named=0, island=0, stepped=0)
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
            if wd:
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
            seen_n = set()
            names = [n for n in names if (n['lang'], n['name']) not in seen_n and not seen_n.add((n['lang'], n['name']))]
        elif tags:
            local = (tags.get(f'name:{a.lang}') or tags.get('name') or '').strip()
            if local and local != name and len(local) <= 64:
                names.append(dict(lang=a.lang, name=local, local=True))
            if tags.get('name:en') and tags['name:en'] != name:
                names.append(dict(lang='en', name=tags['name:en'].strip(), local=False))
        rec = dict(key=key(x), lat=x['at'][0], lon=x['at'][1], name=name, names=names,
                   countries=x['countries'], altitude=round(x['h'], 2), datum=a.datum,
                   notes=[f'{a.source} at 1″, P {x["prom"]:.0f} m.'])
        if in_coarse(x['at']) or ('col' in x and in_coarse(tuple(x['col']))):
            rec['notes'].append('Less precise: Copernicus GLO-90 (90 m) here, where GLO-30 and FABDEM are withheld.')
            stats['coarse'] = stats.get('coarse', 0) + 1
        hf = x.get('height_from')
        if hf:
            # the height is Wikidata's (burned in before the flood, or capped after it): cite it,
            # datum unknown (Wikidata's heights come in mixed national systems); the col stays the model's
            how = ('raised into the model before the flood' if hf['how'] == 'burned'
                   else 'capped after the flood (the model read it high)')
            rec.update(source='Wikidata', datum=None)
            rec['notes'][0:1] = [f'{a.source} at 1″ for the terrain, P {x["prom"]:.0f} m; the height is Wikidata\'s '
                            f'{hf["qid"]} ({hf.get("label") or "unlabelled"}), {how}; the model read '
                            f'{hf["model"]:.0f} m.']
            stats['wikidata'] = stats.get('wikidata', 0) + 1
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
    json.dump(dict(sources=dict(lidar=a.source, names='Wikidata' if a.wikidata else 'OpenStreetMap'), reference=reference, summits=summits,
                   line_parents=lines),
              open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(len(summits), 'new summits', stats, f'{len(tops) - len(new)} already in the database;',
          len(lines), 'line parents ->', a.out)


if __name__ == '__main__':
    main()
