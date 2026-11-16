"""Render compare.py's JSON as one HTML page:  python3 lidar/report.py comparison.json report.html"""
import json
import sys
from html import escape

r = json.load(open(sys.argv[1]))
out = sys.argv[2]


def fm(ll, zoom=16):
    return f'https://www.freemap.sk/?map={zoom}/{ll[0]:.5f}/{ll[1]:.5f}&amp;layers=X'


def m(v, d=1):
    return '—' if v is None else f'{v:.{d}f}'


def lidar_alt(x):
    return x['bpv'] if x['bpv'] is not None else x['egm']


def kind(text):
    t = text
    if t.startswith('summit') and 'DB position is off' in t:
        return 'misplaced'
    if t.startswith('summit'):
        return 'position'
    if 'does not count' in t:
        return 'uncounted'
    if t.startswith('no '):
        return 'missing'
    if t.startswith('a different key col'):
        return 'keycol'
    if t.startswith('key col') and 'same height' in t:
        return 'saddle'
    if t.startswith('key col altitude'):
        return 'colalt'
    if t.startswith('key col'):
        return 'colpos'
    if t.startswith('prominence'):
        return 'prominence'
    if t.startswith('altitude'):
        return 'altitude'
    if t.startswith('parent'):
        return 'parent'
    return 'other'


WRONG_GROUPS = [
    ('uncounted', 'P100s the DB does not count', 'The DB has the summit, but its prominence there is under 100 m, or missing.'),
    ('missing', 'Missing key col or prominence', 'The summit is in the DB without the data to compute its prominence.'),
    ('misplaced', 'Summits in the wrong place', 'Height and prominence agree with a lidar top that is far from the DB position.'),
    ('position', 'Summits well off the lidar top', 'More than 150 m from the highest lidar ground of that summit.'),
    ('keycol', 'A different key col', 'The DB key col is more than 500 m from the lidar one and at a different height.'),
    ('colalt', 'Key col height off by more than 10 m', ''),
    ('prominence', 'Prominence off by more than 20 m', ''),
    ('altitude', 'Summit height off by more than 10 m', ''),
    ('parent', 'Parent', ''),
]
AMISS_GROUPS = [
    ('colalt', 'Key col height off by 2–10 m', 'Mostly old col heights read off maps before the lidar, like Lysák’s.'),
    ('colpos', 'Key col 50–500 m from the lidar saddle', 'Usually a broad saddle placed on the path rather than at its lowest point.'),
    ('saddle', 'Key col elsewhere at the same height', 'Two equally high saddles, or one long flat one: either can be right.'),
    ('position', 'Summit 15–150 m from the lidar top', ''),
    ('parent', 'Prominence parent differs', 'The lidar walk: from the key col along the divide tree to the first more prominent peak. '
               'Parents across the border are not checked.'),
    ('prominence', 'Prominence off by 5–20 m', ''),
    ('altitude', 'Summit height off by 2–10 m', ''),
    ('missing', 'Missing data', ''),
]


def row(e, messages, severity):
    x, d = e['lidar'], e['db']
    col = (f'<a href="{fm(x["col"])}" target="_blank" rel="noopener">key col</a>'
           if x['col'] and x['col'][0] is not None else '')
    flags = ''.join(f'<li>{escape(f)}</li>' for f in x['flags'])
    msgs = ''.join(f'<li class="{sev}">{escape(t)}</li>' for t, sev in messages)
    return f'''<tr>
  <td class="name"><span class="n">{escape(d["name"] or "unnamed")}</span><span class="pk">#{d["pk"]}</span></td>
  <td class="num">{m(d["alt"])}<span class="u">{escape(d["datum"] or "")}</span></td>
  <td class="num">{m(lidar_alt(x))}</td>
  <td class="num">{m(d["prominence"])}</td>
  <td class="num">{m(x["prom"])}</td>
  <td class="what"><ul class="msgs">{msgs}</ul>{f'<ul class="flags">{flags}</ul>' if flags else ''}</td>
  <td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">summit</a>{col}</td>
</tr>'''


HEAD = '''<thead><tr><th>DB summit</th><th class="num">DB alt</th><th class="num">lidar alt</th>
<th class="num">DB P</th><th class="num">lidar P</th><th>what is off</th><th>Freemap</th></tr></thead>'''


