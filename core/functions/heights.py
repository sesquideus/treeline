"""
Altitudes between vertical datums, and into EGM2008 above all.

TreeLine's canonical system is EGM2008 orthometric height (EPSG:3855): global, so the Tatras
and the Andes sit in one system, and what a GPS shows. Local data stays as entered; this is
how it is carried over.

A conversion is a PROJ pipeline from the datum's compound CRS to WGS 84 + EGM2008 height:
local height → ellipsoid through the national grid (GKÚ for Slovakia, ČÚZK for Czechia,
GUGiK for Poland), ellipsoid → EGM2008 through the NGA grid. PROJ picks the grid for each
point by where it is.

The one trap: with a grid missing, or a point outside every grid, PROJ falls back to a
"ballpark" vertical transformation that returns the height unchanged — a conversion that
looks like it worked. `allow_ballpark=False` plus `errcheck=True` make that an error. The
grids are not shipped with pyproj; see CLAUDE.md for fetching them.
"""
import threading

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError

TARGET = 'EPSG:4326+3855'          # WGS 84 + EGM2008 height
TARGET_CODE = 'EGM2008'

_local = threading.local()          # pyproj transformers must not be shared between threads


class HeightConversionError(ValueError):
    pass


def transformer(crs: str) -> Transformer | None:
    """ The cached transformer from `crs` to EGM2008, or None when `crs` already is it. """
    cache = _local.__dict__.setdefault('transformers', {})
    if crs not in cache:
        source = CRS(crs)
        cache[crs] = None if source == CRS(TARGET) else Transformer.from_crs(
            source, CRS(TARGET), always_xy=True, allow_ballpark=False)
    return cache[crs]


def to_egm2008(longitude: float, latitude: float, altitude: float, crs: str) -> float:
    """
    `altitude`, in the datum whose PROJ CRS is `crs`, as an EGM2008 height.
    Raises `HeightConversionError` rather than ever returning an unconverted number.
    """
    t = transformer(crs)
    if t is None:
        return altitude
    try:
        _, _, height = t.transform(longitude, latitude, altitude, errcheck=True)
    except ProjError as error:
        raise HeightConversionError(
            f'{altitude} m at {latitude:.5f}, {longitude:.5f} from {crs}: {error}') from error
    return height


def operation(longitude: float, latitude: float, crs: str) -> str:
    """ Which PROJ operation converts a height here: for tests and diagnostics. """
    t = transformer(crs)
    if t is None:
        return 'identity'
    t.transform(longitude, latitude, 0.0, errcheck=True)
    return t.get_last_used_operation().description
