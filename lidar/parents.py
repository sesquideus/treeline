"""
TreeLine's prominence parent for the summits `prominence.py` found: the nearest peak with
greater prominence, walking up from the key col — not the highest peak beyond it, which is
what the flood hands out. On the lidar alone, from the pooled grid `prominence.py` cached.

    uv run --group lidar python lidar/parents.py [--min-prominence 100] [--out DIR]

The walk: across the summit's key col to the first summit up the ridge on the far side (by
steepest ascent), then across that summit's key col, and so on, until a summit more
prominent than the one the walk started from. Of the database's thousand-odd parents in
Slovakia it gives 97 %. Where it wanders off through the micro-relief of flat country and
comes out at the very top (Rozhľadňa's key col is in the East Slovak lowland), the nearest
summit to the key col that is higher and more prominent stands in.

Readings that fit the database worse, for the record (share of its parents they give):
the textbook one, walking the ridges from the key col to the top of the higher ground, 75 %;
the nearest higher, more prominent summit to the key col in a straight line, 73 %; the least
prominent summit whose side of its own key col the summit sits on, 52 %.

On the pooled grid two cols within a couple of metres can come out in the wrong order, and
then so does the parent: Veľké Stráne's two cols, 341.7 and 340.1 m, were 341.9 and 342.2
pooled, and it was hung off Šivetický hrad instead of Štyri chotáre. Where competing cols
are that close, a parent wants the 1 m data before it is trusted.
"""
import argparse
import csv
import os
import time

import numpy as np
import rasterio
from numba import njit
from pyproj import Transformer

from prominence import SOURCE, Grid, find, pool


@njit(cache=True)
def ascend(z, w, h, p):
    """Steepest ascent from `p` to a local maximum (a plateau's first cell will do)."""
    while True:
        y = p // w
        x = p - y * w
        best, top = -1, z[p]
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                yy = y + dy
                xx = x + dx
                if (dy == 0 and dx == 0) or yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if z[q] > top:
                    best, top = q, z[q]
        if best < 0:
            return p
        p = best


@njit(cache=True)
def climb(z, w, h, p, owner):
    """
    Steepest ascent from `p` to the summit it leads to, crossing flat ground on the way.

    Without the crossing, a climb stopped on any plateau — a reservoir's surface, a quarry
    floor — and a plateau cell that is not a summit belongs to whatever it had joined, by
    then often the island's top: Kečky's and Sestrč's first summit up the ridge came out as
    Gerlachovský štít. Now a plateau is searched for a way on up; one with none is itself a
    summit, and the climb ends at its first cell.
    """
    stack = np.empty(0, np.int64)
    while True:
        y = p // w
        x = p - y * w
        best, top = -1, z[p]
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                yy = y + dy
                xx = x + dx
                if (dy == 0 and dx == 0) or yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if z[q] > top:
                    best, top = q, z[q]
        if best >= 0:
            p = best
            continue
        # Flat: search the plateau for an edge that leads higher, or its summit.
        seen = {p: True}
        queue = [p]
        found = -1
        i = 0
        while i < len(queue) and found < 0:
            c = queue[i]
            i += 1
            cy = c // w
            cx = c - cy * w
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    yy = cy + dy
                    xx = cx + dx
                    if (dy == 0 and dx == 0) or yy < 0 or yy >= h or xx < 0 or xx >= w:
                        continue
                    q = yy * w + xx
                    if z[q] > z[p]:
                        found = q
                    elif z[q] == z[p] and q not in seen:
                        seen[q] = True
                        queue.append(q)
        if found >= 0:
            p = found
            continue
        for c in queue:
            if owner[c] == c:
                return c
        return p


