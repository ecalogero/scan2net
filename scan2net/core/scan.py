"""
Walkable space from 3D scans: slice a point cloud, rasterise it, find the free
floor area inside the building outline and trace it into polygons.
`walkable_polygons()` runs all the steps (see the README for how they work).
"""

import json
import math

import numpy as np

from . import pointcloud as pc

try:
    from scipy import ndimage
except ImportError:                                       # pragma: no cover
    ndimage = None
try:
    import shapely
    from shapely.geometry import box, Polygon, MultiPolygon
    from shapely.ops import unary_union
except ImportError:                                       # pragma: no cover
    shapely = None


def _need():
    if ndimage is None:
        raise ImportError("3D scans need scipy:  pip install scipy")
    if shapely is None:
        raise ImportError("3D scans need shapely:  pip install shapely")


def to_z_up(pts, up):
    """Reorder so the vertical axis is z (right-handed: Y-up (x,y,z) -> (x,-z,y))."""
    if up == "z":
        return pts
    if up == "y":
        return np.column_stack([pts[:, 0], -pts[:, 2], pts[:, 1]])
    return np.column_stack([pts[:, 1], pts[:, 2], pts[:, 0]])      # X-up


def take_slice(pts, height, tol, floor_percentile):
    z = pts[:, 2]
    floor = float(z.min()) if floor_percentile <= 0 else float(np.percentile(z, floor_percentile))
    plane = floor + height
    keep = np.abs(z - plane) <= tol
    return pts[keep], floor, plane


# --------------------------------------------------------------------------
# raster steps (all distances in cells)
# --------------------------------------------------------------------------

def dilate(mask, r):
    if r <= 0:
        return mask.copy()
    return ndimage.distance_transform_edt(~mask) <= r


def erode(mask, r):
    if r <= 0:
        return mask.copy()
    return ndimage.distance_transform_edt(mask) > r


def envelope(occ, x0, y0, cell, door_max):
    """Building outline as a polygon (a 'chi-shape'): Delaunay-triangulate the
    wall cells, then eat triangles in from the convex hull, crossing only sides
    longer than door_max. Openings to the outside narrower than door_max (open doors,
    unscanned glass) get a straight edge across instead of letting the outside
    in. (A morphological closing does not seal gaps in thin walls, and a
    rolling-disk test leaves round bulges in the openings, hence this.)"""
    from scipy.spatial import Delaunay
    iy, ix = np.nonzero(occ)
    pts = np.column_stack([x0 + (ix + 0.5) * cell, y0 + (iy + 0.5) * cell])
    if len(pts) < 3:
        raise ValueError("too few wall points to find an outline")
    tri = Delaunay(pts)
    simp, nb = tri.simplices, tri.neighbors
    # length of the side opposite each vertex of each triangle
    a, b, c = pts[simp[:, 0]], pts[simp[:, 1]], pts[simp[:, 2]]
    side = np.column_stack([np.linalg.norm(b - c, axis=1), np.linalg.norm(c - a, axis=1),
                            np.linalg.norm(a - b, axis=1)])
    # carve from the convex hull inwards, only ever crossing sides longer than door_max
    outside = np.zeros(len(simp), dtype=bool)
    stack = [i for i in range(len(simp)) for k in range(3) if nb[i, k] == -1 and side[i, k] > door_max]
    for i in stack:
        outside[i] = True
    while stack:
        i = stack.pop()
        for k in range(3):
            j = nb[i, k]
            if j >= 0 and not outside[j] and side[i, k] > door_max:
                outside[j] = True
                stack.append(j)
    keep = pts[simp[~outside]]
    if len(keep) == 0:
        raise ValueError("no outline found - the wall points are further apart than --door-max")
    return unary_union(list(shapely.polygons(keep))).buffer(0)


def raster_of(poly, x0, y0, cell, shape):
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    shapely.prepare(poly)
    return shapely.contains_xy(poly, x0 + (xx + 0.5) * cell, y0 + (yy + 0.5) * cell)


