"""
An index of every terrain tile: what reading its cells tells, kept so that it never has to be read again
for it. One SQLite file per source (`sources.py`), $TREELINE_DATA/index/<key>.sqlite, paths relative to
the source's folder (so moving the folder keeps the index valid).

    cd lidar && uv run --project .. --group lidar python tileindex.py [KEY ...] [--jobs 12]

Incremental: a file whose size and modification time match its rows is skipped; a changed file is
redone; rows are committed as they come, so an interrupted run resumes. Big rasters are indexed in
windows of WIN cells a side (a file up to 1.25 WIN is one window), so each row covers a comparable area.

Tables
  meta    key, value (JSON): the source record, the index's version and settings
  files   path, size, mtime_ns, blake2b (16 bytes of the whole file), epsg, crs_wkt, transform (JSON),
          width, height, dtype, scale, nodata, block
  rows    one per window: file_id, row_off, col_off, height, width, native bounds x0 y0 x1 y1;
          n_valid, n_nodata, n_zero (cells exactly 0 m: a fill, as BW's and KRON86's);
          zmin, zmax, zmean, zstd, p01, p50, p99 (metres);
          xmax, ymax / xmin, ymin (native coordinates of the highest / lowest cell centre);
          spikes_up / spikes_down (cells more than SPIKE m above all 8 neighbours / below them),
          spike (the worst, signed, m), spike_x, spike_y;
          peaks (JSON [[z, x, y, edge], ...]: cells highest within PEAK_R m, at most MAX_PEAKS,
          edge = 1 within PEAK_R of the window's edge, where higher ground may lie beyond it);
          coarse_max, coarse_min, coarse_mean (COARSE x COARSE int32 cm, INT32_MIN = no data),
          coarse_n (uint16 valid cells per coarse cell), all zlib-compressed blobs
  rows_3035  R*tree of each row's bounds in EPSG:3035; rows_ll  the same in lon/lat

`coarse(row, 'max')` and friends decode the blobs; `query(key, lon0, lat0, lon1, lat1)` finds rows.
"""
import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources  # noqa: E402
from data import path  # noqa: E402

VERSION = 1
WIN = 2048
COARSE = 32
SPIKE = 5.0
PEAK_R = 100.0
MAX_PEAKS = 64
NODATA = np.iinfo(np.int32).min
BIG = 256 << 20                         # bytes: a file this big is split into slices of windows up front
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime_ns INTEGER,
  blake2b TEXT, epsg INTEGER, crs_wkt TEXT, transform TEXT, width INTEGER, height INTEGER, dtype TEXT,
  scale REAL, nodata REAL, block TEXT, windows INTEGER);
CREATE TABLE IF NOT EXISTS rows (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
  row_off INTEGER, col_off INTEGER, height INTEGER, width INTEGER, x0 REAL, y0 REAL, x1 REAL, y1 REAL,
  n_valid INTEGER, n_nodata INTEGER, n_zero INTEGER, zmin REAL, zmax REAL, zmean REAL, zstd REAL,
  p01 REAL, p50 REAL, p99 REAL, xmax REAL, ymax REAL, xmin REAL, ymin REAL,
  spikes_up INTEGER, spikes_down INTEGER, spike REAL, spike_x REAL, spike_y REAL, peaks TEXT,
  coarse_max BLOB, coarse_min BLOB, coarse_mean BLOB, coarse_n BLOB, UNIQUE (file_id, row_off, col_off));
