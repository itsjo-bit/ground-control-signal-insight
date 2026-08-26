"""Benchmark package entry point — allows python -m backend.app.benchmark.runner_cli"""
from .runner_cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
