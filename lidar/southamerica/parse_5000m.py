import re, json, sys, html, glob
from html.parser import HTMLParser
class T(HTMLParser):
    def __init__(s): super().__init__(); s.rows=[]; s.row=None; s.cell=None
    def handle_starttag(s, t, a):
        if t == 'tr': s.row = []
        elif t == 'td' and s.row is not None: s.cell = []
    def handle_endtag(s, t):
        if t == 'td' and s.cell is not None: s.row.append(' '.join(''.join(s.cell).split())); s.cell = None
        elif t == 'tr' and s.row is not None: s.rows.append(s.row); s.row = None
    def handle_data(s, d):
        if s.cell is not None: s.cell.append(d)
out = []
for n in 'one two three four five six seven eight'.split():
    p = T(); p.feed(open(f'{sys.argv[1]}/5000m-{n}.html', encoding='cp1252').read())
    for r in p.rows:
        if len(r) >= 7 and re.fullmatch(r'\d+', r[0].strip()):
            nm = r[1].replace('*', '').replace('\xa0', ' ').strip()
            h = r[2].replace('\xa0', ' ').strip()
            m = re.search(r'\d{4}', h)
            out.append(dict(rank=int(r[0]), name=' '.join(nm.split()), height=int(m.group()) if m else None, raw_height=h,
                            approx=not re.fullmatch(r'\d{4}', h), area=r[4].replace('\xa0', ' ').strip(), country=r[6].replace('\xa0',' ').strip(), page=n))
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False, indent=0)
print(len(out), out[0], out[-1], sep='\n')
print('approx', [ (o['name'], o['raw_height']) for o in out if o['approx']][:20])
r = [o['rank'] for o in out]; print('ranks', min(r), max(r), 'dups', len(r) - len(set(r)), 'gaps', sorted(set(range(min(r), max(r)+1)) - set(r))[:20])
