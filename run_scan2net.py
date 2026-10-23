#!/usr/bin/env python3
"""Run scan2net without installing it:  python3 run_scan2net.py scan.ply"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan2net.cli import main  # noqa: E402

sys.exit(main())
