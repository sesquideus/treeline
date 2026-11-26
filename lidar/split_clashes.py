"""
A changeset of new summits (`catchments_changeset.py`) split against what the database already holds,
for a country the database holds some of: a clean part to apply, and the clashes to leave alone and
ask about. Nothing the database has is changed by the clean part.

    python3 lidar/split_clashes.py RUN.json CHANGESET.json DB.json OSM.json COUNTRY OUT_DIR [--near 400]

DB.json: the database's summits around ([{pk, name, lat, lon, alt, source, review, countries, col:
{lat, lon, alt}, parent: {pk, name, lat, lon}}]). OSM.json: `osm_peaks.py` output.

Clashes (OUT_DIR/clashes.json, and clashes.md for a person):
- For each summit of COUNTRY the database holds, at the run's top within --near metres of it: its
  height and the run's (the model, or the height burned into it) more than 20 m apart; its height
  and the nearest OSM peak's `ele` (within 300 m) more than 20 m apart; its key col more than 300 m
  from the run's or more than 20 m off its height; its prominence parent not the summit the run
  has. None of these is applied (the changeset never touches a summit the database holds): they
  are listed for a person.
- Two summits of the database on one top of the run.
- A summit of COUNTRY the database holds with no top of the run within --near metres.
- A new summit within --near metres of a summit the database holds, or named as one within 3 km,
  or named almost so (one spelling from the other, 85 % alike) within 5 km at a height within 15 m,
  or standing for the same massif (within 5 km, heights within 3 m, both P100 or more and their
  prominences within 25 %: the model and the database's source disagree which top is the higher):
  taken out of the clean part (the importer would take it for that summit or duplicate it). The new
  summits that had it as their parent take its parent instead; line parents to or from it are dropped.

OUT_DIR/changeset.json is the clean part.
"""
import argparse
import difflib
import json
import math
import os
import re


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


def ele(v):
    try:
        return float(str(v).replace(',', '.').split()[0].rstrip('m'))
    except (TypeError, ValueError, IndexError):
        return None


def norm(n):
    return re.sub(r'\s+', ' ', (n or '').lower()).strip()


