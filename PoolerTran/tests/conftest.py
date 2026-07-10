"""Shared test fixtures.

Unit tests run with no external services.  Integration tests (not included
here) would need a real gateway_db + result_db via PT_TEST_GW_DSN /
PT_TEST_RESULT_DSN; they are skipped when those are unset.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make src/ importable without installing.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