def grouped(entries, groups, field, severity, open_=True):
    buckets = {k: [] for k, _, _ in groups}
    for e in entries:
        msgs = [(t, severity) for t in e[field]] + ([(t, 'warn') for t in e['issues']] if field == 'wrong' else [])
        first = next((k for k, _, _ in groups if any(kind(t) == k for t in e[field])), groups[-1][0])
        buckets[first].append((e, msgs))
    html = []
    for k, title, note in groups:
        items = sorted(buckets[k], key=lambda t: -(t[0]['lidar']['prom'] or 0))
        if not items:
            continue
        rows = '\n'.join(row(e, msgs, severity) for e, msgs in items)
        html.append(f'''<details class="group" {'open' if open_ else ''}>
<summary><span class="gt">{escape(title)}</span><span class="count">{len(items)}</span></summary>
{f'<p class="note">{escape(note)}</p>' if note else ''}
<div class="scroll"><table>{HEAD}<tbody>{rows}</tbody></table></div>
</details>''')
    return '\n'.join(html)


missed_rows = []
for e in sorted(r['missed'], key=lambda e: -(e['lidar']['prom'] or 0)):
    x = e['lidar']
    near = e['nearest']
    hint = (f'named point <b>{escape(e["point"]["name"])}</b> {e["point_dist"]:.0f} m away'
            if e['point'] else '')
    o = x.get('osm')
    osm_name = (f'<span class="n">{escape(" / ".join(o["names"]))}</span>'
                f'<span class="pk">{o["dist"]} m</span>' if o and o['names'] else '<span class="muted">—</span>')
    nearest = (f'nearest DB summit: {escape(near["name"] or "unnamed")} '
               f'<span class="pk">#{near["pk"]}</span>, {e["nearest_dist"] / 1000:.1f} km'
               if near else '')
    flags = ''.join(f'<li>{escape(f)}</li>' for f in x['flags'])
    col = (f'<a href="{fm(x["col"])}" target="_blank" rel="noopener">key col</a>'
           if x['col'] and x['col'][0] is not None else '')
    missed_rows.append(f'''<tr>
  <td class="name">{osm_name}</td>
  <td class="num">{x["ll"][0]:.5f}, {x["ll"][1]:.5f}</td>
  <td class="num">{m(lidar_alt(x))}</td>
  <td class="num">{m(x["prom"])}</td>
  <td class="num">{m(x["col_bpv"] if x["col_bpv"] is not None else x["col_egm"])}</td>
  <td class="what">{' · '.join(t for t in (hint, nearest) if t)}{f'<ul class="flags">{flags}</ul>' if flags else ''}</td>
  <td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">summit</a>{col}</td>
</tr>''')

abroad_rows = []
for e in sorted(r['abroad'], key=lambda e: -(e['lidar']['prom'] or 0)):
    x, d, o = e['lidar'], e['db'], e['lidar'].get('osm')
    abroad_rows.append(f'''<tr>
  <td class="name">{f'<span class="n">{escape(d["name"] or "unnamed")}</span><span class="pk">#{d["pk"]}</span>' if d else '<span class="muted">—</span>'}</td>
  <td class="name">{escape(" / ".join(o["names"])) if o and o["names"] else '<span class="muted">—</span>'}</td>
  <td class="num">{m(lidar_alt(x))}</td><td class="num">{m(x["prom"])}</td><td class="num">{x["border_m"]} m</td>
  <td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">summit</a></td>
</tr>''')

PROM = 5
doubtful_rows = []
for e in sorted(r['doubtful'], key=lambda e: -(e['lidar']['prom'] or 0)):
    x, d = e['lidar'], e['db']
    if d and d['prominence'] is not None:
        verdict = ('agrees: not a P100' if d['prominence'] < 100
                   else f'agrees with the lowered run ({d["prominence"] - x["prom"]:+.1f} m)'
                   if abs(d['prominence'] - x['prom']) <= PROM else 'a P100, but by another key col')
    else:
        verdict = 'not in the DB' if not d else 'no prominence in the DB'
    parent = f' · DB parent {escape(d["parent"])}' if d and d.get('parent') else ''
    o = x.get('osm')
    name = (f'<span class="n">{escape(d["name"] or "unnamed")}</span><span class="pk">#{d["pk"]}</span>'
            if d else (f'<span class="n">{escape(o["name"])}</span><span class="pk">Freemap</span>' if o and o['name']
                       else f'<span class="num">{x["ll"][0]:.5f}, {x["ll"][1]:.5f}</span>'))
    doubtful_rows.append(f'''<tr>
  <td class="name">{name}</td>
  <td class="num">{m(lidar_alt(x))}</td>
  <td class="num">{m(d["prominence"]) if d else "—"}</td>
  <td class="num">{escape(x["prom_raw"])}</td>
  <td class="num">{m(x["prom"])}</td>
  <td class="what">{verdict}{parent}</td>
  <td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">summit</a></td>
</tr>''')