def split(run, changeset, db, osm, country, near_m=400.0, agree=20.0):
    near = near_m / 1000
    tops = run['summits']
    T = {tuple(t['top']): t for t in tops}
    tg = grid(tops, 0.02, lambda t: tuple(t['top']))
    dg = grid(db, 0.05, lambda d: (d['lat'], d['lon']))
    og = grid([o for o in osm if o.get('kind') in ('peak', 'volcano', 'hill')], 0.02, lambda o: (o['lat'], o['lon']))
    clashes = []

    def label(d):
        return f'{d["name"] or "unnamed"} (#{d["pk"]}, {d["alt"]} m)'
    ours = [d for d in db if country in d['countries']]
    top_of, on_top = {}, {}
    for d in ours:
        c = [(km((d['lat'], d['lon']), tuple(t['top'])), t) for t in around(tg, 0.02, (d['lat'], d['lon']))]
        c = [x for x in c if x[0] <= near]
        if not c:
            clashes.append(dict(kind='no top', summit=d['pk'], name=d['name'], db=dict(alt=d['alt'], at=[d['lat'], d['lon']]),
                                why=f'no top of P >= 30 within {near_m:.0f} m of {label(d)} in the run'))
            continue
        dist, t = min(c, key=lambda x: (x[0], -x[1]['prom']))
        top_of[d['pk']] = t
        on_top.setdefault(tuple(t['top']), []).append(d)
        why = []
        if abs(t['h'] - d['alt']) > agree:
            why.append(f'height: the database {d["alt"]} m, the run {t["h"]} m')
        o = min(((km((d['lat'], d['lon']), (e['lat'], e['lon'])), e) for e in around(og, 0.02, (d['lat'], d['lon']))
                 if ele(e.get('ele')) is not None), key=lambda x: x[0], default=None)
        if o and o[0] <= 0.3 and abs(ele(o[1]['ele']) - d['alt']) > agree:
            why.append(f'height: the database {d["alt"]} m, OSM node {o[1]["id"]} ({o[1]["name"]}) {o[1]["ele"]} m')
        if d.get('col') and t.get('col'):
            dc = km((d['col']['lat'], d['col']['lon']), tuple(t['col']))
            if dc > 0.3 or abs(d['col']['alt'] - t['col_h']) > agree:
                why.append(f'key col: the database {d["col"]["alt"]} m at {d["col"]["lat"]:.5f}, {d["col"]["lon"]:.5f}; '
                           f'the run {t["col_h"]} m at {t["col"][0]:.5f}, {t["col"][1]:.5f} ({dc:.2f} km apart)')
        elif bool(d.get('col')) != bool(t.get('col')) and not d.get('island'):
            why.append('key col: ' + ('the database has none, the run has one' if t.get('col') else 'the run has none'))
        if t.get('parent'):
            pdb = d.get('parent')
            pt = tuple(t['parent'])
            if pdb is None:
                why.append(f'parent: the database has none, the run has the top at {pt[0]:.5f}, {pt[1]:.5f} '
                           f'({T[pt]["h"] if pt in T else "?"} m)')
            elif km((pdb['lat'], pdb['lon']), pt) > near:
                why.append(f'parent: the database {pdb["name"] or "unnamed"} (#{pdb["pk"]}), the run the top at '
                           f'{pt[0]:.5f}, {pt[1]:.5f} ({T[pt]["h"] if pt in T else "?"} m)')
        if why:
            clashes.append(dict(kind='differs', summit=d['pk'], name=d['name'], source=d['source'], review=d['review'],
                                db=dict(alt=d['alt'], at=[d['lat'], d['lon']], col=d.get('col'), parent=d.get('parent')),
                                run=dict(top=t['top'], h=t['h'], prom=t['prom'], col=t.get('col'), col_h=t.get('col_h'),
                                         parent=t.get('parent')), km=round(dist, 3), why=why))
    for at, ds in on_top.items():
        if len(ds) > 1:
            clashes.append(dict(kind='two on one top', summits=[d['pk'] for d in ds], names=[d['name'] for d in ds],
                                run=dict(top=list(at), h=T[at]['h']),
                                why=f'{", ".join(label(d) for d in ds)} all stand on the run\'s top at '
                                    f'{at[0]:.5f}, {at[1]:.5f} ({T[at]["h"]} m)'))

    # the new summits: out of the clean part when they stand on or are named as one the database has
    summits = changeset['summits']
    drop = {}
    for s in summits:
        here = (s['lat'], s['lon'])
        for d in around(dg, 0.05, here):
            dist = km(here, (d['lat'], d['lon']))
            names = {norm(s['name'])} | {norm(n['name']) for n in s.get('names', [])}
            similar = norm(d['name']) and dist <= 5.0 and abs((d['alt'] or 0) - s['altitude']) <= 15 and any(
                difflib.SequenceMatcher(None, norm(d['name']), n).ratio() >= 0.85 for n in names if n)
            dp = (d['alt'] - d['col']['alt']) if d.get('col') and d.get('alt') is not None else None
            sp = s.get('prominence')
            massif = (dist <= 5.0 and dp is not None and sp is not None and dp >= 100 and sp >= 100
                      and abs(d['alt'] - s['altitude']) <= 3 and abs(dp - sp) <= 0.25 * max(dp, sp))
            same = dist <= 3.0 and norm(d['name']) and norm(d['name']) in names
            if dist <= near or same or similar or massif:
                drop[s['key']] = (d, dist, 'stands' if dist <= near else 'has its name' if same else
                                  'has nearly its name' if similar else
                                  f'may stand for the same massif (P{sp:.0f} against P{dp:.0f}; uncertain)')
                break
    by_key = {s['key']: s for s in summits}
    for k, (d, dist, how) in drop.items():
        s = by_key[k]
        clashes.append(dict(kind='new near a database summit', key=k, name=s['name'], at=[s['lat'], s['lon']],
                            altitude=s['altitude'], prominence=s.get('prominence'), summit=d['pk'], summit_name=d['name'],
                            km=round(dist, 3),
                            why=f'the new {s["name"] or "unnamed"} ({s["altitude"]} m) stands {dist * 1000:.0f} m from '
                                f'{label(d)}' + ('' if how == 'stands' else f' and {how}')))

    def up(parent):
        for _ in range(100):
            if parent and 'new' in parent and parent['new'] in drop:
                parent = by_key[parent['new']].get('parent')
            else:
                return parent
        return None
    clean = []
    for s in summits:
        if s['key'] in drop:
            continue
        if s.get('parent') and 'new' in s['parent'] and s['parent']['new'] in drop:
            s = dict(s, parent=up(s['parent']))
            s['notes'] = s['notes'] + ['Its parent in the run was left out of the import (it clashes with a summit '
                                       'the database holds): linked to the next one up.']
        clean.append(s)
    gone = {(by_key[k]['lat'], by_key[k]['lon']) for k in drop}
    lines = [lp for lp in changeset.get('line_parents', [])
             if tuple(lp['summit_at']) not in gone and not (lp.get('parent_at') and tuple(lp['parent_at']) in gone)]
    out = dict(changeset, summits=clean, line_parents=lines)
    return out, clashes


def markdown(clashes, country):
    lines = [f'# Clashes with the database ({country.upper()})', '',
             'Nothing here was applied. Each item: what the database has, what the run says, and why it is listed.', '']
    titles = {'differs': 'Summits the database holds that the run reads differently',
              'two on one top': 'Two database summits on one top',
              'no top': 'Database summits with no P30 top of the run near them',
              'new near a database summit': 'New summits left out: on or named as a database summit'}
    for kind, title in titles.items():
        items = [c for c in clashes if c['kind'] == kind]
        lines += [f'## {title} ({len(items)})', '']
        for c in items:
            if kind == 'differs':
                lines.append(f'- **{c["name"] or "unnamed"}** (#{c["summit"]}, {c["source"] or "no source"}'
                             f'{", review " + c["review"] if c["review"] else ""}): ' + '; '.join(c['why']))
            else:
                lines.append(f'- {c["why"]}')
        lines.append('')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run')
    ap.add_argument('changeset')
    ap.add_argument('db')
    ap.add_argument('osm')
    ap.add_argument('country')
    ap.add_argument('out')
    ap.add_argument('--near', type=float, default=400.0, help='m')
    a = ap.parse_args()
    out, clashes = split(json.load(open(a.run)), json.load(open(a.changeset)), json.load(open(a.db)),
                         json.load(open(a.osm)), a.country.lower(), a.near)
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, 'changeset.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump(clashes, open(os.path.join(a.out, 'clashes.json'), 'w'), ensure_ascii=False, indent=1)
    open(os.path.join(a.out, 'clashes.md'), 'w').write(markdown(clashes, a.country))
    from collections import Counter
    print(f'{len(out["summits"])} new summits clean, {len(out["line_parents"])} line parents; clashes:',
          dict(Counter(c['kind'] for c in clashes)), '->', a.out)


if __name__ == '__main__':
    main()
