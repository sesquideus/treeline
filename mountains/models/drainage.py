"""
Where a drop falling on either side of a col ends up, and where the two waters meet again.

Three fields on `Col` carry it, in the order the catalogue refines them:

- `confluence_river` — the primary fact: the waters of the two sides meet at this river's
  mouth, where it joins its parent. Set only when that junction is a catalogued mouth.
- `river_a`, `river_b` — optional refinements: on each side, the most minor catalogued river
  the water reaches. Symmetric; the same river twice means the col is a spur inside that
  river's basin and the waters meet somewhere on it. Cataloguing a minor creek later moves a
  side further upstream and lengthens its chain at the top; nothing below changes.

Each side's lineage is its chain of `parent`s. With a confluence X → P, the side whose chain
passes X is the *tributary side*, cut at X; the other is the *main side*, cut at P, and must
join P above X's mouth. Without a confluence, both chains run to their first common river.

The river tree has one root (the ocean). Any other river without a parent is a fragment not
yet connected downstream: a legitimate gap, never an error. So the checks report three kinds
of finding — `error`, a contradiction the data proves; `unverifiable`, a rule that cannot be
checked yet (a gap, a missing altitude); `hint`, something that could be filled in.
"""
from dataclasses import dataclass, field

from django.apps import apps

ERROR, UNVERIFIABLE, HINT = 'error', 'unverifiable', 'hint'


@dataclass
class Finding:
    level: str
    message: str
    field: str | None = None        # the Col field it concerns, for `clean()`


@dataclass
class Side:
    start: int                      # river pk
    chain: list                     # river pks, start first, down to `end`
    end: str                        # 'target': reached the meeting river; 'gap'; 'sea'
    role: str | None = None         # 'tributary' or 'main' when there is a confluence


@dataclass
class Drainage:
    sides: list = field(default_factory=list)
    meet: str | None = None         # 'mouth' (of `meet_river` into `meet_into`), 'on', 'unknown'
    meet_river: int | None = None
    meet_into: int | None = None
    findings: list = field(default_factory=list)

    def errors(self):
        return [f for f in self.findings if f.level == ERROR]


class RiverIndex:
    """
    The whole river tree as plain values, from one query: parent, name, mouth point and its
    altitude per river, and which parentless river is the root — the one with the most
    rivers flowing into it; every other parentless river is a fragment.
    """

    def __init__(self, rows=None):
        if rows is None:
            River = apps.get_model('mountains', 'River')
            rows = River.objects.values_list('pk', 'parent_id', 'source__name', 'mouth_id',
                                             'mouth__altitude')
        self.parent, self.name, self.mouth, self.altitude = {}, {}, {}, {}
        for pk, parent, name, mouth, altitude in rows:
            self.parent[pk] = parent
            self.name[pk] = name or f'river #{pk}'
            self.mouth[pk] = mouth
            self.altitude[pk] = altitude
        sizes = {}
        for pk in self.parent:
            chain = self.chain(pk)
            sizes[chain[-1]] = sizes.get(chain[-1], 0) + 1
        self.root = max(sizes, key=sizes.get) if sizes else None

    def chain(self, pk):
        """ `pk` and every river below it, down to one without a parent. Stops on a cycle. """
        chain, seen = [], set()
        while pk is not None and pk not in seen:
            chain.append(pk)
            seen.add(pk)
            pk = self.parent.get(pk)
        return chain

    def end(self, chain):
        return 'sea' if chain and chain[-1] == self.root else 'gap'


def analyse(col, index=None):
    """
    The drainage of `col` from its three fields. The river tree (`index`, one query) is read
    only when a side is set: a col with just its confluence needs nothing beyond that river
    and its parent, which a page has usually loaded already.
    """
    a, b, x = col.river_a_id, col.river_b_id, col.confluence_river_id
    result = Drainage()
    if a is None and b is None:
        if x is not None:
            confluence = col.confluence_river
            result.meet, result.meet_river, result.meet_into = 'mouth', x, confluence.parent_id
            if confluence.parent_id is None:
                result.findings.append(Finding(UNVERIFIABLE,
                    f'{confluence} is not yet connected downstream', 'confluence_river'))
        return result

    index = index or RiverIndex()

    if x is not None:
        _with_confluence(result, index, a, b, x)
    else:
        _without_confluence(result, index, a, b)
    return result


def _cut(chain, target):
    return chain[:chain.index(target) + 1]


