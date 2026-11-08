# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""Portable entrypoint; run from any current working directory."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel.autoresearch.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
