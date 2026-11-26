"""
Isolation for a country's summits from any terrain: each summit's nearest higher point (the
nearest ground higher than its top, `isolation.search`, bilinear between cell centres) and its
isolation parent (the summit that ground belongs to, `nhn.Climb`), as a changeset for
`manage.py apply_lidar_changeset`.

    LIDAR_DB=DB.json uv run python manage.py shell < lidar/export_db.py
    cd lidar && uv run --project .. --group lidar python isolation_run.py model ua DB.json OUT \\
        --burn .../west/burns.json --burn .../east/burns.json         # GEDTM30 (TREELINE_MODEL=gedtm30)
    cd lidar && uv run --project .. --group lidar python isolation_run.py lidar at DB.json OUT
    cd lidar && uv run --project .. --group lidar python isolation_run.py changeset OUT DB.json

`model`: a 1" model (GEDTM30 with the OSM heights burned in, as the Ukrainian pipeline ran it);
whatever lies in a tile it lacks where Copernicus has land, or beyond --box, is unknown. `lidar`:
every source of `sources.py` with a tile index, heights on EGM2008; whether the nearest higher
ground could lie where no lidar is, Copernicus GLO-30 decides (`terrain.HoleTerrain`): a
summit whose ground may be there, by GLO-30 reading within --margin of its height, is left
unresolved. A summit is the country's (its point's countries) and not in --skip (results of an
earlier country). OUT/results.json holds every summit's outcome; `changeset` writes
OUT/changeset.json, OUT/review-overridden.json (manual isolations the computed ones replace,
their nearest higher points more than 100 m apart) and OUT/summary.json.

A summit's top: the highest cell within --radius of its point (15 m in the lidar, 50 m in the
1" model), the highest over all sources where several cover it (they differ by centimetres to
decimetres: taking the highest keeps a summit's own top in another survey from being its nearest
higher ground). A point a little off its top is walked uphill to it: in the lidar at full
resolution, within 60 m (a rock tower is its own top at 1 m, where a pooled grid would merge it
with the face behind it); in the model on its own grid within 200 m, across dips under 3 m (the
model's noise: twin bumps of one summit), and a summit whose walk leaves it for higher ground
farther off has no top in the model (a knob 30 m cells smooth away) and is left unresolved. So is
a summit whose top differs from its database height (EGM2008) by more than 20 m in the lidar or
60 m in the model: its point is not on the summit described; and one whose climb from its nearest
higher point first reaches a top no other summit claims, within 150 m of its point and 15 m of its
database height (300 m in the model), and nearer that height than the ground at the point
is: that is its own top, out of the walk's reach. Distances are from the summit's
point, as the database measures isolation.
"""
import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import EPS, first_leaf, search  # noqa: E402
from nhn import Climb  # noqa: E402

G = {}          # per process: terrain, holes, summits, climb


class Summits:
    """ The database's summits for matching tops: by 3-D unit vectors in a k-d tree. """

    def __init__(self, summits):
        from scipy.spatial import cKDTree
        self.s = summits
        lat = np.radians([x['point']['lat'] for x in summits])
        lon = np.radians([x['point']['lon'] for x in summits])
        self.xyz = np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
        self.tree = cKDTree(self.xyz)

    def near(self, lon, lat, metres):
        la, lo = math.radians(lat), math.radians(lon)
        p = [math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)]
        return [self.s[i] for i in self.tree.query_ball_point(p, metres / 6371008.8)]


def metres(lon0, lat0, lon1, lat1):
    from terrain import distances
    return float(distances(lon0, lat0, [lon1], [lat1])[0])


