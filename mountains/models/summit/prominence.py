"""
The bands summits are grouped into by prominence.

The cutoffs are the conventional ones — 1500 m is the ultra definition, then 600, 300 and
200, and below them 100 and 30, the last being the usual threshold for counting as an
independent summit at all. The map has painted summits by these bands for a while; this
module is the same vocabulary in Python, so the admin can *filter* by what the map *colours*
by and the two cannot quietly come to mean different things.

They are, unavoidably, two copies: the browser cannot import Python and the ramp in
`static/js/styles.js` has to be in JavaScript. `mountains/test_prominence_bands.py` closes
that by running the real `styles.js` and asserting band for band that the keys, the bounds
and the labels agree — so a threshold moved on one side fails the suite rather than
silently disagreeing with the other.

Only the thresholds live here. The colours do not: nothing server-side draws a summit, and a
hex value copied into Python would be a second copy with no reader.
"""

from dataclasses import dataclass

from django.db.models import Q


@dataclass(frozen=True)
class ProminenceBand:
    """
    One band: a key, and the half-open interval `[minimum, maximum)` of prominences in it.

    Half-open and not inclusive at both ends because the bands tile the range without gaps
    or overlap — a summit of exactly 600 m is `major`, matching `styles.js`, which walks the
    bands downwards and takes the first whose `min` the prominence reaches.

    `None` at either end means unbounded there. Both `None` is the unknown band, which is
    not an interval at all — see `PROMINENCE_UNKNOWN`.
    """

    key: str
    minimum: float | None
    maximum: float | None

    @property
    def label(self):
        """
        The band as the legend and the admin sidebar word it — generated, not stored, so a
        threshold cannot be changed without its wording following.
        """
        if self.minimum is None and self.maximum is None:
            return self.key
        if self.maximum is None:
            return f'{self.key} (≥ {self.minimum:g} m)'
        # The bottom band is written as a ceiling rather than `0–30`: its lower bound is
        # only there to keep a negative prominence — which is a data error — out of it.
        if not self.minimum:
            return f'{self.key} (< {self.maximum:g} m)'
        return f'{self.key} ({self.minimum:g}–{self.maximum:g} m)'

    def q(self, field='prominence'):
        """
        The band as a filter on a `prominence` annotation.

        The unknown band is the complement of the others and is written as such: no
        prominence recorded, or one that cannot be read as a height — which is how
        `styles.js` reads it too (`!(prominence >= 0)`), and why a negative value shows up
        here rather than among the subsidiary summits where a plain `< 30` would put it.
        """
        if self.minimum is None and self.maximum is None:
            return Q(**{f'{field}__isnull': True}) | Q(**{f'{field}__lt': 0})
        bounds = Q()
        if self.minimum is not None:
            bounds &= Q(**{f'{field}__gte': self.minimum})
        if self.maximum is not None:
            bounds &= Q(**{f'{field}__lt': self.maximum})
        return bounds


#: Descending, the order the legend and the sidebar list them in.
PROMINENCE_BANDS = (
    ProminenceBand('ultra', 1500, None),
    ProminenceBand('major', 600, 1500),
    ProminenceBand('notable', 300, 600),
    ProminenceBand('modest', 200, 300),
    ProminenceBand('minor', 100, 200),
    ProminenceBand('small', 30, 100),
    ProminenceBand('subsidiary', 0, 30),
)

#: Not a band but an absence: a summit whose key col is unrecorded has no prominence to
#: place. Kept apart from the seven so that `PROMINENCE_BANDS` stays a clean partition of
#: the numbers, and listed after them wherever both are offered.
PROMINENCE_UNKNOWN = ProminenceBand('unknown', None, None)

#: Every band a lookup can name, keyed the way a query string spells it.
PROMINENCE_BANDS_BY_KEY = {band.key: band for band in (*PROMINENCE_BANDS, PROMINENCE_UNKNOWN)}
