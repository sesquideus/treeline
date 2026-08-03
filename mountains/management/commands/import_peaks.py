"""
Bulk-import summits and their key cols from a peak list.

Written for alpsover589.xls — the Alpine prominence list, 633 peaks down to P 590 m — and kept
around because the next sheet will have different columns and this way that is an edit to
COLUMNS rather than a rewrite. One row is one summit plus its key col; the parent column names
another summit, which works for a region whose prominence forest closes inside the sheet.

    # .xls is not readable here; convert once, then feed the .xlsx (or a CSV export) in
    libreoffice --headless --convert-to xlsx --outdir /tmp alpsover589.xls
    uv run python manage.py import_peaks /tmp/alpsover589.xlsx --source "Alps P590 list" --dry-run
    uv run python manage.py import_peaks /tmp/alpsover589.xlsx --source "Alps P590 list"

The dry run is a real one: the work happens inside a transaction that is rolled back at the
end, so parent links and every invariant are exercised against the live database rather than
guessed at. Nothing is written unless the whole sheet validates — --partial keeps the good rows.
"""
import collections
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point, Polygon
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.functions.world import distance
from core.models import Country
from mountains.management.alps_names import FINAL_NAMES
from mountains.models import Col, NamedPoint, Source, Summit

# Countries this sheet needs that a TreeLine database may not have yet. Created on demand so
# the 23 Slovenian and Liechtenstein peaks do not land without countries — they cannot be
# backfilled by a later run, because countries are only set when a point is created.
COUNTRY_DEFAULTS = {
    'si': dict(name='Slovenija', full_name='Republika Slovenija', english_name='Slovenia'),
    'li': dict(name='Liechtenstein', full_name='Fürstentum Liechtenstein',
               english_name='Liechtenstein'),
}

# How COLUMNS values are read. 'letter' means spreadsheet column letters, 'header' means names
# from the first row. Not a guess-either-way: this sheet has a column *headed* "D" (dominance)
# at column E and one headed "P" at column D, so header-first resolution silently reads the
# wrong column. Whichever scheme a sheet wants, say so here.
COLUMN_ADDRESSING = 'letter'

# Where each field lives. This sheet leaves half its headers blank (name, coordinates, col
# altitude), so it is addressed by letter.
COLUMNS = {
    'name': 'B',
    'altitude': 'C',
    'latitude': 'J',
    'longitude': 'K',

    'col_name': 'M',                # prose: "Brenner Pass (1370)", "near Ozero Kubenskoye"
    'col_altitude': 'N',
    'col_latitude': 'O',
    'col_longitude': 'P',

    'parent': 'X',                  # PROMINENCE MASTER — a summit name, resolved in pass 2
    'prominence': 'D',              # cross-checked against the key col, never stored
    'countries': 'I',               # NAT: "CH", "D/A", "SLO/I"
    'area': 'L',                    # used to disambiguate repeated names
    'island_high_point': None,
}

REQUIRED = ('name', 'altitude', 'latitude', 'longitude')

# NAT is not ISO. Compound cells ("CH/I") split on the slash.
NATION_CODES = {
    'F': 'fr', 'A': 'at', 'CH': 'ch', 'I': 'it', 'D': 'de',
    'SLO': 'si', 'LIE': 'li', 'FL': 'li', 'MC': 'mc',
}

# The parent column abbreviates German where no amount of normalising will find the target.
# Hard-coded rather than fuzzy-matched: a guessed parent silently corrupts the forest.
PARENT_ALIASES = {
    'BLÜEMLISALP': 'BLÜEMLISALPHORN',
    'HINT. SONNWENDJOCH': 'HINTERES SONNWENDJOCH',
    'U. WILDGRUBENSPITZE': 'UNTERE WILDGRUBENSPITZE',
    'ÖSTL. KARWENDELSPITZE': 'ÖSTLICHE KARWENDELSPITZE',
}

