#!/usr/bin/env python
"""Convenience entrypoint for the GCSI Phase 2B benchmark.

Usage:
    python scripts/run_benchmark.py --help
    python scripts/run_benchmark.py --dry-run
    python scripts/run_benchmark.py --provider Granite --suite core --repetitions 5 --execute-live
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.app.benchmark.runner_cli import main

if __name__ == "__main__":
    sys.exit(main())