def matcher(s, on_top_m, near_m):
    """
    The `match` of `nhn.Climb`: the catalogued summit standing on a top — one within `on_top_m`
    whose own climb reaches the same top, else the nearest within `near_m` — if the database has it
    higher than `s` (the invariant compares the stored altitudes); otherwise the climb goes on.
    """
    def match(lon, lat, plateau, climb):
        cands = [c for c in G['summits'].near(lon, lat, on_top_m) if c['pk'] != s['pk']]
        if not cands:
            return None
        plateau = set(plateau)
        on = [c for c in cands if (climb.top_of(c['point']['lon'], c['point']['lat']) or set()) & plateau]
        if not on:
            on = sorted((c for c in cands if metres(lon, lat, c['point']['lon'], c['point']['lat']) <= near_m),
                        key=lambda c: metres(lon, lat, c['point']['lon'], c['point']['lat']))[:1]
        if not on:
            return None
        best = max(on, key=lambda c: c['point']['alt'])
        return best if best['point']['alt'] > s['point']['alt'] else None
    return match


def one(s):
    T, kind = G['terrain'], G['kind']
    lon, lat = s['point']['lon'], s['point']['lat']
    rec = dict(pk=s['pk'], name=s['point']['name'], lat=lat, lon=lon, alt=s['point']['alt'])
    top = T.top(lon, lat, G['radius'], ascent=G['ascent_m']) if kind == 'lidar' else T.top(lon, lat, G['radius'])
    if top is None:
        rec['status'] = 'no data at the summit'
        return rec
    egm = s['point'].get('egm')
    egm = egm if egm is not None else s['point']['alt']        # unknown datum: the height as given
    if egm is not None and abs(top[0] - egm) > G['max_dz']:
        # the ground at the point is not the summit the database describes: a wrong position or height
        rec.update(status='summit not where its point is', top_h=round(top[0], 2), egm=round(egm, 2))
        return rec
    h = top[0]
    rec.update(top_h=round(h, 2), top=[round(top[2], 7), round(top[1], 7)], top_source=top[3])
    if top[4] and top[4][1] < h:
        rec['own_burn'] = top[4]
    # A point a little off its top (a flat top, whose highest cell lies 60 m from the point): the top
    # it walks up to, if near, is the summit's.
    # A model's twin bumps a metre apart in height are one summit: its top is the highest reached
    # across dips under --noise (3 m in the 1" model, none in the lidar) within the ascent distance.
    # A summit the terrain has no top for (a small knob a 30 m model smooths into a slope): the walk
    # uphill leaves it for higher ground farther off. Its isolation would be the slope's: none.
    up = G['climb'].ascend(lon, lat, dip=G['noise'], within=G['ascent_m'], origin=(lon, lat)) if kind == 'model' else None
    if up and up[0] > h + EPS:
        if metres(lon, lat, up[1], up[2]) <= G['ascent_m']:
            h = up[0]
            rec.update(top_h=round(h, 2), top=[round(up[2], 7), round(up[1], 7)], ascended=True)
            rec.pop('own_burn', None)
        else:
            rec.update(status='no top in the terrain', uphill=[round(up[2], 7), round(up[1], 7), round(up[0], 2)])
            return rec
    t = h + EPS
    r = search(T, lon, lat, t)
    if kind == 'model':
        edge = T.edge_distance(lon, lat)
    else:
        edge = min(G['holes'].edge_distance(lon, lat),
                   first_leaf(G['holes'], lon, lat, t - G['margin'], limit=r['metres'] if r else math.inf))
    rec['edge_m'] = round(edge, 1) if math.isfinite(edge) else None
    if r is None or edge < r['metres']:
        rec['status'] = 'beyond the data'
        if r:
            rec['nhp_in_data'] = dict(lat=round(r['lat'], 7), lon=round(r['lon'], 7), metres=round(r['metres'], 1))
        return rec
    rec.update(nhp=[round(r['lat'], 7), round(r['lon'], 7)], metres=round(r['metres'], 2),
               centre_m=round(r['centre_m'], 2), source=r['source'],
               nhp_burned=[b for b in r['burned'] if b[1] < t])
    climb = G['climb']
    hit, info = climb.run(r['anchor'][0], r['anchor'][1], matcher(s, G['on_top_m'], G['near_m']))
    rec['climb'] = info['status'] if hit is None else 'ok'
    rec['climb_tops'] = len(info['tops'])
    # The first top the climb reaches, unclaimed by any other summit and right by the point: the
    # summit's own top, its point off it by more than the walk up reaches (a position from a list)
    first = info['tops'][0] if info['tops'] else None
    if (first and (hit is None or len(info['tops']) > 1) and metres(lon, lat, first[0], first[1]) <= G['own_m']
            and abs(first[2] - egm) <= G['own_dz'] and abs(first[2] - egm) < abs(h - egm)):
        rec.update(status='summit not where its point is', own_top=[round(first[1], 7), round(first[0], 7),
                                                                     round(first[2], 2)])
        return rec
    if hit is not None:
        rec['parent'] = dict(pk=hit['pk'], name=hit['point']['name'], lat=hit['point']['lat'], lon=hit['point']['lon'],
                             alt=hit['point']['alt'])
        b = info['tops'][-1][3]
        if b and b[1] < t:              # without the burn, the top would not be higher than the summit
            rec['parent_burned'] = b
    rec['status'] = 'ok'
    return rec


