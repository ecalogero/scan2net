"""
The three steps, usable from Python as well as from the command line:

    from scan2net.pipeline import Options, run
    result = run("scan.ply", "out/", Options(units="m", exits=[(12.5, 3.0)]))

1. slice     keep the points within `tol` of a plane `height` above the floor
2. walkable  turn the slice into walkable-space polygons (free floor area)
3. network   a visibility route network inside the polygons, simplified, with
             the exit(s) and locations you give linked in, written as a Pajek
             .net in millimetres - the format OrientSim reads
"""

import json
import math
import os
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .core import pointcloud as pc
from .core import scan
from .core.attrs import parse_overrides
from .core.build import build_visibility
from .core.pajek import write_pajek
from .core.simplify import simplify as simplify_network


@dataclass
class Options:
    units: str = "m"                  # units of the scan: m, cm, mm, in, ft
    up: str = "z"                     # vertical axis of the scan
    sliced: bool = False              # the input is already a slice
    height: float = 1.0               # slice height above the floor, m
    tol: float = 0.003                # half thickness of the slice, m
    floor_percentile: float = 0.1     # floor = this percentile of heights (0 = lowest point)
    cell: float = 0.05                # grid cell, m
    gap: float = 0.3                  # close holes in walls narrower than this, m
    door_max: float = 2.0             # openings to the outside up to this wide are bridged, m
    clearance: float = 0.0            # extra distance kept from walls, m
    min_area: float = 1.0             # drop free regions smaller than this, m2
    polygon_simplify: float = 0.15    # simplification of the walkable polygons, m
    exits: List[Tuple[float, float]] = field(default_factory=list)       # scan units, Z up
    locations: List[Tuple[float, float]] = field(default_factory=list)   # scan units, Z up
    seed_regions: bool = False        # keep only the walkable regions containing exits / locations
    simplify: bool = True
    min_length: float = 0.5           # network: minimum segment length, m
    angle: float = 10.0               # network: bends within this of straight are removed, degrees
    width: float = 1.2                # corridor width written to the network, m
    capacity: float = 30              # vertex capacity written to the network, pupils
    stop_after: Optional[str] = None  # "slice" or "walkable"


class Result:
    def __init__(self):
        self.files = {}
        self.lines = []
        self.warnings = []
        self.polygons = []
        self.slice_xy = None
        self.floor = 0.0
        self.vertices = []
        self.edges = []
        self.exit_points = []
        self.location_points = []


def _say(res, text, progress):
    res.lines.append(text)
    if progress:
        progress(text)


# ---------------------------------------------------------------------------
def write_slice(path, out_path, opt):
    """Step 1 as a file: the scan's points near the plane, same format as the input."""
    ext = os.path.splitext(path)[1].lower()
    axis = pc.AXES[opt.up]
    scale = pc.UNITS[opt.units]
    if ext == ".ply":
        ply = pc.Ply(path)
        arr, lines = ply.read_vertices()
        h = np.asarray(arr["xyz"[axis]], dtype=np.float64)
    else:
        h = pc._text_heights(path, axis)
    if h.size == 0:
        raise ValueError("no points in %s" % path)
    low = float(h.min()) if opt.floor_percentile <= 0 else float(np.percentile(h, opt.floor_percentile))
    plane = low + opt.height / scale
    lo, hi = plane - opt.tol / scale, plane + opt.tol / scale
    if ext == ".ply":
        kept = ply.write_subset(out_path, arr, lines, (h >= lo) & (h <= hi))
    else:
        kept = pc.slice_text(path, out_path, axis, lo, hi)
    span = (h.max() - h.min()) * scale
    return h.size, kept, low * scale, span


def _g(c):
    return "%.6g" % round(c, 4)


