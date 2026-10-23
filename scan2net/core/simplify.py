"""
Simplify a route network: consolidate chains of short, nearly straight
segments into single edges, and merge vertices that are closer together than a
minimum segment length.

Drawn and generated networks are often cut into many tiny pieces - a curved
corridor traced as dozens of 20-30 cm segments, or junctions a few cm apart.
For a simulation each piece is a separate place with its own (tiny) capacity,
so pupils queue at what is really an open corridor. This pass fixes that.

Three steps, repeated until nothing changes:

  1. straighten  a vertex with exactly two edges whose turn is within
                 --angle of straight (180 deg) is removed and its two edges
                 become one - as long as the corridor it bent stays within
                 --max-deviation of the new straight edge
  2. shorten     a two-edge vertex at the end of a segment shorter than
                 --min-length is removed whatever the angle (small corners),
                 allowing a deviation of up to half the minimum length
  3. merge       an edge still shorter than --min-length between vertices that
                 cannot be removed that way (junctions) is collapsed: its two
                 ends become one vertex. Locations (L) and kept vertices never
                 move; a corridor vertex merges into them; two corridor
                 vertices meet at the middle.

Never removed or moved: locations (L), vertices whose VTYPE is not DEFAULT,
and any vertex listed in `keep` (labels, e.g. the ones a scenario refers to).
Vertex labels are kept (V123 stays V123) so scenarios still find them; the
Pajek numbers are renumbered 1..N.

Merged edges get INPUTCOST = the sum of their parts (the walking length), the
narrowest WIDTH, the lowest LFR, TYPE=STAIR if any part was a stair, and
LENGTH = the walking length in metres - so lengths are real numbers, not the
2.0 placeholder. Distances in the options are in metres; `unit` says how many
metres one coordinate unit is (0.001 for millimetre models).
"""

import math

from .pajek import Vertex, vb_str