def init(state):
    G.update(state)
    G['climb'] = Climb(G['terrain'].frame(), cutoff=G['cutoff'], size=256, max_size=G['max_window'],
                       cap=G['max_window'] ** 2 // 2)


def chunk_job(chunk):
    out = []
    for s in chunk:
        try:
            out.append(one(s))
        except Exception as e:          # one summit's failure is reported, not the run's
            import traceback
            out.append(dict(pk=s['pk'], name=s['point']['name'], lat=s['point']['lat'], lon=s['point']['lon'],
                            status='error', error=f'{type(e).__name__}: {e}', trace=traceback.format_exc()[-1500:]))
    return out


def select(db, country, skip, pks=None):
    done = set()
    for p in skip or []:
        done |= {r['pk'] for r in json.load(open(p))}
    out = [s for s in db['summits'] if country in s['point']['countries'] and s['pk'] not in done]
    if pks:
        out = [s for s in out if s['pk'] in pks]
    return out


def run(args):
    from tqdm import tqdm
    db = json.load(open(args.db))
    pks = set(int(x) for x in args.only.split(',')) if args.only else None
    todo = select(db, args.country, args.skip, pks)
    os.makedirs(args.out, exist_ok=True)
    print(f'{len(todo)} summits of {args.country}', flush=True)
    box = [int(v) for v in args.box.split(',')]
    import terrain as tm
    state = dict(kind=args.kind, cutoff=args.cutoff, margin=args.margin)
    if args.kind == 'model':
        state['terrain'] = tm.ModelTerrain(box, burns=args.burn)
        print(f'{len(state["terrain"].burns)} burns, {len(state["terrain"]._holes)} land tiles without heights '
              f'(of them without a single height: {", ".join(state["terrain"].empty) or "none"})', flush=True)
        state.update(radius=args.radius or 50.0, on_top_m=150.0, near_m=60.0, max_window=args.max_window or 8192, ascent_m=200.0, noise=3.0, max_dz=60.0, own_m=300.0, own_dz=15.0)
    else:
        T = tm.LidarTerrain(box=box)
        state['terrain'] = T
        cov = tm.Coverage(T, box)
        state['holes'] = tm.HoleTerrain(box, cov)
        state.update(radius=args.radius or 15.0, on_top_m=100.0, near_m=40.0, max_window=args.max_window or 2048, ascent_m=60.0, noise=0.0, max_dz=20.0, own_m=150.0, own_dz=15.0)
    # nearby summits together, so each worker's caches serve its chunk
    todo.sort(key=lambda s: (round(s['point']['lat'] / 0.1), s['point']['lon']))
    chunks = [todo[i:i + args.chunk] for i in range(0, len(todo), args.chunk)]
    # matching needs every summit nearby, of any country
    lon0, lat0, lon1, lat1 = box
    state['summits'] = Summits([s for s in db['summits']
                                if lat0 - 1 <= s['point']['lat'] <= lat1 + 1 and lon0 - 1 <= s['point']['lon'] <= lon1 + 1])
    t0 = time.time()
    out = []
    if args.jobs <= 1:
        init(state)
        for c in tqdm(chunks, unit='chunk', mininterval=30):
            out += chunk_job(c)
    else:
        ctx = mp.get_context('fork')
        with ctx.Pool(args.jobs, initializer=init, initargs=(state,)) as pool:
            for res in tqdm(pool.imap_unordered(chunk_job, chunks), total=len(chunks), unit='chunk', mininterval=30):
                out += res
    json.dump(out, open(os.path.join(args.out, 'results.json'), 'w'), ensure_ascii=False, indent=0)
    if args.merge:
        new = {r['pk']: r for r in out}
        old = json.load(open(args.merge))
        json.dump([new.get(r['pk'], r) for r in old], open(args.merge, 'w'), ensure_ascii=False, indent=0)
        print(f'{len(new)} outcomes merged into {args.merge}', flush=True)
    from collections import Counter
    print(dict(Counter(r['status'] for r in out)), dict(Counter(r.get('climb') for r in out if 'climb' in r)),
          f'{(time.time() - t0) / 60:.1f} min', flush=True)


# --------------------------------------------------------------------------------------- changeset

MANUAL_SUBSTANTIAL = 100.0       # m between a manual nearest higher point and the computed one


def burn_notes(r, model):
    out = []
    if r.get('own_burn'):
        h, m, label = r['own_burn']
        out.append(f'Isolation: this summit\'s height in the terrain is the OpenStreetMap height of {label} '
                   f'({h:.0f} m) burned into {model}, which reads {m:.0f} m there; the nearest higher point is the '
                   f'nearest ground above that height.')
    for h, m, label in r.get('nhp_burned') or []:
        out.append(f'Isolation: the nearest higher point is set by the OpenStreetMap height of {label} ({h:.0f} m) '
                   f'burned into {model}, which reads {m:.0f} m there.')
    if r.get('parent_burned'):
        h, m, label = r['parent_burned']
        out.append(f'Isolation parent: the climb from the nearest higher point ends on the OpenStreetMap height of '
                   f'{label} ({h:.0f} m) burned into {model}, which reads {m:.0f} m there.')
    return out


def changeset(args):
    from terrain import source_name
    db = {s['pk']: s for s in json.load(open(args.db))['summits']}
    res = json.load(open(os.path.join(args.out, 'results.json')))
    model = None
    ours = set()
    isolations, review, unresolved = [], [], []
    counts = dict(processed=len(res), set=0, parentless=0, overridden=0, substantial=0, reviewed_skipped=0,
                  beyond=0, no_data=0, no_top=0, misplaced=0, errors=0, climb_failed_kept=0)
    for r in res:
        if r['status'] != 'ok':
            key = {'beyond the data': 'beyond', 'no data at the summit': 'no_data', 'error': 'errors',
                   'no top in the terrain': 'no_top', 'summit not where its point is': 'misplaced'}[r['status']]
            counts[key] += 1
            unresolved.append(r)
            continue
        src = r['source'] if r['source'] not in __import__('sources').BY_KEY else source_name(r['source'])
        ours.add(src)
    for r in res:
        if r['status'] != 'ok':
            continue
        s = db[r['pk']]
        if s['point'].get('review') == 'R':
            counts['reviewed_skipped'] += 1
            continue
        src = r['source'] if r['source'] not in __import__('sources').BY_KEY else source_name(r['source'])
        model = model or src
        parent = r.get('parent')
        had = s.get('nhp')
        manual = had is not None and s.get('isolation_source') not in ours | {'DMR 5.0'}
        if parent is None and had is not None:
            counts['climb_failed_kept'] += 1
            unresolved.append(dict(r, kept='the database\'s isolation (the climb found no parent)'))
            continue
        iso = dict(summit=dict(pk=s['pk'], name=s['point']['name'], was=[s['point']['lat'], s['point']['lon']]),
                   parent=dict(pk=parent['pk'], name=parent['name'], was=[parent['lat'], parent['lon']]) if parent else None,
                   nhp=r['nhp'], metres=r['metres'], source=src)
        notes = burn_notes(r, src)
        if notes:
            iso['notes'] = notes
        isolations.append(iso)
        counts['set'] += 1
        if parent is None:
            counts['parentless'] += 1
            unresolved.append(dict(r, kept='the nearest higher point alone'))
        if manual:
            counts['overridden'] += 1
            from terrain import distances
            lat, lon = s['point']['lat'], s['point']['lon']
            off = float(distances(had[1], had[0], [r['nhp'][1]], [r['nhp'][0]])[0])
            if off > MANUAL_SUBSTANTIAL:
                counts['substantial'] += 1
                old_parent = db.get(s.get('isolation_parent_pk'))
                review.append(dict(
                    pk=s['pk'], name=s['point']['name'], lat=lat, lon=lon, alt=s['point']['alt'],
                    manual_source=s.get('isolation_source'), nhp_manual=list(had), nhp_computed=r['nhp'],
                    nhp_apart_m=round(off, 1),
                    isolation_before_m=round(float(distances(lon, lat, [had[1]], [had[0]])[0]), 1),
                    isolation_after_m=r['metres'],
                    parent_before=old_parent['point']['name'] if old_parent else None,
                    parent_before_pk=s.get('isolation_parent_pk'),
                    parent_after=parent['name'] if parent else None, parent_after_pk=parent['pk'] if parent else None,
                    parent_changed=(parent['pk'] if parent else None) != s.get('isolation_parent_pk')))
    review.sort(key=lambda x: -x['nhp_apart_m'])
    json.dump(dict(sources=dict(lidar=model or 'lidar', names='OpenStreetMap'), isolations=isolations),
              open(os.path.join(args.out, 'changeset.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump(review, open(os.path.join(args.out, 'review-overridden.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump(unresolved, open(os.path.join(args.out, 'unresolved.json'), 'w'), ensure_ascii=False, indent=1)
    shift = [r['centre_m'] - r['metres'] for r in res if r['status'] == 'ok']
    counts['bilinear_shift_m'] = dict(median=round(float(np.median(shift)), 2) if shift else None,
                                      p90=round(float(np.percentile(shift, 90)), 2) if shift else None,
                                      max=round(float(np.max(shift)), 2) if shift else None)
    from collections import Counter
    counts['climb'] = dict(Counter(r.get('climb') for r in res if r['status'] == 'ok'))
    json.dump(counts, open(os.path.join(args.out, 'summary.json'), 'w'), indent=1)
    print(json.dumps(counts, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for kind in ('model', 'lidar'):
        p = sub.add_parser(kind)
        p.add_argument('country')
        p.add_argument('db')
        p.add_argument('out')
        p.add_argument('--box', default='18,43,42,55' if kind == 'model' else '4,43,26,56',
                       help='lon0,lat0,lon1,lat1 (whole degrees): the data loaded')
        p.add_argument('--burn', action='append', help='burn_match.py outputs (model)')
        p.add_argument('--skip', action='append', help='results.json of earlier countries: their summits are done')
        p.add_argument('--only', help='summit pks, comma-separated (spot checks)')
        p.add_argument('--radius', type=float)
        p.add_argument('--cutoff', type=float, default=10.0)
        p.add_argument('--margin', type=float, default=30.0, help='m: GLO-30 this close below a summit may hide higher ground')
        p.add_argument('--jobs', type=int, default=8)
        p.add_argument('--chunk', type=int, default=40)
        p.add_argument('--max-window', type=int, help='cells a side the climb may grow its window to')
        p.add_argument('--merge', help='results.json to update with these summits\' outcomes (a rerun of some)')
        p.set_defaults(kind=kind)
    p = sub.add_parser('changeset')
    p.add_argument('out')
    p.add_argument('db')
    args = ap.parse_args()
    if args.cmd == 'changeset':
        changeset(args)
    else:
        run(args)


if __name__ == '__main__':
    main()