CREATE VIRTUAL TABLE IF NOT EXISTS rows_3035 USING rtree(id, x0, x1, y0, y1);
CREATE VIRTUAL TABLE IF NOT EXISTS rows_ll USING rtree(id, x0, x1, y0, y1);
"""


def windows(h, w):
    if h <= WIN * 1.25 and w <= WIN * 1.25:
        return [(0, 0, h, w)]
    return [(r, c, min(WIN, h - r), min(WIN, w - c)) for r in range(0, h, WIN) for c in range(0, w, WIN)]


def _edges(n):
    return np.linspace(0, n, COARSE + 1).round().astype(np.int64)


def _reduce(a, how, h, w):
    """ a (h x w float64, NaN = no data) to COARSE x COARSE by `how` over each block. """
    re, ce = _edges(h), _edges(w)
    ok = (np.diff(re) > 0)[:, None] & (np.diff(ce) > 0)[None, :]
    r = np.clip(re[:-1], 0, h - 1)
    c = np.clip(ce[:-1], 0, w - 1)
    if how == 'n':
        v = (~np.isnan(a)).astype(np.int64)
        out = np.add.reduceat(np.add.reduceat(v, r, axis=0), c, axis=1)
    elif how == 'sum':
        out = np.add.reduceat(np.add.reduceat(np.nan_to_num(a, nan=0.0), r, axis=0), c, axis=1)
    else:
        f = np.fmax if how == 'max' else np.fmin
        out = f.reduceat(f.reduceat(a, r, axis=0), c, axis=1)       # fmax/fmin skip NaN
    return np.where(ok, out, np.nan if how in ('max', 'min', 'sum') else 0)


def _blob(a, dtype):
    return zlib.compress(np.ascontiguousarray(a, dtype=dtype).tobytes(), 6)


def _cm(a):
    q = np.full(a.shape, NODATA, np.int64)
    good = np.isfinite(a)
    q[good] = np.round(a[good] * 100)
    return q


def stats(z, transform, res_m):
    """ The row's numbers for heights `z` (float64, NaN = no data) on `transform` (the window's). """
    from scipy import ndimage
    h, w = z.shape
    valid = ~np.isnan(z)
    nv = int(valid.sum())
    cx = lambda c: transform.c + (c + 0.5) * transform.a
    cy = lambda r: transform.f + (r + 0.5) * transform.e
    out = dict(n_valid=nv, n_nodata=int(z.size - nv), n_zero=int((z == 0).sum()))
    if nv == 0:
        return out
    v = z[valid]
    out.update(zmin=float(v.min()), zmax=float(v.max()), zmean=float(v.mean()), zstd=float(v.std()))
    out['p01'], out['p50'], out['p99'] = (float(x) for x in np.percentile(v, [1, 50, 99]))
    lo = np.where(valid, z, np.inf)
    hi = np.where(valid, z, -np.inf)
    r, c = np.unravel_index(np.argmax(hi), z.shape)
    out.update(xmax=cx(c), ymax=cy(r))
    r, c = np.unravel_index(np.argmin(lo), z.shape)
    out.update(xmin=cx(c), ymin=cy(r))
    # spikes: above all 8 neighbours with data, or below them, by more than SPIKE
    ring = np.ones((3, 3), bool)
    ring[1, 1] = False
    nbmax = ndimage.maximum_filter(hi, footprint=ring, mode='constant', cval=-np.inf)
    nbmin = ndimage.minimum_filter(lo, footprint=ring, mode='constant', cval=np.inf)
    up = np.where(valid & np.isfinite(nbmax), z - nbmax, 0)
    down = np.where(valid & np.isfinite(nbmin), nbmin - z, 0)
    out.update(spikes_up=int((up > SPIKE).sum()), spikes_down=int((down > SPIKE).sum()))
    iu, idn = np.argmax(up), np.argmax(down)
    worst = (up.flat[iu], iu, 1) if up.flat[iu] >= down.flat[idn] else (down.flat[idn], idn, -1)
    if worst[0] > SPIKE:
        r, c = np.unravel_index(worst[1], z.shape)
        out.update(spike=float(worst[0] * worst[2]), spike_x=cx(c), spike_y=cy(r))
    # peaks: highest within PEAK_R, greedily thinned so no two lie within PEAK_R
    k = max(1, int(math.ceil(PEAK_R / res_m)))
    mf = ndimage.maximum_filter(hi, size=2 * k + 1, mode='constant', cval=-np.inf)
    rr, cc = np.nonzero(valid & (hi == mf))
    order = np.argsort(-z[rr, cc], kind='stable')
    peaks, taken = [], []
    for i in order:
        r, c = int(rr[i]), int(cc[i])
        if any((r - a) ** 2 + (c - b) ** 2 <= k * k for a, b in taken):
            continue
        taken.append((r, c))
        edge = int(r < k or c < k or r >= h - k or c >= w - k)
        peaks.append([round(float(z[r, c]), 2), round(cx(c), 3), round(cy(r), 3), edge])
        if len(peaks) >= MAX_PEAKS:
            break
    out['peaks'] = json.dumps(peaks)
    n = _reduce(z, 'n', h, w)
    s = _reduce(z, 'sum', h, w)
    out.update(coarse_max=_blob(_cm(_reduce(z, 'max', h, w)), np.int32),
               coarse_min=_blob(_cm(_reduce(z, 'min', h, w)), np.int32),
               coarse_mean=_blob(_cm(np.where(n > 0, s / np.maximum(n, 1), np.nan)), np.int32),
               coarse_n=_blob(np.minimum(n, 65535), np.uint16))
    return out


def job(a):
    """ One file's windows (a slice of them for a big file): its header, its hash (with the first slice), its rows. """
    full, rel, wins, first, res_m = a
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window
    head, rows = None, []
    with rasterio.open(full) as d:
        st = os.stat(full)
        if first:
            hsh = hashlib.blake2b(digest_size=16)
            with open(full, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 24), b''):
                    hsh.update(chunk)
            head = dict(path=rel, size=st.st_size, mtime_ns=st.st_mtime_ns, blake2b=hsh.hexdigest(),
                        epsg=d.crs.to_epsg() if d.crs else None, crs_wkt=d.crs.to_wkt() if d.crs else None,
                        transform=json.dumps(list(d.transform)[:6]), width=d.width, height=d.height,
                        dtype=d.dtypes[0], scale=float(d.scales[0] or 1.0), nodata=d.nodata,
                        block=json.dumps(d.block_shapes[0]), windows=len(windows(d.height, d.width)))
        scale = float(d.scales[0] or 1.0)
        offset = float(d.offsets[0] or 0.0)
        geographic = d.crs.is_geographic if d.crs else False
        if wins is None:
            wins = windows(d.height, d.width)
        for r0, c0, h, w in wins:
            win = Window(c0, r0, w, h)
            m = d.read(1, window=win, masked=True)
            z = np.where(np.ma.getmaskarray(m), np.nan, m.data.astype(np.float64) * scale + offset)
            t = d.window_transform(win)
            x0, y1 = t.c, t.f
            x1, y0 = t.c + t.a * w, t.f + t.e * h
            rm = res_m
            if geographic:                       # cells in degrees: metres from the latitude
                rm = abs(t.e) * 111320.0
            row = dict(row_off=r0, col_off=c0, height=h, width=w, x0=x0, y0=y0, x1=x1, y1=y1)
            row.update(stats(z, t, rm))
            if d.crs:                           # a file without a CRS: its numbers, but no footprint (the validator lists it)
                row['b3035'] = transform_bounds(d.crs, 'EPSG:3035', x0, y0, x1, y1, densify_pts=21)
                row['bll'] = transform_bounds(d.crs, 'EPSG:4326', x0, y0, x1, y1, densify_pts=21)
            rows.append(row)
    return rel, st.st_size, st.st_mtime_ns, head, rows