# Words the parent column drops: "ANTELAO" for "MONTE ANTELAO", "GRANDE CASSE" for
# "POINTE DE LA GRANDE CASSE". Removed before comparing, never from a stored name.
GENERIC = {
    'MONTE', 'MONT', 'MONTAGNE', 'MOUNT', 'DE', 'DI', 'DEL', 'DELLA', 'DELLE', 'DES', 'DU',
    'LA', 'LE', 'IL', 'PIZ', 'PIZZO', 'POINTE', 'PUNTA', 'CIMA', 'CORNA', 'GRAND', 'GRANDE',
    'TETE', 'PIC', 'AIGUILLE', 'AIGUILLES', 'DLES', 'DENT', 'MERIDIONALE', 'D', 'L', 'CRODA',
}

TRUTHY = {'1', 'y', 'yes', 'true', 't', 'x'}

# "45°49'57"" — this sheet's only coordinate form, no hemisphere (all of it is N/E).
DMS = re.compile(r'^(\d+)\s*°\s*(\d+)\s*[\'′]\s*(\d+(?:\.\d+)?)\s*["″]?\s*([NSEWnsew])?$')
DECIMAL = re.compile(r'^([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSEWnsew])?$')

# A col description that is a name ("Brenner Pass") versus one that is a location
# ("near Ozero Kubenskoye", "West of Simplonpass"). Cols are often genuinely unnamed and the
# domain rule is to leave them so rather than invent one.
NOT_A_NAME = re.compile(r'^(near|at|above|below|between|N|S|E|W|NE|NW|SE|SW|north|south|east|'
                        r'west|of|by|close)\b', re.IGNORECASE)


class RowError(Exception):
    """A problem with one row: reported against its line number, does not abort the pass."""


def fold(text):
    """
    Upper-case, accent-free, punctuation-free — for comparing names, never for storing.

    Two characters need handling before the ASCII pass deletes them, because deleting is not
    the same as separating: ß is spelled out ("Großglockner" would not match "GROSSGLOCKNER"),
    and a curly apostrophe becomes a space, so the database's "Tête de l’Estrop" folds the same
    way as the sheet's "TÊTE DE L'ESTROP" rather than to "LESTROP".
    """
    text = (text or '').replace('ß', 'ss').replace('ẞ', 'SS')
    text = re.sub(r'[‘’ʼ´`]', ' ', text)
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9]+', ' ', text).strip()


def tokens(text):
    return frozenset(t for t in fold(text).split() if t not in GENERIC)


def name_forms(name):
    """Every way this sheet might refer to one peak: "A / B", "A (B)", and the bare parts."""
    forms = set()
    for part in (name or '').split('/'):
        part = part.strip()
        if not part:
            continue
        forms.add(part)
        forms.add(re.sub(r'\(.*?\)', ' ', part).strip())
        forms.update(inner.strip() for inner in re.findall(r'\((.*?)\)', part))
    return {f for f in forms if f}


def parse_angle(raw, field, limit=180):
    """
    Signed degrees from DMS or decimal; hemisphere optional, N/E assumed when absent.

    The range check is not paranoia: a mis-addressed column parses as a perfectly good number
    and PostGIS will store a "longitude" of 4695 without complaint.
    """
    text = (raw or '').strip()
    if not text:
        raise RowError(f'{field} is empty')
    for pattern, build in (
        (DMS, lambda m: int(m.group(1)) + int(m.group(2)) / 60 + float(m.group(3)) / 3600),
        (DECIMAL, lambda m: float(m.group(1))),
    ):
        match = pattern.match(text)
        if match:
            value = build(match)
            if (match.groups()[-1] or '') in ('S', 's', 'W', 'w'):
                value = -value
            if abs(value) > limit:
                raise RowError(f'{field}: {value:.4f}° is outside ±{limit}° — wrong column?')
            return value
    raise RowError(f'{field}: cannot read {text!r} as a coordinate')


def parse_float(raw, field):
    text = re.sub(r'[^\d.,+-]', '', (raw or '').strip()).replace(',', '.')
    if not text:
        raise RowError(f'{field} is empty')
    try:
        return float(text)
    except ValueError:
        raise RowError(f'{field}: {raw!r} is not a number')


def letter_index(spec):
    """'A' -> 0, 'B' -> 1, 'AA' -> 26."""
    if not re.fullmatch(r'[A-Z]{1,2}', spec or ''):
        return None
    index = 0
    for char in spec:
        index = index * 26 + ord(char) - 64
    return index - 1