def _mm(c):
    s = ("%.1f" % c).rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _inside(polys, pt):
    """Move a point (metres) into the nearest walkable polygon if it is just outside."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    p = Point(pt)
    best, bd = None, None
    for poly in polys:
        if poly.covers(p):
            return pt, 0.0
        q = nearest_points(poly.buffer(-0.05) if poly.buffer(-0.05).area > 0 else poly, p)[0]
        d = q.distance(p)
        if bd is None or d < bd:
            best, bd = (q.x, q.y), d
    return best, bd


def run(path, outdir, opt=None, progress=None):
    opt = opt or Options()
    res = Result()
    os.makedirs(outdir, exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".ply",) + pc.XYZ_EXTS:
        raise ValueError("unsupported file type %r (use .ply, .xyz, .txt, .pts, .csv or .asc)" % ext)
    scale = pc.UNITS[opt.units]

    # 1 -- slice -------------------------------------------------------------------
    if not opt.sliced:
        slice_path = os.path.join(outdir, "1_slice" + ext)
        n, kept, floor_m, span = write_slice(path, slice_path, opt)
        res.files["slice"] = slice_path
        _say(res, "1. slice: %d of %d points within %g mm of a plane %g m above the floor (scan %.2f m tall)"
             % (kept, n, opt.tol * 1000, opt.height, span), progress)
        if kept == 0:
            raise ValueError("no points near the slice plane - check --units and --up")
        if kept < 1000:
            res.warnings.append("only %d points in the slice - for a sparse scan try --tol 0.01 or 0.02" % kept)
        if span > 50:
            res.warnings.append("the scan is %.0f m tall - if it is in millimetres use --units mm" % span)
        elif span < 0.5:
            res.warnings.append("the scan is only %.2f m tall - wrong --units or --up?" % span)
        if opt.stop_after == "slice":
            return res
        source, sliced = slice_path, True
    else:
        source, sliced = path, True

    # 2 -- walkable space -----------------------------------------------------------------
    _say(res, "2. finding walkable space…", progress)
    seeds = []
    if opt.seed_regions:
        seeds = [(x * scale, y * scale) for x, y in opt.exits + opt.locations]
    sres = scan.walkable_polygons(source, units=opt.units, up=opt.up, sliced=sliced, height=opt.height,
                                  tol=opt.tol, floor_percentile=opt.floor_percentile, cell=opt.cell,
                                  gap=opt.gap, door_max=opt.door_max, clearance=opt.clearance,
                                  min_area=opt.min_area, simplify=opt.polygon_simplify, seeds=seeds)
    res.polygons, res.floor, res.slice_xy = sres.polygons, sres.floor, sres.slice_points
    if not res.polygons:
        raise ValueError("no walkable space found - try a larger --door-max, a smaller --gap, or check --units/--up")
    area = sum(p.area for p in res.polygons)
    holes = sum(len(p.interiors) for p in res.polygons)
    _say(res, "   %d walkable area(s), %.1f m2, %d holes (columns, furniture), floor at %.3f m"
         % (len(res.polygons), area, holes, res.floor), progress)
    wpath = os.path.join(outdir, "2_walkable.geojson")
    feats = []
    for i, p in enumerate(res.polygons, 1):
        rings = [[[round(x, 4), round(y, 4), round(res.floor, 4)] for x, y in r.coords]
                 for r in [p.exterior] + list(p.interiors)]
        feats.append({"type": "Feature", "properties": {"layer": "Walkable", "name": "walkable %d" % i,
                                                         "area_m2": round(p.area, 2)},
                      "geometry": {"type": "Polygon", "coordinates": rings}})
    with open(wpath, "w") as f:
        json.dump({"type": "FeatureCollection", "units": "m", "features": feats}, f)
    res.files["walkable"] = wpath
    if opt.stop_after == "walkable":
        return res

    # 3 -- network -----------------------------------------------------------------------------
    _say(res, "3. building the route network…", progress)
    extra = []
    for kind, pts, vtype in (("exit", opt.exits, "EXIT"), ("location", opt.locations, "LOCN")):
        for i, (x, y) in enumerate(pts, 1):
            pt, moved = _inside(res.polygons, (x * scale, y * scale))
            if pt is None:
                res.warnings.append("%s %d is not near any walkable area; left out" % (kind, i))
                continue
            if moved > 0.0:
                res.warnings.append("%s %d was %.2f m outside the walkable area; moved inside" % (kind, i, moved))
            xyz = (pt[0] * 1000.0, pt[1] * 1000.0, res.floor * 1000.0)
            extra.append((xyz, {"VTYPE": vtype, "role": kind}, "%s%d" % (kind, i)))
            (res.exit_points if vtype == "EXIT" else res.location_points).append(pt)
    areas = scan.polygons_to_areas(res.polygons, 0.001, res.floor)            # millimetres
    overrides = parse_overrides(["WIDTH=%g" % opt.width, "CAPACITY=%g" % opt.capacity])
    vertices, edges, notes, _ = build_visibility(areas, extra, 1.0, 1000.0, overrides)
    res.warnings += notes
    n0v, n0e = len(vertices), len(edges)
    if opt.simplify:
        vertices, edges, rep = simplify_network(vertices, edges, opt.angle, opt.min_length, 0.1, 0.001)
        _say(res, "   %d corners and points linked by %d corridors, simplified to %d vertices and %d corridors"
             % (n0v, n0e, len(vertices), len(edges)), progress)
    else:
        _say(res, "   %d vertices, %d corridors" % (n0v, n0e), progress)
    res.vertices, res.edges = vertices, edges
    for v in vertices:                                   # tidy coordinates: 0.1 mm is plenty
        v.xyz_text = [_mm(c) for c in v.xyz]
    npath = os.path.join(outdir, "3_network.net")
    write_pajek(npath, vertices, edges)
    res.files["network"] = npath
    lo, med, hi = length_summary(vertices, edges)
    _say(res, "   corridor segments %.2f m to %.1f m (median %.1f m)" % (lo, hi, med), progress)
    for v in vertices:
        t = dict(v.attrs).get("VTYPE")
        if t in ("EXIT", "LOCN"):
            _say(res, "   %s %s%s at %s, %s" % ("exit" if t == "EXIT" else "location", v.kind, v.label_no,
                                             _g(v.xyz[0] / 1000.0 / scale), _g(v.xyz[1] / 1000.0 / scale)),
                 progress)
    for line in check(vertices, edges):
        _say(res, line, progress)
    return res


def check(vertices, edges):
    """Short summary: exits, locations, and whether everyone can get out."""
    nbr = {v.number: set() for v in vertices}
    for a, b, _ in edges:
        nbr[a].add(b)
        nbr[b].add(a)
    vt = {v.number: dict(v.attrs).get("VTYPE", "DEFAULT") for v in vertices}
    exits = [n for n, t in vt.items() if t == "EXIT"]
    locs = [n for n, t in vt.items() if t == "LOCN"]
    out = []
    if not exits:
        out.append("   no exit marked - add --exit X,Y (or mark one in OrientSim's ✎ Edit) before simulating "
                   "an evacuation")
        return out
    seen, todo = set(exits), deque(exits)
    while todo:
        v = todo.popleft()
        for w in nbr[v]:
            if w not in seen:
                seen.add(w)
                todo.append(w)
    stuck = [n for n in locs if n not in seen]
    unreached = sum(1 for v in vertices if v.number not in seen)
    out.append("   %d exit(s), %d location(s); %s" % (
        len(exits), len(locs), "every location can reach an exit" if not stuck else
        "%d location(s) cannot reach an exit" % len(stuck)))
    if unreached:
        out.append("   %d vertices are in walkable areas not connected to an exit" % unreached)
    return out


def length_summary(vertices, edges):
    pos = {v.number: v.xyz for v in vertices}
    lens = sorted(math.dist(pos[a], pos[b]) / 1000.0 for a, b, _ in edges) or [0.0]
    return lens[0], lens[len(lens) // 2], lens[-1]
