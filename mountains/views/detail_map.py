"""
The maps on the summit, col and river pages: one FeatureCollection each, embedded in the
page and drawn by `initDetailMap()` in static/js/summit-detail-map.js.

**What a feature looks like.** Every summit, col and river on these maps that the global map
also draws is serialized the way the global map serializes it — by the model's own
`to_geojson()`, with `type` 'summit', 'col' or 'river' — so it carries the same properties,
is drawn by the same style and opens the same popup. A key col here is the global map's key
col. Only the relations the global map has no mark for get one of their own: the lit
parent, the children, an isolation point, the landmarks of a river. Those carry just a
`label` and a `url` for their popup.

**What the map is fitted to.** A feature that must stay in view carries `frame: true`, and
`fitToFramed()` (static/js/map.js) zooms to those alone: the object, and what it is
directly related to. Context — an isolation circle hundreds of kilometres across, the river
a col drains into — is drawn but never framed, or it would shrink everything that matters
to one dot.
"""

from django.db.models import Prefetch
from django.urls import reverse

from core.functions.world import distance
from mountains.models import Summit

SUMMIT_MARK = '⛰'
PARENT_MARK = '⊃'
CHILD_MARK = '⊂'               # subset of: the inverse of the parent's ⊃
ISOLATION_MARK = '⇥'
ENCIRCLEMENT_MARK = '◎'
SOURCE_SUMMIT_MARK = '↑'       # the peak up-slope from the source
WATERSHED_MARK = '⩓'           # the highest point of the basin


# ---------------------------------------------------------------- building blocks

def present(geometry):
    """
    Whether a geometry is there to draw. Not `is not None`: the database also holds empty
    points (`POINT EMPTY`), which GeoDjango reads as a falsy Point with no coordinates —
    a dozen summits have one for a nearest higher point, and reading `.x` off it raises.
    """
    return bool(geometry)


def has_location(point):
    return point is not None and present(point.location)


def coords(location):
    """GeoJSON order: longitude first. Takes a `Point` geometry, not a NamedPoint."""
    return [location.x, location.y]


def point_label(glyph, point):
    return f'{glyph} {point.display_name()} ({point.altitude:.1f} m)'


def framed(feature, frame=True):
    """`feature`, marked to be kept in view (or not); None passes through."""
    if feature is not None:
        feature['properties']['frame'] = frame
    return feature


def mark(location, kind, label, url=None, frame=True, **properties):
    """A point for a relation the global map has no mark for: a label, a link, nothing more."""
    if not present(location):
        return None
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': coords(location)},
        'properties': {'type': kind, 'label': label, 'url': url, 'frame': frame,
                       **properties},
    }


def summit_mark(summit, kind, glyph, frame=True):
    if summit is None or not has_location(summit.point):
        return None
    return mark(summit.point.location, kind, point_label(glyph, summit.point),
                summit.get_absolute_url(), frame=frame, prom=summit.compute_prominence())


def line(kind, points, frame=False):
    """A line through the NamedPoints given, skipping any without a position."""
    path = [coords(p.location) for p in points if has_location(p)]
    if len(path) < 2:
        return None
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': path},
        'properties': {'type': kind, 'frame': frame},
    }


def collection(features):
    return {'type': 'FeatureCollection', 'features': [f for f in features if f is not None]}


# ---------------------------------------------------------------- as the global map has them

def summit_feature(summit, kind='summit', frame=True):
    """
    A summit with everything the global map's popup shows: the skeleton's properties and
    the viewport detail merged onto them, as the global map merges them once a viewport's
    detail arrives. `kind` is the mark it is drawn with — the popup is the same for any of
    them (`popupHtml()` in static/js/map.js). Read from `with_popup()`'s annotations.
    """
    feature = summit.to_geojson()
    if feature is not None:
        feature['properties'].update(summit.to_detail_dict(), type=kind)
    return framed(feature, frame)


def col_feature(col, key_for=None, frame=True):
    """
    A col as the global map draws it: the col marker, the col popup, and the line down to
    its confluence on hover.

    `Col.to_dict()` reads the summit it is the key col for through the reverse one-to-one,
    with that summit's prominence and parent; handing over the summit in hand primes the
    cache and saves a query per col.
    """
    if col is None or not has_location(col.point):
        return None
    if key_for is not None:
        col.key_for = key_for
    return framed(col.to_geojson(), frame)


def river_feature(river, frame=True, role=None):
    """
    A river's course as the global map draws it. `role` tells the style which of a
    confluence's two rivers it is, on a col's map; the river's own page has none.
    """
    if river is None or river.source is None:
        return None
    feature = river.to_geojson()
    if feature is not None and role is not None:
        feature['properties']['role'] = role
    return framed(feature, frame)


# ---------------------------------------------------------------- one summit