def column_index(spec, headers):
    """Resolve a COLUMNS value according to COLUMN_ADDRESSING — never by guessing."""
    if COLUMN_ADDRESSING == 'letter':
        return letter_index(spec)
    return headers.index(spec) if spec in headers else None


def read_xlsx(path):
    """First worksheet as a list of lists. Minimal on purpose — no openpyxl on this machine."""
    M = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    tag = lambda t: f'{{{M}}}{t}'
    archive = zipfile.ZipFile(path)
    shared = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).iter(tag('si')):
            shared.append(''.join(t.text or '' for t in item.iter(tag('t'))))
    rows = []
    for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).iter(tag('row')):
        cells = {}
        for cell in row.iter(tag('c')):
            letters = ''.join(c for c in cell.get('r', '') if c.isalpha())
            value, inline = cell.find(tag('v')), cell.find(tag('is'))
            if cell.get('t') == 's' and value is not None:
                text = shared[int(value.text)]
            elif inline is not None:
                text = ''.join(t.text or '' for t in inline.iter(tag('t')))
            else:
                text = value.text if value is not None else ''
            cells[letter_index(letters)] = text
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, '') for i in range(width)])
    return rows


def read_csv(path, delimiter, encoding):
    import csv
    with open(path, newline='', encoding=encoding) as handle:
        return [row for row in csv.reader(handle, delimiter=delimiter)]


