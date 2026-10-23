"""
Point clouds from 3D scans: read .ply (ASCII / binary) and .xyz-style text
point lists, and write subsets of them. Used by slice_scan.py,
walkable_from_scan.py and the OrientSim importer.
"""

import os
import re

import numpy as np

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "ft": 0.3048, "in": 0.0254}
AXES = {"x": 0, "y": 1, "z": 2}
XYZ_EXTS = (".xyz", ".txt", ".pts", ".csv", ".asc")

# -------------------------------------------------------------------------
# .xyz and other text point lists: two streaming passes, lines copied as-is
# -------------------------------------------------------------------------

_SPLIT = re.compile(r"[,\s;]+")


def _coords(line):
    parts = _SPLIT.split(line.strip(), 4)
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def _text_heights(path, axis, chunk=2_000_000):
    """All heights, one float per point."""
    out, buf = [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            c = _coords(line)
            if c is not None:
                buf.append(c[axis])
                if len(buf) >= chunk:
                    out.append(np.asarray(buf, dtype=np.float64))
                    buf = []
    if buf:
        out.append(np.asarray(buf, dtype=np.float64))
    return np.concatenate(out) if out else np.empty(0)


def read_points(path, chunk=2_000_000):
    """Any supported file -> (N, 3) float64 array of x, y, z in file units."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ply":
        arr, _ = Ply(path).read_vertices()
        return np.column_stack([np.asarray(arr[a], dtype=np.float64) for a in "xyz"])
    if ext not in XYZ_EXTS:
        raise ValueError("unsupported file type %r (use .ply or %s)" % (ext, " ".join(XYZ_EXTS)))
    out, buf = [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            c = _coords(line)
            if c is not None:
                buf.append(c)
                if len(buf) >= chunk:
                    out.append(np.asarray(buf, dtype=np.float64))
                    buf = []
    if buf:
        out.append(np.asarray(buf, dtype=np.float64))
    return np.concatenate(out) if out else np.empty((0, 3))


def slice_text(path, out_path, axis, plane_lo, plane_hi):
    kept, seen_point = 0, False
    with open(path, "r", errors="replace") as f, open(out_path, "w") as g:
        for line in f:
            c = _coords(line)
            if c is None:
                if not seen_point and line.strip():
                    g.write(line)          # header lines before the first point are kept
                continue
            seen_point = True
            if plane_lo <= c[axis] <= plane_hi:
                g.write(line)
                kept += 1
    return kept


# -------------------------------------------------------------------------
# .ply
# -------------------------------------------------------------------------

PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


class Ply:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            if f.readline().strip() != b"ply":
                raise ValueError("%s is not a PLY file" % path)
            self.header_lines = [b"ply"]
            self.elements = []          # [name, count, [(type, name) or ('list', ct, it, name)]]
            self.format = None
            while True:
                raw = f.readline()
                if not raw:
                    raise ValueError("PLY header has no end_header")
                line = raw.strip()
                self.header_lines.append(line)
                words = line.split()
                if not words:
                    continue
                key = words[0]
                if key == b"format":
                    self.format = words[1].decode()
                elif key == b"element":
                    self.elements.append([words[1].decode(), int(words[2]), []])
                elif key == b"property":
                    if words[1] == b"list":
                        self.elements[-1][2].append(("list", words[2].decode(), words[3].decode(),
                                                     words[4].decode()))
                    else:
                        self.elements[-1][2].append((words[1].decode(), words[2].decode()))
                elif key == b"end_header":
                    break
            self.data_offset = f.tell()
        if not self.elements or self.elements[0][0] != "vertex":
            # vertex is nearly always first; we only need what comes before it to be skippable
            names = [e[0] for e in self.elements]
            if "vertex" not in names:
                raise ValueError("PLY file has no vertex element")
            if names.index("vertex") != 0:
                raise ValueError("PLY vertex element is not the first element; not supported")
        self.n = self.elements[0][1]
        self.props = self.elements[0][2]
        if any(p[0] == "list" for p in self.props):
            raise ValueError("PLY vertex element has list properties; not supported")
        names = [p[1] for p in self.props]
        for a in ("x", "y", "z"):
            if a not in names:
                raise ValueError("PLY vertices have no '%s' property" % a)

    def vertex_dtype(self):
        end = "<" if self.format == "binary_little_endian" else ">"
        return np.dtype([(name, end + PLY_TYPES[t]) for t, name in self.props])

    def read_vertices(self):
        """Returns (structured array of vertices, raw text lines or None)."""
        if self.format == "ascii":
            lines = []
            with open(self.path, "rb") as f:
                f.seek(self.data_offset)
                for _ in range(self.n):
                    lines.append(f.readline())
            names = [p[1] for p in self.props]
            idx = [names.index(a) for a in ("x", "y", "z")]
            arr = np.empty(self.n, dtype=[("x", "f8"), ("y", "f8"), ("z", "f8")])
            for i, line in enumerate(lines):
                w = line.split()
                arr[i] = (float(w[idx[0]]), float(w[idx[1]]), float(w[idx[2]]))
            return arr, lines
        if self.format not in ("binary_little_endian", "binary_big_endian"):
            raise ValueError("unknown PLY format %r" % self.format)
        arr = np.memmap(self.path, dtype=self.vertex_dtype(), mode="r",
                        offset=self.data_offset, shape=(self.n,))
        return arr, None

    def write_subset(self, out_path, arr, lines, mask):
        kept = int(mask.sum())
        header, current = [], None
        for line in self.header_lines:
            words = line.split()
            if words[:1] == [b"element"]:
                current = words[1]
                if current != b"vertex":
                    continue                       # faces etc. are dropped
                line = b"element vertex %d" % kept
            elif words[:1] == [b"property"] and current != b"vertex":
                continue
            header.append(line)
        header.insert(2, b"comment sliced by slice_scan.py from " + os.path.basename(self.path).encode())
        with open(out_path, "wb") as g:
            g.write(b"\n".join(header) + b"\n")
            if lines is not None:
                for line, keep in zip(lines, mask):
                    if keep:
                        g.write(line if line.endswith(b"\n") else line + b"\n")
            else:
                np.asarray(arr[mask]).tofile(g)
        return kept


# -------------------------------------------------------------------------
