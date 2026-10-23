"""
scan2net - from a 3D scan to a route network ready for simulation.

    scan2net scan.ply --exit 12.5,3
    scan2net scan.xyz --units mm --up y --exit 12500,3000 --location 4000,6000 --location 9000,6000
    scan2net scan.ply --stop-after walkable          # just the slice and the walkable polygons

Writes into <scan>_scan2net/ (or -o FOLDER):
    1_slice.<ext>        the scan's points near a plane 1 m above the floor
    2_walkable.geojson   the walkable space (metres; opens in QGIS, Rhino, OrientSim's importer)
    3_network.net        the route network, Pajek, millimetres - open it in OrientSim directly
    preview.svg          a picture of all three, to check at a glance
    report.txt           what was done, with the numbers

--exit / --location are in the scan's own units and axes (as you would read
them off the point cloud in CloudCompare or Rhino), plan coordinates only.
"""

import argparse
import os
import sys
import time

from . import __version__


def xy(text):
    parts = [float(v) for v in text.replace(";", ",").split(",")]
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("expected X,Y")
    return parts[0], parts[1]


def build_parser():
    ap = argparse.ArgumentParser(prog="scan2net", description="3D scan -> walkable space -> route network.",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("scan", help="point cloud: .ply (ASCII or binary) or .xyz/.txt/.pts/.csv/.asc")
    ap.add_argument("-o", "--out", help="output folder (default: <scan>_scan2net next to the scan)")
    g = ap.add_argument_group("the scan")
    g.add_argument("--units", default="m", choices=("m", "cm", "mm", "in", "ft"), help="units of the scan")
    g.add_argument("--up", default="z", choices=("z", "y", "x"), help="vertical axis (many phone/photogrammetry "
                   "exports are y-up)")
    g.add_argument("--sliced", action="store_true", help="the input is already a slice; skip step 1")
    g = ap.add_argument_group("1. slice (metres)")
    g.add_argument("--height", type=float, default=1.0, help="height of the slice above the floor")
    g.add_argument("--tol", type=float, default=0.003, help="keep points within this of the slice plane")
    g.add_argument("--floor-percentile", type=float, default=0.1,
                   help="floor = this percentile of the heights; 0 = the lowest point (stray points below "
                        "the floor then spoil it)")
    g = ap.add_argument_group("2. walkable space (metres)")
    g.add_argument("--cell", type=float, default=0.05, help="grid cell size")
    g.add_argument("--gap", type=float, default=0.3,
                   help="holes in walls narrower than this are closed (doorways must be wider); the "
                        "walkable space also stays half of this away from walls")
    g.add_argument("--door-max", type=float, default=2.0,
                   help="openings to the outside up to this wide (open doors, unscanned glass) are bridged")
    g.add_argument("--min-area", type=float, default=1.0, help="drop free areas smaller than this, m2")
    g.add_argument("--clearance", type=float, default=0.0, help="extra distance to keep from walls")
    g.add_argument("--polygon-simplify", type=float, default=0.15, help="polygon simplification tolerance")
    g.add_argument("--only-connected", action="store_true",
                   help="keep only the walkable areas containing an --exit or --location")
    g = ap.add_argument_group("3. network")
    g.add_argument("--exit", type=xy, action="append", default=[], metavar="X,Y",
                   help="an exit (repeatable; scan units and axes)")
    g.add_argument("--location", type=xy, action="append", default=[], metavar="X,Y",
                   help="a room / location to link in (repeatable)")
    g.add_argument("--min-length", type=float, default=0.5, help="minimum corridor segment length, m")
    g.add_argument("--angle", type=float, default=10.0, help="bends within this many degrees of straight are "
                   "joined")
    g.add_argument("--no-simplify", action="store_true", help="keep every corner of the walkable space")
    g.add_argument("--width", type=float, default=1.2, help="corridor width written to the network, m")
    g.add_argument("--capacity", type=float, default=30, help="vertex capacity written to the network")
    ap.add_argument("--stop-after", choices=("slice", "walkable"), help="stop after this step")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        from .pipeline import Options, run
        from .preview import write_svg
    except ImportError as exc:                     # pragma: no cover
        sys.exit("error: %s\nInstall the requirements with:  pip install numpy scipy shapely" % exc)
    if not os.path.exists(args.scan):
        sys.exit("error: %s does not exist" % args.scan)
    out = args.out or os.path.splitext(args.scan)[0] + "_scan2net"
    opt = Options(units=args.units, up=args.up, sliced=args.sliced, height=args.height, tol=args.tol,
                  floor_percentile=args.floor_percentile, cell=args.cell, gap=args.gap,
                  door_max=args.door_max, clearance=args.clearance, min_area=args.min_area,
                  polygon_simplify=args.polygon_simplify, exits=args.exit, locations=args.location,
                  seed_regions=args.only_connected, simplify=not args.no_simplify,
                  min_length=args.min_length, angle=args.angle, width=args.width, capacity=args.capacity,
                  stop_after=args.stop_after)
    t0 = time.time()
    print("scan2net %s: %s" % (__version__, args.scan))
    try:
        res = run(args.scan, out, opt, progress=print)
    except (ValueError, OSError, ImportError) as exc:
        sys.exit("error: %s" % exc)
    if res.polygons:
        svg = os.path.join(out, "preview.svg")
        write_svg(svg, res)
        res.files["preview"] = svg
    for w in res.warnings:
        print("warning: " + w)
    report = os.path.join(out, "report.txt")
    with open(report, "w") as f:
        f.write("scan2net %s\nscan: %s\noptions: %s\n\n" % (__version__, os.path.abspath(args.scan), opt))
        f.write("\n".join(res.lines + ["warning: " + w for w in res.warnings]) + "\n\nfiles:\n")
        f.write("\n".join("  %-9s %s" % (k, v) for k, v in res.files.items()) + "\n")
    print("done in %.1fs - files in %s" % (time.time() - t0, os.path.abspath(out)))
    for k, v in res.files.items():
        print("  %-9s %s" % (k, os.path.basename(v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
