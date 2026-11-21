"""
The catchment engine against the flood in one piece (`tiled.flood_core`, `tiled.walk_parents`).

    uv run --group lidar python -m unittest lidar/test_catchments.py
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catchments import despike, reference, run, walk, walk_line  # noqa: E402


def noise(h, w, seed, scale=100.0, quantum=None):
    rng = np.random.default_rng(seed)
    z = rng.random((h, w)) * scale + 1
    if quantum:
        z = np.round(z / quantum) * quantum + 1
    return z.astype(np.float32)


def smooth_hills(h, w, seed, quantum=None):
    """Hills on a slope with rough ground on them: big tops, many small ones, long ridges."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    z = 50 + 0.2 * xx
    for _ in range(max(3, h * w // 400)):
        cy, cx = rng.random() * h, rng.random() * w
        a, s = rng.random() * 120, 3 + rng.random() * 12
        z += a * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
    z += rng.random((h, w)) * 6
    if quantum:
        z = np.round(z / quantum) * quantum
    return z.astype(np.float32)


def border_flags(z, rec):
    """By brute force: does a top's ground above its key col (all its land, an island's) reach the edge of the data?"""
    H, W = z.shape
    flat = z.ravel()
    out = []
    for top, col in zip(rec['top'].tolist(), rec['col'].tolist()):
        if col >= 0:
            cz = flat[col]
            ok = lambda q: flat[q] > cz or (flat[q] == cz and q < col)
        else:
            ok = lambda q: flat[q] > 0
        seen = {top}
        stack = [top]
        hit = False
        while stack:
            p = stack.pop()
            r, c = divmod(p, W)
            if r in (0, H - 1) or c in (0, W - 1) or any(
                    flat[rr * W + cc] <= 0 for rr in (r - 1, r, r + 1) for cc in (c - 1, c, c + 1)
                    if 0 <= rr < H and 0 <= cc < W):
                hit = True                      # the window's edge, or a hole in the data
                break
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    rr, cc = r + dy, c + dx
                    if 0 <= rr < H and 0 <= cc < W:
                        q = rr * W + cc
                        if q not in seen and ok(q):
                            seen.add(q)
                            stack.append(q)
        out.append(hit)
    return out


class DespikeTests(unittest.TestCase):
    def test_spikes_go_ground_stays(self):
        rng = np.random.default_rng(3)
        yy, xx = np.mgrid[0:40, 0:40]
        z = (300 + 40 * np.exp(-((yy - 12) ** 2 + (xx - 25) ** 2) / 60)        # a round top
             + 30 * np.exp(-((xx - 8) ** 2) / 2)                                # a sharp ridge
             + rng.random((40, 40)) * 0.5).astype(np.float32)                  # rough ground
        clean, n = despike(z)
        self.assertEqual(n, 0, 'hills, ridges and tops are not spikes')
        spiky = z.copy()
        spiky[32, 30] += 15                     # a lone spike
        spiky[20, 20] += 12                     # a pair
        spiky[20, 21] += 14
        out, n = despike(spiky)
        self.assertEqual(n, 3)
        self.assertTrue(np.all(out <= np.maximum(z, out)))
        for y, x in ((32, 30), (20, 20), (20, 21)):
            self.assertLess(out[y, x], z[y - 1:y + 2, x - 1:x + 2].max() + 0.01)


class EngineTests(unittest.TestCase):
    def check(self, z, tile, thr=10.0):
        H, W = z.shape
        rec, stats = run(lambda a, b, c, d: z[a:b, c:d], H, W, tile, thr)
        parents, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
        cutoffs = (thr, 3 * thr)
        ref = reference(z, thr, cutoffs)
        self.assertEqual(rec['top'].tolist(), ref['top'].tolist(), 'the same tops of P >= threshold')
        self.assertEqual(rec['col'].tolist(), ref['col'].tolist(), 'the same key cols')
        np.testing.assert_array_equal(rec['hz'], ref['hz'])
        np.testing.assert_array_equal(rec['cz'], ref['cz'])
        self.assertEqual(parents.tolist(), ref['parent'].tolist(), 'the same parents')
        for cutoff in cutoffs:
            line = walk_line(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], cutoff)
            self.assertEqual(line.tolist(), ref['line'][cutoff].tolist(), f'the same line parents at {cutoff}')
        if H * W * len(rec['top']) <= 3_000_000:
            self.assertEqual(rec['border'].tolist(), border_flags(z, rec), 'reaching the window\'s edge')
        return rec, stats, (ta, tb)

    def test_noise(self):
        for seed in range(4):
            z = noise(37, 41, seed)
            for tile in (41, 16, 9, 5, 2):
                with self.subTest(seed=seed, tile=tile):
                    self.check(z, tile)

    def test_quantised_noise_with_plateaus(self):
        for seed in range(4):
            z = noise(40, 40, seed, scale=60, quantum=7)
            for tile in (40, 10, 7, 3):
                with self.subTest(seed=seed, tile=tile):
                    self.check(z, tile)

    def test_hills(self):
        for seed in range(3):
            z = smooth_hills(64, 80, seed)
            for tile in (80, 32, 13, 8):
                with self.subTest(seed=seed, tile=tile):
                    self.check(z, tile)

    def test_hills_quantised(self):
        for seed in range(3):
            z = smooth_hills(60, 60, seed, quantum=2.0)
            for tile in (60, 16, 9):
                with self.subTest(seed=seed, tile=tile):
                    self.check(z, tile)

    def test_flat_plateau(self):
        z = smooth_hills(48, 48, 7)
        z[10:38, 12:40] = 150.0                   # one flat table across several tiles
        for tile in (48, 16, 6):
            with self.subTest(tile=tile):
                self.check(z, tile)

    def test_terraces(self):
        yy, xx = np.mgrid[0:50, 0:50]
        z = (np.floor((60 - np.hypot(yy - 25, xx - 25)) / 4) * 4 + 20).astype(np.float32)
        z += (noise(50, 50, 3, scale=3) > 3.5) * 11   # some small bumps on the steps
        for tile in (50, 12, 5):
            with self.subTest(tile=tile):
                self.check(z.astype(np.float32), tile)

    def test_ridge_along_a_seam(self):
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0:40, 0:48]
        z = 200 - np.abs(xx - 16) * 6 + rng.random((40, 48)) * 30   # the crest on column 16
        z = z.astype(np.float32)
        for tile in (16, 8, 4):                   # and the seam right on it
            with self.subTest(tile=tile):
                self.check(z, tile)

    def test_holes_in_the_data(self):
        z = smooth_hills(60, 60, 11)
        z[20:30, 5:50] = 0                        # a lake right across, and specks
        z[noise(60, 60, 12) > 98] = 0
        for tile in (60, 15, 7):
            with self.subTest(tile=tile):
                self.check(z, tile)

    def test_strips(self):
        z = smooth_hills(1, 300, 2)
        for tile in (300, 37, 4):
            with self.subTest(tile=tile):
                self.check(z, tile)
        z = smooth_hills(200, 3, 4)
        for tile in (200, 11, 2):
            with self.subTest(tile=tile):
                self.check(z, tile)

    def test_threshold(self):
        z = smooth_hills(64, 64, 9)
        for thr in (0.0, 1.0, 30.0, 100.0):
            with self.subTest(thr=thr):
                self.check(z, 13, thr)

    def test_ties_reported(self):
        # B 40 over a col of 20, C 50 over a col of 30: both P20, and B's walk passes C on to A
        x = np.arange(21)
        z = np.interp(x, [0, 2, 5, 8, 11, 14, 20], [5, 40, 20, 50, 30, 100, 90]).astype(np.float32)[None, :]
        for tile in (21, 6):
            with self.subTest(tile=tile):
                rec, _, (ta, tb) = self.check(z, tile, thr=1.0)
                self.assertEqual(list(zip(ta.tolist(), tb.tolist())), [(2, 8)])

    def test_pruning_shrinks_the_graph(self):
        z = smooth_hills(120, 120, 1)
        rec, stats, _ = self.check(z, 30)
        tops = sum(t['tops'] for t in stats['tiles'])
        self.assertLess(len(rec['top']), tops / 3)


