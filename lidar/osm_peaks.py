"""
Named peaks from an OpenStreetMap extract, for naming lidar tops: every node tagged natural=peak,
rock, stone, saddle, volcano or hill that has a name, with its other-language names and ele.

    cd lidar && uv run --project .. --group lidar python osm_peaks.py EXTRACT.osm.pbf OUT.json

The same shape as ~/.cache/treeline/lidar/osm/peaks.json (Slovakia): a list of
{id, lat, lon, kind, name, other_names: {lang: name}, ele}.
"""
import json
import sys

import osmium

KINDS = {'peak', 'rock', 'stone', 'saddle', 'volcano', 'hill'}


def peaks(src):
    """ The named peaks of an extract; the tag filter runs in osmium's C++, so the planet's ten
    billion nodes never reach Python. """
    out = []
    flt = osmium.filter.TagFilter(*[('natural', k) for k in sorted(KINDS)])
    for n in osmium.FileProcessor(src, osmium.osm.NODE).with_filter(flt):
        if 'name' not in n.tags:
            continue
        other = {t.k[5:]: t.v for t in n.tags if t.k.startswith('name:') and len(t.k) <= 8}
        out.append(dict(id=n.id, lat=n.location.lat, lon=n.location.lon, kind=n.tags['natural'],
                        name=n.tags['name'], other_names=other, ele=n.tags.get('ele')))
    return out


if __name__ == '__main__':
    out = peaks(sys.argv[1])
    json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False)
    print(len(out), 'named peaks ->', sys.argv[2])