def isolation_features(summit):
    """
    The nearest higher point, the line to it, the isolation circle through it, and — if the
    point is attributed to a summit — that summit, the nearest higher neighbour, and the
    line on to it. Drawn, not framed: the circle alone can be hundreds of kilometres across.
    """
    # Imported here: the module defining it imports this one.
    from mountains.views.summit.json import isolation_circle

    here = summit.point.location
    nearest = summit.nearest_higher_point if present(summit.nearest_higher_point) else None
    parent = summit.isolation_parent
    if nearest is not None:
        radius = distance((here.y, here.x), (nearest.y, nearest.x))
        of = f' {parent.point.display_name()}' if parent is not None else ''
        yield mark(nearest, 'isolation_point',
                   f'{ISOLATION_MARK} {summit.isolation_name}{of} ({radius.km:.3f} km)',
                   frame=False)
        yield {'type': 'Feature',
               'geometry': {'type': 'LineString', 'coordinates': [coords(nearest), coords(here)]},
               'properties': {'type': 'isolation_line_first'}}
        yield {'type': 'Feature',
               'geometry': isolation_circle(here.y, here.x, radius.m),
               'properties': {'type': 'isolation_circle',
                              'name': f'Isolation radius: {radius.km:.3f} km'}}
    if parent is not None and has_location(parent.point):
        there = parent.point.location
        # The nearest higher neighbour is a peak like any other, with the popup any summit
        # has — so it is loaded the way the popup needs it, which the summit's own join to
        # it does not.
        neighbour = (Summit.objects.with_popup().prefetch_related(*COL_RELATIONS)
                     .filter(pk=parent.pk).first())
        yield summit_feature(neighbour, 'isolation_parent', frame=False)
        # The second leg starts from the nearest higher point; without one there is none.
        if nearest is not None:
            yield {'type': 'Feature',
                   'geometry': {'type': 'LineString',
                                'coordinates': [coords(nearest), coords(there)]},
                   'properties': {'type': 'isolation_line_second'}}


def encirclement_features(summit):
    """
    The first summit up the prominence chain whose key col is lower than this one's, and
    a dashed line to it. When that is the prominence parent itself — as it often is — only
    the line: a second marker on the same spot would cover the lit parent and take its
    clicks.
    """
    parent = summit.compute_encirclement_parent()
    if parent is None:
        return
    if parent.pk != summit.prominence_parent_id:
        yield summit_mark(parent, 'encirclement_parent', ENCIRCLEMENT_MARK, frame=False)
    yield line('encirclement_line', [summit.point, parent.point])


def child_features(summit):
    """
    Every summit whose prominence parent is `summit`: its mark, its key col as the global
    map draws it, and the climb from it through that col up to `summit` — the summit's own
    prominence line one level down, straightened by the same checkbox.
    """
    for child in summit.prominence_children.all():
        if not has_location(child.point):
            continue
        child.prominence_parent = summit        # what Col.to_dict() reads as `major`
        yield summit_feature(child, 'prominence_child')
        yield col_feature(child.key_col, child)
        col = child.key_col.point if child.key_col else None
        yield line('child_line', [child.point, col, summit.point])


def summit_features(summit):
    """Everything on a summit's map, as a list of features."""
    parent = summit.prominence_parent
    col = summit.key_col
    own = summit_feature(summit)
    if own is not None:
        # The one the page is about: drawn above everything, so it can always be reached
        # however crowded the view it is fitted to — see `Z_SUBJECT` in styles.js.
        own['properties']['subject'] = True
    features = [
        own,
        *isolation_features(summit),
        summit_mark(parent, 'lit_parent', PARENT_MARK),
        col_feature(col, summit),
    ]
    if parent is not None and col is not None:
        features.append(line('prominence_line', [summit.point, col.point, parent.point]))
    features.extend(encirclement_features(summit))
    features.extend(child_features(summit))
    return [f for f in features if f is not None]


#: What a key col's popup reads beyond the col itself, loaded through the key col that
#: `with_prominence()` already prefetches — so the summit's col and every child's cost a
#: fixed number of queries, however many children there are.
COL_RELATIONS = ('key_col__point__countries', 'key_col__confluence_river__source',
                 'key_col__confluence_river__mouth', 'key_col__confluence_river__parent__source')


def summit_map_queryset():
    """Summits with everything `summit_features()` reads already loaded."""
    return Summit.objects.with_popup().prefetch_related(
        *COL_RELATIONS,
        Prefetch('prominence_children',
                 queryset=Summit.objects.with_popup().prefetch_related(*COL_RELATIONS)),
    )


# ---------------------------------------------------------------- one col, one river

def col_features(col):
    """
    The col; both slopes that meet at it — the summit it is the key col for and that
    summit's parent — and the climb from one to the other through it. Also the two rivers
    meeting below it: the minor one, whose mouth is the col's confluence, and the major one
    it flows into. Drawn, not framed.
    """
    lower = getattr(col, 'key_for', None)
    upper = lower.prominence_parent if lower else None
    features = [col_feature(col, lower)]
    if lower is not None:
        features.append(summit_mark(lower, 'key_for_summit', SUMMIT_MARK))
        if upper is not None:
            features += [summit_mark(upper, 'lit_parent', PARENT_MARK),
                         line('prominence_line', [lower.point, col.point, upper.point])]
    minor = col.confluence_river
    features += [river_feature(minor, frame=False, role='minor'),
                 river_feature(minor.parent if minor else None, frame=False, role='major')]
    return collection(features)


def river_features(river):
    """
    The river from source to mouth through every junction on the way, and the two summits
    it is named against: the one up-slope from its source and the highest point of the
    basin. A river with only a source to its name is shown as that point.
    """
    course = river_feature(river)
    features = [course]
    if course is None and has_location(river.source):
        features.append(mark(river.source.location, 'river_source', f'≋ {river}'))
    features.append(summit_mark(river.source_summit, 'source_summit', SOURCE_SUMMIT_MARK))
    point = river.watershed_high_point
    if has_location(point):
        summit = getattr(point, 'summit', None)
        url = (summit.get_absolute_url() if summit
               else reverse('point-detail', kwargs={'pk': point.pk}))
        features.append(mark(point.location, 'watershed_high_point',
                             point_label(WATERSHED_MARK, point), url))
    return collection(features)
