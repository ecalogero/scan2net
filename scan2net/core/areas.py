"""
Walkable areas: polygons with holes, gathered from a Scene.

Areas come from two places:
  * Scene.areas - formats that store holes explicitly (GeoJSON, CSV)
  * closed curves (3dm, DXF, CSV-less sources): nested by containment with the
    even-odd rule, so a closed curve inside another becomes a hole, a curve
    inside that hole becomes a new area (an island), and so on.

Pure Python; the visibility code turns these into shapely polygons.
"""

import math

from .model import Area


def ring_area(ring):
    """Signed area of a ring in plan (x, y)."""
    s = 0.0
    for (x1, y1, *_), (x2, y2, *_) in zip(ring, ring[1:] + ring[:1]):
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ring_contains(ring, pt):
    """Point strictly inside a ring, in plan (ray casting)."""
    x, y = pt[0], pt[1]
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def clean_ring(ring):
    """Drop a repeated closing point and consecutive duplicates."""
    out = []
    for p in ring:
        if not out or math.dist(p, out[-1]) > 0:
            out.append(tuple(p))
    if len(out) > 1 and math.dist(out[0], out[-1]) == 0:
        out.pop()
    return out


def ring_z(ring):
    return sum(p[2] for p in ring) / len(ring) if ring else 0.0


def collect_areas(scene, layers=None, z_tol=1.0):
    """All walkable areas in the scene.

    layers: restrict closed curves / areas to these layer names (None = all).
    z_tol:  closed curves are only nested inside others at the same height
            (within z_tol), so floor plans stacked in 3D stay separate.
    """
    def wanted(obj):
        return layers is None or obj.layer in layers

    result = []
    for a in scene.areas:
        if wanted(a):
            outer = clean_ring(a.outer)
            if len(outer) >= 3:
                result.append(Area(outer, [clean_ring(h) for h in a.holes if len(clean_ring(h)) >= 3],
                                   a.layer, a.name, a.attrs))

    rings = []
    for c in scene.closed_curves:
        if wanted(c):
            r = clean_ring(c.pts)
            if len(r) >= 3 and abs(ring_area(r)) > 0:
                rings.append((r, c))
    rings.sort(key=lambda rc: -abs(ring_area(rc[0])))

    # depth of each ring = number of larger rings (same height) containing it
    placed = []  # (ring, curve, depth, area-or-None)
    for r, c in rings:
        z = ring_z(r)
        parents = [p for p in placed
                   if abs(ring_z(p[0]) - z) <= z_tol and ring_contains(p[0], r[0])]
        depth = len(parents)
        if depth % 2 == 0:
            area = Area(r, [], c.layer, c.name, c.attrs)
            result.append(area)
            placed.append((r, c, depth, area))
        else:
            # hole of the innermost containing outer ring
            owner = max((p for p in parents if p[3] is not None), key=lambda p: p[2])
            owner[3].holes.append(r)
            placed.append((r, c, depth, None))
    return result
