"""
A geoid grid in the ISG format (isgeoid.polimi.it) as a PROJ grid, for height systems PROJ ships
no grid for: Taiwan's TWVD 2001 through the hybrid geoid TWGEOID18h, say.

    manage.py install_isg_grid https://isgeoid.polimi.it/Geoid/Asia/Taiwan/public/Taiwan_2018_TWGEOID2018h_gravG_20201201.isg tw_isg_twgeoid18h

Writes NAME.gtx into PROJ's user data directory (~/.local/share/proj), where the other grids
are; a VerticalDatum then names it in its CRS (`+geoidgrids=NAME.gtx`). GTX needs nothing but
the standard library, so the server needs neither GDAL nor numpy for it.
"""
import re
import struct
import urllib.request
from pathlib import Path

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
    """ (header dict, rows N to S as lists of floats) from an ISG 1.0/2.0 grid file. """
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
    z = [float(v) for v in body.split('\n', 1)[1].split()]
    if len(z) != rows * cols:
        raise CommandError(f'{len(z)} values for {rows} × {cols}')
    return h, [z[r * cols:(r + 1) * cols] for r in range(rows)]


def gtx(h, z):
    """
    The GTX bytes: big-endian origin (the south-west node) and steps in degrees, rows and
    columns, then the rows south to north. No data is -88.8888 there.
    """
    nodata = float(h.get('nodata', -9999))
    rows = [[-88.8888 if v == nodata else v for v in row] for row in reversed(z)]
    lat0, lon0 = degrees(h['lat min']), degrees(h['lon min'])
    dlat, dlon = degrees(h['delta lat']), degrees(h['delta lon'])
    cols = len(rows[0])
    return struct.pack('>4d2i', lat0, lon0, dlat, dlon, len(rows), cols) + b''.join(
        struct.pack(f'>{cols}f', *row) for row in rows)


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
        values = [v for row in z for v in row if v != float(h.get('nodata', -9999))]
        self.stdout.write(f'{h.get("model name", name)}: {len(z)} × {len(z[0])}, '
                          f'{min(values):.3f} … {max(values):.3f} m -> {out}')
