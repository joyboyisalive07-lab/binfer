"""Sample loading, size classification and window alignment.

Two alignment modes exist. When every sample has the same size, offsets are
absolute and the whole file is one coordinate system. When sizes differ, only a
window at the start and a window at the end can be compared across samples; the
span between them is per-file and is handed to the record and region stages
rather than to the column statistics.
"""

from __future__ import annotations

import enum
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from binfer.model import CorpusSummary

if TYPE_CHECKING:
    from pathlib import Path

MIN_SAMPLES = 4
RECOMMENDED_SAMPLES = 12

# Column statistics, the checksum search and the record scan are all linear in
# window size. 64 KiB at each end covers the fixed part of every format this was
# tested against, and whatever falls between the two windows is reported as
# unanalysed rather than quietly dropped.
WINDOW_LIMIT = 64 * 1024

# A file far larger than the others is nearly always a different thing that
# happens to share the directory, and loading it would dominate memory use.
MAX_SAMPLE_BYTES = 64 * 1024 * 1024


class CorpusError(Exception):
    """The sample set cannot support any analysis."""


class AlignmentMode(enum.Enum):
    """How offsets in the report are to be read."""

    FIXED = "fixed"
    HEAD_TAIL = "head-tail"


@dataclass(frozen=True, slots=True)
class Sample:
    """One loaded sample file."""

    name: str
    data: bytes

    @property
    def size(self) -> int:
        """Return the file length in bytes."""
        return len(self.data)


@dataclass(frozen=True, slots=True)
class Alignment:
    """The two windows that can be compared across the whole corpus."""

    mode: AlignmentMode
    head_size: int
    tail_size: int

    def middle(self, sample_size: int) -> tuple[int, int]:
        """Return the span of one sample that neither window covers."""
        start = self.head_size
        end = sample_size - self.tail_size
        return (start, end) if end > start else (start, start)

    def tail_offset(self, sample_size: int) -> int:
        """Return the absolute offset at which the tail window begins."""
        return sample_size - self.tail_size


@dataclass(frozen=True, slots=True)
class Corpus:
    """The loaded sample set, in a fixed order."""

    samples: tuple[Sample, ...]
    discovered: int
    warnings: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        """Return the number of analysed samples."""
        return len(self.samples)

    @property
    def sizes(self) -> tuple[int, ...]:
        """Return the sample sizes in corpus order."""
        return tuple(sample.size for sample in self.samples)

    @property
    def min_size(self) -> int:
        """Return the smallest sample size."""
        return min(self.sizes)

    @property
    def max_size(self) -> int:
        """Return the largest sample size."""
        return max(self.sizes)

    @property
    def uniform(self) -> bool:
        """Return whether every sample has the same size."""
        return self.min_size == self.max_size

    def head_window(self, size: int) -> tuple[bytes, ...]:
        """Return the first ``size`` bytes of every sample."""
        return tuple(sample.data[:size] for sample in self.samples)

    def tail_window(self, size: int) -> tuple[bytes, ...]:
        """Return the last ``size`` bytes of every sample."""
        if size == 0:
            return tuple(b"" for _ in self.samples)
        return tuple(sample.data[-size:] for sample in self.samples)

    def summary(self) -> CorpusSummary:
        """Return the size statistics rendered in the CORPUS section."""
        sizes = self.sizes
        return CorpusSummary(
            discovered=self.discovered,
            analyzed=len(sizes),
            min_size=min(sizes),
            max_size=max(sizes),
            mean_size=statistics.fmean(sizes),
            median_size=int(statistics.median_low(sizes)),
            distinct_sizes=len(set(sizes)),
        )


def load_corpus(directory: Path, *, max_files: int | None = None) -> Corpus:
    """Load every regular file directly inside ``directory``.

    Files are taken in name order so that ``--max-files`` selects the same
    subset on every run and on every platform.
    """
    if not directory.is_dir():
        raise CorpusError(f"not a directory: {directory}")

    paths = sorted((p for p in directory.iterdir() if p.is_file()), key=lambda p: p.name)
    discovered = len(paths)
    if max_files is not None:
        paths = paths[:max_files]

    samples: list[Sample] = []
    empty = 0
    oversized = 0
    for path in paths:
        size = path.stat().st_size
        if size == 0:
            empty += 1
        elif size > MAX_SAMPLE_BYTES:
            oversized += 1
        else:
            samples.append(Sample(path.name, path.read_bytes()))

    if len(samples) < MIN_SAMPLES:
        raise CorpusError(
            f"need at least {MIN_SAMPLES} non-empty samples to compare, loaded {len(samples)}"
        )

    warnings: list[str] = []
    if empty:
        warnings.append(f"skipped {empty} empty file(s)")
    if oversized:
        warnings.append(f"skipped {oversized} file(s) larger than {MAX_SAMPLE_BYTES} bytes")
    if len(samples) < RECOMMENDED_SAMPLES:
        warnings.append(
            f"only {len(samples)} samples; below {RECOMMENDED_SAMPLES} a constant column is"
            " weak evidence, since few files can coincide by chance"
        )

    return Corpus(samples=tuple(samples), discovered=discovered, warnings=tuple(warnings))


def plan_alignment(corpus: Corpus) -> Alignment:
    """Choose the head and tail windows for this corpus."""
    min_size = corpus.min_size
    if corpus.uniform and min_size <= 2 * WINDOW_LIMIT:
        return Alignment(AlignmentMode.FIXED, min_size, 0)

    mode = AlignmentMode.FIXED if corpus.uniform else AlignmentMode.HEAD_TAIL
    half = min_size // 2
    if half == 0:
        return Alignment(mode, min_size, 0)
    budget = min(WINDOW_LIMIT, half)
    return Alignment(mode, budget, budget)
