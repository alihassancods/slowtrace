"""Root conftest — ensures backend/src is on sys.path for all tests."""

import sys
from pathlib import Path

# backend/src must be importable (agent.*, api.*, services.*, etc.)
_SRC = str(Path(__file__).resolve().parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
