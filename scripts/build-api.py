#!/usr/bin/env python3
"""omso Builder JSON API v2: send one JSON request on stdin; read one JSON result on stdout.

Logs stream to stderr. See docs/API.md.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from omso_builder import api  # noqa: E402

if __name__ == '__main__':
    sys.exit(api.main())
