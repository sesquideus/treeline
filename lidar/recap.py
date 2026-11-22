"""
Known heights over a world run's tops, after the flood: where a surveyed summit stands lower
than the model reads it (Copernicus GLO-30 over Antarctica's steep rock, a sharp FABDEM summit
read high), the model's top is capped at the surveyed height, and the key cols, parents and line
parents are worked out again from the run's saddle tree; no new flood. Every summit says where
its height comes from: the model, a burn (`burn_match.py`, Wikidata raised into the model before
the flood) or a cap.

    python3 lidar/recap.py RUN.json PEAKS.json BURNS.json OUT.json [--min-prominence 600]
        [--above 30] [--review REVIEW.json]

RUN.json is `catchments.py world` output with its RUN.npz beside it.

Why no new flood is needed: lowering a top changes no col. The saddle tree (each top, its key
col and the top across it) keeps which tops the ground joins at every level, whatever their
heights; joining the tops again, highest col first, gives each top's key col for the new
heights. The tops below the run's threshold are gone from the records, so a cap may leave a
lesser top nearby that the model reads higher than the capped summit; the summit stands for
its massif at the surveyed height.

Caps, conservatively: Wikidata summits placed to 0.001° (~100 m) or better with a stated
prominence of at least --min-prominence, whose top within 500 m stands more than --above m
above the stated height, and which agree with themselves: capped, the summit's prominence over
the model's cols comes out within 100 m or 15 % of the stated one (else the height is for
another point, or wrong, or the top is a lesser one). Wikidata errs too (low more often than high: against Slovakia's lidar,
3 % of its heights are over 5 m too high, 8 % over 5 m too low), so lesser summits are not
capped: the model is no worse a guess there.

--reviewed: the caps looked at by hand, with why; only the accepted ones are applied.

REVIEW.json: the caps, and the burns to look at by hand: those that raised the model by 100 m
or more below 2,500 m (where FABDEM has no reason to be that wrong; rather Wikidata's height, or
its top paired with a lesser neighbour), and those placed at the model's top because Wikidata's
position was vague (within 2 km: possibly on the wrong top).
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catchments import ARC, walk, walk_line  # noqa: E402


def resettle(top, col, hz, cz, far):
    """
    Key cols and the tops across them for heights `hz`, from the saddle tree (`col`, `cz`, `far`
    as the run settled them; `far` is the catchment directly across the col, not the highest
    top beyond it). Returns new (col, cz, far). A top the run found joined to nothing
    (an island: col -1) stays so; a key col leading to no top (far -1: the data's edge) stays
    the top's own.
    """
    n = len(top)
    idx = {int(g): i for i, g in enumerate(top)}
    parent = list(range(n))
    best = list(range(n))                  # each set's highest top: by height, a tie by smaller gid

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def higher(a, b):
        return hz[a] > hz[b] or (hz[a] == hz[b] and top[a] < top[b])

    new_col, new_cz, new_far = col.copy(), cz.copy(), far.copy()
    edges = [i for i in range(n) if col[i] >= 0 and far[i] >= 0]
    for i in edges:                        # only the losers get a key col: the winner is a root
        new_col[i], new_cz[i], new_far[i] = -1, np.nan, -1
    edges.sort(key=lambda i: (-cz[i], col[i]))
    for i in edges:
        a, b = find(i), find(idx[int(far[i])])
        if a == b:
            continue                       # cannot happen in a tree; kept safe
        ma, mb = best[a], best[b]
        lo, hi = (ma, mb) if higher(mb, ma) else (mb, ma)
        # what is across the col: the catchment touching it on the other side, as the engine has
        # it (walk goes on from there): the edge's own partner from this top's side, the edge's
        # top from the partner's
        new_col[lo], new_cz[lo] = col[i], cz[i]
        new_far[lo] = far[i] if lo == ma else top[i]
        parent[a] = b
        best[b] = hi
    return new_col, new_cz, new_far


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110.574, (a[1] - b[1]) * 111.32 * k)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run')
    ap.add_argument('peaks')
    ap.add_argument('burns')
    ap.add_argument('out')
    ap.add_argument('--min-prominence', type=float, default=600.0)
    ap.add_argument('--above', type=float, default=30.0)
    ap.add_argument('--review')
    ap.add_argument('--line-cutoff', type=float, default=600.0)
    ap.add_argument('--check', action='store_true', help='no caps: must reproduce the run exactly')
    ap.add_argument('--reviewed', help='JSON {accept: {qid: why}, refuse: {qid: why}}: only the accepted caps')
    a = ap.parse_args()

    z = np.load(os.path.splitext(a.run)[0] + '.npz')
    top, col, hz, cz, far = (z[k].copy() for k in ('top', 'col', 'hz', 'cz', 'far'))
    border = z['border']
    # A key col at the sea's level (lifted height 0) is no col: the flood joined two islands across
    # open sea where a tile seam crosses it (an engine fault, 73 tops of 2.1 million in the
    # 2026-11-22 run, all islets). Such a top is an island; its prominence is its height.
    sea = (col >= 0) & (cz <= 0)
    if sea.any():
        col[sea], cz[sea], far[sea] = -1, np.nan, -1
        print(f'{int(sea.sum())} key cols at the sea\'s level dropped: those tops are islands')
    north, west, W, lift = int(z['north']), int(z['west']), int(z['W']), float(z['lift'])

    def ll(g):
        r, c = divmod(int(g), W)
        return [round(north - r / ARC, 7), round((west + c / ARC + 180) % 360 - 180, 7)]

    pos = np.array([ll(g) for g in top])
    cell = 0.01
    grid = {}
    for i, (la, lo) in enumerate(pos):
        grid.setdefault((math.floor(la / cell), math.floor(lo / cell)), []).append(i)

    def near(la, lo, within):
        best = None
        ci, cj = math.floor(la / cell), math.floor(lo / cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in grid.get((ci + di, cj + dj), ()):
                    d = km((la, lo), pos[i])
                    if d <= within and (best is None or d < best[0]):
                        best = (d, i)
        return best

    # where each height comes from: the burns first (the run's top on the burned cell)
    height_from = {}
    burns = json.load(open(a.burns))['burns']
    for b in burns:
        m = near(b['at'][0], b['at'][1], 0.05)
        if m and abs(hz[m[1]] - lift - b['elev']) < 0.01:
            height_from[m[1]] = dict(source='Wikidata', qid=b['qid'], label=b['label'], how='burned',
                                     placed=b['placed'], model=b['model'])

    caps, refused = [], []
    old_prom = np.where(col < 0, hz - lift, hz - cz)
    if not a.check:
        cands = {}
        for p in json.load(open(a.peaks)):
            if not (p['elev'] and p['prom'] and p['prom'] >= a.min_prominence and p['precision'] is not None
                    and p['precision'] <= 0.001):
                continue
            m = near(p['lat'], p['lon'], 0.5)
            if not m:
                continue
            i = m[1]
            h = float(hz[i]) - lift
            if h - p['elev'] > a.above and (i not in cands or m[0] < cands[i]['km']):
                cands[i] = dict(i=i, qid=p['qid'], label=p['label'], elev=p['elev'], stated_prom=p['prom'],
                                model=round(h, 2), km=round(m[0], 3))
        # A cap must agree with itself: Wikidata's prominence is worked out from its height, so the
        # capped summit, joined again over the model's cols, should come out at about it. One that
        # does not is Wikidata's height for another point (or wrong), or a lesser top the model has.
        keep = list(cands.values())
        for _ in range(4):
            h2 = hz.copy()
            for c in keep:
                h2[c['i']] = c['elev'] + lift
            c2, z2, f2 = resettle(top, col, h2, cz, far)
            p2 = np.where(c2 < 0, h2 - lift, h2 - z2)
            ok = [c for c in keep if p2[c['i']] > 0
                  and abs(p2[c['i']] - c['stated_prom']) <= max(100.0, 0.15 * c['stated_prom'])]
            if len(ok) == len(keep):
                break
            keep = ok
        caps = keep
        if a.reviewed:
            rv = json.load(open(a.reviewed))
            for c in caps:
                c['review'] = rv['accept'].get(c['qid']) or rv['refuse'].get(c['qid']) or 'not reviewed'
            caps = [c for c in caps if c['qid'] in rv['accept']]
        refused = [c for c in cands.values() if c not in caps]
        for c in caps:
            hz[c['i']] = c['elev'] + lift
            height_from[c['i']] = dict(source='Wikidata', qid=c['qid'], label=c['label'], how='capped',
                                       model=c['model'], review=c.get('review'))

    new_col, new_cz, new_far = resettle(top, col, hz, cz, far)
    if a.check:
        same = (np.array_equal(new_col, col), np.array_equal(new_cz, cz, equal_nan=True), np.array_equal(new_far, far))
        # (with the sea's cols dropped first, the comparison is against the run so corrected)
        print('resettled with the run\'s own heights: col, cz, far identical:', same)
        if not all(same):
            bad = np.flatnonzero((new_col != col) | (new_far != far))
            print(len(bad), 'differ, e.g.', [(int(top[i]), int(col[i]), int(new_col[i]), int(far[i]), int(new_far[i]))
                                            for i in bad[:5]])
            sys.exit(1)
    col, cz, far = new_col, new_cz, new_far
    par, ta, tb = walk(top, col, hz, cz, far)
    line = walk_line(top, col, hz, cz, far, a.line_cutoff)
    prom = np.where(col < 0, hz - lift, hz - cz)

    res = []
    for i in range(len(top)):
        x = dict(top=ll(top[i]), h=round(float(hz[i]) - lift, 2), prom=round(float(prom[i]), 2),
                 border=bool(border[i]))
        if col[i] >= 0:
            x.update(col=ll(col[i]), col_h=round(float(cz[i]) - lift, 2))
        if par[i] >= 0:
            x['parent'] = ll(par[i])
        if line[i] >= 0:
            x['line'] = ll(line[i])
        if i in height_from:
            x['height_from'] = height_from[i]
        res.append(x)
    run = json.load(open(a.run))
    st = run['stats']
    st.update(ties=[[ll(p), ll(q)] for p, q in zip(ta, tb)], caps=len(caps),
              heights='the model, a Wikidata height burned in before the flood, or one capped after it '
                      '(height_from)')
    json.dump(dict(stats=st, summits=res), open(a.out, 'w'))
    print(f'{len(caps)} caps ({len(refused)} candidates refused: the stated prominence disagrees); {sum(1 for v in height_from.values() if v["how"] == "burned")} tops at a burned '
          f'height; {len(ta)} ties -> {a.out}')
    for c in sorted(caps, key=lambda c: -c['stated_prom']):
        i = c['i']
        c.update(new_prom=round(float(prom[i]), 2), old_prom=round(float(old_prom[i]), 2), at=ll(top[i]))
        print(f"  {c['label']}: {c['model']} -> {c['elev']} m, P {c['old_prom']} -> {c['new_prom']} "
              f"(stated P{c['stated_prom']})")

    if a.review:
        doubtful = []
        for b in burns:
            raised = b['elev'] - b['model']
            why = []
            if raised >= 100 and b['elev'] < 2500:
                why.append(f'raised {raised:.0f} m below 2,500 m')
            if b['placed'] == 'model top':
                why.append("placed at the model's top: Wikidata's position is vague")
            if why:
                doubtful.append(dict(b, raised=round(raised, 1), why=why))
        for c in refused:
            c.update(at=ll(top[c['i']]), old_prom=round(float(old_prom[c['i']]), 2))
        json.dump(dict(caps=[{k: v for k, v in c.items() if k != 'i'} for c in caps],
                       refused_caps=[{k: v for k, v in c.items() if k != 'i'} for c in refused], burns=doubtful),
                  open(a.review, 'w'), ensure_ascii=False, indent=0)
        print(f'review: {len(caps)} caps, {len(doubtful)} burns '
              f'({sum(1 for d in doubtful if len(d["why"]) == 2)} for both reasons) -> {a.review}')


if __name__ == '__main__':
    main()