if __name__ == '__main__':
    unittest.main()


class LineParentTests(unittest.TestCase):
    """
    `tiled.walk_line_parents` on synthetic ground, against what the definition says rather than
    how the walk goes: the line parent is higher, of at least the cutoff, and on the island the
    summit is cut off from when the water reaches its key col; with no cutoff it is the first
    higher summit, never further than the prominence parent.
    """

    def deaths(self, z2d):
        from tiled import flood_core
        h, w = z2d.shape
        z = np.ascontiguousarray(z2d, dtype=np.float32).ravel()
        order = np.argsort(-z, kind='stable')
        d_top, d_col, d_isle, d_far, *_ = flood_core(order, z, np.arange(len(z), dtype=np.int64),
                                                     np.zeros(len(z), np.bool_), np.empty(1, np.int64),
                                                     np.empty(1, np.int64), w, h)
        srt = np.argsort(d_top)
        d_top, d_col, d_isle, d_far = d_top[srt], d_col[srt], d_isle[srt], d_far[srt]
        hz = z[d_top]
        cz = np.where(d_col >= 0, z[np.maximum(d_col, 0)], np.nan).astype(np.float32)
        return z, d_top, d_col, d_isle, d_far, hz, cz

    def above(self, z, a, b):
        return z[a] > z[b] or (z[a] == z[b] and a < b)

    def island(self, z, w, h, start, col):
        """The cells connected to `start` through cells above `col` in the strict order."""
        seen, todo = {start}, [start]
        while todo:
            c = todo.pop()
            r, x = divmod(c, w)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    rr, xx = r + dy, x + dx
                    q = rr * w + xx
                    if 0 <= rr < h and 0 <= xx < w and q not in seen and self.above(z, q, col):
                        seen.add(q)
                        todo.append(q)
        return seen

    def test_the_definition_on_rough_ground(self):
        from tiled import _far, walk_line_parents, walk_parents
        for seed in range(6):
            for z2d in (noise(40, 50, seed), smooth_hills(60, 60, seed, quantum=5.0)):
                h, w = z2d.shape
                z, tops, col, isle, far, hz, cz = self.deaths(z2d)
                prom = np.where(col >= 0, hz - cz, hz)
                for cutoff in (0.0, 5.0, 20.0):
                    starts = tops[prom >= cutoff]
                    line = walk_line_parents(z.reshape(h, w), h, w, tops, col, isle, hz, cz, far, starts, cutoff)
                    parents = walk_parents(z.reshape(h, w), h, w, tops, col, isle, hz, cz, far, starts)
                    for s, l, p in zip(starts.tolist(), line.tolist(), parents.tolist()):
                        i = int(np.searchsorted(tops, s))
                        if col[i] < 0:
                            self.assertEqual(l, -1)        # the island high point: none
                            continue
                        with self.subTest(seed=seed, cutoff=cutoff, top=s):
                            self.assertGreaterEqual(l, 0)
                            j = int(np.searchsorted(tops, l))
                            self.assertTrue(self.above(z, l, s))
                            self.assertGreaterEqual(prom[j], cutoff)
                            self.assertIn(l, self.island(z, w, h, int(isle[i]), int(col[i])))
                            # The prominence parent is higher and of at least the cutoff
                            # too, so the walk meets the line parent first, or at it.
                            walked, q = [], s
                            while q != p and q >= 0 and len(walked) < 100000:
                                q = _far(z.reshape(h, w), h, w, tops, col, isle, cz, far, q)
                                walked.append(q)
                            self.assertIn(l, walked)