def detect_unit(points):
    """0.001 if the coordinates look like millimetres (a building more than
    2000 units across), else 1.0 (metres)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if not xs:
        return 1.0
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    return 0.001 if span > 2000 else 1.0


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def turn_angle(prev, here, nxt):
    """Angle at `here` between the directions to prev and nxt: 180 = straight on."""
    u, w = _sub(prev, here), _sub(nxt, here)
    nu, nw = _norm(u), _norm(w)
    if nu == 0 or nw == 0:
        return 180.0
    c = (u[0] * w[0] + u[1] * w[1] + u[2] * w[2]) / (nu * nw)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def point_line_distance(p, a, b):
    ab = _sub(b, a)
    L2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
    if L2 == 0:
        return _norm(_sub(p, a))
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1] + (p[2] - a[2]) * ab[2]) / L2))
    q = (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t)
    return _norm(_sub(p, q))


def _num(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


class _E:
    __slots__ = ("a", "b", "cost", "walk", "attrs", "inner", "alive")

    def __init__(self, a, b, cost, walk, attrs, inner):
        self.a, self.b = a, b
        self.cost = cost          # INPUTCOST sum
        self.walk = walk          # geometric walking length (file units)
        self.attrs = attrs        # dict of the other attributes
        self.inner = inner        # positions of removed vertices along it, a -> b
        self.alive = True

    def other(self, v):
        return self.b if v == self.a else self.a

    def inner_from(self, v):
        return self.inner if v == self.a else self.inner[::-1]


def _merge_attrs(x, y):
    out = dict(x)
    for key, pick in (("WIDTH", min), ("LFR", min)):
        vx, vy = _num(x.get(key)), _num(y.get(key))
        if vx is not None and vy is not None:
            out[key] = vb_str(pick(vx, vy))
    tx, ty = x.get("TYPE", x.get("ETYPE")), y.get("TYPE", y.get("ETYPE"))
    if "STAIR" in (str(tx).upper(), str(ty).upper()):
        out["TYPE" if "TYPE" in out else "ETYPE"] = "STAIR"
    return out


class SimplifyReport:
    def __init__(self):
        self.vertices_before = self.vertices_after = 0
        self.edges_before = self.edges_after = 0
        self.straightened = self.shortened = self.merged = 0
        self.duplicates = self.loops = 0
        self.shortest_before = self.shortest_after = 0.0
        self.median_before = self.median_after = 0.0
        self.short_left = 0          # edges still under min length (between fixed vertices)
        self.unit = 1.0

    def lines(self):
        u = self.unit
        out = ["simplified: %d -> %d vertices, %d -> %d edges"
               % (self.vertices_before, self.vertices_after, self.edges_before, self.edges_after),
               "  %d nearly straight bends removed, %d short corners removed, %d short edges merged"
               % (self.straightened, self.shortened, self.merged),
               "  edge length: median %.2f -> %.2f m, shortest %.3f -> %.3f m"
               % (self.median_before * u, self.median_after * u,
                  self.shortest_before * u, self.shortest_after * u)]
        if self.duplicates or self.loops:
            out.append("  %d duplicate edges and %d loops created by merging were dropped"
                       % (self.duplicates, self.loops))
        if self.short_left:
            out.append("  %d edges are still shorter than the minimum: both their ends are "
                       "locations or kept vertices" % self.short_left)
        return out


def simplify(vertices, edges, angle=10.0, min_length=0.5, max_deviation=0.1, unit=None,
             keep=(), merge_junctions=True):
    """
    vertices: list of netlib Vertex (any numbering) or {number: Vertex}
    edges:    list of (a, b, attrs) with attrs a dict or list of (key, value),
              or netlib pajek.Edge objects
    angle, min_length, max_deviation: degrees, metres, metres
    unit:     metres per coordinate unit (None: guess from the extent)
    keep:     vertex labels (e.g. "V4", "L2461") that must stay

    Returns (new_vertices, new_edges, report) in the same form write_pajek takes.
    """
    vlist = list(vertices.values()) if isinstance(vertices, dict) else list(vertices)
    if unit is None:
        unit = detect_unit([v.xyz for v in vlist])
    min_len = min_length / unit
    max_dev = max_deviation / unit
    straight_min = 180.0 - angle
    keep = {k.strip().lower() for k in keep if k and k.strip()}

    pos = {v.number: tuple(v.xyz) for v in vlist}
    label = {v.number: "%s%s" % (v.kind, v.label_no) for v in vlist}
    vattrs = {v.number: dict(v.attrs) for v in vlist}

    def fixed(n):
        v = byno[n]
        vt = str(vattrs[n].get("VTYPE", "DEFAULT")).upper()
        return v.kind != "V" or vt != "DEFAULT" or label[n].lower() in keep

    byno = {v.number: v for v in vlist}
    es = []
    adj = {n: [] for n in pos}
    for e in edges:
        if hasattr(e, "start"):
            a, b, attrs = e.start, e.end, dict(e.attrs)
        else:
            a, b, attrs = e[0], e[1], dict(e[2])
        if a == b:
            continue
        walk = _norm(_sub(pos[a], pos[b]))
        cost = _num(attrs.pop("INPUTCOST", None), walk)
        ed = _E(a, b, cost, walk, attrs, [])
        es.append(ed)
        adj[a].append(ed)
        adj[b].append(ed)

    rep = SimplifyReport()
    rep.unit = unit
    rep.vertices_before, rep.edges_before = len(vlist), len(es)
    lens = sorted(e.walk for e in es) or [0.0]
    rep.shortest_before, rep.median_before = lens[0], lens[len(lens) // 2]

    def live(n):
        return [e for e in adj[n] if e.alive]

    def try_remove(n, short_only):
        """Join the two edges of vertex n into one, if allowed. Returns True if done."""
        if fixed(n):
            return False
        es2 = live(n)
        if len(es2) != 2:
            return False
        e1, e2 = es2
        u, w = e1.other(n), e2.other(n)
        if u == w:
            return False
        short = min(e1.walk, e2.walk) < min_len
        straight = turn_angle(pos[u], pos[n], pos[w]) >= straight_min
        if short_only and not short:
            return False
        if not (straight or short):
            return False
        inner = e1.inner_from(u) + [pos[n]] + e2.inner_from(n)
        dev_ok = max_dev if not short else max(max_dev, min_len / 2.0)
        if any(point_line_distance(p, pos[u], pos[w]) > dev_ok for p in inner):
            return False
        e1.alive = e2.alive = False
        new = _E(u, w, e1.cost + e2.cost, e1.walk + e2.walk, _merge_attrs(e1.attrs, e2.attrs), inner)
        es.append(new)
        adj[u].append(new)
        adj[w].append(new)
        adj[n] = []
        dropped.add(n)
        if straight:
            rep.straightened += 1
        else:
            rep.shortened += 1
        return True

    dropped = set()

    def collapse(e):
        """Merge the two ends of a short edge between vertices that stay."""
        a, b = e.a, e.b
        fa, fb = fixed(a), fixed(b)
        if fa and fb:
            return False
        if fa or fb:                # the free end moves onto the fixed one
            keep_n, gone = (a, b) if fa else (b, a)
            newpos = pos[keep_n]
            add_gone, add_keep = (e.cost, e.walk), (0.0, 0.0)
        else:                       # two corridor vertices meet in the middle
            keep_n, gone = (a, b) if a < b else (b, a)
            newpos = tuple((pos[a][i] + pos[b][i]) / 2.0 for i in range(3))
            add_gone = add_keep = (e.cost / 2.0, e.walk / 2.0)
        e.alive = False
        for f in live(keep_n):
            f.cost += add_keep[0]
            f.walk += add_keep[1]
        for f in live(gone):
            if f.a == gone:
                f.a = keep_n
            if f.b == gone:
                f.b = keep_n
            f.cost += add_gone[0]
            f.walk += add_gone[1]
            adj[keep_n].append(f)
        adj[gone] = []
        dropped.add(gone)
        pos[keep_n] = newpos
        rep.merged += 1
        return True

    changed = True
    while changed:
        changed = False
        # 1 + 2: shortest segments first, so tiny pieces fold into their neighbours
        order = sorted((n for n in pos if len(live(n)) == 2 and not fixed(n)),
                       key=lambda n: min(e.walk for e in live(n)))
        for n in order:
            if try_remove(n, short_only=False):
                changed = True
        # 3: collapse what is still too short
        if merge_junctions:
            for e in sorted((e for e in es if e.alive and e.walk < min_len), key=lambda e: e.walk):
                if e.alive and e.walk < min_len and e.a != e.b and collapse(e):
                    changed = True
        # tidy: loops and duplicate edges (keep the cheapest)
        best = {}
        for e in es:
            if not e.alive:
                continue
            if e.a == e.b:
                e.alive = False
                rep.loops += 1
                continue
            key = (min(e.a, e.b), max(e.a, e.b))
            if key in best:
                rep.duplicates += 1
                if e.cost < best[key].cost:
                    best[key].alive = False
                    best[key] = e
                else:
                    e.alive = False
            else:
                best[key] = e

    # output, keeping the original vertex order and labels
    used = [n for n in sorted(pos) if n not in dropped]
    renum = {n: i for i, n in enumerate(used, 1)}
    new_vertices = []
    for n in used:
        v = byno[n]
        xyz = pos[n]
        moved = xyz != tuple(v.xyz)
        new_vertices.append(Vertex(renum[n], v.kind, v.label_no, list(v.attrs), xyz,
                                   None if moved else v.xyz_text))
    new_edges = []
    for e in es:
        if not e.alive:
            continue
        attrs = dict(e.attrs)
        out = []
        for key in ("TYPE", "ETYPE", "LENGTH", "WIDTH"):
            if key in attrs:
                out.append((key, attrs.pop(key)))
        out = [(k, vb_str(round(e.walk * unit, 3)) if k == "LENGTH" else v) for k, v in out]
        if not any(k == "LENGTH" for k, _ in out):
            out.append(("LENGTH", vb_str(round(e.walk * unit, 3))))
        out.append(("INPUTCOST", vb_str(e.cost)))
        out += list(attrs.items())
        new_edges.append((renum[e.a], renum[e.b], out))
    new_edges.sort(key=lambda t: (min(t[0], t[1]), max(t[0], t[1])))

    rep.vertices_after, rep.edges_after = len(new_vertices), len(new_edges)
    lens = sorted(e.walk for e in es if e.alive) or [0.0]
    rep.shortest_after, rep.median_after = lens[0], lens[len(lens) // 2]
    rep.short_left = sum(1 for e in es if e.alive and e.walk < min_len)
    return new_vertices, new_edges, rep


# --------------------------------------------------------------------------
# shared command-line options
# --------------------------------------------------------------------------

def add_arguments(ap, default_on=False):
    g = ap.add_argument_group("simplifying the network (consolidate short / straight segments)")
    if default_on:
        g.add_argument("--no-simplify", dest="simplify", action="store_false",
                       help="write the network as it is, without consolidating segments")
    else:
        g.add_argument("--simplify", action="store_true",
                       help="consolidate nearly straight chains and segments shorter than --min-length")
    g.add_argument("--angle", type=float, default=10.0,
                   help="bends within this many degrees of straight are removed (default 10)")
    g.add_argument("--min-length", type=float, default=0.5,
                   help="minimum segment length, metres (default 0.5)")
    g.add_argument("--max-deviation", type=float, default=0.1,
                   help="how far a removed bend may sit off the new straight edge, metres (default 0.1)")
    g.add_argument("--units", choices=("auto", "mm", "cm", "m"), default="auto",
                   help="coordinate units, for the metre values above (default: guess from the size)")
    g.add_argument("--keep", action="append", default=[], metavar="LABEL[,LABEL]",
                   help="vertex labels that must stay, e.g. V4,V10 (repeatable)")
    g.add_argument("--keep-scenario", action="append", default=[], metavar="SCENARIO.xml",
                   help="keep every vertex an OrientSim scenario file names")
    g.add_argument("--no-merge-junctions", dest="merge_junctions", action="store_false",
                   help="only remove two-edge vertices; never move junctions together")


def scenario_labels(path):
    """Vertex labels named in an OrientSim / StupidModel scenario file."""
    import xml.etree.ElementTree as ET
    names = set()
    root = ET.parse(path).getroot()
    for tag in ("StartVertex", "HomeVertex", "Vertex"):
        for el in root.iter(tag):
            if el.text and not el.text.strip().startswith("~"):
                names.add(el.text.strip())
    return names


def from_args(vertices, edges, args):
    keep = set()
    for k in args.keep:
        keep.update(x.strip() for x in k.split(","))
    for path in args.keep_scenario:
        keep |= scenario_labels(path)
    unit = {"auto": None, "mm": 0.001, "cm": 0.01, "m": 1.0}[args.units]
    return simplify(vertices, edges, args.angle, args.min_length, args.max_deviation, unit,
                    keep, args.merge_junctions)
