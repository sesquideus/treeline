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


class Checkpoint:
    """
    `run`'s finished blocks on disk (see its docstring): one file per block, `L_i_j.pkl`, with
    its settled tops and forwarding, and the whole block while it waits for its siblings.
    When a parent is merged its own file is written first and only then are its children's
    cut down to their tops; a child still whole whose parent's file exists was merged, so a
    run cut off at any moment resumes consistently. `run.json` records what the run is (grid,
    tile, threshold): a directory left by another run is refused rather than mixed in.
    """

    def __init__(self, path, identity):
        import pickle
        self.pickle = pickle
        self.path = path
        os.makedirs(path, exist_ok=True)
        meta = os.path.join(path, 'run.json')
        identity = [int(v) if float(v).is_integer() else float(v) for v in identity]
        if os.path.exists(meta):
            with open(meta) as f:
                held = json.load(f)
            if held != identity:
                raise ValueError(f'{path} holds another run ({held}, not {identity})')
        else:
            with open(meta, 'w') as f:
                json.dump(identity, f)

    def _file(self, level, k):
        return os.path.join(self.path, f'{level}_{k[0]}_{k[1]}.pkl')

    def exists(self, level, k):
        return os.path.exists(self._file(level, k))

    def save(self, level, k, block, whole=True):
        tmp = self._file(level, k) + '.tmp'
        with open(tmp, 'wb') as f:
            self.pickle.dump(dict(records=block['records'], fwd=block['fwd'], block=block if whole else None),
                             f, protocol=5)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._file(level, k))

    def merged(self, level, k, block):
        """Its parent is on disk: keep only the tops."""
        self.save(level, k, block, whole=False)

    def load(self):
        """(level, key, records, fwd, block or None) for every block on disk."""
        for name in os.listdir(self.path):
            if not name.endswith('.pkl'):
                continue
            level, i, j = (int(v) for v in name[:-4].split('_'))
            with open(os.path.join(self.path, name), 'rb') as f:
                d = self.pickle.load(f)
            yield level, (i, j), d['records'], d['fwd'], d['block']


