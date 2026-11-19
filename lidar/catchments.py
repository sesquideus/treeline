"""
Prominence, key cols and TreeLine's parents from a grid too large to hold, by catchments: a
proof of concept for 1 m lidar (Poland's NMT is 1.25 TB).

    uv run --group lidar python lidar/catchments.py window --center LAT,LON --size 10000 --tile 2500
    cd lidar && uv run --project .. --group lidar python catchments.py dmr5 [--bbox S,N,W,E] --out P10.json
    CATCHMENTS=P10.json OUT=DIR uv run python manage.py shell < lidar/catchments_db.py

`window` checks the engine on a square of DMR 5.0 against the flood in one piece; `dmr5` runs it
over all of Slovakia (about 47 minutes on 10 workers, the disk the limit) or a box of it, spikes
filtered; `catchments_db.py` turns the result into changesets against the database.

The idea. Every cell climbs to its highest neighbour (in the strict order: by height, a tie by
the smaller position), and the cells that climb to the same top are its catchment: the finest
tessellation of the ground into peaks. Every cell climbs to its top without ever going below
itself, so the highest route between two tops over the grid is exactly as high as the highest
route over the graph of catchments, where two neighbouring catchments meet at their pass (the
lower cell of the highest pair of neighbouring cells, one on each side). So the flood can run on
the graph: one linear pass over a tile builds it, and the tile is not needed again.

Tiles. A cell on a tile's edge does not know its neighbours across, so it is made a top of its
own (a node), and a pass on the edge settles nothing. Within a tile the graph is flooded from
the highest pass down. A top whose surroundings close at its key col without touching the edge
is settled for good: its key col, prominence, and the catchment across the col it climbs to
(which is what the parent walk follows). Below P10 it is merged into that catchment and
forgotten, its catchment with it. What touches the edge goes up, 2×2, with the tile's outer two
rows of cells; at a seam the edge cells see their neighbours at last, and an edge cell that
only looked like a top climbs on across it.

The parent. Partition the ground into peaks more prominent than the summit; its parent is the
peak whose part holds the catchment across its key col — the walk of `tiled.walk_parents`, from
the summit across its key col and on across each lesser top's, to the first more prominent one.
A top of equal prominence is walked past, and noted.

Everything is checked against `tiled.flood_core` in one piece: lidar/test_catchments.py.
"""
import argparse
import json
import math
import os
import resource
import sys
import time

import numpy as np
from numba import njit, types
from numba.typed import Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tiled import _above, _find  # noqa: E402

THRESHOLD = 10.0


# ------------------------------------------------------------------ the data

SPIKE = 2.0         # m above the second-highest neighbour: not ground


@njit(cache=True)
def despike(z, spike=SPIKE):
    """
    DMR 5.0 has spikes: single cells (or pairs) 5-20 m above the ground around them, many in
    straight lines along its own grid (in Vihorlat, 75 of 813 tops of P10 were such). No ground
    at 1 m stands metres above all but one of its neighbours, so such a cell is lowered to its
    second-highest neighbour; a pair, each the other's highest, goes down together. Slopes,
    ridges and real tops always have two neighbours near their own height and are untouched.
    Edge cells are left as they are: read the window a cell wider. Returns a copy and the count.
    """
    h, w = z.shape
    out = z.copy()
    n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            v = z[y, x]
            if v <= 0:
                continue
            a = -np.inf
            b = -np.inf
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    if dy == 0 and dx == 0:
                        continue
                    q = z[y + dy, x + dx]
                    if q > a:
                        b = a
                        a = q
                    elif q > b:
                        b = q
            if b > 0 and v > b + spike:
                out[y, x] = b
                n += 1
    return out, n


# ------------------------------------------------------------------ one tile

@njit(cache=True)
def label_tile(z, w, h, r0, c0, W, H):
    """
    Each cell's top, by steepest ascent within the tile. `ptr`: -2 no data (z <= 0), the cell
    itself for a top. Cells on the tile's edge (not the window's) are tops of their own.
    """
    n = w * h
    ptr = np.full(n, -2, np.int64)
    edge = np.zeros(n, np.bool_)
    for v in range(n):
        if z[v] <= 0:
            continue
        y = v // w
        x = v - y * w
        if (y == 0 and r0 > 0) or (y == h - 1 and r0 + h < H) or (x == 0 and c0 > 0) or (x == w - 1 and c0 + w < W):
            edge[v] = True
            ptr[v] = v
            continue
        best = v
        gb = (r0 + y) * W + c0 + x
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if z[q] <= 0:
                    continue
                gq = (r0 + yy) * W + c0 + xx
                if _above(z[q], gq, z[best], gb):
                    best = q
                    gb = gq
        ptr[v] = best
    lab = np.full(n, -1, np.int64)
    stack = np.empty(n, np.int64)
    for v in range(n):
        if ptr[v] == -2 or lab[v] >= 0:
            continue
        sp = 0
        p = v
        while lab[p] < 0 and ptr[p] != p:
            stack[sp] = p
            sp += 1
            p = ptr[p]
        t = lab[p] if lab[p] >= 0 else p
        lab[p] = t
        for i in range(sp):
            lab[stack[i]] = t
    return ptr, lab, edge


