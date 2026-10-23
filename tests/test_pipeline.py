"""End-to-end: a synthetic two-room scan -> slice -> walkable space -> network."""

import json
import os
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scan2net.cli import main                                  # noqa: E402
from scan2net.core.pajek import read_pajek                     # noqa: E402
from scan2net.pipeline import Options, run                     # noqa: E402


def two_rooms(scale=1.0):
    """Two 5 x 4 m rooms side by side, a 1 m door between them; returns N x 3 (x, y, z)."""
    rng = np.random.default_rng(3)
    pts = [np.c_[rng.uniform(0, 10, 20000), rng.uniform(0, 4, 20000), np.zeros(20000)]]

    def wall(x0, y0, x1, y1, gap=None, n=40000):
        t = rng.uniform(0, 1, n)
        z = rng.uniform(0, 2.7, n)
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        keep = np.ones(n, bool) if gap is None else ~((y > gap[0]) & (y < gap[1]) & (z < 2.1))
        pts.append(np.c_[x, y, z][keep])
    for w in [(0, 0, 10, 0), (10, 0, 10, 4), (10, 4, 0, 4), (0, 4, 0, 0)]:
        wall(*w)
    wall(5, 0, 5, 4, gap=(1.5, 2.5))
    return np.vstack(pts) * scale


def write_binary_ply(path, xyz):
    with open(path, "wb") as f:
        f.write(("ply\nformat binary_little_endian 1.0\nelement vertex %d\nproperty float x\n"
                 "property float y\nproperty float z\nend_header\n" % len(xyz)).encode())
        f.write(xyz.astype("<f4").tobytes())


def test_xyz_all_three_steps(tmp_path):
    scan = tmp_path / "rooms.xyz"
    np.savetxt(str(scan), two_rooms(), fmt="%.4f")
    opt = Options(tol=0.01, exits=[(0.5, 2.0)], locations=[(8.0, 2.0)])
    res = run(str(scan), str(tmp_path / "out"), opt)
    assert set(res.files) == {"slice", "walkable", "network"}
    assert len(res.polygons) == 1 and 30 < res.polygons[0].area < 40      # the door joins the rooms
    gj = json.load(open(res.files["walkable"]))
    assert gj["features"][0]["geometry"]["type"] == "Polygon"
    vs, es = read_pajek(res.files["network"])
    types = [dict(v.attrs)["VTYPE"] for v in vs.values()]
    assert types.count("EXIT") == 1 and types.count("LOCN") == 1
    xs = [v.xyz[0] for v in vs.values()]
    assert max(xs) > 9000                                  # millimetres
    for e in es:
        assert float(e.attrs["LENGTH"]) >= 0.5 - 1e-6      # metres, no slivers
    assert any("every location can reach an exit" in line for line in res.lines)


def test_binary_ply_in_mm_y_up(tmp_path):
    xyz = two_rooms(scale=1000.0)[:, [0, 2, 1]]          # millimetres, Y up
    scan = tmp_path / "rooms.ply"
    write_binary_ply(str(scan), xyz)
    res = run(str(scan), str(tmp_path / "out"), Options(units="mm", up="y", tol=0.01, stop_after="walkable"))
    assert res.files["slice"].endswith("1_slice.ply") and "network" not in res.files
    assert len(res.polygons) == 1 and 30 < res.polygons[0].area < 40


def test_exit_just_outside_is_moved_in(tmp_path):
    scan = tmp_path / "rooms.xyz"
    np.savetxt(str(scan), two_rooms(), fmt="%.4f")
    res = run(str(scan), str(tmp_path / "out"), Options(tol=0.01, exits=[(-0.3, 2.0)]))
    assert any("moved inside" in w for w in res.warnings)
    assert len(res.exit_points) == 1 and res.polygons[0].covers(__import__("shapely").geometry.Point(res.exit_points[0]))


def test_cli(tmp_path, capsys):
    scan = tmp_path / "rooms.xyz"
    np.savetxt(str(scan), two_rooms(), fmt="%.4f")
    out = tmp_path / "o"
    assert main([str(scan), "-o", str(out), "--tol", "0.01", "--exit", "0.5,2"]) == 0
    for name in ("1_slice.xyz", "2_walkable.geojson", "3_network.net", "preview.svg", "report.txt"):
        assert (out / name).exists(), name
    assert "<svg" in (out / "preview.svg").read_text()


def test_wrong_units_give_a_clear_error(tmp_path):
    scan = tmp_path / "rooms.xyz"
    np.savetxt(str(scan), two_rooms(), fmt="%.4f")
    with pytest.raises(ValueError):
        run(str(scan), str(tmp_path / "out"), Options(units="mm"))      # 1 m above floor = 1000 m: nothing
