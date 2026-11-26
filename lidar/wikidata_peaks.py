"""
Summits from Wikidata (CC0) around the tops of a catchment run, for matching and for burning
known heights into the model: every item that is a mountain (or a subclass) with a coordinate
and an elevation of at least --min-elevation, its preferred elevation, prominence where given,
English label and the coordinate's stated precision.

    python3 lidar/wikidata_peaks.py RUN.json OUT_DIR [--min-prominence 600] [--min-elevation 600]

Asks the public query service tile by tile (5°), one request at a time, only where the run has
tops of at least --min-prominence; each tile is saved as OUT_DIR/tile_LAT_LON.json, so a rerun
resumes. OUT_DIR/peaks.json gathers them all.
"""
import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request

ENDPOINT = 'https://query.wikidata.org/sparql'
AGENT = 'treeline-client/0.1'
STEP = 5

QUERY = '''SELECT DISTINCT ?item ?label ?lon ?lat ?precision ?elev ?prom WHERE {{
  SERVICE wikibase:box {{ ?item wdt:P625 ?coord.
    bd:serviceParam wikibase:cornerSouthWest "Point({w} {s})"^^geo:wktLiteral.
    bd:serviceParam wikibase:cornerNorthEast "Point({e} {n})"^^geo:wktLiteral. }}
  ?item p:P2044 ?es . ?es a wikibase:BestRank ; psn:P2044/wikibase:quantityAmount ?elev .
  FILTER(?elev >= {min_elev})
  ?item wdt:P31/wdt:P279* wd:Q8502 .
  ?item p:P625 ?cs . ?cs a wikibase:BestRank ; psv:P625 ?cv .
  ?cv wikibase:geoLongitude ?lon ; wikibase:geoLatitude ?lat .
  OPTIONAL {{ ?cv wikibase:geoPrecision ?precision }}
  OPTIONAL {{ ?item p:P2660 ?ps . ?ps a wikibase:BestRank ; psn:P2660/wikibase:quantityAmount ?prom }}
  OPTIONAL {{ ?item rdfs:label ?label FILTER(lang(?label) = "en") }}
}}'''


def fetch(s, n, w, e, min_elev, attempts=5):
    q = QUERY.format(s=s, n=n, w=w, e=e, min_elev=min_elev)
    req = urllib.request.Request(ENDPOINT, data=urllib.parse.urlencode({'query': q}).encode(),
                                 headers={'User-Agent': AGENT, 'Accept': 'application/sparql-results+json'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                rows = json.load(r)['results']['bindings']
            out = {}
            for b in rows:
                qid = b['item']['value'].rsplit('/', 1)[1]
                rec = out.setdefault(qid, dict(qid=qid, label=b.get('label', {}).get('value'),
                                               lat=float(b['lat']['value']), lon=float(b['lon']['value']),
                                               precision=float(b['precision']['value']) if 'precision' in b else None,
                                               elev=[], prom=None))
                rec['elev'].append(float(b['elev']['value']))
                if 'prom' in b:
                    rec['prom'] = float(b['prom']['value'])
            for rec in out.values():          # several preferred values: the highest stated
                rec['elev'] = max(rec['elev'])
            return list(out.values())
        except Exception as ex:               # the service throttles: wait and try again
            wait = 30 * (attempt + 1)
            print(f'  tile {s},{w}: {ex}; again in {wait} s', flush=True)
            time.sleep(wait)
    raise RuntimeError(f'tile {s},{w} failed {attempts} times')


def fetch_split(s, n, w, e, min_elev, depth=0):
    """ `fetch`, and where a box keeps failing (too many items for the service's time limit), its
    four quarters, down to a quarter of a degree. """
    try:
        return fetch(s, n, w, e, min_elev, attempts=2 if depth < 4 else 5)
    except RuntimeError:
        if depth >= 4:
            raise
        print(f'  {s},{w} to {n},{e}: in quarters', flush=True)
        ms, mw = (s + n) / 2, (w + e) / 2
        rows = {}
        for box in ((s, ms, w, mw), (s, ms, mw, e), (ms, n, w, mw), (ms, n, mw, e)):
            for r in fetch_split(*box, min_elev, depth + 1):
                rows[r['qid']] = r
        return list(rows.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run')
    ap.add_argument('out')
    ap.add_argument('--min-prominence', type=float, default=600)
    ap.add_argument('--min-elevation', type=float, default=600)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tops = [x['top'] for x in json.load(open(a.run))['summits'] if x['prom'] >= a.min_prominence]
    tiles = sorted({(math.floor(lat / STEP) * STEP, math.floor(lon / STEP) * STEP) for lat, lon in tops})
    print(len(tops), 'tops in', len(tiles), 'tiles', flush=True)
    peaks = {}
    for i, (s, w) in enumerate(tiles):
        path = os.path.join(a.out, f'tile_{s}_{w}.json')
        if not os.path.exists(path):
            rows = fetch_split(s, s + STEP, w, w + STEP, a.min_elevation)
            json.dump(rows, open(path + '.tmp', 'w'), ensure_ascii=False)
            os.replace(path + '.tmp', path)
            time.sleep(1)                     # one request at a time, with a breath between
        for r in json.load(open(path)):
            peaks[r['qid']] = r
        if (i + 1) % 25 == 0:
            print(f'{i + 1} of {len(tiles)} tiles, {len(peaks)} summits', flush=True)
    json.dump(list(peaks.values()), open(os.path.join(a.out, 'peaks.json'), 'w'), ensure_ascii=False)
    print(len(peaks), 'summits ->', os.path.join(a.out, 'peaks.json'))


if __name__ == '__main__':
    main()
