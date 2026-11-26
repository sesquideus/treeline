"""
Known summits to burn into the model before flooding: each Wikidata summit (`wikidata_peaks.py`)
matched to a top of a catchment run, so that the flood sees its surveyed height where the
30 m model reads a sharp summit low (and so swaps it with a lesser neighbour).

    python3 lidar/burn_match.py RUN.json PEAKS.json OUT.json [--below 250] [--above 30]

- A pair: the top within 2 km of the summit, the model at most 250 m below the stated
  elevation and at most 30 m above (it reads sharp summits low, rarely high); --below and
  --above tighten that where the model is known to be closer (the Carpathians: FABDEM reads
  their summits 4–15 m low, up to ~40 m on sharp ones, so 40 below keeps out Wikidata's
  heights that are simply wrong, as about one in five there is, by 50–590 m). Every top of
  the run counts, not only the prominent ones: a summit the model got wrong may have come out
  with a small prominence (the Finsteraarhorn, P572 instead of P2279).
- Score: distance / 2 km + height difference / --below; the best pairs are taken first.
- Unclear is left alone: a summit whose second-best top scores within 0.1 of its best, or a
  top two summits compete for within 0.1. Those keep the model's height (source FABDEM).
- Where to burn: at Wikidata's coordinate when its stated precision is 0.001° (~100 m) or
  better, else at the model's top. Only where the summit is higher than the model there.
- A summit placed that precisely needs no top to confirm where it is, only a model that agrees
  with it: if its pairing failed (a neighbour standing on the model's top took it, as the
  Glocknerwand did from the Großglockner), it is burned anyway when the model's highest cell
  within 150 m of it lies between --below and --above its stated height.
- --check-position: a summit placed to 0.001° is burned at its coordinate only if the model's
  highest cell within 150 m of it lies within --below/--above too, and the model reads within
  5 m of the paired top's height at the coordinate itself (or the top is within 60 m); else at
  its top. One with no top of its own is burned at that highest cell within 150 m, unless the
  model reads within 5 m of it at the coordinate itself. Wikidata puts Berlebashka 1.4 km off its summit, and Khomiak 180 m down its slope:
  burned there, each made a summit of a slope and a second summit of the model's own top.
- PEAKS.json may be OSM's (`osm_peaks.py`: its peaks, volcanoes and hills with a numeric `ele`,
  placed precisely), the burns then citing `source` OpenStreetMap and `qid` 'osm:ID'. --also
  WIKIDATA.json cross-checks them (an OSM summit Wikidata states within 20 m of, within 300 m,
  may stand --confirmed-below under the model rather than --below) and adds its own summits
  where OSM has none with a height within 300 m.
- --known KNOWN.json (the database's summits, [{pk, name, lat, lon, alt}]): the database's own
  heights burned too, each at its own position when the model's highest cell within 150 m of it
  lies between --below and --above it, so that the flood sees the summit the database has where
  the model reads two neighbours about level the other way round (and a changeset finds it
  there, rather than beside it). The database's height wins: a Wikidata summit within --known-km
  of one is not burned (it is listed as `deferred`).
"""
import argparse
import json
import math
import sys

MAX_KM, BELOW, ABOVE, MARGIN, PRECISE = 2.0, 250.0, 30.0, 0.1, 0.001
SLOPE = 5.0                  # m: --check-position, a coordinate this far below its top is on the slope


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110.574, (a[1] - b[1]) * 111.32 * k)


def pair(tops, peaks, below=BELOW, above=ABOVE):
    """
    Each summit (`peaks`, with `elev`) paired with a top of the run (`tops`: {top, h}): the
    pairs [(peak index, top index, score)], and the summits and tops left unclear.
    """
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
                    if d <= MAX_KM and -p.get('above', above) <= dh <= p.get('below', below):
                        out.append((d / MAX_KM + abs(dh) / below, i))
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
    return pairs, cands, unclear_peaks, unclear_tops, claims