@njit(cache=True)
def flood_divide(z, order, w, h):
    """
    The flood, recording the divide tree. Every merge is an edge between the two peaks a
    climb from the col reaches on either side (`d_u` on the dying side, `d_v` on the other),
    so that paths through the tree follow ridges. Per summit (by cell): its prominence (inf
    for what never dies) and `isle`, the top of the higher ground it joins at its key col.
    Per merge: the col, the dying summit, a cell beside the col on either side.
    """
    n = w * h
    parent = np.full(n, -1, np.int32)
    peak = np.full(n, -1, np.int32)
    owner = np.full(n, -1, np.int32)          # per cell: its component's summit when it joined
    prom = np.full(n, np.inf, np.float32)
    isle = np.full(n, -1, np.int32)
    cap = n // 8
    d_col = np.empty(cap, np.int32)
    d_peak = np.empty(cap, np.int32)
    d_sd = np.empty(cap, np.int32)
    d_sb = np.empty(cap, np.int32)
    d_u = np.empty(cap, np.int32)
    d_v = np.empty(cap, np.int32)
    c = 0
    roots = np.empty(8, np.int64)
    via = np.empty(8, np.int64)
    for k in range(n):
        p = order[k]
        if z[p] == -np.inf:
            break
        y = p // w
        x = p - y * w
        m = 0
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if parent[q] < 0:
                    continue
                r = find(parent, q)
                dup = False
                for j in range(m):
                    if roots[j] == r:
                        dup = True
                if not dup:
                    roots[m] = r
                    via[m] = q
                    m += 1
        if m == 0:
            parent[p] = p
            peak[p] = p
            owner[p] = p
            continue
        best = 0
        for j in range(1, m):
            if z[peak[roots[j]]] > z[peak[roots[best]]]:
                best = j
        b = roots[best]
        far = owner[climb(z, w, h, via[best], owner)]
        for j in range(m):
            r = roots[j]
            if r == b:
                continue
            d = peak[r]
            prom[d] = z[d] - z[p]
            isle[d] = peak[b]
            if c < cap:
                d_col[c] = p
                d_peak[c] = d
                d_sd[c] = via[j]
                d_sb[c] = via[best]
                d_u[c] = owner[climb(z, w, h, via[j], owner)]
                d_v[c] = far
                c += 1
            parent[r] = b
        parent[p] = b
        owner[p] = peak[b]
    return prom, isle, d_col[:c], d_peak[:c], d_sd[:c], d_sb[:c], d_u[:c], d_v[:c]


@njit(cache=True)
def root_tree(u, v, nodes, root):
    """Parent and depth of every node of the tree with edges (u, v), rooted at `root`."""
    m = len(nodes)
    ui = np.searchsorted(nodes, u)
    vi = np.searchsorted(nodes, v)
    deg = np.zeros(m + 1, np.int64)
    for i in range(len(ui)):
        deg[ui[i] + 1] += 1
        deg[vi[i] + 1] += 1
    start = np.cumsum(deg)
    fill = start[:-1].copy()
    adj = np.empty(2 * len(ui), np.int64)
    for i in range(len(ui)):
        adj[fill[ui[i]]] = vi[i]
        fill[ui[i]] += 1
        adj[fill[vi[i]]] = ui[i]
        fill[vi[i]] += 1
    eid = np.empty(2 * len(ui), np.int64)
    fill2 = start[:-1].copy()
    for i in range(len(ui)):
        eid[fill2[ui[i]]] = i
        fill2[ui[i]] += 1
        eid[fill2[vi[i]]] = i
        fill2[vi[i]] += 1
    up = np.full(m, -1, np.int64)
    up_edge = np.full(m, -1, np.int64)
    depth = np.full(m, -1, np.int64)
    queue = np.empty(m, np.int64)
    r = np.searchsorted(nodes, root)
    queue[0] = r
    depth[r] = 0
    head, tail = 0, 1
    while head < tail:
        a = queue[head]
        head += 1
        for k in range(start[a], start[a + 1]):
            b = adj[k]
            if depth[b] < 0:
                depth[b] = depth[a] + 1
                up[b] = a
                up_edge[b] = eid[k]
                queue[tail] = b
                tail += 1
    return up, depth, up_edge


@njit(cache=True)
def first_on_path(a, b, up, depth, nodes, prom, z, ps, zs, height_only):
    """
    Walking the tree path from node a to node b: the first node more prominent than `ps`
    (or, `height_only`, higher than `zs`), as a cell; b's cell if none before it.
    """
    left = [np.int64(0) for _ in range(0)]
    right = [np.int64(0) for _ in range(0)]
    while depth[a] > depth[b]:
        left.append(a)
        a = up[a]
    while depth[b] > depth[a]:
        right.append(b)
        b = up[b]
    while a != b:
        left.append(a)
        right.append(b)
        a = up[a]
        b = up[b]
    left.append(a)
    for i in range(len(right) - 1, -1, -1):
        left.append(right[i])
    for q in left:
        cell = nodes[q]
        if (z[cell] > zs) if height_only else (prom[cell] > ps):
            return cell
    return nodes[left[len(left) - 1]]


