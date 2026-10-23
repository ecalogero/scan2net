# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install:**
```bash
pip install .
# or without installing:
pip install numpy scipy shapely
python run_scan2net.py scan.ply ...
```

**Run tests:**
```bash
pip install pytest
python -m pytest tests/
python -m pytest tests/test_pipeline.py::test_xyz_all_three_steps   # single test
```

**Generate example scan:**
```bash
python examples/make_example_scan.py   # writes examples/school_scan.xyz
```

**Run the tool:**
```bash
scan2net myscan.ply --exit 12.5,3.0 --location 4,8
# or via module:
python -m scan2net myscan.ply --exit 12.5,3.0
```

## Architecture

The tool is a 3-step pipeline: **slice → walkable space → network**.

```
scan.ply → 1_slice.ply → 2_walkable.geojson → 3_network.net → OrientSim
```

### Entry points

- **`scan2net/pipeline.py`** — `run(path, outdir, opt)` is the main programmatic API. `Options` is a dataclass with all parameters. `Result` holds output polygons, vertices, edges, and file paths.
- **`scan2net/cli.py`** — `main()` wraps `run()` for CLI use; parses args and writes `preview.svg` and `report.txt`.

### Core modules (`scan2net/core/`)

- **`scan.py`** — `walkable_polygons()`: reads the point cloud, slices it, rasterises it, finds the building outline via Delaunay chi-shape, labels free regions, and traces them to shapely Polygons (metres). Also `polygons_to_areas()` converts to `Area` objects for the network step.
- **`pointcloud.py`** — reads `.ply` (ASCII/binary) and text formats (`.xyz`, `.txt`, `.pts`, `.csv`, `.asc`), handles unit scaling and axis remapping.
- **`build.py`** — `build_visibility()`: builds a visibility graph from polygon corners + extra points (exits/locations). Two vertices connect if the line segment between them stays inside the walkable polygons.
- **`simplify.py`** — `simplify()`: post-processes the visibility graph by straightening near-collinear chains, removing short segments, and merging nearby junctions.
- **`pajek.py`** — reads/writes Pajek `.net` format (millimetres). This is what OrientSim consumes.
- **`model.py`** — `Area`, `Vertex`, `Edge` data classes used between build/simplify/pajek.
- **`attrs.py`** — parses key=value attribute strings for edge/vertex metadata (`WIDTH`, `LENGTH`, `VTYPE`, etc.).
- **`visibility.py`**, **`areas.py`** — geometric helpers for the visibility graph computation.

### Key unit conventions

- **Scan input**: any units (`m`, `cm`, `mm`, `in`, `ft`) and any up-axis (`z`, `y`, `x`); converted to metres Z-up internally.
- **Polygons** (`2_walkable.geojson`, shapely objects in `Result`): **metres**.
- **Network** (`3_network.net`, `Vertex.xyz`): **millimetres** (OrientSim's native unit).
- Exits/locations passed via CLI or `Options` are in the scan's own units.

### Output files (written to `<scan>_scan2net/` by default)

| File | Description |
|---|---|
| `1_slice.<ext>` | Points near the slice plane, same format as input |
| `2_walkable.geojson` | Walkable polygons, metres |
| `3_network.net` | Route network, Pajek format, millimetres |
| `preview.svg` | Visual check: slice points + polygons + network |
| `report.txt` | Log of all steps, warnings, and output file paths |