OSM_KINDS = {'peak', 'volcano', 'hill'}


def osm_ele(v):
    """ An OSM `ele` as metres, or None: '2061', '2061 m', '2 061', '2061,5'. """
    if not v:
        return None
    v = v.strip().lower().removesuffix('m').strip().replace(' ', '').replace(',', '.')
    try:
        h = float(v)
    except ValueError:
        return None
    return h if -500 < h < 9000 else None


def load_peaks(path):
    """ Summits with heights from PEAKS.json: Wikidata's (`wikidata_peaks.py`) as they are, or OSM's
    (`osm_peaks.py`: peaks, volcanoes and hills with a numeric `ele`), in Wikidata's shape with
    `qid` 'osm:ID' and `source` OpenStreetMap; an OSM node is placed precisely. """
    items = json.load(open(path))
    if items and 'ele' in items[0] and 'id' in items[0]:
        out = []
        for e in items:
            h = osm_ele(e.get('ele'))
            if e.get('kind') in OSM_KINDS and h is not None:
                out.append(dict(qid=f'osm:{e["id"]}', label=e.get('name'), lat=e['lat'], lon=e['lon'],
                                precision=1e-5, elev=h, prom=None, source='OpenStreetMap'))
        return out
    return [dict(p, source=p.get('source', 'Wikidata')) for p in items if p.get('elev') is not None]


def combine(primary, secondary, within_km=0.3, agree=20.0, confirmed_below=None):
    """
    The primary summits (OSM's), each cross-checked against the secondary (Wikidata's) within
    `within_km`: where the two agree within `agree` m, the primary's guard below the stated height
    widens to `confirmed_below` (two sources against the model); the secondary summits with no
    primary near them join as summits of their own. Returns the list, the primary first.
    """
    CELL = 0.05
    grid = {}
    for p in primary:
        grid.setdefault((math.floor(p['lat'] / CELL), math.floor(p['lon'] / CELL)), []).append(p)
    extra = []
    for q in secondary:
        ci, cj = math.floor(q['lat'] / CELL), math.floor(q['lon'] / CELL)
        near = [p for di in (-1, 0, 1) for dj in (-1, 0, 1) for p in grid.get((ci + di, cj + dj), ())
                if km((p['lat'], p['lon']), (q['lat'], q['lon'])) <= within_km]
        if not near:
            extra.append(q)
            continue
        for p in near:
            p.setdefault('checked', []).append(dict(qid=q['qid'], elev=q['elev']))
            if confirmed_below and abs(p['elev'] - q['elev']) <= agree:
                p['below'] = max(p.get('below', 0), confirmed_below)
    return primary + extra


def near_known(peaks, known, within_km):
    """ The indices of `peaks` standing within `within_km` of a summit in `known`. """
    CELL = 0.05
    grid = {}
    for k in known:
        grid.setdefault((math.floor(k['lat'] / CELL), math.floor(k['lon'] / CELL)), []).append(k)
    out = set()
    for j, p in enumerate(peaks):
        ci, cj = math.floor(p['lat'] / CELL), math.floor(p['lon'] / CELL)
        if any(km((p['lat'], p['lon']), (k['lat'], k['lon'])) <= within_km
               for di in (-1, 0, 1) for dj in (-1, 0, 1) for k in grid.get((ci + di, cj + dj), ())):
            out.add(j)
    return out