@njit(cache=True)
def tile_passes(z, ptr, lab, node_of, nnodes, w, h, r0, c0, W):
    """
    The passes: for every pair of neighbouring catchments the highest cell at which they meet,
    with, for each, the catchments of its higher neighbours in the scan order (node, top's gid),
    its own node if it is a top, and the top its steepest higher neighbour climbs to.
    """
    n = w * h
    best = Dict.empty(key_type=types.int64, value_type=types.int64)
    lst = np.empty(9, np.int64)
    for v in range(n):
        if ptr[v] == -2:
            continue
        y = v // w
        x = v - y * w
        gv = (r0 + y) * W + c0 + x
        m = 0
        if ptr[v] == v:
            lst[0] = node_of[v]
            m = 1
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if z[q] <= 0 or not _above(z[q], (r0 + yy) * W + c0 + xx, z[v], gv):
                    continue
                a = node_of[lab[q]]
                dup = False
                for i in range(m):
                    if lst[i] == a:
                        dup = True
                if not dup:
                    lst[m] = a
                    m += 1
        if m < 2:
            continue
        for i in range(m):
            for j in range(i + 1, m):
                a, b = lst[i], lst[j]
                if a > b:
                    a, b = b, a
                key = a * nnodes + b
                if key in best:
                    u = best[key]
                    yu = u // w
                    gu = (r0 + yu) * W + c0 + (u - yu * w)
                    if not _above(z[v], gv, z[u], gu):
                        continue
                best[key] = v
    mark = np.zeros(n, np.bool_)
    for v in best.values():
        mark[v] = True
    cells = np.flatnonzero(mark)
    k = len(cells)
    p_gid = np.empty(k, np.int64)
    p_own = np.full(k, -1, np.int64)
    p_nb = np.full((k, 8), -1, np.int64)
    p_lab = np.full((k, 8), -1, np.int64)
    p_steep = np.full(k, -1, np.int64)
    for i in range(k):
        v = cells[i]
        y = v // w
        x = v - y * w
        gv = (r0 + y) * W + c0 + x
        p_gid[i] = gv
        if ptr[v] == v:
            p_own[i] = node_of[v]
        j = -1
        sb = -1
        gsb = -1
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                j += 1
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                gq = (r0 + yy) * W + c0 + xx
                if z[q] <= 0 or not _above(z[q], gq, z[v], gv):
                    continue
                t = lab[q]
                p_nb[i, j] = node_of[t]
                p_lab[i, j] = (r0 + t // w) * W + c0 + t % w
                if sb < 0 or _above(z[q], gq, z[sb], gsb):
                    sb = q
                    gsb = gq
                    p_steep[i] = p_lab[i, j]
    return cells, p_gid, p_own, p_nb, p_lab, p_steep


# ------------------------------------------------------------------ the flood on a graph

@njit(cache=True)
def reduce_core(node_gid, node_z, node_open, node_bz, node_bg, order, p_gid, p_z, p_edge, p_own, p_nb, p_lab,
                p_steep, thr, top_level):
    """
    The flood of a graph of catchments, highest pass first (`order`). Returns the settled tops of
    prominence >= `thr` (top, key col, heights, the label across the col, whether their ground
    touched the window's edge), the forwarding of the tops settled below `thr` (to the label
    across their col; -1 for none) and of edge cells that turned out no tops (to the label
    their steepest neighbour climbs to), which nodes go up unsettled, and each node's next
    root (`up`), from which a node's representative among those going up is read.
    """
    nn = len(node_gid)
    uf = np.arange(nn)
    rtop = np.arange(nn)
    copen = node_open.copy()
    cbz = node_bz.copy()           # when the ground first reached the window's edge, as a pass
    cbg = node_bg.copy()           # (height, gid); -inf never, +inf from the top
    uz = np.full(nn, -np.inf, np.float32)
    ug = np.zeros(nn, np.int64)
    carried = np.zeros(nn, np.bool_)
    up = np.full(nn, -1, np.int64)
    r_top = np.empty(nn, np.int64)
    r_col = np.empty(nn, np.int64)
    r_hz = np.empty(nn, np.float32)
    r_cz = np.empty(nn, np.float32)
    r_far = np.empty(nn, np.int64)
    r_bord = np.empty(nn, np.bool_)
    nr = 0
    f_key = np.empty(nn, np.int64)
    f_val = np.empty(nn, np.int64)
    nf = 0
    roots = np.empty(9, np.int64)
    vias = np.empty(9, np.int64)
    for k in order:
        m = 0
        own = p_own[k]
        ownroot = -1
        if own >= 0:
            ownroot = _find(uf, own)
            roots[0] = ownroot
            vias[0] = -1
            m = 1
        for j in range(8):
            a = p_nb[k, j]
            if a < 0:
                continue
            r = _find(uf, a)
            dup = False
            for i in range(m):
                if roots[i] == r:
                    dup = True
            if not dup:
                roots[m] = r
                vias[m] = j
                m += 1
        if m < 2:
            continue
        bj = 0
        for i in range(1, m):
            a = rtop[roots[i]]
            b = rtop[roots[bj]]
            if _above(node_z[a], node_gid[a], node_z[b], node_gid[b]):
                bj = i
        best = roots[bj]
        final = not p_edge[k]
        if final:
            for i in range(m):
                r = roots[i]
                if r != best and r != ownroot and copen[r]:
                    final = False
        across = p_lab[k, vias[bj]]
        for i in range(m):
            r = roots[i]
            if r == best:
                continue
            t = rtop[r]
            if r == ownroot:
                # an edge cell that is no top: it climbs on, as its steepest neighbour does
                if p_edge[k]:
                    carried[t] = True
                else:
                    f_key[nf] = node_gid[t]
                    f_val[nf] = p_steep[k]
                    nf += 1
            elif final:
                prom = node_z[t] - p_z[k]
                if prom >= thr:
                    r_top[nr] = node_gid[t]
                    r_col[nr] = p_gid[k]
                    r_hz[nr] = node_z[t]
                    r_cz[nr] = p_z[k]
                    r_far[nr] = across
                    r_bord[nr] = _above(cbz[r], cbg[r], p_z[k], p_gid[k])
                    nr += 1
                else:
                    f_key[nf] = node_gid[t]
                    f_val[nf] = across
                    nf += 1
            else:
                carried[t] = True
            up[t] = rtop[best]
            uz[t] = p_z[k]
            ug[t] = p_gid[k]
        for i in range(m):
            r = roots[i]
            if r != best:
                uf[r] = best
                copen[best] = copen[best] or copen[r]
                if _above(cbz[r], cbg[r], cbz[best], cbg[best]):
                    cbz[best] = cbz[r]
                    cbg[best] = cbg[r]
    for i in range(nn):
        if uf[i] != i:
            continue
        t = rtop[i]
        if copen[i] and not top_level:
            carried[t] = True
        elif node_z[t] >= thr:                   # an island: its key col is the sea
            r_top[nr] = node_gid[t]
            r_col[nr] = -1
            r_hz[nr] = node_z[t]
            r_cz[nr] = np.nan
            r_far[nr] = -1
            r_bord[nr] = cbz[i] > -np.inf
            nr += 1
        else:
            f_key[nf] = node_gid[t]
            f_val[nf] = -1
            nf += 1
    return (r_top[:nr], r_col[:nr], r_hz[:nr], r_cz[:nr], r_far[:nr], r_bord[:nr],
            f_key[:nf], f_val[:nf], carried, up, uz, ug)


@njit(cache=True)
def edge_times(carried, up, uz, ug, bz, bg):
    """
    For each node going up, when its ground first reaches the window's edge: a node merged into
    it brings its own time, but no earlier than the pass at which it merged.
    """
    az = bz.copy()
    ag = bg.copy()
    idx = np.flatnonzero(up >= 0)
    # the earliest merge first: by the pass's height, down, a tie by its gid, up
    idx = idx[np.argsort(ug[idx], kind='mergesort')]
    keys = np.empty(len(idx), np.float64)
    for i in range(len(idx)):
        keys[i] = -uz[idx[i]]
    srt = idx[np.argsort(keys, kind='mergesort')]
    for i in srt:
        if carried[i]:
            continue
        j = up[i]
        ez, eg = az[i], ag[i]
        if _above(ez, eg, uz[i], ug[i]):
            ez, eg = uz[i], ug[i]
        if _above(ez, eg, az[j], ag[j]):
            az[j] = ez
            ag[j] = eg
    return az, ag


@njit(cache=True)
def representatives(carried, up):
    """Each node's representative among the carried nodes (-1: settled with all its ground)."""
    nn = len(carried)
    rep = np.full(nn, -2, np.int64)
    stack = np.empty(nn, np.int64)
    for i in range(nn):
        if rep[i] != -2:
            continue
        sp = 0
        p = i
        while rep[p] == -2 and not carried[p] and up[p] >= 0:
            stack[sp] = p
            sp += 1
            p = up[p]
        if rep[p] == -2:
            rep[p] = p if carried[p] else -1
        r = rep[p]
        for s in range(sp):
            rep[stack[s]] = r
    return rep


@njit(cache=True)
def resolve(labels, keys, vals):
    """Labels through the forwarding (sorted `keys`), as far as it goes."""
    out = labels.copy()
    for i in range(len(out)):
        x = out[i]
        for _ in range(1 << 30):
            if x < 0:
                break
            j = np.searchsorted(keys, x)
            if j < len(keys) and keys[j] == x:
                x = vals[j]
            else:
                break
        out[i] = x
    return out


@njit(cache=True)
def keep_passes(order, own, nb, nnodes):
    """Of the passes, those that are the highest between some pair of distinct representatives."""
    seen = Dict.empty(key_type=types.int64, value_type=types.boolean)
    keep = np.zeros(len(own), np.bool_)
    lst = np.empty(9, np.int64)
    for k in order:
        m = 0
        if own[k] >= 0:
            lst[0] = own[k]
            m = 1
        for j in range(8):
            a = nb[k, j]
            if a < 0:
                continue
            dup = False
            for i in range(m):
                if lst[i] == a:
                    dup = True
            if not dup:
                lst[m] = a
                m += 1
        for i in range(m):
            for j in range(i + 1, m):
                a, b = lst[i], lst[j]
                if a > b:
                    a, b = b, a
                key = a * nnodes + b
                if key not in seen:
                    seen[key] = True
                    keep[k] = True
    return keep


def pass_order(p_z, p_gid):
    return np.lexsort((p_gid, -p_z.astype(np.float64)))


def is_edge(gid, bounds, W, H):
    """On the block's edge, where it is not the window's: a cell with neighbours unknown."""
    r0, r1, c0, c1 = bounds
    r, c = np.divmod(gid, W)
    return (((r == r0) & (r0 > 0)) | ((r == r1 - 1) & (r1 < H)) |
            ((c == c0) & (c0 > 0)) | ((c == c1 - 1) & (c1 < W)))


def in_ring(gid, bounds, W, depth=2):
    r0, r1, c0, c1 = bounds
    r, c = np.divmod(gid, W)
    return (r < r0 + depth) | (r >= r1 - depth) | (c < c0 + depth) | (c >= c1 - depth)


def reduce_block(nodes, passes, ring, bounds, W, H, thr, top_level, persist):
    """
    Flood a block's graph and hand up what is left. `nodes`: gid (sorted), z, and when its ground
    first reaches the window's edge (bz, bg: a pass's height and gid).
    `passes`: gid, z, own and nb as node indices, lab, steep. `ring`: gid (sorted), z, rep
    (node index or -1), lab, edge_node.
    """
    node_open = is_edge(nodes['gid'], bounds, W, H)
    p_edge = is_edge(passes['gid'], bounds, W, H)
    order = pass_order(passes['z'], passes['gid'])
    (r_top, r_col, r_hz, r_cz, r_far, r_bord, f_key, f_val, carried, up, uz, ug) = reduce_core(
        nodes['gid'], nodes['z'], node_open, nodes['bz'], nodes['bg'], order, passes['gid'], passes['z'], p_edge,
        passes['own'], passes['nb'], passes['lab'], passes['steep'], np.float32(thr), top_level)
    srt = np.argsort(f_key)
    f_key, f_val = f_key[srt], f_val[srt]
    f_val = resolve(f_val, f_key, f_val)
    rep = representatives(carried, up)
    kept = np.flatnonzero(carried)
    newidx = np.full(len(carried), -1, np.int64)
    newidx[kept] = np.arange(len(kept))
    az, ag = edge_times(carried, up, uz, ug, nodes['bz'], nodes['bg'])
    out_nodes = dict(gid=nodes['gid'][kept], z=nodes['z'][kept], bz=az[kept], bg=ag[kept])

    def remap(a):
        if not len(rep):
            return np.full(a.shape, -1, np.int64)
        b = np.where(a >= 0, rep[np.maximum(a, 0)], -1)
        return np.where(b >= 0, newidx[np.maximum(b, 0)], -1)

    nb = remap(passes['nb'])
    # A cell's own node stays its own only while it is carried as itself: an edge cell found
    # to be no top has climbed on into a neighbour's ground, which `nb` already holds.
    o = passes['own']
    own = np.full(o.shape, -1, np.int64) if not len(rep) else np.where((o >= 0) & (rep[np.maximum(o, 0)] == np.maximum(o, 0)), newidx[np.maximum(o, 0)], -1)
    keep = keep_passes(order, own, nb, max(len(kept), 1))
    out_passes = dict(gid=passes['gid'][keep], z=passes['z'][keep], own=own[keep], nb=nb[keep],
                      lab=resolve(passes['lab'][keep].ravel(), f_key, f_val).reshape(-1, 8),
                      steep=resolve(passes['steep'][keep], f_key, f_val))
    sel = in_ring(ring['gid'], bounds, W)
    out_ring = dict(gid=ring['gid'][sel], z=ring['z'][sel], rep=remap(ring['rep'][sel]),
                    lab=resolve(ring['lab'][sel], f_key, f_val), edge_node=is_edge(ring['gid'][sel], bounds, W, H))
    records = dict(top=r_top, col=r_col, hz=r_hz, cz=r_cz, far=resolve(r_far, f_key, f_val), border=r_bord)
    fwd = dict(key=f_key, val=f_val) if persist else dict(key=f_key[:0], val=f_val[:0])
    return dict(nodes=out_nodes, passes=out_passes, ring=out_ring, records=records, fwd=fwd, bounds=bounds)


def flood_tile(zp, bounds, W, H, thr, top_level=False):
    """
    One tile from its heights (2-D, float32, z <= 0 no data), with a ring of one cell around it
    where the window has one: the edge of the data — the window's, or a hole — is known per cell.
    """
    r0, r1, c0, c1 = bounds
    h, w = r1 - r0, c1 - c0
    t, l = int(r0 > 0), int(c0 > 0)
    z = zp[t:t + h, l:l + w]
    zf = np.ascontiguousarray(z, dtype=np.float32).ravel()
    ptr, lab, edge = label_tile(zf, w, h, r0, c0, W, H)
    tops = np.flatnonzero(ptr == np.arange(len(ptr)))
    node_of = np.full(len(zf), -1, np.int64)
    node_of[tops] = np.arange(len(tops))
    yy, xx = np.divmod(np.arange(len(zf), dtype=np.int64), w)
    gid = (r0 + yy) * W + c0 + xx
    rr, cc = r0 + yy, c0 + xx
    on_border = (rr == 0) | (rr == H - 1) | (cc == 0) | (cc == W - 1)
    # next to no data: beyond it the key col may lie as well
    hole = np.zeros((h + 2, w + 2), np.bool_)
    hole[1 - t:1 - t + zp.shape[0], 1 - l:1 - l + zp.shape[1]] = zp <= 0
    near = np.zeros((h, w), np.bool_)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            near |= hole[dy:dy + h, dx:dx + w]
    on_border |= near.ravel()
    # A catchment reaches the window's edge at its highest cell there: from then on its ground does.
    bz = np.full(len(tops), -np.inf, np.float32)
    bg = np.zeros(len(tops), np.int64)
    sel = np.flatnonzero(on_border & (lab >= 0))
    sel = sel[np.lexsort((gid[sel], -zf[sel]))]          # the highest first, in the strict order
    nd, first = np.unique(node_of[lab[sel]], return_index=True)
    bz[nd] = zf[sel[first]]
    bg[nd] = gid[sel[first]]
    nodes = dict(gid=gid[tops], z=zf[tops], bz=bz, bg=bg)
    cells, p_gid, p_own, p_nb, p_lab, p_steep = tile_passes(zf, ptr, lab, node_of, max(len(tops), 1), w, h, r0, c0, W)
    passes = dict(gid=p_gid, z=zf[cells], own=p_own, nb=p_nb, lab=p_lab, steep=p_steep)
    rsel = np.flatnonzero(in_ring(gid, bounds, W))
    rl = lab[rsel]
    ring = dict(gid=gid[rsel], z=zf[rsel], rep=np.where(rl >= 0, node_of[np.maximum(rl, 0)], -1),
                lab=np.where(rl >= 0, gid[np.maximum(rl, 0)], -1), edge_node=edge[rsel])
    out = reduce_block(nodes, passes, ring, bounds, W, H, thr, top_level, persist=False)
    out['stats'] = dict(cells=int(len(zf)), tops=int(len(tops) - edge.sum()), edge_nodes=int(edge.sum()),
                        passes=int(len(cells)))
    return out


# ------------------------------------------------------------------ stitching

@njit(cache=True)
def seam_passes(cells, W, H, rg, rz, rrep, rlab):
    """The passes at cells that were on a tile's edge, now with all their neighbours."""
    k = len(cells)
    p_z = np.empty(k, np.float32)
    p_own = np.full(k, -1, np.int64)
    p_nb = np.full((k, 8), -1, np.int64)
    p_lab = np.full((k, 8), -1, np.int64)
    p_steep = np.full(k, -1, np.int64)
    for i in range(k):
        v = cells[i]
        iv = np.searchsorted(rg, v)
        zv = rz[iv]
        p_z[i] = zv
        p_own[i] = rrep[iv]
        r = v // W
        c = v - r * W
        j = -1
        sb = -1
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                j += 1
                rr = r + dy
                cc = c + dx
                if rr < 0 or rr >= H or cc < 0 or cc >= W:
                    continue
                q = rr * W + cc
                iq = np.searchsorted(rg, q)
                if iq >= len(rg) or rg[iq] != q:
                    continue
                if rz[iq] <= 0 or not _above(rz[iq], q, zv, v):
                    continue
                p_nb[i, j] = rrep[iq]
                p_lab[i, j] = rlab[iq]
                if sb < 0 or _above(rz[iq], q, rz[sb], rg[sb]):
                    sb = iq
                    p_steep[i] = rlab[iq]
    return p_z, p_own, p_nb, p_lab, p_steep


def merge(children, bounds, W, H, thr, top_level):
    """Stitch up to four blocks' leftovers into one block and flood its graph."""
    gids = np.concatenate([c['nodes']['gid'] for c in children])
    srt = np.argsort(gids)
    nodes = {k: np.concatenate([c['nodes'][k] for c in children])[srt] for k in ('gid', 'z', 'bz', 'bg')}

    def to_global(c, a):            # node indices of a child to gids
        g = c['nodes']['gid']
        if not len(g):
            return np.full(a.shape, -1, np.int64)
        return np.where(a >= 0, g[np.maximum(a, 0)], -1)

    ring = {k: np.concatenate([c['ring'][k] for c in children]) for k in ('gid', 'z', 'lab', 'edge_node')}
    ring['rep'] = np.concatenate([to_global(c, c['ring']['rep']) for c in children])
    rs = np.argsort(ring['gid'])
    ring = {k: v[rs] for k, v in ring.items()}
    seam = ring['gid'][ring['edge_node']]
    p_gid = np.concatenate([c['passes']['gid'] for c in children])
    old = ~np.isin(p_gid, seam)
    passes = dict(gid=p_gid[old],
                  z=np.concatenate([c['passes']['z'] for c in children])[old],
                  own=np.concatenate([to_global(c, c['passes']['own']) for c in children])[old],
                  nb=np.concatenate([to_global(c, c['passes']['nb']) for c in children])[old],
                  lab=np.concatenate([c['passes']['lab'] for c in children])[old],
                  steep=np.concatenate([c['passes']['steep'] for c in children])[old])
    s_z, s_own, s_nb, s_lab, s_steep = seam_passes(seam, W, H, ring['gid'], ring['z'], ring['rep'], ring['lab'])
    passes = dict(gid=np.concatenate([passes['gid'], seam]), z=np.concatenate([passes['z'], s_z]),
                  own=np.concatenate([passes['own'], s_own]), nb=np.concatenate([passes['nb'], s_nb]),
                  lab=np.concatenate([passes['lab'], s_lab]), steep=np.concatenate([passes['steep'], s_steep]))

    def to_index(a):
        i = np.searchsorted(nodes['gid'], np.maximum(a, 0))
        i = np.minimum(i, max(len(nodes['gid']) - 1, 0))
        ok = (a >= 0) & (nodes['gid'][i] == a) if len(nodes['gid']) else np.zeros(a.shape, np.bool_)
        return np.where(ok, i, -1)

    passes['own'] = to_index(passes['own'])
    passes['nb'] = to_index(passes['nb'])
    ring['rep'] = to_index(ring['rep'])
    out = reduce_block(nodes, passes, ring, bounds, W, H, thr, top_level, persist=True)
    out['stats'] = dict(nodes=int(len(nodes['gid'])), passes=int(len(passes['gid'])), seam=int(len(seam)))
    return out


# ------------------------------------------------------------------ the whole

_READ = None        # the reader, handed to the workers by fork


def _tile_job(args):
    b, W, H, thr, top = args
    halo = (max(b[0] - 1, 0), min(b[1] + 1, H), max(b[2] - 1, 0), min(b[3] + 1, W))
    return flood_tile(_READ(*halo), b, W, H, thr, top_level=top)


def run(read, H, W, tile, thr=THRESHOLD, log=None, jobs=1):
    """
    `read(r0, r1, c0, c1)` gives a window's heights. Returns the settled tops of P >= thr
    (sorted by top), the forwarding, and per-level statistics. With `jobs` > 1 the tiles are
    flooded in forked workers: `read` must then open its own file in each.
    """
    global _READ
    ti, tj = -(-H // tile), -(-W // tile)
    top = ti == 1 and tj == 1
    blocks = {}
    stats = {'tiles': [], 'levels': []}
    t0 = time.time()
    keys = [(bi, bj) for bi in range(ti) for bj in range(tj)]
    args = [((bi * tile, min((bi + 1) * tile, H), bj * tile, min((bj + 1) * tile, W)), W, H, thr, top)
            for bi, bj in keys]
    _READ = read
    if jobs > 1:
        import multiprocessing
        with multiprocessing.get_context('fork').Pool(jobs) as pool:
            outs = pool.map(_tile_job, args, chunksize=1)
    else:
        outs = [_tile_job(a) for a in args]
    for k, o in zip(keys, outs):
        blocks[k] = o
        stats['tiles'].append(o['stats'])
    stats['tile_seconds'] = time.time() - t0
    records = [b['records'] for b in blocks.values()]
    fwds = [b['fwd'] for b in blocks.values()]
    level = 0
    size = tile
    while len(blocks) > 1:
        level += 1
        size *= 2
        t1 = time.time()
        nxt = {}
        ni, nj = -(-H // size), -(-W // size)
        for bi in range(ni):
            for bj in range(nj):
                kids = [blocks[(2 * bi + a, 2 * bj + b)] for a in (0, 1) for b in (0, 1)
                        if (2 * bi + a, 2 * bj + b) in blocks]
                b = (bi * size, min((bi + 1) * size, H), bj * size, min((bj + 1) * size, W))
                nxt[(bi, bj)] = merge(kids, b, W, H, thr, top_level=(ni == 1 and nj == 1))
                records.append(nxt[(bi, bj)]['records'])
                fwds.append(nxt[(bi, bj)]['fwd'])
        blocks = nxt
        lv = [b['stats'] for b in blocks.values()]
        stats['levels'].append(dict(level=level, blocks=len(lv), nodes=sum(s['nodes'] for s in lv),
                                    passes=sum(s['passes'] for s in lv), seam=sum(s['seam'] for s in lv),
                                    seconds=round(time.time() - t1, 2)))
        if log:
            log(f'level {level}: {stats["levels"][-1]}')
    rec = {k: np.concatenate([r[k] for r in records]) for k in ('top', 'col', 'hz', 'cz', 'far', 'border')}
    key = np.concatenate([f['key'] for f in fwds])
    val = np.concatenate([f['val'] for f in fwds])
    srt = np.argsort(key)
    key, val = key[srt], val[srt]
    stats['forward'] = int(len(key))
    rec['far'] = resolve(rec['far'], key, val)
    srt = np.argsort(rec['top'])
    rec = {k: v[srt] for k, v in rec.items()}
    return rec, stats


@njit(cache=True)
def walk(tops, col, hz, cz, far):
    """
    Each top's parent: across its key col and on across each lesser top's, to the first more
    prominent one. Returns the parents (gid, -1 none) and the pairs walked past at equal
    prominence.
    """
    n = len(tops)
    prom = np.where(col >= 0, hz - cz, hz)
    out = np.full(n, -1, np.int64)
    tie_a = np.empty(0, np.int64)
    tie_b = np.empty(0, np.int64)
    ta = []
    tb = []
    for s in range(n):
        p = far[s]
        for _ in range(1 << 30):
            if p < 0:
                break
            i = np.searchsorted(tops, p)
            if i >= n or tops[i] != p:       # cannot happen: every label resolves to a settled top
                p = -2
                break
            if prom[i] > prom[s]:
                break
            if prom[i] == prom[s]:
                ta.append(tops[s])
                tb.append(p)
            p = far[i]
        out[s] = p
    if len(ta):
        tie_a = np.array(ta)
        tie_b = np.array(tb)
    return out, tie_a, tie_b


# ------------------------------------------------------------------ the reference

def reference(z2d, thr=THRESHOLD):
    """`tiled.flood_core` in one piece and `tiled.walk_parents`: deaths and parents of P >= thr."""
    from tiled import flood_core, walk_parents
    h, w = z2d.shape
    z = np.ascontiguousarray(z2d, dtype=np.float32).ravel()
    gid = np.arange(len(z), dtype=np.int64)
    order = np.argsort(-z, kind='stable')
    d_top, d_col, d_isle, d_far, *_ = flood_core(order, z, gid, np.zeros(len(z), np.bool_),
                                                 np.empty(1, np.int64), np.empty(1, np.int64), w, h)
    srt = np.argsort(d_top)
    d_top, d_col, d_isle, d_far = d_top[srt], d_col[srt], d_isle[srt], d_far[srt]
    hz = z[d_top]
    cz = np.where(d_col >= 0, z[np.maximum(d_col, 0)], np.nan).astype(np.float32)
    prom = np.where(d_col >= 0, hz - cz, hz)
    sel = np.flatnonzero(prom >= thr)
    parents = walk_parents(z.reshape(h, w), h, w, d_top, d_col, d_isle, hz, cz, d_far, d_top[sel])
    return dict(top=d_top[sel], col=d_col[sel], hz=hz[sel], cz=cz[sel], parent=parents, all=len(d_top))


# ------------------------------------------------------------------ a window of DMR 5.0

def window(center, size, tile, thr, out, check):
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window
    from prominence import SOURCE
    lat, lon = center
    with rasterio.open(SOURCE) as d:
        x, y = Transformer.from_crs('EPSG:4326', d.crs, always_xy=True).transform(lon, lat)
        row, colm = d.index(x, y)
        r0, c0 = row - size // 2, colm - size // 2
        z = d.read(1, window=Window(c0, r0, size, size), boundless=True, fill_value=d.nodata)
        transform = d.window_transform(Window(c0, r0, size, size))
        crs = d.crs
    z[~np.isfinite(z) | (z > 1e30)] = 0
    H, W = z.shape
    print(f'window {H}×{W} at row {r0}, col {c0}; no data: {(z <= 0).sum()} cells', flush=True)
    t0 = time.time()
    rec, stats = run(lambda a, b, c, e: z[a:b, c:e], H, W, tile, thr, log=lambda s: print(s, flush=True))
    t_run = time.time() - t0
    parents, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    t_walk = time.time() - t0 - t_run
    tiles = stats['tiles']
    summary = dict(cells=H * W, km2=H * W / 1e6, tile=tile, tops=sum(t['tops'] for t in tiles),
                   edge_nodes=sum(t['edge_nodes'] for t in tiles), tile_passes=sum(t['passes'] for t in tiles),
                   p_thr=len(rec['top']), forward=stats['forward'], levels=stats['levels'],
                   tile_seconds=round(stats['tile_seconds'], 1), seconds=round(t_run, 1), walk_seconds=round(t_walk, 2),
                   ties=len(ta), border=int(rec['border'].sum()),
                   max_rss_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2))
    print(json.dumps(summary, indent=1), flush=True)
    to_ll = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)

    def ll(g):
        r, c = divmod(int(g), W)
        xx, yy = transform * (c + 0.5, r + 0.5)
        lo, la = to_ll.transform(xx, yy)
        return [round(la, 6), round(lo, 6)]

    prom = np.where(rec['col'] >= 0, rec['hz'] - rec['cz'], rec['hz'])
    res = []
    for i in range(len(rec['top'])):
        r = dict(top=ll(rec['top'][i]), h=round(float(rec['hz'][i]), 2), prom=round(float(prom[i]), 2),
                 border=bool(rec['border'][i]))
        if rec['col'][i] >= 0:
            r.update(col=ll(rec['col'][i]), col_h=round(float(rec['cz'][i]), 2))
        if parents[i] >= 0:
            r['parent'] = ll(parents[i])
        res.append(r)
    ties = [[ll(a), ll(b)] for a, b in zip(ta, tb)]
    json.dump(dict(summary=summary, summits=res, ties=ties), open(out, 'w'), indent=0)
    print(f'{len(res)} tops of P >= {thr} -> {out}', flush=True)
    if check:
        t1 = time.time()
        ref = reference(z, thr)
        print(f'reference: {ref["all"]} deaths in one piece, {time.time() - t1:.0f}s; '
              f'max RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.1f} GB', flush=True)
        report = compare(rec, parents, ref)
        print(json.dumps(report, indent=1), flush=True)


# ------------------------------------------------------------------ DMR 5.0, any part or all of it

_DS = {}


def dmr5_reader(source, r0, c0):
    """Windows of the raster from (r0, c0), spikes filtered: each forked worker opens its own file."""
    import rasterio
    from rasterio.windows import Window

    def read(a, b, c, e):
        ds = _DS.get(os.getpid()) or _DS.setdefault(os.getpid(), rasterio.open(source))
        # a cell wider all round for the spike filter, then trimmed
        z = ds.read(1, window=Window(c0 + c - 1, r0 + a - 1, e - c + 2, b - a + 2), boundless=True,
                    fill_value=ds.nodata)
        z[~np.isfinite(z) | (z > 1e30)] = 0
        z, n = despike(z)
        if n:
            print(f'  {n} spikes in rows {a}-{b}, cols {c}-{e}', flush=True)
        return np.ascontiguousarray(z[1:-1, 1:-1])
    return read


def dmr5(bbox, out, tile=5000, thr=THRESHOLD, jobs=10):
    """
    Every top of P >= thr in DMR 5.0 (all of it, or the box S,N,W,E), with its key col, its
    parent and whether its ground reaches the edge of the data (border or window). Heights as
    the raster has them (ETRS89-h); `catchments_db.py` converts them.
    """
    import rasterio
    from pyproj import Transformer
    from prominence import SOURCE
    d = rasterio.open(SOURCE)
    if bbox:
        s, n, w, e = bbox
        fw = Transformer.from_crs('EPSG:4326', d.crs, always_xy=True)
        xs, ys = zip(*[fw.transform(lo, la) for la in (s, n) for lo in (w, e)])
        r0, c0 = d.index(min(xs), max(ys))
        r1, c1 = d.index(max(xs), min(ys))
    else:
        r0, c0, r1, c1 = 0, 0, d.height, d.width
    H, W = r1 - r0, c1 - c0
    print(f'{H} × {W} cells ({H * W / 1e9:.1f} G), tiles of {tile}, {jobs} workers', flush=True)
    t0 = time.time()
    rec, stats = run(dmr5_reader(SOURCE, r0, c0), H, W, tile, thr, log=lambda s: print(s, flush=True), jobs=jobs)
    par, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    print(f'{time.time() - t0:.0f} s: {len(rec["top"])} tops of P >= {thr:g}; {len(ta)} ties', flush=True)
    back = Transformer.from_crs(d.crs, 'EPSG:4326', always_xy=True)

    def ll(g):
        r, c = divmod(int(g), W)
        x, y = d.transform * (c0 + c + 0.5, r0 + r + 0.5)
        lo, la = back.transform(x, y)
        return [round(la, 7), round(lo, 7)]

    prom = np.where(rec['col'] >= 0, rec['hz'] - rec['cz'], rec['hz'])
    res = []
    for i in range(len(rec['top'])):
        x = dict(top=ll(rec['top'][i]), h=float(rec['hz'][i]), prom=float(prom[i]), border=bool(rec['border'][i]))
        if rec['col'][i] >= 0:
            x.update(col=ll(rec['col'][i]), col_h=float(rec['cz'][i]))
        if par[i] >= 0:
            x['parent'] = ll(par[i])
        res.append(x)
    st = {k: v for k, v in stats.items() if k != 'tiles'}
    st.update(tops=sum(t['tops'] for t in stats['tiles']), ties=[[ll(a), ll(b)] for a, b in zip(ta, tb)])
    json.dump(dict(stats=st, summits=res), open(out, 'w'))
    print(f'-> {out}', flush=True)


def compare(rec, parents, ref):
    mine = dict(zip(rec['top'].tolist(), zip(rec['col'].tolist(), parents.tolist())))
    theirs = dict(zip(ref['top'].tolist(), zip(ref['col'].tolist(), ref['parent'].tolist())))
    both = set(mine) & set(theirs)
    return dict(mine=len(mine), reference=len(theirs), both=len(both),
                same_col=sum(mine[k][0] == theirs[k][0] for k in both),
                same_parent=sum(mine[k][1] == theirs[k][1] for k in both),
                only_mine=sorted(set(mine) - set(theirs))[:10], only_reference=sorted(set(theirs) - set(mine))[:10],
                col_differs=[(k, mine[k][0], theirs[k][0]) for k in sorted(both) if mine[k][0] != theirs[k][0]][:10],
                parent_differs=[(k, mine[k][1], theirs[k][1]) for k in sorted(both) if mine[k][1] != theirs[k][1]][:10])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('window', help='a square of DMR 5.0, tiled, against the flood in one piece')
    p.add_argument('--center', required=True, help='LAT,LON')
    p.add_argument('--size', type=int, default=10000, help='cells (metres) a side')
    p.add_argument('--tile', type=int, default=2500)
    p.add_argument('--threshold', type=float, default=THRESHOLD)
    p.add_argument('--out', default='catchments.json')
    p.add_argument('--no-check', action='store_true')
    p = sub.add_parser('dmr5', help='all of DMR 5.0, or a box of it: every top of P >= threshold')
    p.add_argument('--bbox', help='S,N,W,E (default: the whole raster)')
    p.add_argument('--tile', type=int, default=5000)
    p.add_argument('--threshold', type=float, default=THRESHOLD)
    p.add_argument('--jobs', type=int, default=10)
    p.add_argument('--out', required=True)
    a = ap.parse_args()
    if a.cmd == 'dmr5':
        dmr5([float(v) for v in a.bbox.split(',')] if a.bbox else None, a.out, a.tile, a.threshold, a.jobs)
    if a.cmd == 'window':
        window([float(v) for v in a.center.split(',')], a.size, a.tile, a.threshold, a.out, not a.no_check)


if __name__ == '__main__':
    main()
