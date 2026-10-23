"""
Visibility network inside walkable areas: a port of AutoNetwork_006.rvb
(March 2010), using shapely (pip install shapely) for the geometry.

For each walkable area (a polygon, optionally with holes):
  * nodes are the area's corners (outer ring and holes, duplicates removed)
    plus any extra points that lie inside it (start/end/locations)
  * every pair of nodes is joined by a straight link if the link stays inside
    the area; links along the boundary count as inside

This matches what the original produced (all corner-to-corner and
corner-to-start/end lines, minus those leaving the polygon) and fixes its
gaps: links grazing a corner on the way out are now rejected, holes are
supported, and nothing depends on the Rhino selection state.

Heights: areas are treated in plan (x, y). Each extra point is assigned to the
area that contains it in plan and whose height is within z_tol of the point,
so floor plans stacked at different heights stay separate.
"""

import math
from itertools import combinations

from .areas import ring_z


def _require():
    try:
        import shapely  # noqa: F401
        from shapely.geometry import Polygon, LineString, Point
        from shapely.prepared import prep
        return Polygon, LineString, Point, prep
    except ImportError:
        raise ImportError("the visibility network needs shapely: pip install shapely")


class VisNode:
    __slots__ = ("xyz", "kind", "attrs", "name", "area")

    def __init__(self, xyz, kind, attrs=None, name="", area=None):
        self.xyz = tuple(xyz)
        self.kind = kind          # "V" corner, "L" location/start/end
        self.attrs = attrs or {}  # raw attribute texts from the input
        self.name = name
        self.area = area          # index of the area it belongs to


def _dedupe(points, tol):
    out = []
    for p in points:
        if not any(math.dist(p[:2], q[:2]) <= tol for q in out):
            out.append(p)
    return out


def visibility_network(areas, extra_points=(), tol=1e-6, z_tol=1.0, eps=None):
    """Build the visibility network.

    areas:        list of netlib.model.Area (rings without repeated closing point)
    extra_points: list of (xyz, attrs, name) to connect (start/end/locations)
    tol:          corners closer than this (in plan) are merged
    eps:          numeric slack when testing "inside" (default: 1e-9 x area size)

    Returns (nodes, links, notes): nodes is a list of VisNode, links a list of
    (i, j, length) with node indexes, notes a list of warnings.
    """
    Polygon, LineString, Point, prep = _require()
    nodes, links, notes = [], [], []
    polys = []
    for a in areas:
        poly = Polygon([p[:2] for p in a.outer], [[p[:2] for p in h] for h in a.holes])
        if not poly.is_valid:
            fixed = poly.buffer(0)
            notes.append("area %s was not a valid polygon (self-intersecting?); repaired"
                         % (a.name or len(polys) + 1))
            poly = fixed
        polys.append(poly)

    assigned = [[] for _ in areas]
    for xyz, attrs, name in extra_points:
        hits = [i for i, (a, poly) in enumerate(zip(areas, polys))
                if abs(ring_z(a.outer) - xyz[2]) <= z_tol and poly.covers(Point(xyz[0], xyz[1]))]
        if not hits:
            notes.append("point %s at (%g, %g, %g) is not inside any walkable area; left out"
                         % (name or "?", xyz[0], xyz[1], xyz[2]))
            continue
        assigned[hits[0]].append((xyz, attrs, name))

    for ai, (a, poly) in enumerate(zip(areas, polys)):
        minx, miny, maxx, maxy = poly.bounds
        slack = eps if eps is not None else 1e-9 * max(maxx - minx, maxy - miny, 1.0)
        test = prep(poly.buffer(slack)) if slack > 0 else prep(poly)
        corners = _dedupe([p for ring in [a.outer] + list(a.holes) for p in ring], tol)
        first = len(nodes)
        for p in corners:
            nodes.append(VisNode(p, "V", {}, "", ai))
        for xyz, attrs, name in assigned[ai]:
            nodes.append(VisNode(xyz, "L", attrs, name, ai))
        idx = range(first, len(nodes))
        for i, j in combinations(idx, 2):
            p, q = nodes[i].xyz, nodes[j].xyz
            if p[:2] == q[:2]:
                continue
            if test.covers(LineString([p[:2], q[:2]])):
                links.append((i, j, math.dist(p, q)))
    return nodes, links, notes