def main(run_path, peaks_path, out_path, below=BELOW, above=ABOVE, known_path=None, known_km=0.3,
         check_position=False, also_path=None, confirmed_below=None):
    tops = json.load(open(run_path))['summits']
    peaks = load_peaks(peaks_path)
    if also_path:
        peaks = combine(peaks, load_peaks(also_path), confirmed_below=confirmed_below)
    known = [k for k in json.load(open(known_path)) if k.get('alt') is not None] if known_path else []
    deferred = []
    if known:
        skip = near_known(peaks, known, known_km)
        deferred = [dict(qid=peaks[j]['qid'], label=peaks[j]['label'], elev=peaks[j]['elev'],
                         why=f'within {known_km:g} km of a summit the database has') for j in sorted(skip)]
        peaks = [p for j, p in enumerate(peaks) if j not in skip]
    pairs, cands, unclear_peaks, unclear_tops, claims = pair(tops, peaks, below, above)

    # the model's highest cell near a point, to check a stated height against
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from catchments import ARC, model_tile, read_model, _glo_name, _to_arc
    import rasterio
    cache = {}

    def model_max(lat, lon, radius_cells=5):
        la0, lo0 = math.floor(lat), math.floor(lon)
        if (la0, lo0) not in cache:
            try:
                cache[(la0, lo0)] = read_model(model_tile(la0, lo0))
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

    def model_argmax(lat, lon, radius_cells=5):
        """ Where the model's highest cell within `radius_cells` of (lat, lon) is: [lat, lon]. """
        la0, lo0 = math.floor(lat), math.floor(lon)
        model_max(lat, lon, radius_cells)
        t = cache[(la0, lo0)]
        r, c = int(round((la0 + 1 - lat) * ARC)), int(round((lon - lo0) * ARC))
        r0, c0 = max(0, r - radius_cells), max(0, c - radius_cells)
        w = t[r0:r + radius_cells + 1, c0:c + radius_cells + 1]
        i, j = divmod(int(w.argmax()), w.shape[1])
        return [round(la0 + 1 - (r0 + i) / ARC, 7), round(lo0 + (c0 + j) / ARC, 7)]

    burns = []
    for j, i, score in pairs:
        p, t = peaks[j], tops[i]
        precise = p['precision'] is not None and p['precision'] <= PRECISE
        placed = 'wikidata' if precise else 'model top'
        if precise and check_position:
            # stated precisely, but is it on the summit? The model must read about its height
            # within 150 m, and about its top's height (5 m) at the coordinate itself: a cell raised
            # on the slope beside the model's top makes two summits of one, the col between them
            m, here = model_max(p['lat'], p['lon']), model_max(p['lat'], p['lon'], 1)
            if m is None or not (-p.get('above', above) <= p['elev'] - m <= p.get('below', below)):
                precise = False
                placed = (f"model top: Wikidata's coordinate is off the summit (the model reads "
                          f"{'nothing' if m is None else f'{m:.0f} m'} within 150 m of it)")
            elif here < t['h'] - SLOPE and km((p['lat'], p['lon']), t['top']) > 0.06:
                precise = False
                placed = (f"model top: Wikidata's coordinate is on its slope ({here:.0f} m there, "
                          f"{t['h']:.0f} m at the top)")
        at = [p['lat'], p['lon']] if precise else list(t['top'])
        if p['elev'] <= t['h'] and not precise:
            continue                 # nothing to raise where the model is already as high
        burns.append(dict(qid=p['qid'], source=p.get('source', 'Wikidata'), label=p['label'], elev=p['elev'],
                          prom=p['prom'], at=at, placed=placed, top=t['top'], model=t['h'],
                          model_prom=t['prom'], km=round(km((p['lat'], p['lon']), t['top']), 3),
                          score=round(score, 3)))
    unclear = ([dict(qid=peaks[j]['qid'], label=peaks[j]['label'], elev=peaks[j]['elev'], why='two tops fit')
                for j in sorted(unclear_peaks)]
               + [dict(top=tops[i]['top'], model=tops[i]['h'], why='two summits fit',
                       summits=[peaks[j]['qid'] for _, j in claims[i]]) for i in sorted(unclear_tops)])
    json.dump(dict(burns=burns, unclear=unclear), open(out_path, 'w'), ensure_ascii=False, indent=0)
    # Precise summits whose pairing failed, checked against the model itself (one paired, even if
    # not burned because its top reads as high, is not a failure: its top stands for it)
    done = {b['qid'] for b in burns} | ({peaks[j]['qid'] for j, _, _ in pairs} if check_position else set())
    rescued = 0
    for p in sorted((p for p in peaks if p['qid'] not in done and p['precision'] is not None
                     and p['precision'] <= PRECISE), key=lambda p: (math.floor(p['lat']), math.floor(p['lon']))):
        m = model_max(p['lat'], p['lon'])
        if m is None or not (-p.get('above', above) <= p['elev'] - m <= p.get('below', below)) or p['elev'] <= m:
            continue
        at, placed = [p['lat'], p['lon']], 'wikidata, checked against the model'
        if check_position and model_max(p['lat'], p['lon'], 1) < m - SLOPE:
            # on the slope of the model's highest cell within 150 m: raised there, not beside it
            at = model_argmax(p['lat'], p['lon'])
            placed = (f"the model's highest cell within 150 m: Wikidata's coordinate is on its slope "
                      f"({model_max(p['lat'], p['lon'], 1):.0f} m there, {m:.0f} m at the top)")
        burns.append(dict(qid=p['qid'], source=p.get('source', 'Wikidata'), label=p['label'], elev=p['elev'],
                          prom=p['prom'], at=at, placed=placed, top=None, model=m, model_prom=None, km=None,
                          score=None))
        rescued += 1
    print(f'{rescued} precisely placed summits burned without a top of their own')
    if known:
        n = 0
        for k in sorted(known, key=lambda k: (math.floor(k['lat']), math.floor(k['lon']))):
            m = model_max(k['lat'], k['lon'])
            if m is None or not (-above <= k['alt'] - m <= below) or k['alt'] <= m:
                continue
            burns.append(dict(qid=None, db=k['pk'], source='database', label=k['name'], elev=k['alt'], prom=k.get('prom'),
                              at=[k['lat'], k['lon']], placed='database, checked against the model', top=None,
                              model=m, model_prom=None, km=None, score=None))
            n += 1
        print(f'{n} of {len(known)} summits of the database burned at their own heights; '
              f'{len(deferred)} Wikidata summits left to them')
    json.dump(dict(burns=burns, unclear=unclear, deferred=deferred), open(out_path, 'w'), ensure_ascii=False, indent=0)
    raised = [b['elev'] - b['model'] for b in burns]
    print(f'{len(peaks)} summits, {len(cands)} with a top that fits; {len(burns)} to burn '
          f'({sum(b["placed"] == "wikidata" for b in burns)} at Wikidata\'s position); '
          f'{len(unclear_peaks)} summits and {len(unclear_tops)} tops unclear')
    if raised:
        raised.sort()
        print(f'raised by: median {raised[len(raised) // 2]:.0f} m, 90 % {raised[int(len(raised) * .9)]:.0f} m, '
              f'max {raised[-1]:.0f} m')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run')
    ap.add_argument('peaks')
    ap.add_argument('out')
    ap.add_argument('--below', type=float, default=BELOW, help='m the model may read below the stated height')
    ap.add_argument('--above', type=float, default=ABOVE, help='m the model may read above the stated height')
    ap.add_argument('--known', help="the database's summits (KNOWN.json), their heights burned too")
    ap.add_argument('--known-km', type=float, default=0.3,
                    help='km: a Wikidata summit this near a known one is left to it')
    ap.add_argument('--check-position', action='store_true',
                    help="a precisely placed summit is burned at its coordinate only where the model reads its height "
                         "there (within --below/--above); else at its top")
    ap.add_argument('--also', help="a second PEAKS.json (Wikidata's beside OSM's): a cross-check of the first, and "
                                   'summits of its own where the first has none within 300 m')
    ap.add_argument('--confirmed-below', type=float,
                    help='m: the guard below a summit both sources state within 20 m of each other')
    a = ap.parse_args()
    main(a.run, a.peaks, a.out, a.below, a.above, a.known, a.known_km, a.check_position, a.also,
         a.confirmed_below)
