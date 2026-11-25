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


class Peaks(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.out = []

    def node(self, n):
        kind = n.tags.get('natural')
        if kind not in KINDS or 'name' not in n.tags:
            return
        other = {t.k[5:]: t.v for t in n.tags if t.k.startswith('name:') and len(t.k) <= 8}
        self.out.append(dict(id=n.id, lat=n.location.lat, lon=n.location.lon, kind=kind,
                             name=n.tags['name'], other_names=other, ele=n.tags.get('ele')))


if __name__ == '__main__':
    h = Peaks()
    h.apply_file(sys.argv[1])
    json.dump(h.out, open(sys.argv[2], 'w'), ensure_ascii=False)
    print(len(h.out), 'named peaks ->', sys.argv[2])
