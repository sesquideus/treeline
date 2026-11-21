"""
A geoid grid in the ISG format (isgeoid.polimi.it) as a PROJ grid, for height systems PROJ ships
no grid for: Taiwan's TWVD 2001 through the hybrid geoid TWGEOID18h, say.

    manage.py install_isg_grid https://isgeoid.polimi.it/Geoid/Asia/Taiwan/public/Taiwan_2018_TWGEOID2018h_gravG_20201201.isg tw_isg_twgeoid18h

Writes NAME.gtx into PROJ's user data directory (~/.local/share/proj), where the other grids
are; a VerticalDatum then names it in its CRS (`+geoidgrids=NAME.gtx`). GTX needs nothing but
numpy, so the server needs no GDAL tooling for it.
"""
import re
import struct
import urllib.request
from pathlib import Path

import numpy as np
from django.core.management.base import BaseCommand, CommandError
from pyproj.datadir import get_user_data_dir

AGENT = 'treeline-client/0.1'


def degrees(text):
    """ An ISG header angle: decimal degrees (`21.0`), or any of `21°00'00"`, `30'`, `30"`. """
    text = text.strip()
    m = re.fullmatch(r'''(-)?\s*(?:([\d.]+)°)?\s*(?:([\d.]+)')?\s*(?:([\d.]+)")?''', text)
    if not m or not any(m.groups()[1:]):
        return float(text)
    d, mi, s = (float(x) if x else 0.0 for x in m.groups()[1:])
    return (-1 if m.group(1) else 1) * (d + mi / 60 + s / 3600)


def parse_isg(text):
    """ (header dict, grid N-to-S as float32) from an ISG 1.0/2.0 grid file. """
    head, _, body = text.partition('end_of_head')
    head = head.split('begin_of_head')[-1]
    h = {}
    for line in head.splitlines():
        for sep in (':', '='):
            if sep in line:
                k, v = line.split(sep, 1)
                h[k.strip()] = v.strip()
                break
    if h.get('data format', 'grid') != 'grid' or h.get('data ordering', 'N-to-S, W-to-E') != 'N-to-S, W-to-E':
        raise CommandError(f'not a N-to-S, W-to-E grid: {h.get("data format")}, {h.get("data ordering")}')
    if h.get('data units', 'meters') != 'meters':
        raise CommandError(f'units {h["data units"]}, not metres')
    rows, cols = int(h['nrows']), int(h['ncols'])
    z = np.array(body.split('\n', 1)[1].split(), dtype=np.float32)
    if z.size != rows * cols:
        raise CommandError(f'{z.size} values for {rows} × {cols}')
    return h, z.reshape(rows, cols)


def gtx(h, z):
    """
    The GTX bytes: big-endian origin (the south-west node) and steps in degrees, rows and
    columns, then the rows south to north. No data is -88.8888 there.
    """
    nodata = float(h.get('nodata', -9999))
    z = np.where(z == nodata, np.float32(-88.8888), z)[::-1]
    lat0, lon0 = degrees(h['lat min']), degrees(h['lon min'])
    dlat, dlon = degrees(h['delta lat']), degrees(h['delta lon'])
    return struct.pack('>4d2i', lat0, lon0, dlat, dlon, *z.shape) + z.astype('>f4').tobytes()


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('source', help='URL or path of the .isg file')
        parser.add_argument('name', help='the grid name, without .gtx')

    def handle(self, source, name, **options):
        if source.startswith(('http://', 'https://')):
            req = urllib.request.Request(source, headers={'User-Agent': AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                text = r.read().decode('utf-8', errors='replace')
        else:
            text = Path(source).read_text(encoding='utf-8', errors='replace')
        h, z = parse_isg(text)
        out = Path(get_user_data_dir(True)) / f'{name}.gtx'
        tmp = out.with_suffix('.tmp')
        tmp.write_bytes(gtx(h, z))
        tmp.replace(out)
        self.stdout.write(f'{h.get("model name", name)}: {z.shape[0]} × {z.shape[1]}, '
                          f'{np.nanmin(z):.3f} … {np.nanmax(z):.3f} m -> {out}')
