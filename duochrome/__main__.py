"""Enable `python -m duochrome` (same entry point as the `duochrome` script)."""
from __future__ import annotations

import sys

from duochrome.cli import app

if __name__ == "__main__":
    # Double-clicking the packaged .app gives us argv == [binary] with no
    # subcommand — default to the desktop GUI so a window actually opens.
    if len(sys.argv) == 1:
        sys.argv.append("desktop")
    app()
