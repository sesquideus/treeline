"""
Prominence, key cols and TreeLine's parents at the full 1″ of Copernicus GLO-30, for an area
far too large to flood in memory (all of South America is 43 billion cells), in bounded memory.

    uv run --group lidar python lidar/tiled.py mosaic  WORK --region S,N,W,E --tiles DIR
    uv run --group lidar python lidar/tiled.py burn    WORK --heights HEIGHTS.json
    uv run --group lidar python lidar/tiled.py flood   WORK [--jobs 12]
    uv run --group lidar python lidar/tiled.py walk    WORK --min-prominence 600 --out P.json
    uv run --group lidar python lidar/tiled.py check   WORK --block S,N,W,E     # against the in-memory flood

The grid. One 1″ mosaic of the area as a raw float32 array on disk (`mosaic.f32`, row 0 along
the northern edge), tiles south of 50° — 1.5″ east–west in GLO — repeated out to 1″. Cells at
or below 0 m are sea, and outside every tile is sea too: a summit whose key col would be the
sea is an island. Known summit heights are burned in before anything is flooded (`burn`): a
published height (andes.org.uk) or the database's own wins over the surface model, which reads
sharp and glaciated summits low — Illimani 6438 m but 6404 in GLO, below Ancohuma, which turned
the whole massif around it inside out.

The order. Every cell is ranked by its height, and a tie by its position (the northern, then
the western first): a strict order, so a flat lake or salar is no plateau to search but a slope
like any other, and the tiled flood and the in-memory one see exactly the same terrain.

The flood. Each 1° tile is flooded on its own, highest cell first. A summit whose surroundings
reach the tile's edge before they reach higher ground may yet find a higher col beyond it; one
whose do not has its key col settled for good, and is written down (its death: summit, key col,
the summit of the higher ground it joins) and forgotten. What touches the edge is kept as a join
tree — every edge cell, the summits behind them and the saddles joining them — small next to the
tile. Then 2×2 blocks are flooded as graphs of their four tiles' trees joined across the seams,
then 2×2 blocks of those, up to one block covering the area: the memory a level needs is that of
its blocks' edges, not their area. Every local maximum of the grid dies once, somewhere, and the
deaths, a few hundred million of them, go to disk.

The walk. TreeLine's parent, as `parents.py` reads it: across the key col to the first summit up
the ridge on the far side (steepest ascent, in the same strict order), then across that one's key
col, and on, to the first summit more prominent than the one the walk started from. Which side of
a col a climb has come up on is read off the deaths themselves — a summit belongs to the higher
ground at a col's level if following the summits it died into, while their cols are above that
level, ends at that ground's summit — so the flood itself never climbs.
"""
import argparse
import glob
import json
import math
import os
import re
import sys
import time
import multiprocessing
from multiprocessing import Pool

import numpy as np
from numba import njit

from data import path

ARC = 3600                  # cells per degree


# ------------------------------------------------------------------ the grid

class Grid:
    """The mosaic on disk and where it lies: rows from `north` down, columns from `west` east."""

    def __init__(self, work):
        meta = json.load(open(os.path.join(work, 'grid.json')))
        self.south, self.north, self.west, self.east = meta['region']
        self.rows = (self.north - self.south) * ARC
        self.cols = (self.east - self.west) * ARC
        self.path = os.path.join(work, 'mosaic.f32')

    def array(self, mode='r'):
        return np.memmap(self.path, dtype=np.float32, mode=mode, shape=(self.rows, self.cols))

    def cell(self, lat, lon):
        return int((self.north - lat) * ARC), int((lon - self.west) * ARC)

    def latlon(self, r, c):
        return round(self.north - (r + 0.5) / ARC, 6), round(self.west + (c + 0.5) / ARC, 6)