ROWCOLS = ['row_off', 'col_off', 'height', 'width', 'x0', 'y0', 'x1', 'y1', 'n_valid', 'n_nodata', 'n_zero',
           'zmin', 'zmax', 'zmean', 'zstd', 'p01', 'p50', 'p99', 'xmax', 'ymax', 'xmin', 'ymin', 'spikes_up',
           'spikes_down', 'spike', 'spike_x', 'spike_y', 'peaks', 'coarse_max', 'coarse_min', 'coarse_mean', 'coarse_n']


def db_path(key):
    return os.path.join(path('index'), key + '.sqlite')


def connect(key):
    os.makedirs(path('index'), exist_ok=True)
    con = sqlite3.connect(db_path(key))
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA journal_mode = WAL')
    con.executescript(SCHEMA)
    return con


def build(s, jobs):
    from tqdm import tqdm
    con = connect(s['key'])
    con.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('source', json.dumps(s)))
    con.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('settings', json.dumps(
        dict(version=VERSION, win=WIN, coarse=COARSE, spike=SPIKE, peak_r=PEAK_R, max_peaks=MAX_PEAKS))))
    con.commit()
    root = sources.folder(s)
    known = {p: (sz, mt, fid) for p, sz, mt, fid in con.execute('SELECT path, size, mtime_ns, id FROM files')}
    wins_of = dict(con.execute('SELECT id, windows FROM files'))
    done = {}
    for fid, r0, c0 in con.execute('SELECT file_id, row_off, col_off FROM rows'):
        done.setdefault(fid, set()).add((r0, c0))
    import rasterio
    work = []
    fs = sources.files(s)
    for full in fs:
        rel = os.path.relpath(full, root)
        st = os.stat(full)
        k = known.get(rel)
        if k and (k[0], k[1]) != (st.st_size, st.st_mtime_ns):      # changed: redo it
            con.execute('DELETE FROM rows_3035 WHERE id IN (SELECT id FROM rows WHERE file_id = ?)', (k[2],))
            con.execute('DELETE FROM rows_ll WHERE id IN (SELECT id FROM rows WHERE file_id = ?)', (k[2],))
            con.execute('DELETE FROM files WHERE id = ?', (k[2],))
            k = None
        have = done.get(k[2], set()) if k else set()
        if k and wins_of.get(k[2]) == len(have):
            continue                            # whole: nothing to open
        if st.st_size < BIG:                    # a tile: the worker finds its windows
            work.append((full, rel, None, k is None, s['res']))
            continue
        with rasterio.open(full) as d:
            ws = windows(d.height, d.width)
        todo = [x for x in ws if (x[0], x[1]) not in have]
        for i in range(0, len(todo), 8):        # a big file in slices of 8 windows, so its work spreads
            work.append((full, rel, todo[i:i + 8], k is None and i == 0, s['res']))
    con.commit()
    if not work:
        print(f'{s["key"]:10s} {len(fs):6d} files, nothing to do', flush=True)
        return
    fid_of = {p: fid for p, (_, _, fid) in known.items()}
    t0, n = time.time(), 0
    with ProcessPoolExecutor(jobs) as pool:
        for rel, size, mtime, head, rows in tqdm(pool.map(job, work, chunksize=4), total=len(work),
                                                 desc=s['key'], unit='job', mininterval=10):
            if head:
                cur = con.execute('INSERT OR REPLACE INTO files (path, size, mtime_ns, blake2b, epsg, crs_wkt, transform,'
                                  ' width, height, dtype, scale, nodata, block, windows) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  tuple(head[k] for k in ('path', 'size', 'mtime_ns', 'blake2b', 'epsg', 'crs_wkt',
                                                          'transform', 'width', 'height', 'dtype', 'scale', 'nodata',
                                                          'block', 'windows')))
                fid_of[rel] = cur.lastrowid
            fid = fid_of[rel]
            for r in rows:
                cur = con.execute(f'INSERT OR REPLACE INTO rows (file_id, {", ".join(ROWCOLS)}) VALUES '
                                  f'(?, {", ".join("?" * len(ROWCOLS))})', (fid, *[r.get(k) for k in ROWCOLS]))
                b, ll = r.get('b3035'), r.get('bll')
                if b is None:
                    print(f'{s["key"]}: {rel} has no CRS: indexed without a footprint', flush=True)
                else:
                    con.execute('INSERT OR REPLACE INTO rows_3035 VALUES (?,?,?,?,?)', (cur.lastrowid, b[0], b[2], b[1], b[3]))
                    con.execute('INSERT OR REPLACE INTO rows_ll VALUES (?,?,?,?,?)', (cur.lastrowid, ll[0], ll[2], ll[1], ll[3]))
                n += 1
            if n % 500 < len(rows):
                con.commit()
    con.commit()
    print(f'{s["key"]:10s} {len(fs):6d} files, {n:7d} rows indexed in {(time.time() - t0) / 60:5.1f} min', flush=True)