good = sorted(r['good'], key=lambda e: -(e['lidar']['prom'] or 0))
good_items = ''.join(
    f'<li><span class="n">{escape(e["db"]["name"] or "unnamed")}</span> '
    f'<span class="num">{"P" + m(e["lidar"]["prom"], 0) if e["lidar"]["prom"] is not None else "top"}</span></li>' for e in good)

def matched_entries(r):
    for k in ('wrong', 'amiss', 'good', 'doubtful'):
        for e in r[k]:
            if e.get('db'):
                yield e


def name_table(rows, head):
    if not rows:
        return ''
    th = ''.join(f'<th>{h}</th>' for h in head)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def names_section(r):
    STATUS = {'differs': ('bad', 'different'), 'spelling': ('warn', 'spelling'), 'osm only': ('warn', 'only Freemap names it')}
    summit, col, far, none = [], [], [], []
    for e in sorted(matched_entries(r), key=lambda e: -(e['lidar']['prom'] or 0)):
        x, d = e['lidar'], e['db']
        if e.get('name') in STATUS:
            sev, label = STATUS[e['name']]
            summit.append(f'''<tr><td class="name"><span class="n">{escape(d["name"] or "unnamed")}</span><span class="pk">#{d["pk"]}</span></td>
<td class="name">{escape(" / ".join(x["osm"]["names"]))}<span class="pk">{x["osm"]["dist"]} m</span></td>
<td><ul class="msgs"><li class="{sev}">{label}</li></ul></td>
<td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">summit</a></td></tr>''')
        if e.get('col_name') in STATUS:
            sev, label = STATUS[e['col_name']]
            oc = x['osm_col']
            col.append(f'''<tr><td class="name"><span class="n">{escape(d.get("col_name") or "unnamed")}</span>
<span class="pk">key col of {escape(d["name"] or "unnamed")}</span></td>
<td class="name">{escape(" / ".join(oc["names"]))}<span class="pk">{oc["dist"]} m</span></td>
<td><ul class="msgs"><li class="{sev}">{label}</li></ul></td>
<td class="links"><a href="{fm(x["col"])}" target="_blank" rel="noopener">key col</a></td></tr>''')
        if e.get('name') == 'db only':
            f = e.get('osm_far')
            if f and f['name']:
                far.append(f'''<tr><td class="name"><span class="n">{escape(d["name"] or "unnamed")}</span><span class="pk">#{d["pk"]}</span></td>
<td class="name">{escape(" / ".join(f["names"]))}</td>
<td class="num">{f["dist"]} m</td>
<td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">lidar top</a><a href="{fm((f["lat"], f["lon"]))}" target="_blank" rel="noopener">Freemap’s</a></td></tr>''')
            else:
                none.append(f'''<tr><td class="name"><span class="n">{escape(d["name"] or "unnamed")}</span><span class="pk">#{d["pk"]}</span></td>
<td class="num">{m(lidar_alt(x))}</td><td class="num">{m(x["prom"])}</td>
<td class="links"><a href="{fm(x["ll"])}" target="_blank" rel="noopener">summit</a></td></tr>''')

    LANG = {'missing': ('warn', 'only on Freemap'), 'differs': ('bad', 'different'), 'spelling': ('warn', 'spelling')}
    lang_rows = {'missing': [], 'conflict': []}
    for e in sorted(matched_entries(r), key=lambda e: -(e['lidar']['prom'] or 0)):
        d, x = e['db'], e['lidar']
        for what, items, where in (('summit', e.get('langs', []), x['ll']), ('key col', e.get('col_langs', []), x['col'])):
            for l in items:
                sev, label = LANG[l['status']]
                owner = (escape(d['name'] or 'unnamed') if what == 'summit'
                         else f'{escape(d.get("col_name") or "unnamed")} <span class="pk">key col of {escape(d["name"] or "unnamed")}</span>')
                lang_rows['missing' if l['status'] == 'missing' else 'conflict'].append(f'''<tr>
<td class="name"><span class="n">{owner}</span>{f'<span class="pk">#{d["pk"]}</span>' if what == 'summit' else ''}</td>
<td class="num">{escape(l["lang"])}</td>
<td class="name">{escape(" / ".join(l["db"])) or '<span class="muted">—</span>'}</td>
<td class="name">{escape(" / ".join(l["osm"]))}</td>
<td><ul class="msgs"><li class="{sev}">{label}</li></ul></td>
<td class="links"><a href="{fm(where)}" target="_blank" rel="noopener">{what}</a></td></tr>''')

    def group(title, note, table, n, open_=True):
        return (f'<details class="group" {"open" if open_ else ""}><summary><span class="gt">{title}</span>'
                f'<span class="count">{n}</span></summary><p class="note">{note}</p>{table}</details>')
    same = sum(1 for e in matched_entries(r) if e.get('name') == 'same')
    named_missing = sum(1 for e in r['missed'] if e['lidar'].get('osm') and e['lidar']['osm']['name'])
    return f'''<section>
  <h2>Names against Freemap</h2>
  <p>Freemap draws OpenStreetMap: the nearest named OSM peak within 150&nbsp;m of each lidar top, and saddle within 150&nbsp;m of each key col,
  against every name the DB has for the point: Freemap’s <code>name</code>, every <code>name:xx</code>, <code>alt_name</code> and <code>old_name</code>. A qualifier in brackets is ignored, and so is the word “sedlo” on cols.
  {same} summit names agree; {named_missing} of the {len(r["missed"])} missing P100s have a name on Freemap, shown in that table above.</p>
  {group("Summit names that differ", "Different words, or the same ones spelled differently.", name_table(summit, ["DB", "Freemap", "", "Freemap"]), len(summit))}
  {group("Key col names that differ", "Includes key cols the DB leaves unnamed and Freemap names.", name_table(col, ["DB", "Freemap", "", "Freemap"]), len(col))}
  {group("Other languages: Freemap has, the DB does not", "A <code>name:xx</code> tag on the Freemap point in a language the DB has no name in, historical names included. Candidates for new PointNames.", name_table(lang_rows["missing"], ["DB", "lang", "DB name", "Freemap", "", "Freemap"]), len(lang_rows["missing"]))}
  {group("Other languages: they disagree", "Both have a name in the language, and none of the DB’s matches Freemap’s. Spelling: the same once accents, case, hyphens and spaces are ignored.", name_table(lang_rows["conflict"], ["DB", "lang", "DB name", "Freemap", "", "Freemap"]), len(lang_rows["conflict"]))}
  {group("Freemap has the name, elsewhere", "No named OSM peak within 150 m of the lidar top, but one with this name within a kilometre: Freemap’s point may be on another top, or a lower shoulder.", name_table(far, ["DB", "Freemap", "away", "Freemap"]), len(far), open_=False)}
  {group("Freemap has no name nearby", "Named in the DB, nothing named on Freemap within a kilometre.", name_table(none, ["DB", "alt", "P", "Freemap"]), len(none), open_=False)}
</section>'''


