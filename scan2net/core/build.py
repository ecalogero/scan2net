"""
High-level network building: a visibility route network inside walkable-space
polygons (the same code OrientSim's importer uses).
"""

from .attrs import AttributeResolver
from .pajek import Vertex, vb_str
from . import visibility


def build_visibility(areas, extra, tolerance=0.001, z_tol=1.0, overrides=None):
    """Visibility route network inside walkable areas (AutoNetwork).

    areas: netlib Areas; extra: [(xyz, attrs, name)] points to connect
    (locations, start, end). Returns (vertices, edges, notes, resolver) with
    corners numbered first, then locations, as route files are."""
    nodes, links, notes = visibility.visibility_network(areas, extra, tolerance, z_tol)
    resolver = AttributeResolver(overrides)
    order = sorted(range(len(nodes)), key=lambda i: (nodes[i].kind != "V", i))
    number = {}
    out_vertices = []
    for n, i in enumerate(order, 1):
        node = nodes[i]
        attrs = resolver.resolve(node.attrs, node.kind)
        out_vertices.append(Vertex(n, node.kind, str(n), list(attrs.items()), node.xyz))
        number[i] = n
    out_edges = []
    for i, j, length in links:
        area_attrs = areas[nodes[i].area].attrs
        attrs = resolver.resolve(area_attrs, "E")
        out_edges.append((number[i], number[j],
                          [("TYPE", attrs["ETYPE"]), ("LENGTH", attrs["LENGTH"]),
                           ("WIDTH", attrs["WIDTH"]), ("INPUTCOST", vb_str(length)),
                           ("LFR", attrs["LFR"])]))
    out_edges.sort(key=lambda e: (e[0], e[1]))
    return out_vertices, out_edges, notes, resolver
