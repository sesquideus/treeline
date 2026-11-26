"""
Where each terrain source has data, from the tile index (`tileindex.py`) alone: every row's coarse
valid-cell grid is laid onto one grid of RES m cells in EPSG:3035 over the view, then outlined as GeoJSON
in lon/lat for the coverage map. No tile is read, so the map takes minutes; it is as good as the index
is current (rerun `tileindex.py` first). Outlines are good to about RES m.

    cd lidar && uv run --project .. --group lidar python coverage_map.py OUT_DIR [KEY ...]

OUT_DIR/coverage.json: {box, view, res, crs, unit, frame, box_geometry, sources: [{key, label, ..., km2, km2_box,
files, bytes, geometry}], gap: {km2, geometry, countries}, countries, states}; every geometry `encode`d in
EPSG:3035 (see there). The page is OUT_DIR/index.html.
"""
import json
import math
import os
import sqlite3
import sys
import zlib
from collections import defaultdict

import numpy as np
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine
from rasterio.warp import transform_bounds

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources  # noqa: E402
import tileindex  # noqa: E402
from data import path  # noqa: E402

BOX = (9.5, 46.3, 24.2, 52.0)                    # lon0, lat0, lon1, lat1: the joint run
VIEW = (5.8, 45.6, 24.9, 55.2)                   # what the map shows
RES = 200.0


def grid():
    x0, y0, x1, y1 = transform_bounds('EPSG:4326', 'EPSG:3035', *VIEW, densify_pts=50)
    x0, y1 = math.floor(x0 / RES) * RES, math.ceil(y1 / RES) * RES
    w, h = int(math.ceil((x1 - x0) / RES)), int(math.ceil((y1 - y0) / RES))
    return Affine(RES, 0, x0, 0, -RES, y1), h, w


