"""
Known summits to burn into the model before flooding: each Wikidata summit (`wikidata_peaks.py`)
matched to a top of a catchment run, so that the flood sees its surveyed height where the
30 m model reads a sharp summit low (and so swaps it with a lesser neighbour).

    python3 lidar/burn_match.py RUN.json PEAKS.json OUT.json

- A pair: the top within 2 km of the summit, the model at most 250 m below the stated
  elevation and at most 30 m above (it reads sharp summits low, rarely high). Every top of
  the run counts, not only the prominent ones: a summit the model got wrong may have come out
  with a small prominence (the Finsteraarhorn, P572 instead of P2279).
- Score: distance / 2 km + height difference / 250 m; the best pairs are taken first.
- Unclear is left alone: a summit whose second-best top scores within 0.1 of its best, or a
  top two summits compete for within 0.1. Those keep the model's height (source FABDEM).
- Where to burn: at Wikidata's coordinate when its stated precision is 0.001° (~100 m) or
  better, else at the model's top. Only where the summit is higher than the model there.
- A summit placed that precisely needs no top to confirm where it is, only a model that agrees
  with it: if its pairing failed (a neighbour standing on the model's top took it, as the
  Glocknerwand did from the Großglockner), it is burned anyway when the model's highest cell
  within 150 m of it lies between 250 m below and 30 m above its stated height.
"""
import json
import math
import sys

MAX_KM, BELOW, ABOVE, MARGIN, PRECISE = 2.0, 250.0, 30.0, 0.1, 0.001


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110.574, (a[1] - b[1]) * 111.32 * k)


def main(run_path, peaks_path, out_path):
    tops = json.load(open(run_path))['summits']
    peaks = [p for p in json.load(open(peaks_path)) if p['elev'] is not None]
    CELL = 0.05
    grid = {}
    for i, t in enumerate(tops):
        grid.setdefault((math.floor(t['top'][0] / CELL), math.floor(t['top'][1] / CELL)), []).append(i)

    cands = {}                       # peak index -> [(score, top index)]
    for j, p in enumerate(peaks):
        ci, cj = math.floor(p['lat'] / CELL), math.floor(p['lon'] / CELL)
        out = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in grid.get((ci + di, cj + dj), ()):
                    t = tops[i]
                    d = km((p['lat'], p['lon']), t['top'])
                    dh = p['elev'] - t['h']
                    if d <= MAX_KM and -ABOVE <= dh <= BELOW:
                        out.append((d / MAX_KM + abs(dh) / BELOW, i))
        if out:
            cands[j] = sorted(out)

    unclear_peaks = {j for j, c in cands.items() if len(c) > 1 and c[1][0] - c[0][0] < MARGIN}
    claims = {}                      # top -> [(score, peak)]
    for j, c in cands.items():
        if j not in unclear_peaks:
            claims.setdefault(c[0][1], []).append((c[0][0], j))
    pairs, unclear_tops = [], set()
    for i, cs in claims.items():
        cs.sort()
        if len(cs) > 1 and cs[1][0] - cs[0][0] < MARGIN:
            unclear_tops.add(i)
            continue
        pairs.append((cs[0][1], i, cs[0][0]))

    burns = []
    for j, i, score in pairs:
        p, t = peaks[j], tops[i]
        precise = p['precision'] is not None and p['precision'] <= PRECISE
        at = [p['lat'], p['lon']] if precise else list(t['top'])
        if p['elev'] <= t['h'] and not precise:
            continue                 # nothing to raise where the model is already as high
        burns.append(dict(qid=p['qid'], label=p['label'], elev=p['elev'], prom=p['prom'], at=at,
                          placed='wikidata' if precise else 'model top', top=t['top'], model=t['h'],
                          model_prom=t['prom'], km=round(km((p['lat'], p['lon']), t['top']), 3),
                          score=round(score, 3)))
    unclear = ([dict(qid=peaks[j]['qid'], label=peaks[j]['label'], elev=peaks[j]['elev'], why='two tops fit')
                for j in sorted(unclear_peaks)]
               + [dict(top=tops[i]['top'], model=tops[i]['h'], why='two summits fit',
                       summits=[peaks[j]['qid'] for _, j in claims[i]]) for i in sorted(unclear_tops)])
    json.dump(dict(burns=burns, unclear=unclear), open(out_path, 'w'), ensure_ascii=False, indent=0)
    # Precise summits whose pairing failed, checked against the model itself.
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from catchments import ARC, fabdem_tile, _glo_name, _to_arc
    import rasterio
    done = {b['qid'] for b in burns}
    cache = {}

    def model_max(lat, lon, radius_cells=5):
        la0, lo0 = math.floor(lat), math.floor(lon)
        if (la0, lo0) not in cache:
            try:
                cache[(la0, lo0)] = rasterio.open(fabdem_tile(la0, lo0)).read(1)
            except Exception:
                try:
                    cache[(la0, lo0)] = _to_arc(rasterio.open(_glo_name(la0, lo0, 'DEM')).read(1))
                except Exception:
                    cache[(la0, lo0)] = None
            if len(cache) > 64:
                cache.pop(next(iter(cache)))
        t = cache[(la0, lo0)]
        if t is None:
            return None
        r, c = int(round((la0 + 1 - lat) * ARC)), int(round((lon - lo0) * ARC))
        w = t[max(0, r - radius_cells):r + radius_cells + 1, max(0, c - radius_cells):c + radius_cells + 1]
        return float(w.max()) if w.size else None

    rescued = 0
    for p in sorted((p for p in peaks if p['qid'] not in done and p['precision'] is not None
                     and p['precision'] <= PRECISE), key=lambda p: (math.floor(p['lat']), math.floor(p['lon']))):
        m = model_max(p['lat'], p['lon'])
        if m is None or not (-ABOVE <= p['elev'] - m <= BELOW) or p['elev'] <= m:
            continue
        burns.append(dict(qid=p['qid'], label=p['label'], elev=p['elev'], prom=p['prom'], at=[p['lat'], p['lon']],
                          placed='wikidata, checked against the model', top=None, model=m, model_prom=None,
                          km=None, score=None))
        rescued += 1
    json.dump(dict(burns=burns, unclear=unclear), open(out_path, 'w'), ensure_ascii=False, indent=0)
    print(f'{rescued} precisely placed summits burned without a top of their own')
    raised = [b['elev'] - b['model'] for b in burns]
    print(f'{len(peaks)} summits, {len(cands)} with a top that fits; {len(burns)} to burn '
          f'({sum(b["placed"] == "wikidata" for b in burns)} at Wikidata\'s position); '
          f'{len(unclear_peaks)} summits and {len(unclear_tops)} tops unclear')
    if raised:
        raised.sort()
        print(f'raised by: median {raised[len(raised) // 2]:.0f} m, 90 % {raised[int(len(raised) * .9)]:.0f} m, '
              f'max {raised[-1]:.0f} m')


if __name__ == '__main__':
    main(*sys.argv[1:4])