class Command(BaseCommand):
    help = 'Import summits and their key cols from a peak list (see COLUMNS in this file).'

    def add_arguments(self, parser):
        parser.add_argument('sheet', help='.xlsx (first worksheet) or .csv')
        parser.add_argument('--source', help='name of the Source to credit; created if absent')
        parser.add_argument('--dry-run', action='store_true',
                            help='do all the work, report, then roll back')
        parser.add_argument('--partial', action='store_true',
                            help='commit the rows that validated instead of requiring all of them')
        parser.add_argument('--limit', type=int, help='only read the first N data rows')
        parser.add_argument('--skip-rows', type=int, default=1, help='header rows to drop')
        parser.add_argument('--delimiter', default=',')
        parser.add_argument('--encoding', default='utf-8-sig')
        parser.add_argument('--col-tolerance', type=float, default=100.0,
                            help='metres within which an existing col counts as the same col')
        parser.add_argument('--prominence-tolerance', type=float, default=2.0,
                            help='metres of disagreement with the sheet before it is reported')

    def handle(self, *args, **options):
        self.options = options
        self.errors, self.warnings, self.notes = [], [], []
        self.created = {'summit': 0, 'col': 0}
        self.matched = {'summit': 0, 'col': 0}
        self.summits = {}                  # sheet line -> Summit
        self.renamed = []                  # the repeated names and what they became
        self.uncurated = 0                 # rows with no entry in FINAL_NAMES
        self.unnamed_cols = 0

        rows = self.read()
        self.index_names(rows)

        with transaction.atomic():
            # Inside the transaction: a dry run that leaves a Source row behind is not dry.
            source = self.get_source(options['source'])
            self.ensure_countries()
            self.index_database()
            self.pass_one(rows, source)
            self.pass_two(rows)
            self.pass_three()
            self.report(len(rows))

            if options['dry_run']:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('\nDRY RUN — everything above was rolled back.'))
            elif self.errors and not options['partial']:
                transaction.set_rollback(True)
                raise CommandError(f'{len(self.errors)} row(s) failed — nothing was written. '
                                   'Fix the sheet, or pass --partial to keep what validated.')
            else:
                self.stdout.write(self.style.SUCCESS('\nCommitted.'))
                self.stdout.write('Now run the integrity script: uv run python manage.py shell '
                                  '< .claude/skills/treeline/scripts/check_integrity.py')

    # ── input ──────────────────────────────────────────────────────────────────────
    def read(self):
        path = self.options['sheet']
        try:
            grid = (read_xlsx(path) if path.lower().endswith('.xlsx')
                    else read_csv(path, self.options['delimiter'], self.options['encoding']))
        except FileNotFoundError:
            raise CommandError(f'no such file: {path}')
        if not grid:
            raise CommandError(f'{path} is empty')

        headers = [h.strip() for h in grid[0]]
        self.index = {field: column_index(spec, headers) for field, spec in COLUMNS.items() if spec}
        missing = [f for f in REQUIRED if self.index.get(f) is None]
        if missing:
            raise CommandError(f'cannot locate column(s) {missing} — sheet headers are {headers}. '
                               f'Fix COLUMNS at the top of import_peaks.py.')

        rows = []
        for number, cells in enumerate(grid[self.options['skip_rows']:],
                                       start=self.options['skip_rows'] + 1):
            row = {field: (cells[i].strip() if i is not None and i < len(cells) else '')
                   for field, i in self.index.items()}
            if row.get('name'):
                row['line'] = number
                rows.append(row)
        if self.options['limit']:
            rows = rows[:self.options['limit']]
        self.stdout.write(f'{len(rows)} data row(s) from {path}')
        return rows

    def get_source(self, name):
        if not name:
            self.warnings.append('no --source given: these records will carry no provenance')
            return None
        source, created = Source.objects.get_or_create(name=name)
        if created:
            self.stdout.write(f'created Source {source.name!r}')
        return source

    # ── name resolution ────────────────────────────────────────────────────────────
    def index_names(self, rows):
        """
        Index every row under every form of its name, so the parent column can be resolved.
        Repeated names stay as lists: which one a parent reference means is decided by
        distance at the point of use.
        """
        self.by_exact = collections.defaultdict(list)
        self.by_tokens = collections.defaultdict(list)
        for row in rows:
            for form in name_forms(row['name']):
                self.by_exact[fold(form)].append(row)
                self.by_tokens[tokens(form)].append(row)
        self.duplicates = collections.defaultdict(list)
        for row in rows:
            self.duplicates[row['name']].append(row)
        self.duplicates = {n: rs for n, rs in self.duplicates.items() if len(rs) > 1}

    def ensure_countries(self):
        """Create the Country rows this sheet needs but a database may lack (Slovenia, …)."""
        for code, fields in COUNTRY_DEFAULTS.items():
            country, created = Country.objects.get_or_create(code=code, defaults=fields)
            if created:
                self.stdout.write(f'created Country {country}')

    def index_database(self):
        """
        Fold every existing summit name once, so matching sees through spelling differences a
        SQL `iexact` cannot: "Großglockner" vs "GROSSGLOCKNER", "l’Estrop" vs "L'ESTROP".
        """
        self.db_by_fold = collections.defaultdict(list)
        for summit in Summit.objects.select_related('point').exclude(point__name=None):
            for form in name_forms(summit.point.name):
                self.db_by_fold[fold(form)].append(summit)

    def find_summit(self, row, location, altitude):
        """
        An existing record for this peak — name *and* position must agree. Name alone is not
        enough: the sheet's "KEPA / MITTAGSKOGEL" (2143 m) matched a different Kepa 20 km away
        and 548 m lower, which would have silently merged two peaks into one.
        """
        for form in name_forms(row['name']):
            for candidate in self.db_by_fold.get(fold(form), []):
                if candidate.point.location is None:
                    continue
                separation = distance((location.y, location.x),
                                      (candidate.point.location.y, candidate.point.location.x)).km
                if separation <= 2 and abs(candidate.point.altitude - altitude) <= 100:
                    return candidate
                self.notes.append(
                    f'{row["name"]}: shares a name with {candidate.point.name} '
                    f'({candidate.point.altitude:.0f} m, {separation:.0f} km away) — '
                    f'treated as a different peak')
        return None

    def resolve(self, reference, origin):
        """
        A parent name -> a sheet row, by exact fold, then token set, then token containment.
        Several candidates (a name this sheet uses twice) resolve to the nearest one to the
        child, which is what a divide tree almost always means and is eyeballable on the map.
        """
        reference = PARENT_ALIASES.get(reference.strip(), reference.strip())
        folded = fold(reference)
        candidates = self.by_exact.get(folded) or self.by_tokens.get(tokens(reference)) or []
        if not candidates:
            key = tokens(reference)
            candidates = [row for keyset, rows in self.by_tokens.items() if key and key < keyset
                          for row in rows]
        candidates = [c for c in candidates if c['line'] != origin['line']]
        if not candidates:
            return None, None
        unique = {c['line']: c for c in candidates}.values()
        if len(unique) == 1:
            return next(iter(unique)), None
        nearest = min(unique, key=lambda c: self.separation(origin, c))
        return nearest, (f'{reference!r} is ambiguous ({len(unique)} peaks of that name) — '
                         f'took the nearest, {self.separation(origin, nearest):.0f} km away')

    def separation(self, a, b):
        try:
            return distance((parse_angle(a['latitude'], 'lat', 90), parse_angle(a['longitude'], 'lon')),
                            (parse_angle(b['latitude'], 'lat', 90), parse_angle(b['longitude'], 'lon'))).km
        except RowError:
            return float('inf')

    def resolve_in_db(self, reference):
        """Fall back to an existing record — Mont Blanc's parent is 'EVEREST' = 'Mount Everest'."""
        reference = PARENT_ALIASES.get(reference.strip(), reference.strip())
        summit = Summit.objects.select_related('point').filter(point__name__iexact=reference).first()
        if summit:
            return summit
        wanted = tokens(reference)
        for candidate in Summit.objects.select_related('point').exclude(point__name=None):
            if wanted and (tokens(candidate.point.name) == wanted or wanted < tokens(candidate.point.name)):
                return candidate
        return None

    def storage_name(self, row):
        """
        The name to store, in its proper spelling.

        FINAL_NAMES holds the curated form for every row of this sheet (the sheet itself is ALL
        CAPS) and already carries the area suffix for the five names it uses twice — so it is
        both the orthography and the disambiguation. Anything not in that table falls back to
        the sheet's own text, with AREA appended if the name is not unique: NamedPoint.name is
        globally unique, and a clash is reported rather than resolved silently.
        """
        name = row['name']
        clash = ('repeated in the sheet' if name in self.duplicates
                 else 'name already in the database' if NamedPoint.objects.filter(name=name).exists()
                 else None)
        curated = FINAL_NAMES.get(f'{name}|{row.get("area") or ""}')
        if curated:
            if clash:
                self.renamed.append((name, curated, row['line'], row.get('altitude'),
                                     row.get('prominence'), clash))
            return curated

        self.uncurated += 1
        if not clash:
            return name
        area = row.get('area') or ''
        disambiguated = f'{name} ({area})'[:64].strip() if area else name
        self.renamed.append((name, disambiguated, row['line'], row.get('altitude'),
                             row.get('prominence'), clash))
        return disambiguated

    # ── pass 1: points, cols, summits, key cols ────────────────────────────────────
    def pass_one(self, rows, source):
        for row in rows:
            try:
                self.summits[row['line']] = self.build_summit(row, source)
            except RowError as error:
                self.errors.append((row['line'], row['name'], str(error)))
            except ValidationError as error:
                self.errors.append((row['line'], row['name'], '; '.join(error.messages)))

    def build_summit(self, row, source):
        altitude = parse_float(row['altitude'], 'altitude')
        location = Point(parse_angle(row['longitude'], 'longitude', 180),
                         parse_angle(row['latitude'], 'latitude', 90), srid=4326)  # Point(lon, lat)

        existing = self.find_summit(row, location, altitude)
        name = existing.point.name if existing else self.storage_name(row)
        if existing:
            self.matched['summit'] += 1
            if abs(existing.point.altitude - altitude) > 0.5:
                self.warnings.append(f'{name}: in the database at {existing.point.altitude} m, '
                                     f'sheet says {altitude} m — left as it is')
            summit = existing
        else:
            self.near_duplicate(name, location, altitude)
            point = NamedPoint(name=name, location=location, altitude=altitude, source=source)
            point.full_clean()                       # save() does not validate
            point.save()
            self.set_countries(point, row)
            summit = Summit(point=point)
            self.created['summit'] += 1

        if self.truthy(row.get('island_high_point')):
            summit.island_high_point = True
        elif summit.key_col_id is None:
            summit.key_col = self.build_col(row, name, source, altitude)

        summit.full_clean(exclude=['point'] if summit.pk is None else None)
        summit.save()
        self.check_prominence(row, name, summit)
        return summit

    def build_col(self, row, summit_name, source, summit_altitude):
        if not any(row.get(f) for f in ('col_altitude', 'col_latitude', 'col_longitude')):
            self.warnings.append(f'{summit_name}: no key col in the sheet — left incomplete')
            return None

        altitude = parse_float(row['col_altitude'], 'col_altitude')
        location = Point(parse_angle(row['col_longitude'], 'col_longitude', 180),
                         parse_angle(row['col_latitude'], 'col_latitude', 90), srid=4326)
        if altitude >= summit_altitude:
            raise RowError(f'key col at {altitude} m is not below the summit at {summit_altitude} m')

        existing = self.find_col(location, altitude)
        if existing:
            self.matched['col'] += 1
            # key_col is a OneToOneField — a col already spoken for cannot be shared.
            if getattr(existing, 'key_for', None) is not None:
                raise RowError(f'the col at {altitude} m is already the key col of '
                               f'{existing.key_for.point.name} — two summits cannot share one')
            return existing

        point = NamedPoint(name=self.col_name(row, summit_name), location=location,
                           altitude=altitude, source=source)
        point.full_clean()
        point.save()
        col = Col(point=point)
        col.full_clean(exclude=['point'])
        col.save()
        self.created['col'] += 1
        return col

    def col_name(self, row, summit_name):
        """The KEY COL cell is prose. Keep it only when it reads as a name, else leave null."""
        text = re.sub(r'\([^)]*\)', '', row.get('col_name') or '').strip(' ,;')
        if not text or ',' in text or NOT_A_NAME.match(text):
            self.unnamed_cols += 1
            return None
        if NamedPoint.objects.filter(name__iexact=text).exists():
            self.warnings.append(f'{summit_name}: col name {text!r} is taken — imported unnamed')
            self.unnamed_cols += 1
            return None
        return text[:64]

    def find_col(self, location, altitude):
        return (Col.objects.select_related('point', 'key_for__point')
                .filter(point__altitude__gte=altitude - 5, point__altitude__lte=altitude + 5)
                .annotate(separation=Distance('point__location', location))
                .filter(separation__lte=self.options['col_tolerance'])
                .order_by('separation').first())

    def near_duplicate(self, name, location, altitude):
        twin = (Summit.objects.select_related('point')
                .filter(point__altitude__gte=altitude - 10, point__altitude__lte=altitude + 10)
                .annotate(separation=Distance('point__location', location))
                .filter(separation__lte=200).order_by('separation').first())
        if twin:
            self.warnings.append(f'{name}: {twin.point.name} is already within '
                                 f'{twin.separation.m:.0f} m at {twin.point.altitude} m — '
                                 f'possible duplicate')

    def set_countries(self, point, row):
        raw = [p.strip().upper() for p in re.split(r'[/,]', row.get('countries') or '') if p.strip()]
        codes, unmapped = [], []
        for token in raw:
            code = NATION_CODES.get(token)
            codes.append(code) if code else unmapped.append(token)
        if unmapped:
            self.warnings.append(f'{point.name}: NAT code(s) {unmapped} not in NATION_CODES')
        found = list(Country.objects.filter(code__in=codes))
        absent = set(codes) - {c.code for c in found}
        if absent:
            self.notes.append(f'{point.name}: no Country row for {sorted(absent)}')
        point.countries.set(found)

    def check_prominence(self, row, name, summit):
        """The sheet's prominence checks the col pairing; it is derived, never stored."""
        if not row.get('prominence'):
            return
        computed = summit.compute_prominence()
        if computed is None:
            return
        try:
            expected = parse_float(row['prominence'], 'prominence')
        except RowError:
            return
        if abs(computed - expected) > self.options['prominence_tolerance']:
            self.warnings.append(f'{name}: sheet says prominence {expected:.0f} m, the key col '
                                 f'gives {computed:.0f} m — check the pairing')

    # ── pass 2: parents, once every summit has a prominence ────────────────────────
    def pass_two(self, rows):
        """
        Separate from pass 1 because a parent may sit below its child in the sheet, and the
        "parent must be more prominent" invariant cannot be evaluated until both summits have
        their key col. Resolution goes sheet first, then the database.
        """
        for row in rows:
            summit = self.summits.get(row['line'])
            reference = (row.get('parent') or '').strip()
            if not (summit and reference):
                continue

            target, note = self.resolve(reference, row)
            if target is not None:
                parent = self.summits.get(target['line'])
                if parent is None:
                    self.errors.append((row['line'], row['name'],
                                        f'parent {reference!r} is in the sheet but failed to import'))
                    continue
            else:
                parent = self.resolve_in_db(reference)
                if parent is None:
                    self.errors.append((row['line'], row['name'],
                                        f'parent {reference!r} found in neither sheet nor database'))
                    continue
                note = note or f'{reference!r} resolved to {parent.point.name!r} in the database'
            if note:
                self.notes.append(f'{row["name"]}: {note}')

            summit.prominence_parent = parent
            try:
                summit.full_clean()
                summit.save()
            except ValidationError as error:
                self.errors.append((row['line'], row['name'], '; '.join(error.messages)))

    # ── pass 3: the summits that were already here ─────────────────────────────────
    def pass_three(self):
        """
        Re-validate existing records around the import. Inserting a peak between an existing
        summit and its parent means that summit ought to be re-pointed, and nothing cascades
        that. Note the limit: this catches a *broken* invariant, not a parent that is merely
        now wrong — re-parenting stays a judgement call.
        """
        located = [s.point.location for s in self.summits.values() if s.point_id and s.point.location]
        if not located:
            return
        lons, lats = [p.x for p in located], [p.y for p in located]
        margin = 0.25
        box = Polygon.from_bbox((min(lons) - margin, min(lats) - margin,
                                 max(lons) + margin, max(lats) + margin))
        box.srid = 4326
        neighbours = (Summit.objects
                      .select_related('point', 'key_col__point', 'prominence_parent__point')
                      .filter(point__location__intersects=box)
                      .exclude(pk__in={s.pk for s in self.summits.values() if s.pk}))
        broken = 0
        for summit in neighbours:
            try:
                summit.full_clean()
            except ValidationError as error:
                broken += 1
                self.warnings.append(f'{summit.point.name} (already in the database) no longer '
                                     f'validates: {"; ".join(error.messages)}')
        self.stdout.write(f're-checked {neighbours.count()} existing summit(s) nearby, '
                          f'{broken} now failing')

    def truthy(self, raw):
        return (raw or '').strip().lower() in TRUTHY

    # ── output ─────────────────────────────────────────────────────────────────────
    def report(self, total):
        self.stdout.write('')
        self.stdout.write(f'summits: {self.created["summit"]} created, '
                          f'{self.matched["summit"]} already present')
        if self.uncurated:
            self.stdout.write(self.style.WARNING(
                f'{self.uncurated} row(s) had no entry in alps_names.FINAL_NAMES and kept the '
                f"sheet's own capitalisation"))
        self.stdout.write(f'cols:    {self.created["col"]} created '
                          f'({self.unnamed_cols} left unnamed), {self.matched["col"]} reused')

        if self.renamed:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\nREPEATED NAMES — {len(self.renamed)} rows, disambiguated with AREA '
                f'(NamedPoint.name is unique). Review these:'))
            for original, new, line, alt, prom, why in sorted(self.renamed):
                self.stdout.write(f'  line {line:<4} {original:<14} {alt:>5} m  P{prom:<5} '
                                  f'({why})\n           -> {new}')
        if self.notes:
            self.stdout.write(f'\n{len(self.notes)} note(s):')
            for message in self.notes[:40]:
                self.stdout.write(f'  {message}')
            if len(self.notes) > 40:
                self.stdout.write(f'  … and {len(self.notes) - 40} more')
        if self.warnings:
            self.stdout.write(self.style.WARNING(f'\n{len(self.warnings)} warning(s):'))
            for message in self.warnings[:40]:
                self.stdout.write(f'  {message}')
            if len(self.warnings) > 40:
                self.stdout.write(f'  … and {len(self.warnings) - 40} more')
        if self.errors:
            self.stdout.write(self.style.ERROR(f'\n{len(self.errors)} row(s) failed:'))
            for line, name, message in self.errors:
                self.stdout.write(f'  line {line} ({name}): {message}')
        else:
            self.stdout.write(self.style.SUCCESS(f'\nall {total} row(s) validated'))
