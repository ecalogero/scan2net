"""
Format-neutral geometry model shared by every reader.

A reader turns a file (.3dm, .dxf, .csv, .geojson) into a Scene:
  * points  - Point(xyz, layer, name, attrs)
  * curves  - Curve(pts, closed, layer, name, attrs): open curves are network
              edges, closed curves are candidate walkable areas
  * areas   - Area(outer, holes, layer, name, attrs): polygons that already
              carry their holes (GeoJSON Polygons, CSV polygon/hole rows)

`attrs` is the raw key -> text mapping found in the file (user text, GeoJSON
properties, CSV columns). netlib.attrs decides which keys mean what.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

XYZ = Tuple[float, float, float]


@dataclass
class Point:
    xyz: XYZ
    layer: str = ""
    name: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)


@dataclass
class Curve:
    pts: List[XYZ]
    closed: bool = False
    layer: str = ""
    name: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)
    straight: Optional[bool] = None  # True if known to be a single straight segment

    @property
    def start(self) -> XYZ:
        return self.pts[0]

    @property
    def end(self) -> XYZ:
        return self.pts[-1]


@dataclass
class Area:
    outer: List[XYZ]
    holes: List[List[XYZ]] = field(default_factory=list)
    layer: str = ""
    name: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)


@dataclass
class Scene:
    points: List[Point] = field(default_factory=list)
    curves: List[Curve] = field(default_factory=list)
    areas: List[Area] = field(default_factory=list)
    source: str = ""
    layers: Dict[str, bool] = field(default_factory=dict)   # layer name -> visible
    skipped: Dict[str, int] = field(default_factory=dict)   # reason -> count
    notes: List[str] = field(default_factory=list)

    def skip(self, reason, n=1):
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    @property
    def open_curves(self):
        return [c for c in self.curves if not c.closed]

    @property
    def closed_curves(self):
        return [c for c in self.curves if c.closed]

    def filtered(self, layers=None, exclude_layers=()):
        """A copy restricted to some layers (None = all) and without others."""
        def keep(obj):
            if obj.layer in exclude_layers:
                return False
            return layers is None or obj.layer in layers
        s = Scene(source=self.source, layers=dict(self.layers),
                  skipped=dict(self.skipped), notes=list(self.notes))
        s.points = [p for p in self.points if keep(p)]
        s.curves = [c for c in self.curves if keep(c)]
        s.areas = [a for a in self.areas if keep(a)]
        return s
