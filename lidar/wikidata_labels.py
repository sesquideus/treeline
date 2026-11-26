"""
Wikidata labels (CC0) in given languages for the items of a PEAKS.json (`wikidata_peaks.py`),
for names in the local script: `catchments_changeset.py --local-lang uk --labels LABELS.json`.

    python3 lidar/wikidata_labels.py PEAKS.json LABELS.json [--langs uk,en] [--bbox S,N,W,E]

Asks wbgetentities 50 items at a time, one request at a time with a pause between, as
`treeline-client/0.1`. LABELS.json is {qid: {lang: label}}; a rerun asks only for the items it
lacks, so it can be extended with another PEAKS.json.
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request

API = 'https://www.wikidata.org/w/api.php'
AGENT = 'treeline-client/0.1'
BATCH = 50


def fetch(qids, langs):
    q = urllib.parse.urlencode(dict(action='wbgetentities', ids='|'.join(qids), props='labels',
                                    languages='|'.join(langs), format='json'))
    req = urllib.request.Request(f'{API}?{q}', headers={'User-Agent': AGENT})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                ents = json.load(r)['entities']
            return {q: {lg: v['value'] for lg, v in e.get('labels', {}).items()} for q, e in ents.items()}
        except Exception as e:                 # noqa: BLE001 — retried, then raised
            if attempt == 4:
                raise
            print(f'  retrying after {e}', flush=True)
            time.sleep(10 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('peaks')
    ap.add_argument('out')
    ap.add_argument('--langs', default='uk,en')
    ap.add_argument('--bbox', help='S,N,W,E: only the items in this box')
    a = ap.parse_args()
    langs = a.langs.split(',')
    peaks = json.load(open(a.peaks))
    if a.bbox:
        s, n, w, e = (float(v) for v in a.bbox.split(','))
        peaks = [p for p in peaks if s <= p['lat'] <= n and w <= p['lon'] <= e]
    have = json.load(open(a.out)) if os.path.exists(a.out) else {}
    todo = sorted({p['qid'] for p in peaks} - set(have))
    print(f'{len(peaks)} items, {len(todo)} to ask for', flush=True)
    for i in range(0, len(todo), BATCH):
        have.update(fetch(todo[i:i + BATCH], langs))
        json.dump(have, open(a.out, 'w'), ensure_ascii=False)
        time.sleep(1)
    print(f'{sum(1 for p in peaks if have.get(p["qid"], {}).get(langs[0]))} of {len(peaks)} with a '
          f'{langs[0]} label -> {a.out}')


if __name__ == '__main__':
    main()
