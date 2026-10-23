"""
Pajek .paj reading and writing, matching the files the 2013 RhinoScript tool
produced (labels like "V12|VTYPE=...", Windows line endings, an empty *Arcs
section followed by *Edges), and VBScript-compatible number formatting.
"""

import sys


# --------------------------------------------------------------------------
# VBScript-compatible number formatting
# --------------------------------------------------------------------------

def vb_str(x):
    """Format a float the way VBScript CStr() formats a Double (15 sig. digits)."""
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    s = "%.15g" % x
    if "e" in s:  # VBScript writes e.g. 1E-05
        mant, exp = s.split("e")
        s = "%sE%s%02d" % (mant, "-" if exp.startswith("-") else "+", abs(int(exp)))
    return s


def vb_single_str(x):
    """Format a float as the original wrote single-precision values
    (6 significant digits, e.g. 6.28848)."""
    if x == int(x) and abs(x) < 1e7:
        return str(int(x))
    return "%.6g" % x


def vb_cint(s):
    """VBScript CInt(): round half to even (Python's round does the same)."""
    return int(round(float(s)))


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

class Vertex:
    __slots__ = ("number", "kind", "label_no", "attrs", "xyz", "xyz_text")

    def __init__(self, number, kind, label_no, attrs, xyz, xyz_text=None):
        self.number = number      # position in the *Vertices list
        self.kind = kind          # "V", "L", "MV" or "CL"
        self.label_no = label_no  # number written after the kind in the label
        self.attrs = attrs        # list of (key, value) after the label
        self.xyz = tuple(xyz)     # floats
        self.xyz_text = xyz_text or [vb_str(c) for c in self.xyz]


class Edge:
    __slots__ = ("start", "end", "attrs")

    def __init__(self, start, end, attrs):
        self.start = start  # vertex number
        self.end = end
        self.attrs = attrs  # dict


def split_label(label):
    head, *pairs = label.split("|")
    kind = head.rstrip("0123456789")
    number = head[len(kind):]
    attrs = [tuple(p.split("=", 1)) for p in pairs]
    return kind, number, attrs


# --------------------------------------------------------------------------
# Reading and writing
# --------------------------------------------------------------------------

def read_pajek(path, warn=True):
    """Return ({number: Vertex}, [Edge]). Arcs are read but returned separately
    only through `read_pajek_full`; the original tool never wrote any."""
    vertices, edges, arcs = read_pajek_full(path)
    if arcs and warn:
        print("warning: %d arcs ignored (the original only produced *Edges)" % len(arcs),
              file=sys.stderr)
    return vertices, edges


def read_pajek_full(path):
    vertices, edges, arcs = {}, [], []
    section = None
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("*"):
                section = line.split()[0].lower()
                continue
            if section == "*vertices":
                num, rest = line.split(" ", 1)
                q1 = rest.index('"')
                q2 = rest.index('"', q1 + 1)
                label = rest[q1 + 1:q2]
                xyz_text = rest[q2 + 1:].split()
                while len(xyz_text) < 3:
                    xyz_text.append("0")
                kind, label_no, attrs = split_label(label)
                vertices[int(num)] = Vertex(int(num), kind, label_no, attrs,
                                            tuple(float(v) for v in xyz_text[:3]),
                                            xyz_text[:3])
            elif section in ("*edges", "*arcs"):
                a, b, rest = line.split(" ", 2)
                attrs = dict(p.split("=", 1) for p in rest.strip().strip('"').split("|") if "=" in p)
                (edges if section == "*edges" else arcs).append(Edge(int(a), int(b), attrs))
    return vertices, edges, arcs


def vertex_line(v):
    label = "%s%s|%s" % (v.kind, v.label_no, "|".join("%s=%s" % kv for kv in v.attrs))
    return '%d "%s" %s' % (v.number, label, " ".join(v.xyz_text))


def edge_line(a, b, attrs):
    items = attrs.items() if isinstance(attrs, dict) else attrs
    return '%d %d "%s"' % (a, b, "|".join("%s=%s" % kv for kv in items))


def write_pajek(path, vertices, edges):
    """vertices: list of Vertex in output order; edges: list of (a, b, attrs)."""
    lines = ["*Vertices %d" % len(vertices)]
    lines += [vertex_line(v) for v in vertices]
    lines += ["*Arcs ", "*Edges "]
    lines += [edge_line(a, b, attrs) for a, b, attrs in edges]
    with open(path, "w", newline="\r\n") as f:  # Windows line endings, as Pajek expects
        f.write("\n".join(lines) + "\n")
