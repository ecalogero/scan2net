"""
Network attribute keys, defaults and lookup.

Vertices and locations carry VTYPE, AREA, CAPACITY, LFR, DUR; edges carry
ETYPE, LENGTH, WIDTH, LFR. A key in a file matches case-insensitively, either
plain ("WIDTH") or with a section prefix ("Network\\WIDTH", "Network/WIDTH",
"Network.WIDTH", "Network:WIDTH") because the 2013 scripts kept them in a
"Network" section. Anything missing falls back to DEFAULTS (the values found
in TD_AllFloors_NEW) or to a caller-supplied override.
"""

from collections import Counter, OrderedDict

VERTEX_KEYS = ["VTYPE", "AREA", "CAPACITY", "LFR", "DUR"]
EDGE_KEYS = ["ETYPE", "LENGTH", "WIDTH", "LFR"]

DEFAULTS = {
    "V": OrderedDict(VTYPE="DEFAULT", AREA="25", CAPACITY="30", LFR="100", DUR="0"),
    "L": OrderedDict(VTYPE="LOCN", AREA="25", CAPACITY="30", LFR="100", DUR="30"),
    "E": OrderedDict(ETYPE="REAL", LENGTH="2.0", WIDTH="1.0", LFR="100"),
}

KIND_NAMES = {"V": "vertex", "L": "location", "E": "edge"}

_SEPARATORS = ("\\", "/", ".", ":", "|")


def lookup(texts, key):
    """Find `key` in a raw attribute mapping, allowing a section prefix."""
    k = key.upper()
    for name, value in texts.items():
        n = str(name).upper()
        if n == k or any(n.endswith(sep + k) for sep in _SEPARATORS):
            return None if value is None else str(value)
    return None


class AttributeResolver:
    """Fills attributes for each object and counts what came from defaults."""

    def __init__(self, overrides=None):
        self.overrides = dict(overrides or {})
        self.missing = Counter()

    def resolve(self, texts, kind, keys=None):
        keys = keys or (EDGE_KEYS if kind == "E" else VERTEX_KEYS)
        out = OrderedDict()
        for key in keys:
            value = lookup(texts, key)
            if value is None or value.strip() == "":
                value = self.overrides.get(key, DEFAULTS[kind][key])
                self.missing[(kind, key)] += 1
            out[key] = value
        return out

    def report(self):
        """Lines describing which attributes were filled from defaults."""
        lines = []
        for (kind, key), n in sorted(self.missing.items()):
            value = self.overrides.get(key, DEFAULTS[kind][key])
            lines.append("  %-8s %-8s %6d objects -> %s" % (KIND_NAMES[kind], key, n, value))
        return lines


def parse_overrides(pairs):
    """['WIDTH=1.5', 'LFR=80'] -> {'WIDTH': '1.5', 'LFR': '80'}"""
    out = {}
    for p in pairs or ():
        if "=" not in p:
            raise ValueError("expected KEY=VALUE, got %r" % p)
        k, v = p.split("=", 1)
        out[k.strip().upper()] = v.strip()
    return out
