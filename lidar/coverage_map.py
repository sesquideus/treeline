"""
Where each terrain source has data: every file's no-data mask, read decimated, laid onto one grid of
200 m cells in EPSG:3035 over the joint-run box (with a margin), then outlined as GeoJSON in lon/lat for
the coverage map. A cell counts as covered if the file's cell nearest its centre has data, so the outlines
are good to about 200 m, not 1 m.

    cd lidar && uv run --project .. --group lidar python coverage_map.py OUT_DIR

OUT_DIR/coverage.json: {sources: [{key, label, ..., km2, files, bytes, geometry}], land, box}.
OUT_DIR/masks.npz keeps each source's grid, so a rerun with ONLY=key,key rescans just those.
"""
import glob
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.transform import Affine
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window
from tqdm import tqdm

from data import ROOT

D = ROOT
BOX = (9.5, 46.3, 24.2, 52.0)                    # lon0, lat0, lon1, lat1: the joint run
VIEW = (8.8, 45.8, 24.9, 52.5)                   # what the map shows
RES = 200.0
CHUNK = 8192                                     # a big file is read in windows this size


def dgm(st, name, years, crs='ETRS89 / UTM 32N (EPSG:25832)', tile='1 km tiles'):
    return dict(key='DE-' + st, label=f'DGM1 {name}', country='DE', files=f'{D}/intcm/DE/{st}/tiles/*.tif',
                res='1 m', crs=crs, tiles=tile, heights='DHHN2016 (EPSG:7837)', years=years, nodata=None,
                lidar=True)


SOURCES = [
    dict(key='SK', label='DMR 5.0', country='SK', files=f'{D}/intcm/SK/dmr5_etrs89.tif', res='1 m',
         crs='ETRS89 / UTM 34N (EPSG:3046)', tiles='one mosaic', heights='ETRS89 ellipsoidal (EPSG:4937)',
         years='2017–2023', nodata=None, lidar=True),
    dict(key='CZ', label='DMR 5G', country='CZ', files=f'{D}/intcm/CZ/*.tif', res='1 m (triangulated from points)',
         crs='ETRS89 / TM33N (EPSG:3045)', tiles='2 km tiles', heights='Bpv (EPSG:8357)', years='2009–2013',
         nodata=None, lidar=True),
    dict(key='AT', label='ALS-DGM', country='AT', files=f'{D}/intcm/AT/*.tif', res='1 m',
         crs='ETRS89 / LAEA (EPSG:3035)', tiles='50 km tiles', heights='EVRF2000 Austria (EPSG:9274)',
         years='edition 2025-09', nodata=None, lidar=True),
    dict(key='PL-EVRF', label='NMT EVRF2007, 1992 sheets', country='PL', files=f'{D}/intcm/PL/EVRF/sheets/[MN]-*.tif',
         res='1 m', crs='PUWG 1992 (EPSG:2180)', tiles='1:5000 sheets', heights='PL-EVRF2007-NH (EPSG:9651)',
         years='2018–2026', nodata=None, lidar=True),
    dict(key='PL-EVRF-2000', label='NMT EVRF2007, PL-2000 sheets', country='PL',
         files=f'{D}/intcm/PL/EVRF/sheets/[5-8].*.tif', res='1 m', crs='PL-2000 zones 5–7 (EPSG:2176–2178)',
         tiles='PL-2000 sheets', heights='PL-EVRF2007-NH (EPSG:9651)', years='2018–2026', nodata=None, lidar=True),
    dict(key='PL-KRON86', label='NMT KRON86 (older)', country='PL', files=f'{D}/intcm/PL/KRON86/tiles/*.tif', res='1 m',
         crs='PUWG 1992 (EPSG:2180)', tiles='10 km tiles', heights='PL-KRON86-NH (EPSG:9650)', years='2010–2015',
         nodata=None, lidar=True),
    dgm('BY', 'Bayern', '2008–2024'),
    dgm('BW', 'Baden-Württemberg', '2000–2023', tile='1 km tiles (2 km cells)'),
    dgm('HE', 'Hessen', '2008–2022', tile='1 km squares (WCS)'),
    dgm('NI', 'Niedersachsen', '2013–2024'),
    dgm('ST', 'Sachsen-Anhalt', '2008–2023', tile='2 km tiles'),
    dgm('SN', 'Sachsen', '2015–2023', crs='ETRS89 / UTM 33N (EPSG:25833)', tile='2 km tiles'),
    dgm('BB', 'Brandenburg', '2008–2023', crs='ETRS89 / UTM 33N (EPSG:25833)'),
]
EXTRA = os.environ.get('EXTRA')                  # a JSON file of more sources, same fields
if EXTRA:
    SOURCES += json.load(open(EXTRA))