def run(read, H, W, tile, thr=THRESHOLD, log=None, jobs=1, checkpoint=None):
    """
    `read(r0, r1, c0, c1)` gives a window's heights. Returns the settled tops of P >= thr
    (sorted by top), the forwarding, and per-level statistics. With `jobs` > 1 the tiles are
    flooded in forked workers: `read` must then open its own file in each.

    `checkpoint`: a directory where every finished tile and merged block is kept, so that a
    run broken off (a crash, a power cut, ^C) resumes where it stopped when started again
    with the same arguments: the settled tops of every block (small, kept), and the whole of
    each block still waiting for its siblings (deleted once merged into its parent). Files
    are written to a temporary name and renamed, so a cut-off write leaves nothing half-done.
    """
    global _READ
    ti, tj = -(-H // tile), -(-W // tile)
    top = ti == 1 and tj == 1
    stats = {'tiles': [], 'levels': []}
    t0 = time.time()
    # Tiles go out in Z-order and each 2×2 block is merged the moment its last child arrives,
    # and so on up: only the unfinished front is ever held. Collecting every tile first and
    # merging afterwards held all of them at once, which for the world (33 800 tiles) did not
    # fit in memory. The merges themselves are the same, so the result is too.
    def levels_grid(level):
        size = tile << level
        return -(-H // size), -(-W // size)

    depth = 0
    while levels_grid(depth) != (1, 1):
        depth += 1

    def morton(k):
        bi, bj = k
        code = 0
        for bit in range(32):
            code |= ((bi >> bit) & 1) << (2 * bit + 1) | ((bj >> bit) & 1) << (2 * bit)
        return code

    keys = sorted(((bi, bj) for bi in range(ti) for bj in range(tj)), key=morton)
    args = [((bi * tile, min((bi + 1) * tile, H), bj * tile, min((bj + 1) * tile, W)), W, H, thr, top)
            for bi, bj in keys]
    _READ = read
    records, fwds = [], []
    pending = [dict() for _ in range(depth + 1)]       # per level: block key -> block
    final = {}
    store = Checkpoint(checkpoint, (H, W, tile, thr)) if checkpoint else None
    done_tiles = set()
    if store:
        for level, k, recs, fwd, block in store.load():
            records.append(recs)
            fwds.append(fwd)
            if level == 0:
                done_tiles.add(k)
            if level == depth:
                final['block'] = block
            elif block is not None and not store.exists(level + 1, (k[0] // 2, k[1] // 2)):
                pending[level][k] = block
    level_stats = [dict(level=lv, blocks=0, nodes=0, passes=0, seam=0, seconds=0.0) for lv in range(1, depth + 1)]

    def children(level, bi, bj):
        ni, nj = levels_grid(level)
        return [(2 * bi + a, 2 * bj + b) for a in (0, 1) for b in (0, 1) if 2 * bi + a < ni and 2 * bj + b < nj]

    def arrive(level, k, block):
        """A finished block at `level`: merge its parent once all the parent's children are in."""
        if level == depth:
            final['block'] = block
            return
        pending[level][k] = block
        merge_if_complete(level, (k[0] // 2, k[1] // 2))

    def merge_if_complete(level, parent):
        kids = children(level, *parent)
        if not all(c in pending[level] for c in kids):
            return
        t1 = time.time()
        size = tile << (level + 1)
        bi, bj = parent
        b = (bi * size, min((bi + 1) * size, H), bj * size, min((bj + 1) * size, W))
        kid_blocks = [pending[level].pop(c) for c in kids]
        merged = merge(kid_blocks, b, W, H, thr, top_level=(level + 1 == depth))
        records.append(merged['records'])
        fwds.append(merged['fwd'])
        if store:
            # the parent first; only then are its children cut down to their tops
            store.save(level + 1, parent, merged)
            for c, kb in zip(kids, kid_blocks):
                store.merged(level, c, kb)
        st = level_stats[level]
        ms = merged['stats']
        st['blocks'] += 1
        st['nodes'] += ms['nodes']
        st['passes'] += ms['passes']
        st['seam'] += ms['seam']
        st['seconds'] = round(st['seconds'] + time.time() - t1, 2)
        arrive(level + 1, parent, merged)

    # A run cut off in the middle of a merge left all four children waiting: merge them now.
    for level in range(depth):
        for k in list(pending[level]):
            if k in pending[level]:
                merge_if_complete(level, (k[0] // 2, k[1] // 2))
    todo = [(k, a) for k, a in zip(keys, args) if k not in done_tiles]
    if store and done_tiles and log:
        log(f'resuming: {len(done_tiles)} of {len(keys)} tiles already done')

    def tiles():
        if jobs > 1:
            import multiprocessing
            with multiprocessing.get_context('fork').Pool(jobs) as pool:
                yield from pool.imap(_tile_job, [a for _, a in todo], chunksize=1)
        else:
            for _, a in todo:
                yield _tile_job(a)

    bar = None
    if log:
        # a line on stdout after every tile: done, rate, time left, the tops found so far
        from tqdm import tqdm
        bar = tqdm(total=len(keys), initial=len(done_tiles), unit='tile', file=sys.stdout, mininterval=0,
                   smoothing=0.05, dynamic_ncols=True)
    tops = 0
    for (k, _), o in zip(todo, tiles()):
        stats['tiles'].append(o['stats'])
        records.append(o['records'])
        fwds.append(o['fwd'])
        if store:
            store.save(0, k, o)
        arrive(0, k, o)
        if bar:
            tops += len(o['records']['top'])
            bar.set_postfix_str(f'{tops} tops settled', refresh=False)
            bar.update(1)
    if bar:
        bar.close()
    stats['tile_seconds'] = time.time() - t0
    for st in level_stats:
        stats['levels'].append(st)
        if log:
            log(f'level {st["level"]}: {st}')
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


def walk_line(tops, col, hz, cz, far, cutoff):
    """
    Each top's line parent (Helman): across its key col and on across each top passed, to the
    first top strictly higher (by height, a tie by the smaller gid) of prominence at least
    `cutoff`. The records hold only the tops of at least the threshold, the lesser ones
    already passed on to whatever is across their cols, so `cutoff` below the threshold is
    the threshold. -1 for none (an island high point).
    """
    n = len(tops)
    prom = np.where(col >= 0, hz - cz, hz)
    out = np.full(n, -1, np.int64)
    for s in range(n):
        p = far[s]
        for _ in range(1 << 30):
            if p < 0:
                break
            i = np.searchsorted(tops, p)
            if i >= n or tops[i] != p:       # cannot happen: every label resolves to a settled top
                p = -2
                break
            if (hz[i] > hz[s] or (hz[i] == hz[s] and tops[i] < tops[s])) and prom[i] >= cutoff:
                break
            p = far[i]
        out[s] = p
    return out


# ------------------------------------------------------------------ the reference

def reference(z2d, thr=THRESHOLD, line_cutoffs=()):
    """
    `tiled.flood_core` in one piece and `tiled.walk_parents`: deaths and parents of P >= thr,
    and with `line_cutoffs` their line parents at each (`tiled.walk_line_parents`).
    """
    from tiled import flood_core, walk_line_parents, walk_parents
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
    line = {c: walk_line_parents(z.reshape(h, w), h, w, d_top, d_col, d_isle, hz, cz, d_far, d_top[sel], c)
            for c in line_cutoffs}
    return dict(top=d_top[sel], col=d_col[sel], hz=hz[sel], cz=cz[sel], parent=parents, line=line,
                all=len(d_top))


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


FABDEM = os.environ.get('TREELINE_FABDEM', '/mnt/data/treeline/fabdem/zip')
ARC = 3600                     # cells a degree: FABDEM (and Copernicus GLO-30) at 1″


def fabdem_tile(lat, lon):
    """The GDAL path of FABDEM's 1° tile whose south-west corner is (lat, lon), read in its zip."""
    ns = lambda v, w: f'{"N" if v >= 0 else "S"}{abs(v):0{w}d}'
    ew = lambda v: f'{"E" if v >= 0 else "W"}{abs(v):03d}'
    zlat, zlon = lat // 10 * 10, lon // 10 * 10
    zipname = f'{ns(zlat, 2)}{ew(zlon)}-{ns(zlat + 10, 2)}{ew(zlon + 10)}_FABDEM_V1-2.zip'
    return f'/vsizip/{FABDEM}/{zipname}/{ns(lat, 2)}{ew(lon)}_FABDEM_V1-2.tif'


def fabdem_reader(north, west):
    """
    Windows of a 1″ grid whose cell (0, 0) is centred at (north, west), from FABDEM's 1° tiles
    (pixel centres on whole arcseconds; a tile's first row is its north edge). The sea is 0
    there and no data (z <= 0) to the engine, as is a tile FABDEM does not have.
    """
    import rasterio

    def read(a, b, c, e):
        z = np.zeros((b - a, e - c), np.float32)
        for ty in range(a // ARC, (b - 1) // ARC + 1):
            for tx in range(c // ARC, (e - 1) // ARC + 1):
                lat, lon = north - ty - 1, west + tx
                path = fabdem_tile(lat, lon)
                cache = _DS.setdefault(os.getpid(), {})
                if path not in cache and len(cache) >= 9:     # a window touches at most 4 tiles
                    cache.pop(next(iter(cache)))
                if path not in cache:
                    try:
                        cache[path] = rasterio.open(path).read(1)
                    except rasterio.errors.RasterioIOError:
                        cache[path] = None
                t = cache[path]
                if t is None:
                    continue
                r0, r1 = max(a, ty * ARC), min(b, (ty + 1) * ARC)
                c0, c1 = max(c, tx * ARC), min(e, (tx + 1) * ARC)
                z[r0 - a:r1 - a, c0 - c:c1 - c] = t[r0 - ty * ARC:r1 - ty * ARC, c0 - tx * ARC:c1 - tx * ARC]
        z[~np.isfinite(z) | (z < -1000)] = 0
        return z
    return read


COPERNICUS = os.environ.get('TREELINE_COPERNICUS', '/mnt/data/treeline/copernicus')
#: GLO-90 (3″) for the few land tiles GLO-30 withholds (Armenia, Azerbaijan): coarser, but
#: without it the Caucasus has a hole that cuts the routes through it.
GLO90 = os.environ.get('TREELINE_GLO90', '/mnt/data/treeline/glo90')
#: Added to every land cell, so that land below sea level (the Dead Sea, -430 m) stays above
#: zero, which the engine reads as no data; only the ocean is 0. Taken off again in the output.
LIFT = 1000.0
#: Known summits burned in: {(lat0, lon0) of the 1° tile: [(row, col, height)]}, see `load_burns`.
_BURN = {}


def load_burns(path):
    """
    Surveyed summit heights to write into the world grid before flooding (`burn_match.py`):
    each raises the cell at its position to its height where the model reads lower, so that
    a sharp summit the 30 m model smooths away keeps its place among its neighbours.
    """
    burns = {}
    for b in json.load(open(path))['burns']:
        lat, lon = b['at']
        gr, gc = round((90 - lat) * ARC), round((lon + 180) * ARC)
        lat0, lon0 = 90 - gr // ARC - 1, gc // ARC - 180
        burns.setdefault((lat0, (lon0 + 180) % 360 - 180), []).append((gr % ARC, gc % ARC, float(b['elev'])))
    return burns


def _glo_name(lat, lon, layer):
    stem = (f'Copernicus_DSM_COG_10_{"N" if lat >= 0 else "S"}{abs(lat):02d}_00_'
            f'{"E" if lon >= 0 else "W"}{abs(lon):03d}_00')
    return (os.path.join(COPERNICUS, f'{stem}_DEM.tif') if layer == 'DEM'
            else os.path.join(COPERNICUS, 'aux', f'{stem}_{layer}.tif'))


def _glo90_name(lat, lon, layer):
    stem = (f'Copernicus_DSM_COG_30_{"N" if lat >= 0 else "S"}{abs(lat):02d}_00_'
            f'{"E" if lon >= 0 else "W"}{abs(lon):03d}_00')
    return (os.path.join(GLO90, f'{stem}_DEM.tif') if layer == 'DEM'
            else os.path.join(GLO90, 'aux', f'{stem}_{layer}.tif'))


def _from_90(t):
    """A 3″ tile repeated out to 1″, both ways (east–west first if stored coarser still)."""
    t = t[:, (np.arange(ARC // 3) * t.shape[1]) // (ARC // 3)] if t.shape[1] != ARC // 3 else t
    return np.repeat(np.repeat(t, 3, axis=0), 3, axis=1)


def _to_arc(t):
    """A tile stored coarser east-west (north of 50°) repeated out to 1″."""
    return t if t.shape[1] == ARC else t[:, (np.arange(ARC) * t.shape[1]) // ARC]


def world_tile(lat, lon):
    """
    The 1° tile whose south-west corner is (lat, lon) as the engine is to read it: FABDEM where
    it has the tile, else Copernicus GLO-30 (Antarctica, north of 80°); the ocean (Copernicus's
    water-body mask, class 1) 0, every other cell lifted by LIFT, lakes and rivers included, so
    that a canal joins what it joins. None where Copernicus has no tile (the open ocean, and
    the few land tiles it withholds: those read as no data, an edge).
    """
    import rasterio
    lon = (lon + 180) % 360 - 180
    coarse = not os.path.exists(_glo_name(lat, lon, 'DEM'))
    if coarse and not os.path.exists(_glo90_name(lat, lon, 'DEM')):
        return None
    if coarse:
        z = _from_90(rasterio.open(_glo90_name(lat, lon, 'DEM')).read(1))
    else:
        try:
            z = rasterio.open(fabdem_tile(lat, lon)).read(1)
        except rasterio.errors.RasterioIOError:
            z = _to_arc(rasterio.open(_glo_name(lat, lon, 'DEM')).read(1))
    z = np.where(np.isfinite(z) & (z > -1000), z + LIFT, 0).astype(np.float32)
    try:
        wbm = (_from_90(rasterio.open(_glo90_name(lat, lon, 'WBM')).read(1)) if coarse
               else _to_arc(rasterio.open(_glo_name(lat, lon, 'WBM')).read(1)))
        z[wbm == 1] = 0
    except rasterio.errors.RasterioIOError:
        pass
    for r, c, h in _BURN.get((lat, lon), ()):
        if z[r, c] > 0:
            z[r, c] = max(z[r, c], h + LIFT)
    return z


def world_reader(north, west):
    """Windows of the 1″ world grid whose cell (0, 0) is centred at (north, west); see `world_tile`."""

    def read(a, b, c, e):
        z = np.zeros((b - a, e - c), np.float32)
        cache = _DS.setdefault(os.getpid(), {})
        for ty in range(a // ARC, (b - 1) // ARC + 1):
            for tx in range(c // ARC, (e - 1) // ARC + 1):
                lat, lon = north - ty - 1, west + tx
                if (lat, lon) not in cache:
                    if len(cache) >= 9:            # a window touches at most 4 tiles
                        cache.pop(next(iter(cache)))
                    cache[(lat, lon)] = world_tile(lat, lon)
                t = cache[(lat, lon)]
                if t is None:
                    continue
                r0, r1 = max(a, ty * ARC), min(b, (ty + 1) * ARC)
                c0, c1 = max(c, tx * ARC), min(e, (tx + 1) * ARC)
                z[r0 - a:r1 - a, c0 - c:c1 - c] = t[r0 - ty * ARC:r1 - ty * ARC, c0 - tx * ARC:c1 - tx * ARC]
        return z
    return read


def world(bbox, out, tile=5000, thr=THRESHOLD, jobs=10, line_cutoff=600.0, burn=None):
    """
    Every top of P >= thr within the box S,N,W,E (whole degrees; W may run past 180) of the
    world grid (`world_reader`), as `fabdem` writes it, heights back below the lift (EGM2008).
    """
    global _BURN
    s, n, w, e = bbox
    H, W = (n - s) * ARC, (e - w) * ARC
    _BURN = load_burns(burn) if burn else {}
    print(f'{H} × {W} cells ({H * W / 1e9:.1f} G), tiles of {tile}, {jobs} workers, '
          f'{sum(len(v) for v in _BURN.values())} summits burned in', flush=True)
    t0 = time.time()
    rec, stats = run(world_reader(n, w), H, W, tile, thr, log=lambda m: print(m, flush=True), jobs=jobs,
                     checkpoint=os.path.splitext(out)[0] + '.ckpt')
    par, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    line = walk_line(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], line_cutoff)
    print(f'{time.time() - t0:.0f} s: {len(rec["top"])} tops of P >= {thr:g}; {len(ta)} ties', flush=True)
    np.savez_compressed(os.path.splitext(out)[0] + '.npz', **{k: v for k, v in rec.items() if isinstance(v, np.ndarray)},
                        north=n, west=w, H=H, W=W, threshold=thr, lift=LIFT)

    def ll(g):
        r, c = divmod(int(g), W)
        return [round(n - r / ARC, 7), round((w + c / ARC + 180) % 360 - 180, 7)]

    island = rec['col'] < 0
    prom = np.where(island, rec['hz'] - LIFT, rec['hz'] - rec['cz'])
    res = []
    for i in range(len(rec['top'])):
        x = dict(top=ll(rec['top'][i]), h=round(float(rec['hz'][i]) - LIFT, 2), prom=round(float(prom[i]), 2),
                 border=bool(rec['border'][i]))
        if not island[i]:
            x.update(col=ll(rec['col'][i]), col_h=round(float(rec['cz'][i]) - LIFT, 2))
        if par[i] >= 0:
            x['parent'] = ll(par[i])
        if line[i] >= 0:
            x['line'] = ll(line[i])
        res.append(x)
    st = {k: v for k, v in stats.items() if k != 'tiles'}
    st.update(tops=sum(t['tops'] for t in stats['tiles']), ties=[[ll(a), ll(b)] for a, b in zip(ta, tb)],
              line_cutoff=line_cutoff, source='FABDEM V1-2, Copernicus GLO-30 where FABDEM has no tile')
    json.dump(dict(stats=st, summits=res), open(out, 'w'))
    print(f'-> {out}', flush=True)


def fabdem(bbox, out, tile=5000, thr=THRESHOLD, jobs=10, line_cutoff=100.0):
    """
    Every top of P >= thr in FABDEM within the box S,N,W,E (whole degrees), with its key col,
    parent, line parent at `line_cutoff`, and whether its ground reaches the edge of the data
    (the sea included, so on an island that flag says little). Heights EGM2008, as FABDEM's.
    """
    s, n, w, e = bbox
    H, W = (n - s) * ARC, (e - w) * ARC
    print(f'{H} × {W} cells ({H * W / 1e9:.2f} G), tiles of {tile}, {jobs} workers', flush=True)
    t0 = time.time()
    rec, stats = run(fabdem_reader(n, w), H, W, tile, thr, log=lambda m: print(m, flush=True), jobs=jobs,
                     checkpoint=os.path.splitext(out)[0] + '.ckpt')
    par, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    line = walk_line(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], line_cutoff)
    print(f'{time.time() - t0:.0f} s: {len(rec["top"])} tops of P >= {thr:g}; {len(ta)} ties', flush=True)
    np.savez_compressed(os.path.splitext(out)[0] + '.npz', **{k: v for k, v in rec.items() if isinstance(v, np.ndarray)},
                        north=n, west=w, H=H, W=W, threshold=thr)

    def ll(g):
        r, c = divmod(int(g), W)
        return [round(n - r / ARC, 7), round(w + c / ARC, 7)]

    prom = np.where(rec['col'] >= 0, rec['hz'] - rec['cz'], rec['hz'])
    res = []
    for i in range(len(rec['top'])):
        x = dict(top=ll(rec['top'][i]), h=float(rec['hz'][i]), prom=float(prom[i]), border=bool(rec['border'][i]))
        if rec['col'][i] >= 0:
            x.update(col=ll(rec['col'][i]), col_h=float(rec['cz'][i]))
        if par[i] >= 0:
            x['parent'] = ll(par[i])
        if line[i] >= 0:
            x['line'] = ll(line[i])
        res.append(x)
    st = {k: v for k, v in stats.items() if k != 'tiles'}
    st.update(tops=sum(t['tops'] for t in stats['tiles']), ties=[[ll(a), ll(b)] for a, b in zip(ta, tb)],
              line_cutoff=line_cutoff, source='FABDEM V1-2')
    json.dump(dict(stats=st, summits=res), open(out, 'w'))
    print(f'-> {out}', flush=True)


def raster_reader(path):
    """Windows of one raster, no data and the sea (z <= 0, or the file's nodata) as no data."""
    import rasterio
    from rasterio.windows import Window

    def read(a, b, c, e):
        ds = _DS.get(os.getpid()) or _DS.setdefault(os.getpid(), rasterio.open(path))
        z = ds.read(1, window=Window(c, a, e - c, b - a), boundless=True, fill_value=ds.nodata or 0)
        z = z.astype(np.float32)
        z[~np.isfinite(z) | (z < -1000) | (z > 1e30)] = 0
        return z
    return read


def raster(path, out, crs=None, tile=5000, thr=THRESHOLD, jobs=10, line_cutoff=100.0):
    """
    Every top of P >= thr in one raster of any projection (`crs` overrides the file's own when
    its metadata is wrong), as `fabdem` writes it. Heights as the raster has them.
    """
    import rasterio
    from pyproj import Transformer
    d = rasterio.open(path)
    H, W = d.height, d.width
    back = Transformer.from_crs(crs or d.crs, 'EPSG:4326', always_xy=True)
    print(f'{H} × {W} cells ({H * W / 1e9:.2f} G) of {d.res[0]:g} m, tiles of {tile}, {jobs} workers', flush=True)
    t0 = time.time()
    rec, stats = run(raster_reader(path), H, W, tile, thr, log=lambda m: print(m, flush=True), jobs=jobs,
                     checkpoint=os.path.splitext(out)[0] + '.ckpt')
    par, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    line = walk_line(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], line_cutoff)
    print(f'{time.time() - t0:.0f} s: {len(rec["top"])} tops of P >= {thr:g}; {len(ta)} ties', flush=True)

    def ll(g):
        r, c = divmod(int(g), W)
        x, y = d.transform * (c + 0.5, r + 0.5)
        lo, la = back.transform(x, y)
        return [round(la, 7), round(lo, 7)]

    prom = np.where(rec['col'] >= 0, rec['hz'] - rec['cz'], rec['hz'])
    res = []
    for i in range(len(rec['top'])):
        x = dict(top=ll(rec['top'][i]), h=round(float(rec['hz'][i]), 2), prom=round(float(prom[i]), 2),
                 border=bool(rec['border'][i]))
        if rec['col'][i] >= 0:
            x.update(col=ll(rec['col'][i]), col_h=round(float(rec['cz'][i]), 2))
        if par[i] >= 0:
            x['parent'] = ll(par[i])
        if line[i] >= 0:
            x['line'] = ll(line[i])
        res.append(x)
    st = {k: v for k, v in stats.items() if k != 'tiles'}
    st.update(tops=sum(t['tops'] for t in stats['tiles']), ties=[[ll(a), ll(b)] for a, b in zip(ta, tb)],
              line_cutoff=line_cutoff, source=os.path.basename(path))
    json.dump(dict(stats=st, summits=res), open(out, 'w'))
    print(f'-> {out}', flush=True)


def dmr5(bbox, out, tile=5000, thr=THRESHOLD, jobs=10, line_cutoff=30.0):
    """
    Every top of P >= thr in DMR 5.0 (all of it, or the box S,N,W,E), with its key col, its
    parent, its line parent at `line_cutoff`, and whether its ground reaches the edge of the
    data (border or window). Heights as the raster has them (ETRS89-h); `catchments_db.py`
    converts them. The records themselves go to OUT.npz beside it, with what places them, so
    that another cutoff or walk needs no new flood.
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
    rec, stats = run(dmr5_reader(SOURCE, r0, c0), H, W, tile, thr, log=lambda s: print(s, flush=True), jobs=jobs,
                     checkpoint=os.path.splitext(out)[0] + '.ckpt')
    par, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    line = walk_line(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], line_cutoff)
    print(f'{time.time() - t0:.0f} s: {len(rec["top"])} tops of P >= {thr:g}; {len(ta)} ties', flush=True)
    np.savez_compressed(os.path.splitext(out)[0] + '.npz', **{k: v for k, v in rec.items() if isinstance(v, np.ndarray)},
                        r0=r0, c0=c0, H=H, W=W, transform=np.array(tuple(d.transform)[:6]), crs=str(d.crs),
                        threshold=thr)
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
        if line[i] >= 0:
            x['line'] = ll(line[i])
        res.append(x)
    st = {k: v for k, v in stats.items() if k != 'tiles'}
    st['line_cutoff'] = line_cutoff
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
    p = sub.add_parser('raster', help='one raster of any projection: every top of P >= threshold')
    p.add_argument('path')
    p.add_argument('--crs', help="the raster's real CRS where its metadata is wrong")
    p.add_argument('--tile', type=int, default=5000)
    p.add_argument('--threshold', type=float, default=THRESHOLD)
    p.add_argument('--jobs', type=int, default=10)
    p.add_argument('--line-cutoff', type=float, default=100.0)
    p.add_argument('--out', required=True)
    p = sub.add_parser('world', help='a box of the world grid (FABDEM, else Copernicus; whole degrees)')
    p.add_argument('--bbox', required=True, help='S,N,W,E in whole degrees (W may run past 180)')
    p.add_argument('--tile', type=int, default=5000)
    p.add_argument('--threshold', type=float, default=THRESHOLD)
    p.add_argument('--jobs', type=int, default=10)
    p.add_argument('--line-cutoff', type=float, default=600.0)
    p.add_argument('--burn', help='summits to burn in (burn_match.py output)')
    p.add_argument('--out', required=True)
    p = sub.add_parser('fabdem', help='a box of FABDEM (whole degrees): every top of P >= threshold')
    p.add_argument('--bbox', required=True, help='S,N,W,E in whole degrees')
    p.add_argument('--tile', type=int, default=5000)
    p.add_argument('--threshold', type=float, default=THRESHOLD)
    p.add_argument('--jobs', type=int, default=10)
    p.add_argument('--line-cutoff', type=float, default=100.0)
    p.add_argument('--out', required=True)
    p = sub.add_parser('dmr5', help='all of DMR 5.0, or a box of it: every top of P >= threshold')
    p.add_argument('--bbox', help='S,N,W,E (default: the whole raster)')
    p.add_argument('--tile', type=int, default=5000)
    p.add_argument('--threshold', type=float, default=THRESHOLD)
    p.add_argument('--jobs', type=int, default=10)
    p.add_argument('--line-cutoff', type=float, default=30.0,
                   help='prominence of the least summit a line parent may be (default 30: what the database holds)')
    p.add_argument('--out', required=True)
    a = ap.parse_args()
    if a.cmd == 'raster':
        raster(a.path, a.out, a.crs, a.tile, a.threshold, a.jobs, a.line_cutoff)
    if a.cmd == 'world':
        world([int(v) for v in a.bbox.split(',')], a.out, a.tile, a.threshold, a.jobs, a.line_cutoff, a.burn)
    if a.cmd == 'fabdem':
        fabdem([int(v) for v in a.bbox.split(',')], a.out, a.tile, a.threshold, a.jobs, a.line_cutoff)
    if a.cmd == 'dmr5':
        dmr5([float(v) for v in a.bbox.split(',')] if a.bbox else None, a.out, a.tile, a.threshold, a.jobs,
             a.line_cutoff)
    if a.cmd == 'window':
        window([float(v) for v in a.center.split(',')], a.size, a.tile, a.threshold, a.out, not a.no_check)


if __name__ == '__main__':
    main()