def coarse(row, what='max'):
    """ A row's coarse grid as float metres (NaN = no data), or counts for 'n'. """
    b = row[f'coarse_{what}']
    if b is None:
        return None
    if what == 'n':
        return np.frombuffer(zlib.decompress(b), np.uint16).reshape(COARSE, COARSE)
    q = np.frombuffer(zlib.decompress(b), np.int32).reshape(COARSE, COARSE)
    return np.where(q == NODATA, np.nan, q / 100.0)


def query(key, lon0, lat0, lon1, lat1):
    """ The rows of source `key` whose lon/lat bounds touch the box, as dicts (with their file's path). """
    con = sqlite3.connect(db_path(key))
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(
        'SELECT rows.*, files.path FROM rows_ll JOIN rows ON rows.id = rows_ll.id JOIN files ON files.id = rows.file_id '
        'WHERE rows_ll.x1 >= ? AND rows_ll.x0 <= ? AND rows_ll.y1 >= ? AND rows_ll.y0 <= ?', (lon0, lon1, lat0, lat1))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('keys', nargs='*', help='sources to index (all by default)')
    ap.add_argument('--jobs', type=int, default=12)
    a = ap.parse_args()
    for s in [sources.BY_KEY[k] for k in a.keys] if a.keys else sources.SOURCES:
        if not sources.files(s):
            print(f'{s["key"]:10s} no files in {sources.folder(s)}', flush=True)
            continue
        build(s, a.jobs)


if __name__ == '__main__':
    main()
