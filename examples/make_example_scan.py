#!/usr/bin/env python3
"""
Make a small synthetic scan to try scan2net on:  python3 examples/make_example_scan.py

A 20 x 10 m floor plate: a 2.4 m corridor along the south side, three
classrooms off it (each with a 1 m door), a column in the corridor, and a
1.2 m exit door at the west end left open. Writes examples/school_scan.xyz
(metres, Z up, ~250k points - sparser than a real scan, hence --tol 0.02). Then:

    scan2net examples/school_scan.xyz --tol 0.02 --exit 0.3,1.2 --location 3.5,6 --location 10,6 --location 16.5,6
"""
import os

import numpy as np

rng = np.random.default_rng(7)
pts = []


def floor(x0, y0, x1, y1, n):
    pts.append(np.c_[rng.uniform(x0, x1, n), rng.uniform(y0, y1, n), rng.normal(0, 0.002, n)])


def wall(x0, y0, x1, y1, doors=(), dens=2000):
    """A wall from (x0,y0) to (x1,y1), 2.8 m tall; doors = [(from, to)] along the wall, 2.1 m high."""
    length = float(np.hypot(x1 - x0, y1 - y0))
    n = int(length * dens)
    t = rng.uniform(0, length, n)
    z = rng.uniform(0, 2.8, n)
    keep = np.ones(n, bool)
    for a, b in doors:
        keep &= ~((t > a) & (t < b) & (z < 2.1))
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    t, z = t[keep], z[keep]
    pts.append(np.c_[x0 + ux * t + rng.normal(0, 0.003, t.size), y0 + uy * t + rng.normal(0, 0.003, t.size), z])


def column(cx, cy, r=0.25, n=1500):
    a = rng.uniform(0, 2 * np.pi, n)
    pts.append(np.c_[cx + r * np.cos(a), cy + r * np.sin(a), rng.uniform(0, 2.8, n)])


floor(0, 0, 20, 10, 60000)
wall(0, 0, 20, 0)                                   # south
wall(20, 0, 20, 10)                                 # east
wall(20, 10, 0, 10)                                 # north
wall(0, 10, 0, 0, doors=[(8.2, 9.4)])               # west, the exit door: y 0.6 - 1.8
wall(0, 2.4, 20, 2.4, doors=[(3, 4), (9.5, 10.5), (16, 17)])   # corridor wall with three doors
wall(7, 2.4, 7, 10)                                 # classroom partitions
wall(13, 2.4, 13, 10)
column(10, 1.2)
for x0, x1 in ((1, 6), (8, 12), (14, 19)):          # desks: low, below the slice, just floor-like clutter
    floor(x0, 5, x1, 8, 3000)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "school_scan.xyz")
cloud = np.vstack(pts)
np.savetxt(out, cloud, fmt="%.4f")
print("wrote %s (%d points)" % (out, len(cloud)))