def mask(key, T, H, W):
    """ The source's covered cells on the map grid, from its index rows' coarse valid counts. """
    from pyproj import Transformer
    m = np.zeros((H, W), bool)
    db = tileindex.db_path(key)
    if not os.path.exists(db):
        return m, 0, 0
    con = sqlite3.connect(db)
    files = {fid: (epsg, wkt) for fid, epsg, wkt in con.execute('SELECT id, epsg, crs_wkt FROM files')}
    nfiles, nbytes = con.execute('SELECT count(*), coalesce(sum(size), 0) FROM files').fetchone()
    pts = defaultdict(lambda: ([], []))
    to = {}
    q = 'SELECT file_id, height, width, x0, y0, x1, y1, coarse_n FROM rows WHERE n_valid > 0'
    for fid, h, w, x0, y0, x1, y1, blob in con.execute(q):
        n = np.frombuffer(zlib.decompress(blob), np.uint16).reshape(tileindex.COARSE, tileindex.COARSE)
        re, ce = tileindex._edges(h), tileindex._edges(w)
        fy = (y1 - y0) / h
        fx = (x1 - x0) / w
        rr, cc = np.nonzero(n)
        # sample each coarse cell often enough that no map cell between samples is missed
        ext = max(abs(fx) * w, abs(fy) * h) / tileindex.COARSE
        crs = files[fid][0] or files[fid][1]
        if crs not in to:
            to[crs] = Transformer.from_crs(f'EPSG:{crs}' if isinstance(crs, int) else crs, 'EPSG:3035', always_xy=True)
        k = max(1, int(math.ceil(ext / (RES / 2))))
        if isinstance(crs, int) and crs == 4326:
            k = max(1, int(math.ceil(ext * 111320 / (RES / 2))))
        u = (np.arange(k) + 0.5) / k
        # every covered coarse cell's k x k sample points at once: (cells, k, k)
        ry = re[rr][:, None] + u[None, :] * (re[rr + 1] - re[rr])[:, None]        # (cells, k) rows
        cx = ce[cc][:, None] + u[None, :] * (ce[cc + 1] - ce[cc])[:, None]        # (cells, k) cols
        Y = np.broadcast_to((y1 - ry * fy)[:, :, None], (len(rr), k, k))
        X = np.broadcast_to((x0 + cx * fx)[:, None, :], (len(rr), k, k))
        pts[crs][0].append(X.ravel())
        pts[crs][1].append(Y.ravel())
    for crs, (xs, ys) in pts.items():
        X, Y = to[crs].transform(np.concatenate(xs), np.concatenate(ys))
        c = ((np.asarray(X) - T.c) // RES).astype(np.int64)
        r = ((T.f - np.asarray(Y)) // RES).astype(np.int64)
        ok = (c >= 0) & (c < W) & (r >= 0) & (r < H)
        m[r[ok], c[ok]] = True
    return m, nfiles, nbytes


def outline(m, T, simplify=RES):
    from pyproj import Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform, unary_union
    polys = [shape(g) for g, v in shapes(m.astype(np.uint8), mask=m, transform=T) if v]
    if not polys:
        return None, 0.0
    u = unary_union(polys)
    km2 = u.area / 1e6
    u = u.simplify(simplify, preserve_topology=True).buffer(0)
    return encode(u), km2


UNIT = 100.0                                     # m: the encoded coordinates' unit


def encode(g):
    """ A shapely (multi)polygon in EPSG:3035 as [[ring, ...], ...]: each ring a flat list of integer
    steps in UNIT metres, the first from (0, 0), each next one from the point before (x then y). The
    page draws in EPSG:3035 too, so it needs no projection, and the file is a few times smaller. """
    if g is None or g.is_empty:
        return None
    polys = [x for x in getattr(g, 'geoms', [g]) if x.geom_type == 'Polygon']
    for x in getattr(g, 'geoms', []):
        if x.geom_type == 'MultiPolygon':
            polys += list(x.geoms)
    out = []
    for p in polys:
        rings = []
        for r in [p.exterior, *p.interiors]:
            c = np.round(np.asarray(r.coords)[:, :2] / UNIT).astype(np.int64)
            d = np.diff(c, axis=0, prepend=np.zeros((1, 2), np.int64))
            keep = np.ones(len(d), bool)
            keep[1:] = (d[1:] != 0).any(axis=1)          # drop repeated points
            rings.append(d[keep].ravel().tolist())
        out.append(rings)
    return out


def main():
    from pyproj import Transformer
    from shapely.geometry import box, shape
    from shapely.ops import transform
    out = sys.argv[1]
    keys = sys.argv[2:] or [s['key'] for s in sources.SOURCES]
    os.makedirs(out, exist_ok=True)
    T, H, W = grid()
    ne = json.load(open(path('naturalearth', 'ne_10m_admin_0_countries.geojson')))
    view = box(*VIEW)
    countries = [(f['properties']['ADM0_A3'], f['properties']['NAME_EN'], shape(f['geometry']).intersection(view))
                 for f in ne['features'] if shape(f['geometry']).intersects(view)]
    to3035 = Transformer.from_crs('EPSG:4326', 'EPSG:3035', always_xy=True).transform
    land = rasterize([(transform(to3035, g), 1) for _, _, g in countries], out_shape=(H, W), transform=T, dtype=np.uint8) > 0
    inbox = rasterize([(transform(to3035, box(*BOX).segmentize(0.05)), 1)], out_shape=(H, W), transform=T,
                      dtype=np.uint8) > 0
    lidar = np.zeros((H, W), bool)
    srcs = []
    masks = {}
    for k in keys:
        m, nf, nb = mask(k, T, H, W)
        if not m.any():
            print(f'{k:10s} not indexed yet', flush=True)
            continue
        masks[k] = (m, nf, nb)
    # Where sources overlap, the map shows the one a run uses: the finer, then the one earlier in
    # sources.PRIORITY (newer surveys first). Each is drawn where nothing preferred covers the ground;
    # its whole extent stays in km2.
    claimed = np.zeros((H, W), bool)
    for k in sorted(masks, key=sources.priority):
        s = sources.BY_KEY[k]
        m, nf, nb = masks[k]
        used = m & ~claimed
        claimed |= m
        if s['lidar']:
            lidar |= m
        geom, km2_used = outline(used, T)
        km2 = float(m.sum()) * RES * RES / 1e6
        inb = float((used & inbox).sum()) * RES * RES / 1e6
        srcs.append(dict(key=k, label=s['label'], country=s['country'], res=f'{s["res"]:g} m', crs=s['crs'],
                         tiles=s['tiles'], heights=s['datum'], years=s['years'], licence=s['licence'],
                         lidar=s['lidar'], km2=round(km2), km2_used=round(km2_used), km2_box=round(inb),
                         files=nf, bytes=nb, geometry=geom))
        print(f'{k:10s} {nf:6d} files {km2:9.0f} km2, used {km2_used:9.0f}, {inb:9.0f} in the box', flush=True)
    gap = land & inbox & ~lidar
    ggeom, gkm2 = outline(gap, T, simplify=400)
    per = {}
    for code, name, g in countries:
        c = rasterize([(transform(to3035, g), 1)], out_shape=(H, W), transform=T, dtype=np.uint8) > 0
        a, b = float((c & inbox).sum()), float((c & gap).sum())
        if a:
            per[code] = dict(name=name, km2_box=round(a * RES * RES / 1e6), km2_gap=round(b * RES * RES / 1e6))
    simp = lambda g, t: encode(transform(to3035, g).simplify(t, preserve_topology=True))
    states = []
    ne1 = json.load(open(path('naturalearth', 'ne_10m_admin_1_states_provinces.geojson')))
    for f in ne1['features']:
        p = f['properties']
        if p.get('adm0_a3') in ('DEU', 'AUT', 'ITA', 'CHE', 'POL') and shape(f['geometry']).intersects(view):
            states.append(dict(name=p['name'], country=p['adm0_a3'], geometry=simp(shape(f['geometry']).intersection(view), 600)))
    vb = transform_bounds('EPSG:4326', 'EPSG:3035', *VIEW, densify_pts=50)
    centre = lambda g: [round(v / UNIT) for v in transform(to3035, g).representative_point().coords[0]]
    json.dump(dict(box=BOX, view=VIEW, res=RES, crs='EPSG:3035', unit=UNIT, frame=[round(v / UNIT) for v in vb],
                   box_geometry=encode(transform(to3035, box(*BOX).segmentize(0.05))),
                   sources=srcs, gap=dict(km2=round(gkm2), geometry=ggeom, countries=per),
                   countries=[dict(code=c, name=n, geometry=simp(g, 300), label=centre(g)) for c, n, g in countries],
                   states=states),
              open(os.path.join(out, 'coverage.json'), 'w'), separators=(',', ':'))
    print(f'gap in the box: {gkm2:.0f} km2 of land without lidar', flush=True)


if __name__ == '__main__':
    main()