def free_space(xy, cell, gap, door_max, clearance, min_points):
    """Rasterise and return (free mask, x0, y0, occupied mask)."""
    pad = int(math.ceil(door_max / cell)) + 4
    x0 = xy[:, 0].min() - pad * cell
    y0 = xy[:, 1].min() - pad * cell
    ix = np.floor((xy[:, 0] - x0) / cell).astype(np.int64)
    iy = np.floor((xy[:, 1] - y0) / cell).astype(np.int64)
    nx, ny = ix.max() + pad + 1, iy.max() + pad + 1
    if nx * ny > 400_000_000:
        raise ValueError("grid would be %d x %d cells - use a bigger --cell" % (nx, ny))
    counts = np.zeros((ny, nx), dtype=np.int32)
    np.add.at(counts, (iy, ix), 1)
    occ = counts >= min_points
    obstacle = dilate(occ, gap / 2.0 / cell)
    env = envelope(occ, x0, y0, cell, door_max)
    in_env = raster_of(env, x0, y0, cell, occ.shape)
    free = in_env & ~obstacle
    if clearance > 0:
        free = erode(free, clearance / cell)
    return free, x0, y0, occ, ~in_env


def open_fraction(lab, n, outside):
    """For each region, the share of its edge cells that touch the outside of the
    outline directly (no wall in between)."""
    edge = np.zeros(n + 1)
    opened = np.zeros(n + 1)
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nb_lab = np.roll(lab, (dy, dx), axis=(0, 1))
        nb_out = np.roll(outside, (dy, dx), axis=(0, 1))
        border = (lab > 0) & (nb_lab != lab)
        np.add.at(edge, lab[border], 1)
        np.add.at(opened, lab[border & nb_out], 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(edge > 0, opened / edge, 0.0)


def regions(free, cell, min_area, seeds_ij, outside, max_open):
    """Label 4-connected free regions; return list of (label mask, area m2) kept, and dropped count."""
    lab, n = ndimage.label(free)            # default structure = 4-connectivity
    if n == 0:
        return [], 0
    sizes = np.bincount(lab.ravel())
    frac = open_fraction(lab, n, outside)
    if seeds_ij:
        wanted = set()
        for i, j in seeds_ij:
            if 0 <= i < lab.shape[0] and 0 <= j < lab.shape[1] and lab[i, j] > 0:
                wanted.add(int(lab[i, j]))
            else:
                print("warning: seed at grid cell (%d, %d) is not in free space" % (j, i))
        ids = sorted(wanted)
    else:
        ids = [k for k in range(1, n + 1)
               if sizes[k] * cell * cell >= min_area and frac[k] <= max_open]
    kept = [(lab == k, sizes[k] * cell * cell) for k in ids]
    return kept, n - len(kept)


def trace(mask, x0, y0, cell):
    """Raster region -> shapely (Multi)Polygon, by merging row runs of cells."""
    boxes = []
    for i in np.nonzero(mask.any(axis=1))[0]:
        row = mask[i]
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        yb, yt = y0 + i * cell, y0 + (i + 1) * cell     # same expression for shared edges
        for s, e in zip(starts, ends):
            boxes.append(box(x0 + s * cell, yb, x0 + e * cell, yt))
    # (not coverage_union: runs in neighbouring rows meet at T-junctions, which
    # a coverage union silently leaves unmerged)
    return unary_union(boxes)


def polygons_of(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------

def _ring(coords, scale, z):
    return [[round(x / scale, 6), round(y / scale, 6), round(z / scale, 6)] for x, y in coords]


def write_geojson(path, polys, scale, z, layer):
    feats = []
    for n, p in enumerate(polys, 1):
        p = shapely.geometry.polygon.orient(p, 1.0)        # outer anticlockwise (GeoJSON)
        rings = [_ring(p.exterior.coords, scale, z)] + [_ring(h.coords, scale, z) for h in p.interiors]
        feats.append({"type": "Feature",
                      "properties": {"layer": layer, "name": "walkable %d" % n,
                                     "area_m2": round(p.area, 2), "holes": len(p.interiors)},
                      "geometry": {"type": "Polygon", "coordinates": rings}})
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)


def write_dxf(path, polys, scale, z, layer):
    try:
        import ezdxf
    except ImportError:
        raise ImportError(".dxf output needs ezdxf:  pip install ezdxf")
    doc = ezdxf.new("R2010")
    doc.layers.add(layer, color=3)
    msp = doc.modelspace()
    for p in polys:
        for ring in [p.exterior] + list(p.interiors):
            pts = [(x / scale, y / scale) for x, y in list(ring.coords)[:-1]]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer, "elevation": z / scale})
    doc.saveas(path)