def mosaic(work, region, tiles):
    s, n, w, e = region
    os.makedirs(work, exist_ok=True)
    json.dump(dict(region=region), open(os.path.join(work, 'grid.json'), 'w'))
    g = Grid(work)
    with open(g.path, 'wb') as f:          # sparse: what no tile covers reads as 0, the sea
        f.truncate(g.rows * g.cols * 4)
    import rasterio
    z = g.array('r+')
    done = 0
    for lat in range(s, n):
        for lon in range(w, e):
            name = f'Copernicus_DSM_COG_10_{"N" if lat >= 0 else "S"}{abs(lat):02d}_00_' \
                   f'{"E" if lon >= 0 else "W"}{abs(lon):03d}_00_DEM.tif'
            path = os.path.join(tiles, name)
            if not os.path.exists(path):
                continue
            t = rasterio.open(path).read(1)
            if t.shape[1] != ARC:          # 1.5″ (or coarser) east–west: repeated out to 1″
                t = t[:, (np.arange(ARC) * t.shape[1]) // ARC]
            r0, c0 = (n - lat - 1) * ARC, (lon - w) * ARC
            z[r0:r0 + ARC, c0:c0 + ARC] = t
            done += 1
            if done % 100 == 0:
                print(f'{done} tiles', flush=True)
    z.flush()
    print(f'{done} tiles -> {g.path}')


def burn(work, heights):
    """
    Known summit heights over the surface model. `heights`: [{lat, lon, altitude}], each near
    its summit; the summit is the highest cell within 150 m, which takes the height, and nothing
    else within 150 m may stand higher.
    """
    g = Grid(work)
    z = g.array('r+')
    log = []
    for h in json.load(open(heights)):
        r, c = g.cell(h['lat'], h['lon'])
        R = 5
        win = np.array(z[r - R:r + R + 1, c - R:c + R + 1])
        if win.size == 0 or win.max() <= 0:
            continue
        i, j = np.unravel_index(np.argmax(win), win.shape)
        was = float(win[i, j])
        win = np.minimum(win, h['altitude'])
        win[i, j] = h['altitude']
        z[r - R:r + R + 1, c - R:c + R + 1] = win
        log.append(dict(name=h.get('name'), at=g.latlon(r - R + i, c - R + j), was=was, now=h['altitude']))
    z.flush()
    json.dump(log, open(os.path.join(work, 'burned.json'), 'w'), ensure_ascii=False, indent=0)
    print(f'{len(log)} summit heights burned in')


# ------------------------------------------------------------------ the flood

@njit(cache=True)
def _find(uf, a):
    while uf[a] != a:
        uf[a] = uf[uf[a]]
        a = uf[a]
    return a


@njit(cache=True)
def _above(za, ga, zb, gb):
    """Strictly higher in the order: by height, a tie by the smaller position."""
    return za > zb or (za == zb and ga < gb)


@njit(cache=True)
def flood_core(order, z, gid, edge, ptr, nbr, gw, gh):
    """
    The flood of one block, highest first. Neighbours from (`ptr`, `nbr`), or, if `gw` > 0, the
    eight around each cell of a `gw` × `gh` grid. `edge`: on the block's edge. Returns the deaths
    of summits settled here (summit, key col, the higher ground's summit; key col -1 for an
    island), and the join tree of what touches the edge: its nodes and (child, parent) edges.
    """
    n = len(z)
    uf = np.full(n, -1, np.int64)
    top = np.full(n, -1, np.int64)
    attach = np.full(n, -1, np.int64)
    bnd = np.zeros(n, np.bool_)
    node_of = np.full(n, -1, np.int64)       # cell -> tree node
    t_cell = np.empty(n // 4 + 16, np.int64)
    nt = 0
    e_child = np.empty(n // 4 + 16, np.int64)
    e_par = np.empty(n // 4 + 16, np.int64)
    ne = 0
    d_top = np.empty(n // 4 + 16, np.int64)
    d_col = np.empty(n // 4 + 16, np.int64)
    d_isle = np.empty(n // 4 + 16, np.int64)
    d_far = np.empty(n // 4 + 16, np.int64)
    nd = 0
    roots = np.empty(64, np.int64)
    vias = np.empty(64, np.int64)            # per root, the first neighbour found in it
    for k in range(len(order)):
        v = order[k]
        if z[v] <= 0:
            break                                # the sea and below: done
        m = 0
        if gw > 0:
            y = v // gw
            x = v - y * gw
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    if dy == 0 and dx == 0:
                        continue
                    yy = y + dy
                    xx = x + dx
                    if yy < 0 or yy >= gh or xx < 0 or xx >= gw:
                        continue
                    u = yy * gw + xx
                    if uf[u] < 0:
                        continue
                    r = _find(uf, u)
                    dup = False
                    for j in range(m):
                        if roots[j] == r:
                            dup = True
                    if not dup:
                        roots[m] = r
                        vias[m] = u
                        m += 1
        else:
            for q in range(ptr[v], ptr[v + 1]):
                u = nbr[q]
                if uf[u] < 0:
                    continue
                r = _find(uf, u)
                dup = False
                for j in range(m):
                    if roots[j] == r:
                        dup = True
                if not dup:
                    if m == len(roots):
                        bigger = np.empty(2 * m, np.int64)
                        bigger[:m] = roots
                        roots = bigger
                        bigger = np.empty(2 * m, np.int64)
                        bigger[:m] = vias
                        vias = bigger
                    roots[m] = r
                    vias[m] = u
                    m += 1
        if nt + m + 2 >= len(t_cell):
            t_cell = np.concatenate((t_cell, np.empty(len(t_cell), np.int64)))
        if ne + m + 2 >= len(e_child):
            e_child = np.concatenate((e_child, np.empty(len(e_child), np.int64)))
            e_par = np.concatenate((e_par, np.empty(len(e_par), np.int64)))
        if nd + m + 2 >= len(d_top):
            d_top = np.concatenate((d_top, np.empty(len(d_top), np.int64)))
            d_col = np.concatenate((d_col, np.empty(len(d_col), np.int64)))
            d_isle = np.concatenate((d_isle, np.empty(len(d_isle), np.int64)))
            d_far = np.concatenate((d_far, np.empty(len(d_far), np.int64)))
        if m == 0:                               # a summit
            uf[v] = v
            top[v] = v
            t_cell[nt] = v
            node_of[v] = nt
            attach[v] = nt
            nt += 1
            bnd[v] = edge[v]
            continue
        bj = 0
        for j in range(1, m):
            a = top[roots[j]]
            b = top[roots[bj]]
            if _above(z[a], gid[a], z[b], gid[b]):
                bj = j
        best = roots[bj]
        # Up the ridge on the far side, as parents.flood_divide climbs, while the ground is in
        # memory: on a grid, from the first neighbour found in the higher ground. (On a graph of
        # trees there is no ground to climb; the walk climbs on the mosaic for those few.)
        far = -1
        if gw > 0:
            far = _climb_grid(z, gid, gw, gh, vias[bj])
        kept = 0
        newb = edge[v]
        for j in range(m):
            r = roots[j]
            if r == best or bnd[r]:
                kept += 1
                newb = newb or bnd[r]
            else:                                # enclosed, and lower: its key col is v
                d_top[nd] = top[r]
                d_col[nd] = v
                d_isle[nd] = top[best]
                d_far[nd] = far
                nd += 1
        if kept >= 2 or edge[v]:
            t_cell[nt] = v
            node_of[v] = nt
            for j in range(m):
                r = roots[j]
                if r == best or bnd[r]:
                    e_child[ne] = attach[r]
                    e_par[ne] = nt
                    ne += 1
            att = nt
            nt += 1
        else:
            att = attach[best]
        for j in range(m):
            uf[roots[j]] = best
        uf[v] = best
        attach[best] = att
        bnd[best] = newb
    # what is left: islands, or reaching the edge
    keep = np.zeros(nt, np.bool_)
    for v in range(n):
        if uf[v] == v:
            if bnd[v]:
                keep[attach[v]] = True
            else:
                if nd >= len(d_top):
                    d_top = np.concatenate((d_top, np.empty(len(d_top), np.int64)))
                    d_col = np.concatenate((d_col, np.empty(len(d_col), np.int64)))
                    d_isle = np.concatenate((d_isle, np.empty(len(d_isle), np.int64)))
                    d_far = np.concatenate((d_far, np.empty(len(d_far), np.int64)))
                d_top[nd] = top[v]
                d_col[nd] = -1
                d_isle[nd] = -1
                d_far[nd] = -1
                nd += 1
    # the kept trees: down from each kept root to every node above it
    cnt = np.zeros(nt + 1, np.int64)
    for i in range(ne):
        cnt[e_par[i] + 1] += 1
    start = np.cumsum(cnt)
    fill = start[:-1].copy()
    kids = np.empty(ne, np.int64)
    for i in range(ne):
        kids[fill[e_par[i]]] = e_child[i]
        fill[e_par[i]] += 1
    stack = np.empty(nt + 1, np.int64)
    sp = 0
    for i in range(nt):
        if keep[i]:
            stack[sp] = i
            sp += 1
    while sp > 0:
        sp -= 1
        a = stack[sp]
        for q in range(start[a], start[a + 1]):
            b = kids[q]
            if not keep[b]:
                keep[b] = True
                stack[sp] = b
                sp += 1
    newid = np.full(nt, -1, np.int64)
    k = 0
    for i in range(nt):
        if keep[i]:
            newid[i] = k
            k += 1
    out_cell = np.empty(k, np.int64)
    for i in range(nt):
        if keep[i]:
            out_cell[newid[i]] = t_cell[i]
    oc = np.empty(ne, np.int64)
    op = np.empty(ne, np.int64)
    m2 = 0
    for i in range(ne):
        if keep[e_par[i]]:
            oc[m2] = newid[e_child[i]]
            op[m2] = newid[e_par[i]]
            m2 += 1
    return d_top[:nd], d_col[:nd], d_isle[:nd], d_far[:nd], out_cell, oc[:m2], op[:m2]


@njit(cache=True)
def _climb_grid(z, gid, gw, gh, p):
    """Steepest ascent in the strict order within the block, to its highest cell around."""
    while True:
        y = p // gw
        x = p - y * gw
        best = p
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= gh or xx < 0 or xx >= gw:
                    continue
                q = yy * gw + xx
                if _above(z[q], gid[q], z[best], gid[best]):
                    best = q
        if best == p:
            return p
        p = best


def block_bounds(g, level, bi, bj):
    """Cell rows and columns of block (bi, bj) at `level` (2^level tiles a side), clipped."""
    size = ARC << level
    r0, c0 = bi * size, bj * size
    return r0, min(r0 + size, g.rows), c0, min(c0 + size, g.cols)


def save(work, level, bi, bj, deaths, nodes, edges):
    """`deaths`: summit, key col, higher ground's summit (cells), the summit's and col's heights."""
    d = os.path.join(work, f'L{level}')
    os.makedirs(d, exist_ok=True)
    np.savez(os.path.join(d, f'{bi}_{bj}.npz'), d_top=deaths[0], d_col=deaths[1], d_isle=deaths[2],
             d_hz=deaths[3], d_cz=deaths[4], d_far=deaths[5],
             n_gid=nodes[0], n_z=nodes[1], e_child=edges[0], e_par=edges[1])


NO_DEATHS = [np.empty(0, np.int64)] * 3 + [np.empty(0, np.float32)] * 2 + [np.empty(0, np.int64)]


def deaths_of(gid, z, d_top, d_col, d_isle, d_far):
    """Local indices to cells, with the heights the walk reads and the climb's end (-1: none)."""
    m = lambda a: np.where(a >= 0, gid[np.maximum(a, 0)], -1)
    return (m(d_top), m(d_col), m(d_isle), z[d_top].astype(np.float32),
            np.where(d_col >= 0, z[np.maximum(d_col, 0)], np.nan).astype(np.float32), m(d_far))


BAND = None        # the tile row being flooded, read once and handed to the workers by fork


def flood_tile(args):
    work, bi, bj, top_level = args
    g = Grid(work)
    r0, r1, c0, c1 = block_bounds(g, 0, bi, bj)
    # From the band in memory: read tile by tile, 3600 short runs scattered across a file of
    # 175 GB, a spinning disk spent half an hour on a row of 48 tiles.
    z = (np.ascontiguousarray(BAND[:, c0:c1]) if BAND is not None
         else np.array(g.array()[r0:r1, c0:c1])).ravel()
    h, w = r1 - r0, c1 - c0
    if not (z > 0).any():
        save(work, 0, bi, bj, NO_DEATHS, (np.empty(0, np.int64), np.empty(0, np.float32)),
             (np.empty(0, np.int64), np.empty(0, np.int64)))
        return 0
    rr, cc = np.divmod(np.arange(h * w, dtype=np.int64), w)
    gid = (rr + r0) * g.cols + (cc + c0)
    edge = (rr == 0) | (rr == h - 1) | (cc == 0) | (cc == w - 1)
    if top_level:
        edge[:] = False
    order = np.argsort(-z, kind='stable')        # ties: the earlier cell first, as gid
    d_top, d_col, d_isle, d_far, cell, ec, ep = flood_core(order, z, gid, edge, np.empty(1, np.int64),
                                                           np.empty(1, np.int64), w, h)
    save(work, 0, bi, bj, deaths_of(gid, z, d_top, d_col, d_isle, d_far), (gid[cell], z[cell]), (ec, ep))
    return len(d_top)


def merge_block(args):
    work, level, bi, bj, top_level = args
    g = Grid(work)
    gids, zs, ecs, eps, part = [], [], [], [], []
    off = 0
    for di in (0, 1):
        for dj in (0, 1):
            p = os.path.join(work, f'L{level - 1}', f'{2 * bi + di}_{2 * bj + dj}.npz')
            if not os.path.exists(p):
                continue
            f = np.load(p)
            gids.append(f['n_gid'])
            zs.append(f['n_z'])
            ecs.append(f['e_child'] + off)
            eps.append(f['e_par'] + off)
            part.append(np.full(len(f['n_gid']), len(part), np.int8))
            off += len(f['n_gid'])
    gid = np.concatenate(gids) if gids else np.empty(0, np.int64)
    z = np.concatenate(zs) if zs else np.empty(0, np.float32)
    part = np.concatenate(part) if part else np.empty(0, np.int8)
    a = np.concatenate(ecs) if ecs else np.empty(0, np.int64)
    b = np.concatenate(eps) if eps else np.empty(0, np.int64)
    if len(gid) == 0:
        save(work, level, bi, bj, NO_DEATHS, (gid, z), (a, b))
        return 0
    # the seams: cells side by side (or corner to corner) in different quarters
    srt = np.argsort(gid)
    sg = gid[srt]
    r, c = np.divmod(gid, g.cols)
    sa, sb = [], []
    for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):     # each pair once
        rr, cc = r + dy, c + dx
        ok = (rr < g.rows) & (cc >= 0) & (cc < g.cols)
        want = rr * g.cols + cc
        k = np.searchsorted(sg, want)
        k = np.minimum(k, len(sg) - 1)
        hit = ok & (sg[k] == want)
        i = np.flatnonzero(hit)
        j = srt[k[hit]]
        cross = part[i] != part[j]
        sa.append(i[cross])
        sb.append(j[cross])
    u = np.concatenate([a] + sa)
    v = np.concatenate([b] + sb)
    n = len(gid)
    deg = np.bincount(np.concatenate((u, v)), minlength=n)
    ptr = np.zeros(n + 1, np.int64)
    ptr[1:] = np.cumsum(deg)
    nbr = np.empty(ptr[-1], np.int64)
    fill = ptr[:-1].copy()
    _fill_csr(u, v, fill, nbr)
    R0, R1, C0, C1 = block_bounds(g, level, bi, bj)
    edge = (r == R0) | (r == R1 - 1) | (c == C0) | (c == C1 - 1)
    if top_level:
        edge[:] = False
    order = np.lexsort((gid, -z))
    d_top, d_col, d_isle, d_far, cell, ec, ep = flood_core(order, z, gid, edge, ptr, nbr, 0, 0)
    save(work, level, bi, bj, deaths_of(gid, z, d_top, d_col, d_isle, d_far), (gid[cell], z[cell]), (ec, ep))
    return len(d_top)


@njit(cache=True)
def _fill_csr(u, v, fill, nbr):
    for i in range(len(u)):
        nbr[fill[u[i]]] = v[i]
        fill[u[i]] += 1
        nbr[fill[v[i]]] = u[i]
        fill[v[i]] += 1


def flood(work, jobs):
    g = Grid(work)
    ti, tj = g.rows // ARC, g.cols // ARC
    levels = max(1, math.ceil(math.log2(max(ti, tj)))) if max(ti, tj) > 1 else 0
    t0 = time.time()
    global BAND
    z = g.array()
    n = 0
    for i in range(ti):
        todo = [j for j in range(tj) if not os.path.exists(os.path.join(work, 'L0', f'{i}_{j}.npz'))]
        if not todo:
            continue
        BAND = np.array(z[i * ARC:(i + 1) * ARC])         # one long read, then forked to the workers
        with Pool(jobs) as pool:
            n += sum(pool.map(flood_tile, [(work, i, j, levels == 0) for j in todo], chunksize=1))
        BAND = None
        print(f'tile row {i}: {len(todo)} tiles, {time.time() - t0:.0f}s', flush=True)
    print(f'tiles: {ti * tj}, {n} deaths (this run), {time.time() - t0:.0f}s', flush=True)
    with Pool(jobs) as pool:
        for level in range(1, levels + 1):
            bi, bj = -(-ti // (1 << level)), -(-tj // (1 << level))
            n = sum(pool.map(merge_block, [(work, level, i, j, level == levels)
                                           for i in range(bi) for j in range(bj)], chunksize=1))
            print(f'level {level}: {bi * bj} blocks, {n} deaths, {time.time() - t0:.0f}s', flush=True)
    json.dump(dict(levels=levels), open(os.path.join(work, 'flood.json'), 'w'))


# ------------------------------------------------------------------ the walk

def load_deaths(work):
    levels = json.load(open(os.path.join(work, 'flood.json')))['levels']
    parts = {k: [] for k in ('d_top', 'd_col', 'd_isle', 'd_hz', 'd_cz', 'd_far')}
    for level in range(levels + 1):
        for p in glob.glob(os.path.join(work, f'L{level}', '*.npz')):
            f = np.load(p)
            for k in parts:
                parts[k].append(f[k])
    # One field at a time, its pieces let go of as it is sorted: hundreds of millions of deaths
    # held twice over would not fit.
    top = np.concatenate(parts.pop('d_top'))
    srt = np.argsort(top)
    top = top[srt]
    out = [top]
    for k in ('d_col', 'd_isle', 'd_hz', 'd_cz', 'd_far'):
        whole = np.concatenate(parts.pop(k))
        out.append(whole[srt])
        del whole
    return tuple(out)


@njit(cache=True)
def _death(tops, gidv):
    i = np.searchsorted(tops, gidv)
    if i < len(tops) and tops[i] == gidv:
        return i
    return -1


@njit(cache=True)
def _climb(z, rows, cols, p):
    """Steepest ascent in the strict order, to a local maximum."""
    while True:
        r = p // cols
        c = p - r * cols
        best = p
        bz = z[r, c]
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                rr = r + dy
                cc = c + dx
                if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                    continue
                q = rr * cols + cc
                if _above(z[rr, cc], q, bz, best):
                    best = q
                    bz = z[rr, cc]
        if best == p:
            return p
        p = best


@njit(cache=True)
def _rep(z, rows, cols, tops, col, isle, cz, e, lz, lg):
    """
    The summit of the ground `e` stands on, at the level of the col (lz, lg). A tile's flood
    names the highest cell it holds of some ground, which can lie on its edge with higher
    ground just across the seam: no summit at all. From one of those the climb goes on up to
    the real one, on the same ground.
    """
    for _ in range(1000000):
        i = _death(tops, e)
        if i < 0:
            up = _climb(z, rows, cols, e)
            if up == e:
                return e
            e = up
            continue
        if col[i] < 0:
            return e
        if _above(cz[i], col[i], lz, lg):
            e = isle[i]
        else:
            return e
    return e


@njit(cache=True)
def _far(z, rows, cols, tops, col, isle, cz, farc, m):
    """Across `m`'s key col, up the ridge on the far side: the first summit there, or -1."""
    i = _death(tops, m)
    if i < 0 or col[i] < 0:
        return -1
    e = farc[i]
    if e >= 0:
        # climbed in the tile's flood; where that climb ran into the tile's edge, on up here
        for _ in range(1000000):
            if _death(tops, e) >= 0:
                return e
            up = _climb(z, rows, cols, e)
            if up == e:
                return e
            e = up
        return e
    c = col[i]
    lz = cz[i]
    target = _rep(z, rows, cols, tops, col, isle, cz, isle[i], lz, c)
    r = c // cols
    c0 = c - r * cols
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            if dy == 0 and dx == 0:
                continue
            rr = r + dy
            cc = c0 + dx
            if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                continue
            q = rr * cols + cc
            if not _above(z[rr, cc], q, lz, c):
                continue
            e = _climb(z, rows, cols, q)
            if _rep(z, rows, cols, tops, col, isle, cz, e, lz, c) == target:
                return e
    return target


@njit(cache=True)
def _prom(tops, col, hz, cz, m):
    i = _death(tops, m)
    if i < 0:
        return -np.inf
    if col[i] < 0:
        return hz[i]
    return hz[i] - cz[i]


@njit(cache=True)
def walk_parents(z, rows, cols, tops, col, isle, hz, cz, farc, starts):
    out = np.full(len(starts), -1, np.int64)
    for k in range(len(starts)):
        s = starts[k]
        ps = _prom(tops, col, hz, cz, s)
        p = _far(z, rows, cols, tops, col, isle, cz, farc, s)
        for _ in range(1000000):
            if p < 0 or _prom(tops, col, hz, cz, p) > ps:
                break
            p = _far(z, rows, cols, tops, col, isle, cz, farc, p)
        out[k] = p
    return out


@njit(cache=True)
def walk_line_parents(z, rows, cols, tops, col, isle, hz, cz, farc, starts, cutoff):
    """
    Each start's line parent (Helman): across its key col and on across each top passed, to the
    first top strictly higher than the start (in the strict order: by height, a tie by the
    smaller position) whose prominence is at least `cutoff`. A top below the cutoff is passed
    like a lesser top in `walk_parents`, which is what resolves it to the summit of at least
    the cutoff whose ground it lies in. -1 for none (an island high point).
    """
    out = np.full(len(starts), -1, np.int64)
    for k in range(len(starts)):
        s = starts[k]
        i = _death(tops, s)
        hs = hz[i]
        p = _far(z, rows, cols, tops, col, isle, cz, farc, s)
        for _ in range(100000000):
            if p < 0:
                break
            j = _death(tops, p)
            if j >= 0 and _above(hz[j], p, hs, s) and _prom(tops, col, hz, cz, p) >= cutoff:
                break
            p = _far(z, rows, cols, tops, col, isle, cz, farc, p)
        out[k] = p
    return out


def walk(work, min_prominence, out):
    g = Grid(work)
    z = g.array()
    tops, col, isle, hz, cz, farc = load_deaths(work)
    print(f'{len(tops)} summits, {(farc >= 0).sum()} climbed in the flood', flush=True)
    prom = np.where(col >= 0, hz - cz, hz)
    sel = np.flatnonzero(prom >= min_prominence)
    t0 = time.time()
    parents = walk_parents(z, g.rows, g.cols, tops, col, isle, hz, cz, farc, tops[sel])
    print(f'{len(sel)} walks, {time.time() - t0:.0f}s', flush=True)
    at = lambda cell: list(g.latlon(*divmod(int(cell), g.cols)))
    res = []
    for k, i in enumerate(sel):
        rec = dict(zip(('lat', 'lon'), at(tops[i])), h=round(float(hz[i]), 2), prom=round(float(prom[i]), 2),
                   island=bool(col[i] < 0))
        if col[i] >= 0:
            rr, cc = divmod(int(col[i]), g.cols)
            rec.update(col=at(col[i]), col_h=round(float(cz[i]), 2), higher=at(isle[i]),
                       edge=bool(rr == 0 or rr == g.rows - 1 or cc == 0 or cc == g.cols - 1))
        if parents[k] >= 0:
            rec['parent'] = at(parents[k])
        res.append(rec)
    json.dump(res, open(out, 'w'), indent=0)
    print(f'{len(res)} summits with P >= {min_prominence} -> {out}')


# ------------------------------------------------------------------ the check

def check(work, block, jobs, tiles, reference=False):
    """
    A block small enough to flood in memory, both ways: tiled, as its own area, and in one
    piece with `parents.flood_divide` in the same order. Every death must agree.
    """
    s, n, w, e = block
    sub = os.path.join(work, f'check_{s}_{n}_{w}_{e}')
    if not os.path.exists(os.path.join(sub, 'mosaic.f32')):
        mosaic(sub, [s, n, w, e], tiles)
    g = Grid(sub)
    flood(sub, jobs)
    tops, col, isle, hz, cz, farc = load_deaths(sub)
    mine = dict(zip(tops.tolist(), col.tolist()))
    zz = np.array(g.array()).ravel()
    rr, cc = np.divmod(np.arange(len(zz), dtype=np.int64), g.cols)
    gid = rr * g.cols + cc
    # in one piece: the same flood with no tiles at all
    order = np.argsort(-zz, kind='stable')
    d_top, d_col, *_ = flood_core(order, zz, gid, np.zeros(len(zz), np.bool_), np.empty(1, np.int64),
                                  np.empty(1, np.int64), g.cols, g.rows)
    compare('in one piece', dict(zip(d_top.tolist(), d_col.tolist())), mine, g)
    if reference:
        # and parents.flood_divide, the flood the rest of the lidar tooling uses
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from parents import flood_divide
        z32 = zz.astype(np.float32)
        z32[z32 <= 0] = -np.inf
        prom, isl, d_col, d_peak, *_ = flood_divide(z32, order.astype(np.int32), g.cols, g.rows)
        compare('flood_divide', dict(zip(d_peak.tolist(), d_col.tolist())), mine, g)


def compare(what, ref, mine, g):
    both = set(ref) & set(mine)
    same = sum(1 for k in both if ref[k] == mine[k])
    print(f'{what}: {len(ref)} deaths, tiled {len(mine)}; {len(both)} summits in both, {same} with the '
          f'same key col; {len(set(ref) - set(mine))} only {what}, {len(set(mine) - set(ref))} only tiled')
    for k in [k for k in both if ref[k] != mine[k]][:5]:
        print('  differs:', g.latlon(*divmod(k, g.cols)), ref[k], mine[k])


def main():
    multiprocessing.set_start_method('fork')        # the band reaches the workers without a copy
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('step', choices=['mosaic', 'burn', 'flood', 'walk', 'check'])
    ap.add_argument('work')
    ap.add_argument('--region')
    ap.add_argument('--tiles', default=path('copernicus'))
    ap.add_argument('--heights')
    ap.add_argument('--jobs', type=int, default=12)
    ap.add_argument('--min-prominence', type=float, default=600)
    ap.add_argument('--out')
    ap.add_argument('--block')
    ap.add_argument('--reference', action='store_true', help='check: also against parents.flood_divide (slow on flats)')
    a = ap.parse_args()
    nums = lambda s: [int(v) for v in s.split(',')]
    if a.step == 'mosaic':
        mosaic(a.work, nums(a.region), a.tiles)
    elif a.step == 'burn':
        burn(a.work, a.heights)
    elif a.step == 'flood':
        flood(a.work, a.jobs)
    elif a.step == 'walk':
        walk(a.work, a.min_prominence, a.out)
    else:
        check(a.work, nums(a.block), a.jobs, a.tiles, a.reference)


if __name__ == '__main__':
    main()
