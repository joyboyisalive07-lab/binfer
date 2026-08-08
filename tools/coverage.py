"""Measure line coverage of the core modules with the standard library only.

``pytest-cov`` would be the obvious tool, but the dependency list is fixed at
pytest, ruff and pyinstaller, so this drives :mod:`trace` instead. It runs the
suite in-process, counts the lines that executed, and compares them with the
lines the module actually has.
"""

from __future__ import annotations

import sys
import trace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

# Renderer whitespace and the synthetic generator are not what the target is
# about; the algorithm is.
CORE = ("corpus.py", "stats.py", "types.py", "relations.py", "records.py", "analyze.py")
TARGET = 85.0


def executable_lines(path: Path) -> set[int]:
    """Return the line numbers that carry a statement.

    ``trace`` exposes this as a private helper. It is the same computation the
    module uses for its own reports, and reimplementing it here would only add
    a second thing to be wrong.
    """
    return set(trace._find_executable_linenos(str(path)))  # noqa: SLF001


def main() -> int:
    """Run the suite under trace and print a per-module coverage table."""
    tracer = trace.Trace(count=1, trace=0)
    tracer.runfunc(pytest.main, ["-q", str(ROOT / "tests")])
    executed = tracer.results().counts

    print(f"\n{'MODULE':<16}{'LINES':>8}{'HIT':>8}{'COVER':>9}")
    total_lines = 0
    total_hit = 0
    worst: list[str] = []
    for name in CORE:
        path = ROOT / "src" / "binfer" / name
        lines = executable_lines(path)
        hit = {line for (filename, line) in executed if Path(filename) == path} & lines
        share = 100.0 * len(hit) / len(lines) if lines else 100.0
        total_lines += len(lines)
        total_hit += len(hit)
        print(f"{name:<16}{len(lines):>8}{len(hit):>8}{share:>8.1f}%")
        if share < TARGET:
            worst.append(f"{name} at {share:.1f}%")

    overall = 100.0 * total_hit / total_lines if total_lines else 100.0
    print(f"{'TOTAL':<16}{total_lines:>8}{total_hit:>8}{overall:>8.1f}%")
    if overall < TARGET:
        print(f"\ncore coverage {overall:.1f}% is below the {TARGET:.0f}% target")
        return 1
    for entry in worst:
        print(f"note: {entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