class CheckpointTests(unittest.TestCase):
    """A run cut off anywhere and started again from its checkpoint gives the same result."""

    class Cut(Exception):
        pass

    def plain(self, z, tile):
        H, W = z.shape
        rec, _ = run(lambda a, b, c, d: z[a:b, c:d], H, W, tile, 10.0)
        return rec

    def assertSame(self, a, b):
        for k in ('top', 'col', 'hz', 'cz', 'far', 'border'):
            np.testing.assert_array_equal(a[k], b[k], err_msg=k)

    def test_cut_after_any_tile(self):
        import tempfile
        z = noise(37, 41, 3)
        H, W = z.shape
        want = self.plain(z, 9)
        tiles = -(-H // 9) * -(-W // 9)
        for cut in range(1, tiles):
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as ckpt:
                calls = [0]

                def flaky(a, b, c, d):
                    calls[0] += 1
                    if calls[0] > cut:
                        raise self.Cut
                    return z[a:b, c:d]
                with self.assertRaises(self.Cut):
                    run(flaky, H, W, 9, 10.0, checkpoint=ckpt)
                rec, _ = run(lambda a, b, c, d: z[a:b, c:d], H, W, 9, 10.0, checkpoint=ckpt)
                self.assertSame(rec, want)

    def test_cut_during_any_write(self):
        import tempfile
        import catchments
        z = smooth_hills(48, 48, 2)
        H, W = z.shape
        want = self.plain(z, 8)
        real = catchments.Checkpoint.save
        for cut in range(1, 70):
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as ckpt:
                count = [0]

                def save(store, *a, **k):
                    count[0] += 1
                    if count[0] == cut:
                        raise self.Cut           # a power cut before this write lands
                    return real(store, *a, **k)
                catchments.Checkpoint.save = save
                try:
                    run(lambda a, b, c, d: z[a:b, c:d], H, W, 8, 10.0, checkpoint=ckpt)
                    cut_happened = False
                except self.Cut:
                    cut_happened = True
                finally:
                    catchments.Checkpoint.save = real
                if not cut_happened:
                    break
                rec, _ = run(lambda a, b, c, d: z[a:b, c:d], H, W, 8, 10.0, checkpoint=ckpt)
                self.assertSame(rec, want)

    def test_another_runs_directory_is_refused(self):
        import tempfile
        z = noise(20, 20, 1)
        with tempfile.TemporaryDirectory() as ckpt:
            run(lambda a, b, c, d: z[a:b, c:d], 20, 20, 7, 10.0, checkpoint=ckpt)
            with self.assertRaises(ValueError):
                run(lambda a, b, c, d: z[a:b, c:d], 20, 20, 5, 10.0, checkpoint=ckpt)