class DivideTree:
    """The flood's divide tree, and TreeLine's prominence parent read off it."""

    def __init__(self, flat, w, h):
        order = np.argsort(-flat, kind='stable').astype(np.int32)
        (self.prom, self.isle, self.col, self.peak, self.sd, self.sb,
         u, v) = flood_divide(flat, order, w, h)
        root = int(order[0])
        del order
        self.flat = flat
        self.nodes = np.unique(np.concatenate([u, v, [root]]))
        self.up, self.depth, self.up_edge = root_tree(u, v, self.nodes, root)
        self.far = dict(zip(self.peak.tolist(), v.tolist()))     # per dying summit
        self.edge_of = {int(p): i for i, p in enumerate(self.peak)}

    def parent(self, s, height_only=False):
        """
        TreeLine's prominence parent of summit `s` (with `height_only`, its line parent).

        The walk that matches the database's parents best (97 % of a thousand): across the
        key col to the first summit up the ridge, then across that one's key col, and so
        on, to the first summit more prominent than `s`. From a key col in flat country it
        can wander off through micro-relief and come out at the very top; then the nearest
        summit to the key col that is more prominent and higher stands in for it. (Walking
        the ridges to the top of the higher ground instead — the textbook reading — agrees
        with the database in only three cases out of four.)
        """
        s = int(s)
        p = self.far.get(s, -1)
        self.steps = 0
        for _ in range(1_000_000):
            if p < 0 or ((self.flat[p] > self.flat[s]) if height_only else (self.prom[p] > self.prom[s])):
                break
            p = self.far.get(p, -1)
            self.steps += 1
        return p



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', default=SOURCE)
    ap.add_argument('--min-prominence', type=float, default=100.0)
    ap.add_argument('--margin', type=float, default=20.0)
    ap.add_argument('--cell', type=int, default=10)
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    ap.add_argument('--out', default='.')
    args = ap.parse_args()

    top, at = pool(args.source, args.work, args.cell, 8, None)
    h, w = top.shape
    flat = np.ascontiguousarray(top).ravel()
    t = time.time()
    tree = DivideTree(flat, w, h)
    prom = tree.prom
    print(f'flooded in {time.time() - t:.0f}s, a tree of {len(tree.nodes)} peaks', flush=True)

    with rasterio.open(args.source) as d:
        grid = Grid(np.zeros((1, 1), np.float32), at, args.cell, 0, (0, 0), d.transform)
        grid.h, grid.w, grid.W = h, w, w
        grid.filled = np.zeros((h, w), np.bool_)
        to_ll = Transformer.from_crs(d.crs, 4326, always_xy=True)

        def where(index):
            r, c = grid.pixel(index)
            e, n = d.transform * (c + 0.5, r + 0.5)
            lon, lat = to_ll.transform(e, n)
            return round(lat, 6), round(lon, 6)

    keep = args.min_prominence - args.margin
    summits = np.flatnonzero((prom >= keep) & np.isfinite(prom))
    path = os.path.join(args.out, f'p{args.min_prominence:g}-parents.csv')
    with open(path, 'w', newline='') as f:
        out = csv.writer(f)
        out.writerow(['lat', 'lon', 'pooled_prominence', 'parent_lat', 'parent_lon',
                      'parent_pooled_prominence', 'line_parent_lat', 'line_parent_lon', 'walk_steps'])
        for s in summits:
            p = tree.parent(s)
            steps = tree.steps
            line = tree.parent(s, height_only=True)
            out.writerow([*where(s), round(float(prom[s]), 2),
                          *(where(p) if p >= 0 else (None, None)),
                          round(float(prom[p]), 2) if p >= 0 and np.isfinite(prom[p]) else 'top',
                          *(where(line) if line >= 0 else (None, None)), steps])
    print(f'{len(summits)} summits -> {path}')


if __name__ == '__main__':
    main()
