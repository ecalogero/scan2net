# scan2net

**From a 3D scan to a route network you can simulate in.**

You give it a point cloud of a building floor (`.ply` or `.xyz`). It does three things:

1. **Slice:** it keeps only the points close to a horizontal plane 1 m above the floor. At that height you see the walls, columns and door openings, but not the floor or the ceiling.
2. **Walkable space:** it turns that slice into polygons covering the free floor area, with holes where columns and other obstacles are.
3. **Network:** it builds a route network inside those polygons. Corners connect to every corner they can see. Short wiggles are then tidied away. Your exit(s) and rooms are linked in.

It saves the network as a Pajek `.net` file in millimetres, which OrientSim opens directly.

```
scan.ply ──▶ 1_slice.ply ──▶ 2_walkable.geojson ──▶ 3_network.net ──▶ OrientSim
                                                    preview.svg  (check it at a glance)
                                                    report.txt
```

## Install

You need Python 3.9 or newer and three libraries: numpy, scipy and shapely.

```bash
cd scan2net
python3 -m pip install .            # puts a `scan2net` command on your PATH
```

You can also run it without installing:

```bash
python3 -m pip install numpy scipy shapely
python3 run_scan2net.py scan.ply ...
```

## Try it

The package includes a synthetic scan of a corridor with three classrooms off it:

```bash
python3 examples/make_example_scan.py        # writes examples/school_scan.xyz
scan2net examples/school_scan.xyz --tol 0.02 --exit 0.3,1.2 \
         --location 3.5,6 --location 10,6 --location 16.5,6
```

The output goes to `examples/school_scan_scan2net/`. Open `preview.svg` in a browser. Grey dots are the slice, green is the walkable space, yellow lines are the network, the red dot is the exit and green dots are the rooms.

## Your own scan

```bash
scan2net myscan.ply --exit 12.5,3.0 --location 4,8 --location 20,8
```

Things to get right:

- **Units and up-axis.** The defaults are metres with Z up. Many phone-scanner and photogrammetry exports are **Y up**, so add `--up y`. If the scan is in millimetres, add `--units mm`. The report warns you if the scan's height looks wrong (more than 50 m or less than 0.5 m).
- **Exit and location coordinates** are plan X,Y in the scan's own units and axes. That means you can read them straight off the point cloud in CloudCompare, Rhino or MeshLab. An exit slightly outside the walkable area, for example in the doorway, is moved inside, and the report says so.
- **Negative coordinates** need an `=`, otherwise they look like an option: `--exit=-0.5,1.2`.
- **Sparse scans.** The slice keeps points within ±3 mm of the plane, which suits terrestrial laser scans. For phone or drone scans, or anything thinner, use `--tol 0.01` or `--tol 0.02`. The report warns you if the slice has fewer than 1000 points.
- **Already sliced?** If you made the slice yourself, add `--sliced` and give it the slice file.

Use `--stop-after walkable` to get just the slice and the polygon. You can then draw or adjust the network yourself.

## The knobs

All distances are in metres. `scan2net --help` lists every option with its default.

| option | default | what it does |
|---|---|---|
| `--height` | 1.0 | height of the slice above the floor |
| `--tol` | 0.003 | keep points within this distance of the slice plane |
| `--floor-percentile` | 0.1 | the floor is this percentile of point heights. Use 0 for "the lowest point", but stray points under the floor then throw it off |
| `--gap` | 0.3 | gaps in walls narrower than this get closed. Doorways must be wider than this. The walkable space also keeps half this distance from walls |
| `--door-max` | 2.0 | openings to the outside up to this width get bridged (open doors, unscanned glass). Raise it if the walkable space "leaks" out of the building. Lower it if wide entrances get sealed |
| `--min-area` | 1.0 | free areas smaller than this (m²) are dropped |
| `--only-connected` | off | keep only the areas that contain an `--exit` or `--location` |
| `--clearance` | 0 | extra distance to keep from walls |
| `--cell` | 0.05 | grid size used to find the free space |
| `--min-length` | 0.5 | no corridor segment in the network is shorter than this |
| `--angle` | 10 | bends within this many degrees of straight are straightened out |
| `--no-simplify` | off | keep every corner of the walkable space as a vertex |
| `--width` | 1.2 | corridor width written to every edge |
| `--capacity` | 30 | vertex capacity (people) |

**If the result looks wrong:**

- **Rooms are missing or cut off.** Doors probably weren't picked up. Try a smaller `--gap`, or check that `--height` passes through the door openings rather than above them.
- **The walkable area spills outside.** Raise `--door-max`.
- **Lots of little islands.** Raise `--min-area`, or use `--only-connected`.

## How it works

**Slice.** The floor height is taken as a low percentile of all point heights. The slice keeps the points within `--tol` of floor + `--height`. A `.ply` slice keeps every property of the original: colour, normals, binary format.

**Walkable space.**

1. The slice is rasterised onto a grid. Each occupied cell grows by `gap/2`, which closes small holes in walls.
2. The building outline comes from a Delaunay triangulation of the slice points ("chi-shape"). Long triangles are carved off the edges, but only across openings longer than `--door-max`. This seals open doors without swallowing courtyards.
3. The free cells inside the outline are grouped into regions. Regions that are tiny, or mostly open to the outside, are dropped.
4. The rest are traced into polygons (GeoJSON, metres, at floor height).

**Network.** Every corner of the walkable polygons, plus your exits and locations, becomes a vertex. Two vertices are joined if they can see each other inside the walkable space. The simplify pass then:

- straightens chains of nearly-straight segments;
- removes corners shorter than `--min-length`;
- merges junctions that are too close together.

Edges carry `TYPE=REAL`, `LENGTH` (metres), `WIDTH`, `INPUTCOST` (millimetres) and `LFR`. Exits are `L` vertices with `VTYPE=EXIT`. Locations are `L` vertices with `VTYPE=LOCN`.

## Using the network in OrientSim

Open `3_network.net` in OrientSim with **File → Open Network…**. Then:

- **Network → Check Network** confirms that every location can reach an exit.
- **Network → ✎ Edit** lets you add an exit or change corridor widths.
- **View → 3D** lets you look at it.

Scenario files refer to vertices by label (for example `L35`). `report.txt` lists the label of each exit and location you gave.

## From Python

```python
from scan2net.pipeline import Options, run
res = run("scan.ply", "out/", Options(up="y", exits=[(12.5, 3.0)]))
res.polygons      # shapely Polygons, metres
res.files         # paths written
```

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest tests
```
