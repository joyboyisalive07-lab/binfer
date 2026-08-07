"""Write the synthetic corpora to disk so they can be inspected by hand.

``binfer --self-test`` builds the same corpora in memory and never touches the
filesystem; this script exists for opening the samples in a hex editor or
pointing another tool at them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running from a clone must work without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from binfer.synth import FORMATS, SAMPLES_PER_FORMAT, generate


def main() -> int:
    """Write every synthetic corpus under the requested directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="directory to write the corpora into")
    parser.add_argument(
        "--count",
        type=int,
        default=SAMPLES_PER_FORMAT,
        help=f"samples per format (default {SAMPLES_PER_FORMAT})",
    )
    args = parser.parse_args()

    for fmt in FORMATS:
        target = args.directory / f"{fmt.key.lower()}_{fmt.name}"
        target.mkdir(parents=True, exist_ok=True)
        blobs = generate(fmt, args.count)
        for index, data in enumerate(blobs):
            (target / f"sample_{index:03d}.bin").write_bytes(data)
        print(f"{fmt.key}  {len(blobs):3d} samples  {target}")
        print(f"   {fmt.summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