n_matched = len(r['good']) + len(r['amiss']) + len(r['wrong'])
cov = r['coverage']

page = f'''<title>Lidar P100 audit</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600&display=swap">
<style>
/* A field surveyor's ledger: a summary strip, then the findings worst first, each a table to work through in Freemap. */
:root {{
  --bg: #f6f7f4; --panel: #ffffff; --fg: #1f2420; --muted: #636b63; --line: #dde1da;
  --contour: #8a5a2b; --bad: #b3261e; --warn: #9a6200; --ok: #2e6b3a; --link: #1d5f8a;
  --bad-bg: #fbeceb; --warn-bg: #fbf3e2; --ok-bg: #e9f3eb;
  --sans: "IBM Plex Sans", system-ui, sans-serif;
  --head: "IBM Plex Sans Condensed", "IBM Plex Sans", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #151816; --panel: #1c201d; --fg: #e4e8e2; --muted: #9aa39a; --line: #2f3530;
  --contour: #d19a5f; --bad: #f08a80; --warn: #e5b45a; --ok: #7fc28c; --link: #7cb8e0;
  --bad-bg: #3a1f1d; --warn-bg: #352a14; --ok-bg: #1d3022; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #151816; --panel: #1c201d; --fg: #e4e8e2; --muted: #9aa39a; --line: #2f3530;
  --contour: #d19a5f; --bad: #f08a80; --warn: #e5b45a; --ok: #7fc28c; --link: #7cb8e0;
  --bad-bg: #3a1f1d; --warn-bg: #352a14; --ok-bg: #1d3022; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.5 var(--sans); }}
main {{ max-width: 1180px; margin: 0 auto; padding-block: 32px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 36px; }}
h1, h2 {{ font-family: var(--head); font-weight: 600; text-wrap: balance; margin: 0; }}
h1 {{ font-size: 2rem; letter-spacing: -0.01em; }}
h2 {{ font-size: 1.35rem; display: flex; align-items: baseline; gap: 12px; }}
h2 .count {{ font-size: 1rem; }}
p {{ margin: 0; max-width: 72ch; }}
.lede {{ color: var(--muted); }}
header {{ display: grid; gap: 12px; }}
.strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px;
  background: var(--line); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }}
.strip div {{ background: var(--panel); padding: 12px 14px; display: grid; gap: 2px; }}
.strip b {{ font: 500 1.6rem/1.1 var(--mono); font-variant-numeric: tabular-nums; }}
.strip span {{ font-size: 0.82rem; color: var(--muted); }}
.strip .bad b {{ color: var(--bad); }} .strip .warn b {{ color: var(--warn); }} .strip .ok b {{ color: var(--ok); }}
section {{ display: grid; gap: 14px; }}
.count {{ font: 500 0.85rem var(--mono); color: var(--muted); }}
details.group {{ background: var(--panel); border: 1px solid var(--line); border-radius: 6px; }}
details.group > summary {{ cursor: pointer; padding: 10px 14px; display: flex; gap: 10px; align-items: baseline; list-style: none; }}
details.group > summary::-webkit-details-marker {{ display: none; }}
details.group > summary::before {{ content: "▸"; color: var(--muted); }}
details.group[open] > summary::before {{ content: "▾"; }}
details.group > summary:focus-visible {{ outline: 2px solid var(--link); outline-offset: 2px; }}
.gt {{ font-weight: 600; }}
.note {{ padding: 0 14px 8px; color: var(--muted); font-size: 0.9rem; }}
.scroll {{ overflow-x: auto; border-top: 1px solid var(--line); }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem; }}
th {{ text-align: left; font: 500 0.72rem var(--sans); text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
tbody tr:last-child td {{ border-bottom: 0; }}
.num {{ font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }}
td.name {{ min-width: 150px; }}
.name .n {{ font-weight: 500; }}
.pk {{ font: 0.75rem var(--mono); color: var(--muted); margin-left: 6px; }}
.u {{ font: 0.7rem var(--sans); color: var(--muted); margin-left: 4px; }}
td.what {{ min-width: 260px; }}
ul.msgs, ul.flags {{ margin: 0; padding: 0; list-style: none; display: grid; gap: 3px; }}
ul.msgs li {{ padding: 1px 8px; border-radius: 4px; width: fit-content; }}
ul.msgs li.bad {{ background: var(--bad-bg); color: var(--bad); }}
ul.msgs li.warn {{ background: var(--warn-bg); color: var(--warn); }}
ul.flags {{ margin-top: 6px; font-size: 0.8rem; color: var(--muted); }}
ul.flags li::before {{ content: "◦ "; color: var(--contour); }}
td.links {{ white-space: nowrap; display: flex; gap: 10px; }}
a {{ color: var(--link); text-underline-offset: 2px; }}
.good {{ columns: 220px; column-gap: 24px; margin: 0; padding: 12px 14px; list-style: none; font-size: 0.85rem; }}
.good li {{ display: flex; justify-content: space-between; gap: 8px; break-inside: avoid; border-bottom: 1px dotted var(--line); padding: 2px 0; }}
.good .num {{ color: var(--muted); }}
.muted {{ color: var(--muted); }}
.method {{ font-size: 0.9rem; color: var(--muted); display: grid; gap: 8px; }}
.method h2 {{ color: var(--fg); }}
.sev {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
</style>
<main>
<header>
  <h1>Lidar P100 audit</h1>
  <p class="lede">Every summit of prominence 100 m or more that the Slovak lidar (DMR&nbsp;5.0) shows, found with no help from the database,
  against the {cov["db_summits_covered"]} DB summits inside the lidar’s coverage. Worst first. Heights compared in EGM2008; the table shows each as stored.</p>
  <div class="strip">
    <div><b>{cov["lidar"]}</b><span>P100s in the lidar</span></div>
    <div class="bad"><b>{len(r["missed"])}</b><span>missing from the DB</span></div>
    <div class="bad"><b>{len(r["wrong"])}</b><span>in the DB, but wrong</span></div>
    <div class="warn"><b>{len(r["doubtful"])}</b><span>border, Copernicus only</span></div>
    <div class="warn"><b>{len(r["amiss"])}</b><span>match, something minor off</span></div>
    <div class="ok"><b>{len(r["good"])}</b><span>match within tolerance</span></div>
  </div>
</header>

<section>
  <h2>Missing from the DB <span class="count">{len(r["missed"])}</span></h2>
  <p>No DB summit within 500 m. Heights in Bpv. The Freemap name is the nearest named OSM peak within 150&nbsp;m of the lidar top.</p>
  <details class="group" open><summary><span class="gt">Uncatalogued P100s</span><span class="count">{len(r["missed"])}</span></summary>
  <div class="scroll"><table><thead><tr><th>Freemap name</th><th class="num">position</th><th class="num">alt</th><th class="num">P</th><th class="num">key col</th><th>nearby</th><th>Freemap</th></tr></thead>
  <tbody>{''.join(missed_rows)}</tbody></table></div></details>
</section>

<section>
  <h2>In the DB, but wrong <span class="count">{len(r["wrong"])}</span></h2>
  <p>Off by more than the minor tolerances. A row can carry several findings; it is filed under the worst.</p>
  {grouped(r["wrong"], WRONG_GROUPS, "wrong", "bad")}
</section>

<section>
  <h2>Decided by Copernicus alone <span class="count">{len(r["doubtful"])}</span></h2>
  <p>Summits on the border that make P100 only when Copernicus is lowered 25&nbsp;m to strip the forest. That also lowers the peaks
  across the border, so a higher neighbour there can drop out (Paportna over Jarabá skala). With Copernicus as measured, none of them reaches P80.
  Your DB’s verdict alongside: where it agrees with the lowered run to a metre or two, the key col is likely on the Slovak side and the P100 real.</p>
  <details class="group" open><summary><span class="gt">Border summits to judge yourself</span><span class="count">{len(r["doubtful"])}</span></summary>
  <div class="scroll"><table><thead><tr><th>summit</th><th class="num">alt</th><th class="num">DB P</th><th class="num">P, Copernicus as is</th>
  <th class="num">P, lowered 25 m</th><th>DB says</th><th>Freemap</th></tr></thead>
  <tbody>{''.join(doubtful_rows)}</tbody></table></div></details>
</section>

<section>
  <h2>Match, with something off <span class="count">{len(r["amiss"])}</span></h2>
  <p>Same summit, but a position, height, key col or parent outside tolerance.</p>
  {grouped(r["amiss"], AMISS_GROUPS, "issues", "warn", open_=False)}
</section>

{{NAMES}}

<section>
  <h2>Match within tolerance <span class="count">{len(r["good"])}</span></h2>
  <details class="group"><summary><span class="gt">All of them, most prominent first</span><span class="count">{len(r["good"])}</span></summary>
  <ul class="good">{good_items}</ul></details>
</section>

<section class="method">
  <h2>How it was made</h2>
  <p><b>Lidar.</b> The 1&nbsp;m DMR&nbsp;5.0 pooled to 10&nbsp;m cells (keeping each cell’s highest pixel), flooded from the top with union-find; each key col then found again on the 1&nbsp;m data.
  Ellipsoidal heights converted to EGM2008 and Bpv with the official grids. The ground model shaves some rock tops (Sninský kameň), and may bridge narrow Tatra notches.</p>
  <p><b>Beyond the border.</b> Copernicus GLO-30 fills 20&nbsp;km around the lidar. It is coarser (30&nbsp;m) and a surface model: it reads 10–25&nbsp;m high in forest.
  Each summit was checked with it as it is and lowered by 25&nbsp;m; a P100 that needs the lowering is listed apart, since lowering moves the peaks across the border too. <b>Anything decided by Copernicus is less precise than the lidar</b>, and says so in its notes.</p>
  <p><b>Tolerances.</b> Summit position 15&nbsp;m (wrong beyond 150&nbsp;m), heights 2&nbsp;m (wrong beyond 10&nbsp;m), prominence 5&nbsp;m (wrong beyond 20&nbsp;m),
  key col position 50&nbsp;m (another col beyond 500&nbsp;m, unless at the same height), parent 150&nbsp;m.</p>
  <p><b>Parent.</b> TreeLine’s definition: from the key col along the divide tree to the first peak more prominent than the summit, on the lidar alone.</p>
</section>
</main>
'''
page = page.replace('{NAMES}', names_section(r))
open(out, 'w').write(page)
print(len(page))