def write_svg(path, xy, polys, scale_label=""):
    xs, ys = xy[:, 0], xy[:, 1]
    allx = [xs.min(), xs.max()] + [c for p in polys for c in p.bounds[0::2]]
    ally = [ys.min(), ys.max()] + [c for p in polys for c in p.bounds[1::2]]
    x0, x1, y0, y1 = min(allx), max(allx), min(ally), max(ally)
    w = 1200.0
    k = w / max(x1 - x0, 1e-9)
    h = (y1 - y0) * k
    m = 20

    def pt(x, y):
        return "%.1f,%.1f" % (m + (x - x0) * k, m + (y1 - y) * k)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" style="background:#fff">'
           % (w + 2 * m, h + 2 * m)]
    for p in polys:
        d = ""
        for ring in [p.exterior] + list(p.interiors):
            c = list(ring.coords)
            d += "M" + " L".join(pt(x, y) for x, y in c) + " Z "
        out.append('<path d="%s" fill="#5ac878" fill-opacity="0.45" stroke="#1e7a3c" '
                   'stroke-width="1.2" fill-rule="evenodd"/>' % d)
    step = max(1, len(xy) // 60000)
    out.append('<g fill="#333">')
    for x, y in xy[::step]:
        out.append('<rect x="%.1f" y="%.1f" width="1.2" height="1.2"/>'
                   % (m + (x - x0) * k - 0.6, m + (y1 - y) * k - 0.6))
    out.append("</g>")
    bar = 10 ** math.floor(math.log10(max((x1 - x0) / 5, 1e-9)))
    out.append('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#000" stroke-width="3"/>'
               % (m, h + m + 10, m + bar * k, h + m + 10))
    out.append('<text x="%d" y="%.1f" font-family="sans-serif" font-size="12">%g m%s</text>'
               % (m, h + m + 4, bar, scale_label))
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))


# --------------------------------------------------------------------------


class ScanResult:
    def __init__(self):
        self.polygons = []          # shapely Polygons, metres, Z up
        self.floor = 0.0            # floor height, metres
        self.slice_points = None    # (N, 2) xy of the slice, metres
        self.n_points = 0
        self.regions_kept = self.regions_dropped = 0
        self.notes = []


def walkable_polygons(path, units="m", up="z", sliced=False, height=1.0, tol=0.003,
                      floor_percentile=0.1, cell=0.05, gap=0.3, door_max=2.0, clearance=0.0,
                      min_area=1.0, max_open=0.25, min_points=1, simplify=None, seeds=(),
                      progress=None):
    """Read a scan (or a ready slice with sliced=True) and return a ScanResult
    with the walkable polygons in metres. `seeds` are (x, y) in metres."""
    _need()
    say = progress or (lambda text: None)
    scale = pc.UNITS[units]
    say("reading points")
    pts = pc.read_points(path)
    res = ScanResult()
    res.n_points = len(pts)
    if len(pts) == 0:
        raise ValueError("no points in %s" % path)
    pts = to_z_up(pts, up) * scale
    if not sliced:
        say("taking the slice")
        pts, floor, plane = take_slice(pts, height, tol, floor_percentile)
        if len(pts) == 0:
            raise ValueError("no points near the slice plane - check the units and the vertical axis")
    else:
        plane = float(np.median(pts[:, 2]))
        floor = plane - height
    res.floor = floor
    xy = pts[:, :2]
    res.slice_points = xy
    say("finding free space")
    free, x0, y0, occ, outside = free_space(xy, cell, gap, door_max, clearance, min_points)
    seeds_ij = [(int((sy - y0) // cell), int((sx - x0) // cell)) for sx, sy in seeds]
    kept, dropped = regions(free, cell, min_area, seeds_ij, outside, max_open)
    res.regions_kept, res.regions_dropped = len(kept), dropped
    say("tracing polygons")
    tol_s = simplify if simplify is not None else cell
    for mask, _ in kept:
        for p in polygons_of(trace(mask, x0, y0, cell)):
            for q in polygons_of(p.simplify(tol_s, preserve_topology=True).buffer(0)):
                if not q.is_empty and q.area >= min(min_area, 0.01):
                    res.polygons.append(q)
    res.polygons.sort(key=lambda p: -p.area)
    return res


def polygons_to_areas(polys, out_scale, z, layer="Walkable"):
    """shapely Polygons (metres) -> netlib Areas in output units (for visibility networks)."""
    from .model import Area
    areas = []
    for p in polys:
        p = shapely.geometry.polygon.orient(p, 1.0)
        outer = [(x / out_scale, y / out_scale, z / out_scale) for x, y in list(p.exterior.coords)[:-1]]
        holes = [[(x / out_scale, y / out_scale, z / out_scale) for x, y in list(h.coords)[:-1]]
                 for h in p.interiors]
        areas.append(Area(outer, holes, layer, "walkable", {}))
    return areas
