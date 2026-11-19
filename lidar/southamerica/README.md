# South America P600 (2026-11-19)

How `lidar/changesets/2026-11-19-south-america-p600.json` (and its companions) were made. These are
the one-off scripts as they ran, kept so the import can be traced and redone, not a polished
pipeline: each takes a working directory `D` holding its inputs.

1. **Heights for summits whose surface-model height is wrong** — `burn_heights.json`: the
   andes.org.uk 6000 m and 5000 m lists (pages one…eight, parsed with `parse_5000m.py`), plus
   Aconcagua 6960.8 and Miñiques 5910 as the database has them. Burned into the mosaic before the
   flood: `lidar/tiled.py burn WORK --heights burn_heights.json`.
2. **The flood and the walk at 1″** (Copernicus GLO-30, about 43 billion cells, bounded memory):
   `lidar/tiled.py mosaic | burn | flood | walk --min-prominence 600 --out p600.json`.
3. **Names and list heights** — `lists.py D p600.json OUT`: the andes.org.uk lists placed on the
   P600s by OSM name, checked by height. `compare.py D p600.json`: against the database
   (`D/db_sa.json` from `lidar/export_db.py`) and OSM peaks (`D/osm_sa.json`, an Overpass export of
   `natural=peak|volcano` in the box -57,-82,13,-34).
4. **Countries** — `countries.py D`: Natural Earth admin-0 (`D/ne/admin0.geojson`).
5. **The changeset** — `changeset.py D OUT.json`.

Apply on the server in this order: `2026-11-19-andes-altitudes`, `2026-11-19-south-america-p600`,
`2026-11-19-sa-key-cols`, `2026-11-19-integrity`.
