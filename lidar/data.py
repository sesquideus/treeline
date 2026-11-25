"""
Where the lidar tooling finds its data: one root folder, `TREELINE_DATA` (default /mnt/data/treeline),
and every path below it. The terrain models as integer centimetres sit in ROOT/intcm/<country>/,
FABDEM, Copernicus, the OSM extracts, the run store and the changesets beside it. Reorganising the
disk means changing these names (or setting TREELINE_DATA), not the tools.

A tool's own variable (TREELINE_DMR5, TREELINE_STORE, …) still overrides its one path:
`path('intcm', 'SK', 'dmr5_etrs89.tif', env='TREELINE_DMR5')`.
"""
import os

ROOT = os.environ.get('TREELINE_DATA', '/mnt/data/treeline')


def path(*parts, env=None):
    """ ROOT/parts…, or the variable `env` where it is set. """
    if env and os.environ.get(env):
        return os.environ[env]
    return os.path.join(ROOT, *parts)
