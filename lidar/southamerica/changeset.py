"""The South American P600 (Copernicus GLO-30, 6″ flood, cols at 1″, walk parents at 30″) as a changeset."""
import json, re, sys, unicodedata
sys.path.insert(0, sys.argv[1])
SP, OUT = sys.argv[1], sys.argv[2]
P = json.load(open(f'{SP}/andes/p600_c.json'))
LANG = dict(br='pt', gy='en', fk='en', gs='en', tt='en', sr='nl', fr='fr')
def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"['’`.,]", '', s); s = re.sub(r'[-_/]', ' ', s); s = ' '.join(s.split())
    for _ in range(2): s = re.sub(r'^(cerros?|nevados?|volcan|pico|cumbre|monte|mount|mt|morro|alto|sierra)\s+', '', s)
    return s
def fix(c):
    return ['ar', 'cl'] if c == ['-99'] or '-99' in c else c
keep = [x for x in P if not set(x['countries']) <= {'pa', 'gd'}]
at = {(x['lat'], x['lon']): x for x in keep}
def key(x): return f'glo:{x["lat"]:.6f},{x["lon"]:.6f}'
summits = []
stats = dict(island=0, list=0, named=0, db=0, noparent=0)
for x in sorted(keep, key=lambda x: -x['prom']):
    if x.get('db'):
        stats['db'] += 1
        continue
    cs = fix(x['countries'])
    lang = next((LANG[c] for c in cs if c in LANG), 'es')
    island = bool(x.get('island')) or x['col_h'] <= 0
    notes = []
    osm, lst = x.get('name'), x.get('list')
    if osm and (len(norm(osm)) < 3 or norm(osm) in {'cerro', 'cerros', 'pico', 'nevado', 'volcan', 'monte', 'morro', 'loma', 'punta'}):
        osm = None                     # a bare "Cerro", "P.": not a name
    names = []
    if lst:
        stats['list'] += 1
        lname = lst['name'].split(' (')[0].strip()
        alts = [a.strip() for a in re.findall(r'\(([^)]*)\)', lst['name'])]
        # the OSM spelling when it is the same name (accents, "Cerro", "Nevado"), else the list's
        name = osm if osm and norm(osm) == norm(lname) else lname
        for n in [osm, lname] + alts:
            if n and n != 'un-named' and not any(norm(n) == norm(m['name']) for m in names):
                names.append(dict(lang=lang, name=n, local=n == osm))
        if name == 'un-named':
            name = osm
    else:
        name = osm
        if osm:
            names.append(dict(lang=lang, name=osm, local=True))
    if name: stats['named'] += 1
    rec = dict(key=key(x), lat=x['lat'], lon=x['lon'], name=name, names=names, countries=cs)
    if lst and not lst['approx']:
        rec.update(altitude=float(lst['height']), datum=None, source='andes.org.uk')
        notes.append(f'Height from the andes.org.uk {lst["list"]} m list (#{lst["rank"]} {lst["name"]}); '
                     f'Copernicus GLO-30 reads {x["h"]:.1f} m.')
    else:
        rec.update(altitude=round(x['h'], 1), datum='EGM2008')
        if lst: notes.append(f'The andes.org.uk {lst["list"]} m list gives about {lst["raw_height"] if "raw_height" in lst else lst["height"]} m (#{lst["rank"]} {lst["name"]}).')
    if '-99' in x['countries']:
        notes.append('On the Southern Patagonian Ice Field, where the Argentina–Chile border is not demarcated.')
    if island:
        stats['island'] += 1
        rec.update(island=True, prominence=rec['altitude'], parent=None)
    else:
        col_h = x['col_h']
        rec['prominence'] = round(rec['altitude'] - col_h, 1)
        rec['col'] = dict(lat=x['col'][0], lon=x['col'][1], name=None, names=[], altitude=round(col_h, 1), datum='EGM2008',
                          countries=fix(x.get('col_countries') or cs), notes=[])
        if x.get('edge'):
            notes.append('The key col lies on the edge of the data used (57° S–13° N, 82°–34° W): the real one may be beyond it.')
        if x.get('col_refined') is False:
            notes.append('Key col from the 6″ grid only: the 1″ search did not confirm it.')
        p = at.get(tuple(x['parent'])) if x.get('parent') else None
        if p is None:
            rec['parent'] = None; stats['noparent'] += 1
        elif p.get('db'):
            rec['parent'] = dict(pk=p['db']['pk'], name=p['db']['name'])
        else:
            rec['parent'] = dict(new=key(p))
    rec['notes'] = notes
    summits.append(rec)
COUNTRIES = [('br', 'Brasil', 'República Federativa do Brasil', 'Brazil'), ('co', 'Colombia', 'República de Colombia', 'Colombia'),
             ('ec', 'Ecuador', 'República del Ecuador', 'Ecuador'), ('ve', 'Venezuela', 'República Bolivariana de Venezuela', 'Venezuela'),
             ('gy', 'Guyana', 'Co-operative Republic of Guyana', 'Guyana'), ('sr', 'Suriname', 'Republiek Suriname', 'Suriname'),
             ('tt', 'Trinidad and Tobago', 'Republic of Trinidad and Tobago', 'Trinidad and Tobago'),
             ('fk', 'Falkland Islands', 'Falkland Islands', 'Falkland Islands'),
             ('gs', 'South Georgia', 'South Georgia and the South Sandwich Islands', 'South Georgia and the South Sandwich Islands'),
             ('pa', 'Panamá', 'República de Panamá', 'Panama'), ('py', 'Paraguay', 'República del Paraguay', 'Paraguay'),
             ('uy', 'Uruguay', 'República Oriental del Uruguay', 'Uruguay')]
used = {c for s in summits for c in s['countries'] + (s['col']['countries'] if 'col' in s else [])}
data = dict(sources=dict(lidar='Copernicus GLO-30', names='OpenStreetMap'),
            reference=dict(countries=[dict(code=c, name=n, full_name=f, english_name=e) for c, n, f, e in COUNTRIES if c in used],
                           languages=[dict(iso639_1='pt', country_code='br', name='Português', english_name='Portuguese', priority=25),
                                      dict(iso639_1='nl', country_code='nl', name='Nederlands', english_name='Dutch', priority=45)]),
            summits=summits)
json.dump(data, open(OUT, 'w'), ensure_ascii=False, indent=1)
print(len(summits), stats, sorted(used))