def _with_confluence(result, index, a, b, x):
    n = index.name
    p = index.parent.get(x)
    result.meet, result.meet_river, result.meet_into = 'mouth', x, p
    if p is None:
        result.findings.append(Finding(UNVERIFIABLE,
            f'{n[x]} is not yet connected downstream, so the main side cannot be checked',
            'confluence_river'))

    if a is not None and a == b:
        result.findings.append(Finding(ERROR,
            f'Both sides reach {n[a]}, so their waters meet on it, not at the mouth of {n[x]}: '
            f'either the confluence is empty or a side is not refined far enough',
            'confluence_river'))
        result.sides = [Side(a, index.chain(a), index.end(index.chain(a)))]
        return

    sides = []
    for pk, name in ((a, 'river_a'), (b, 'river_b')):
        if pk is not None:
            sides.append((pk, name, index.chain(pk)))
    through = [s for s in sides if x in s[2]]
    if len(through) == 2:
        result.findings.append(Finding(ERROR,
            f'Both sides drain through {n[x]} ({n[through[0][0]]}, {n[through[1][0]]}), so their '
            f'waters meet above its mouth and {n[x]} is not the confluence', 'confluence_river'))

    for pk, name, chain in sides:
        if x in chain:
            # With both sides through X (reported above) neither is the tributary side.
            role = 'tributary' if len(through) < 2 else None
            result.sides.append(Side(pk, _cut(chain, x), 'target', role))
        elif p is not None and p in chain:
            side = Side(pk, _cut(chain, p), 'target', 'main')
            result.sides.append(side)
            _check_joins_above(result, index, side, x, p, name)
        elif index.end(chain) == 'sea':
            result.sides.append(Side(pk, chain, 'sea'))
            result.findings.append(Finding(ERROR,
                f'{n[pk]} reaches the sea without passing {n[x]}'
                + (f' or {n[p]}' if p is not None else '')
                + f', so this side does not drain to the confluence', name))
        else:
            result.sides.append(Side(pk, chain, 'gap'))
            result.findings.append(Finding(UNVERIFIABLE,
                f'{n[pk]} is connected only as far as {n[chain[-1]]}, so whether it drains to '
                f'{n[x]} or {n[p] if p is not None else "its parent"} cannot be checked yet', name))
    # The tributary side first.
    result.sides.sort(key=lambda side: side.role != 'tributary')


def _check_joins_above(result, index, side, x, p, field_name):
    """ The main side has to reach P above X's mouth, or its water would meet X's lower down. """
    n = index.name
    if len(side.chain) < 2:
        return                     # it is P itself: enters somewhere uncatalogued, nothing to check
    child = side.chain[-2]
    if index.mouth[child] is not None and index.mouth[child] == index.mouth[x]:
        return                     # the very same junction
    here, there = index.altitude[child], index.altitude[x]
    if here is None or there is None:
        result.findings.append(Finding(UNVERIFIABLE,
            f'Cannot check that {n[child]} joins {n[p]} above the mouth of {n[x]}: '
            f'a mouth altitude is missing', field_name))
    elif here <= there:
        result.findings.append(Finding(ERROR,
            f'{n[child]} joins {n[p]} at {here:.1f} m, not above the mouth of {n[x]} '
            f'({there:.1f} m), so the waters meet at its mouth instead', field_name))


def _without_confluence(result, index, a, b):
    n = index.name
    if a is None and b is None:
        return
    if a is None or b is None or a == b:
        pk = a if a is not None else b
        chain = index.chain(pk)
        if a == b:
            result.meet, result.meet_river = 'on', a
            result.sides = [Side(a, [a], 'target')]
        else:
            result.sides = [Side(pk, chain, index.end(chain))]
        return

    ca, cb = index.chain(a), index.chain(b)
    common = next((pk for pk in ca if pk in cb), None)
    if common is None:
        result.meet = 'unknown'
        result.sides = [Side(a, ca, index.end(ca)), Side(b, cb, index.end(cb))]
        result.findings.append(Finding(UNVERIFIABLE,
            f'The two sides ({n[a]}, {n[b]}) are not yet connected to a common river, so '
            f'where their waters meet is unknown'))
        return

    result.sides = [Side(a, _cut(ca, common), 'target'), Side(b, _cut(cb, common), 'target')]
    result.meet, result.meet_river = 'on', common
    ia, ib = ca.index(common), cb.index(common)
    if ia and ib:
        # Both arrive through catalogued mouths: the waters meet at the lower of the two,
        # which is a catalogued junction — the confluence is known and could be filled in.
        first, second = ca[ia - 1], cb[ib - 1]
        lower = _lower_mouth(index, first, second)
        if index.mouth[first] is not None and index.mouth[first] == index.mouth[second]:
            result.findings.append(Finding(HINT,
                f'{n[first]} and {n[second]} join {n[common]} at the same junction: the waters '
                f'meet there, and either is the confluence river', 'confluence_river'))
        elif lower is not None:
            result.findings.append(Finding(HINT,
                f'The waters meet at the mouth of {n[lower]} into {n[common]}: that is the '
                f'confluence river', 'confluence_river'))
        else:
            result.findings.append(Finding(HINT,
                f'The waters meet at the mouth of {n[first]} or of {n[second]} into '
                f'{n[common]}, whichever is lower (a mouth altitude is missing): that is the '
                f'confluence river', 'confluence_river'))


def _lower_mouth(index, first, second):
    one, other = index.altitude[first], index.altitude[second]
    if one is None or other is None or one == other:
        return None
    return first if one < other else second