def grid():
    x0, y0, x1, y1 = transform_bounds('EPSG:4326', 'EPSG:3035', *VIEW, densify_pts=50)
    x0, y1 = math.floor(x0 / RES) * RES, math.ceil(y1 / RES) * RES
    w, h = int(math.ceil((x1 - x0) / RES)), int(math.ceil((y1 - y0) / RES))
    return Affine(RES, 0, x0, 0, -RES, y1), h, w


def jobs(src):
    out = []
    for f in sorted(glob.glob(src['files'])):
        with rasterio.open(f) as d:
            h, w = d.height, d.width
        for r in range(0, h, CHUNK):
            for c in range(0, w, CHUNK):
                out.append((src['key'], f, r, c, min(CHUNK, h - r), min(CHUNK, w - c), src['nodata']))
    return out


def scan(job):
    key, f, r, c, h, w, nodata = job
    T, GH, GW = grid()
    with rasterio.open(f) as d:
        step = max(1, int(RES / 4 / abs(d.res[0])))             # four samples a target cell each way
        oh, ow = max(1, h // step), max(1, w // step)
        win = Window(c, r, w, h)
        if nodata is not None:
            v = d.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.nearest)
            m = (v != nodata).astype(np.uint8)
        else:
            m = (d.read_masks(1, window=win, out_shape=(oh, ow), resampling=Resampling.nearest) > 0).astype(np.uint8)
        if not m.any():
            return key, None
        wt = d.window_transform(win) * Affine.scale(w / ow, h / oh)
        crs = d.crs
    b = transform_bounds(crs, 'EPSG:3035', wt.c, wt.f + wt.e * oh, wt.c + wt.a * ow, wt.f, densify_pts=21)
    c0 = max(0, int((b[0] - T.c) // RES) - 1)
    c1 = min(GW, int((b[2] - T.c) // RES) + 2)
    r0 = max(0, int((T.f - b[3]) // RES) - 1)
    r1 = min(GH, int((T.f - b[1]) // RES) + 2)
    if c1 <= c0 or r1 <= r0:
        return key, None
    dst = np.zeros((r1 - r0, c1 - c0), np.uint8)
    reproject(m, dst, src_transform=wt, src_crs=crs, dst_transform=T * Affine.translation(c0, r0),
              dst_crs='EPSG:3035', resampling=Resampling.max, src_nodata=None, dst_nodata=None)
    return key, (r0, c0, dst)


def outline(mask, T, simplify=RES):
    from shapely.geometry import shape, mapping
    from shapely.ops import transform, unary_union
    from pyproj import Transformer
    polys = [shape(g) for g, v in shapes(mask.astype(np.uint8), mask=mask.astype(bool), transform=T) if v]
    if not polys:
        return None, 0.0
    u = unary_union(polys)
    km2 = u.area / 1e6
    u = u.simplify(simplify, preserve_topology=True).buffer(0)
    to = Transformer.from_crs('EPSG:3035', 'EPSG:4326', always_xy=True).transform
    g = transform(to, u)
    return json.loads(json.dumps(mapping(g), default=float), parse_float=lambda s: round(float(s), 4)), km2


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    T, GH, GW = grid()
    path = os.path.join(out, 'masks.npz')
    masks = dict(np.load(path)) if os.path.exists(path) else {}
    only = os.environ.get('ONLY')
    todo = [s for s in SOURCES if (only and s['key'] in only.split(',')) or (not only and s['key'] not in masks)]
    with ProcessPoolExecutor(int(os.environ.get('JOBS', 10))) as pool:
        for s in todo:                          # one source at a time, each saved when done: a rerun resumes
            js = jobs(s)
            m = np.zeros((GH, GW), np.uint8)
            for key, res in tqdm(pool.map(scan, js, chunksize=8), total=len(js), unit='part', mininterval=10,
                                 desc=s['key']):
                if res is not None:
                    r0, c0, a = res
                    m[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] |= a
            masks[s['key']] = m
            np.savez_compressed(path + '.part.npz', **masks)
            os.replace(path + '.part.npz', path)

    # land in the view, from Natural Earth (countries), and what of it no lidar covers inside the box
    from shapely.geometry import shape, box, mapping
    from shapely.ops import transform, unary_union
    from pyproj import Transformer
    from rasterio.features import rasterize
    ne = json.load(open(f'{D}/naturalearth/ne_10m_admin_0_countries.geojson'))
    view = box(*VIEW)
    countries = []
    for f in ne['features']:
        g = shape(f['geometry'])
        if g.intersects(view):
            countries.append((f['properties']['ADM0_A3'], f['properties']['NAME_EN'], g.intersection(view)))
    to3035 = Transformer.from_crs('EPSG:4326', 'EPSG:3035', always_xy=True).transform
    boxp = transform(to3035, box(*BOX).segmentize(0.05))
    land = rasterize([(transform(to3035, g), 1) for _, _, g in countries], out_shape=(GH, GW), transform=T,
                     dtype=np.uint8)
    inbox = rasterize([(boxp, 1)], out_shape=(GH, GW), transform=T, dtype=np.uint8)
    lidar = np.zeros((GH, GW), bool)
    srcs = []
    for s in SOURCES:
        m = masks.get(s['key'])
        if m is None:
            continue
        if s.get('lidar', True):
            lidar |= m > 0
        files = sorted(glob.glob(s['files']))
        geom, km2 = outline(m > 0, T)
        inb = float(((m > 0) & (inbox > 0)).sum()) * RES * RES / 1e6
        srcs.append({k: v for k, v in s.items() if k not in ('files', 'nodata')} |
                    dict(km2=round(km2), km2_box=round(inb), files=len(files),
                         bytes=sum(os.path.getsize(f) for f in files), geometry=geom))
        print(f'{s["key"]:14s} {len(files):6d} files {km2:9.0f} km2, {inb:9.0f} in the box', flush=True)
    gap = (land > 0) & (inbox > 0) & ~lidar
    ggeom, gkm2 = outline(gap, T, simplify=400)
    # each country's land in the box without lidar, km2
    per = {}
    for code, name, g in countries:
        c = rasterize([(transform(to3035, g), 1)], out_shape=(GH, GW), transform=T, dtype=np.uint8) > 0
        a, b = float((c & (inbox > 0)).sum()), float((c & gap).sum())
        if a:
            per[code] = dict(name=name, km2_box=round(a * RES * RES / 1e6), km2_gap=round(b * RES * RES / 1e6))
    simp = lambda g, t: json.loads(json.dumps(mapping(g.simplify(t, preserve_topology=True))),
                                   parse_float=lambda s: round(float(s), 4))
    states = []
    ne1 = json.load(open(f'{D}/naturalearth/ne_10m_admin_1_states_provinces.geojson'))
    for f in ne1['features']:
        p = f['properties']
        if p.get('adm0_a3') in ('DEU', 'AUT', 'ITA', 'CHE', 'POL') and shape(f['geometry']).intersects(view):
            states.append(dict(name=p['name'], country=p['adm0_a3'],
                               geometry=simp(shape(f['geometry']).intersection(view), 0.01)))
    json.dump(dict(box=BOX, view=VIEW, res=RES, sources=srcs,
                   gap=dict(km2=round(gkm2), geometry=ggeom, countries=per),
                   countries=[dict(code=c, name=n, geometry=simp(g, 0.005)) for c, n, g in countries],
                   states=states),
              open(os.path.join(out, 'coverage.json'), 'w'), separators=(',', ':'))
    print(f'gap in the box: {gkm2:.0f} km2 of land without lidar', flush=True)


if __name__ == '__main__':
    main()
